"""Persistent authentication throttling with short outcome transactions.

Read admission in one transaction, hash outside it, then record the outcome in
another transaction. Raise public refusals only after the outcome commits.
"""
from __future__ import annotations

import hmac
import ipaddress
import itertools
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.deployment_policy import RateLimitRules, get_deployment_policy
from app.core.config import settings
from app.core.security import normalize_email

logger = logging.getLogger(__name__)
# Cleanup cadence is best-effort per worker; all security state is in PostgreSQL.
_attempts = itertools.count(1)


def utcnow() -> datetime:
    """Clock seam shared by service tests and the HTTP scenario."""
    return datetime.now(timezone.utc)


def cleanup_due() -> bool:
    return next(_attempts) % get_deployment_policy().rate_limit_rules.cleanup_every == 0


def key_hash(scope: str, kind: str, value: str) -> bytes:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope):
        raise ValueError("Invalid throttle scope")
    if kind == "email":
        normalized = normalize_email(value)
    elif kind == "ip":
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            normalized = "invalid"
        else:
            if isinstance(address, ipaddress.IPv6Address):
                address = address.ipv4_mapped or ipaddress.IPv6Network((address, 64), strict=False).network_address
            normalized = str(address)
    else:
        raise ValueError("Invalid throttle key kind")
    pepper = hmac.digest(settings.session_secret.encode(), b"verwalino-auth-throttle", "sha256")
    return hmac.digest(pepper, f"{scope}:{normalized}".encode("utf-8"), "sha256")


@dataclass
class ThrottleAttempt:
    session: AsyncSession
    scope: str
    rows: list[dict]
    rules: RateLimitRules
    now: datetime
    request_id: str

    @property
    def allowed(self) -> bool:
        return not any(row["locked_until"] is not None and row["locked_until"] > self.now for row in self.rows)

    async def record_failure(self) -> None:
        """Count every refusal, without extending an active lockout."""
        for key in self.rows:
            # Admission is read-only. Acquire fresh state here, including when
            # a concurrent success deleted the email row after admission.
            row = dict((await self.session.execute(text("""INSERT INTO public.auth_throttle
                (key_kind,key_hash,window_start,updated_at) VALUES (:key_kind,:key_hash,:now,:now)
                ON CONFLICT (key_kind,key_hash) DO UPDATE SET key_hash=EXCLUDED.key_hash
                RETURNING *"""), {**key, "now": utcnow()})).mappings().one())
            # Read after acquiring the row lock so contention cannot shorten a lockout.
            now = utcnow()
            if (row["last_lockout_at"] is not None
                and row["last_lockout_at"] < now - timedelta(minutes=self.rules.lockout_decay_minutes)):
                row["lockouts"] = 0
            active_lock = row["locked_until"] is not None and row["locked_until"] > now
            if not active_lock and (
                row["locked_until"] is not None
                or row["window_start"] + timedelta(minutes=self.rules.window_minutes) <= now
            ):
                row.update(window_start=now, failures=0, locked_until=None)
            row["failures"] += 1
            limit = self.rules.ip_failures if row["key_kind"] == "ip" else self.rules.email_failures
            if not active_lock and row["failures"] >= limit:
                minutes = self.rules.lockout_minutes
                # Saturate history at the cap; no unbounded exponent or counter.
                repeats = 0
                while minutes < self.rules.max_lockout_minutes and repeats < row["lockouts"]:
                    minutes = min(minutes * self.rules.lockout_multiplier, self.rules.max_lockout_minutes)
                    repeats += 1
                row.update(lockouts=repeats + 1, locked_until=now + timedelta(minutes=minutes), last_lockout_at=now)
                logger.warning("Authentication lockout scope=%s kind=%s key_hash=%s request_id=%s",
                               self.scope, row["key_kind"], row["key_hash"].hex(), self.request_id)
            await self.session.execute(text("""UPDATE public.auth_throttle SET
                window_start=:window_start,failures=:failures,locked_until=:locked_until,
                lockouts=:lockouts,last_lockout_at=:last_lockout_at,updated_at=:now
                WHERE key_kind=:key_kind AND key_hash=:key_hash"""), {**row, "now": now})

    async def reset(self) -> None:
        """Success clears email history; IP failures survive successful logins."""
        for row in self.rows:
            if row["key_kind"] == "email":
                await self.session.execute(text("DELETE FROM public.auth_throttle WHERE key_kind=:key_kind AND key_hash=:key_hash"), row)


async def cleanup_stale(rules: RateLimitRules) -> None:
    """One bounded best-effort batch, in its own post-outcome transaction."""
    try:
        async with get_session_factory()() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout='100ms'"))
            await session.execute(text("SET LOCAL statement_timeout='1s'"))
            await session.execute(text("""DELETE FROM public.auth_throttle WHERE ctid IN (
                SELECT ctid FROM public.auth_throttle WHERE updated_at < :cutoff
                ORDER BY updated_at LIMIT :batch_size FOR UPDATE SKIP LOCKED)"""),
                {"cutoff": utcnow() - timedelta(days=1), "batch_size": rules.cleanup_batch_size})
    except SQLAlchemyError:
        logger.warning("Authentication throttle cleanup failed")


async def check_and_record(
    session: AsyncSession, scope: str, keys: Mapping[str, str], rules: RateLimitRules,
    *, request_id: str | None = None,
) -> ThrottleAttempt:
    """Read scoped lock status without writing or locking throttle rows.

    Commit this transaction before hashing. The returned attempt acquires fresh
    row state only when recording its outcome in the caller's next transaction.
    """
    if not keys:
        raise ValueError("Throttle keys are required")
    digests = sorted((kind, key_hash(scope, kind, value)) for kind, value in keys.items())
    rows = []
    for kind, digest in digests:
        locked_until = await session.scalar(text("""SELECT locked_until FROM public.auth_throttle
            WHERE key_kind=:kind AND key_hash=:digest"""), {"kind": kind, "digest": digest})
        rows.append({"key_kind": kind, "key_hash": digest, "locked_until": locked_until})
    return ThrottleAttempt(session, scope, rows, rules, utcnow(), request_id or str(uuid.uuid4()))


@dataclass(frozen=True)
class ThrottleRequest:
    scope: str
    ip: str
    request_id: str

    async def check(self, session: AsyncSession, email: str) -> ThrottleAttempt:
        return await check_and_record(session, self.scope, {"ip": self.ip, "email": email},
                                      get_deployment_policy().rate_limit_rules, request_id=self.request_id)


def throttled(scope: str):
    """Inject scope/IP metadata; the handler owns admission/outcome transactions.

    Uvicorn resolves trusted forwarded headers via FORWARDED_ALLOW_IPS. Never
    parse X-Forwarded-For again here, or an untrusted caller could spoof its key.
    """
    async def dependency(request: Request) -> ThrottleRequest:
        peer = request.client.host if request.client else "invalid"
        return ThrottleRequest(scope, peer, getattr(request.state, "request_id", None) or str(uuid.uuid4()))
    return dependency
