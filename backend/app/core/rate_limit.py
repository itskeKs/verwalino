"""Serialize authentication attempts across workers using global hashed keys.

Call check_and_record inside a transaction, then record_failure or reset before
committing. Raise the public refusal only AFTER commit, so failures persist.
"""
from __future__ import annotations

import hashlib
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deployment_policy import RateLimitRules, get_deployment_policy
from app.core.security import normalize_email

logger = logging.getLogger(__name__)
# Cleanup cadence is best-effort per worker; all security state is in PostgreSQL.
CLEANUP_EVERY = 100
_attempts = itertools.count(1)


def utcnow() -> datetime:
    """Clock seam shared by service tests and the HTTP scenario."""
    return datetime.now(timezone.utc)


def cleanup_due() -> bool:
    return next(_attempts) % CLEANUP_EVERY == 0


def key_hash(scope: str, kind: str, value: str) -> bytes:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope):
        raise ValueError("Invalid throttle scope")
    if kind == "email":
        normalized = normalize_email(value)
    elif kind == "ip":
        address = ipaddress.ip_address(value)
        normalized = str(address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else str(address)
    else:
        raise ValueError("Invalid throttle key kind")
    return hashlib.sha256(f"{scope}:{normalized}".encode("utf-8")).digest()


@dataclass
class ThrottleAttempt:
    session: AsyncSession
    scope: str
    rows: list[dict]
    rules: RateLimitRules
    now: datetime
    request_id: str
    cleanup: bool

    @property
    def allowed(self) -> bool:
        return not any(row["locked_until"] is not None and row["locked_until"] > self.now for row in self.rows)

    async def record_failure(self) -> None:
        """Count every refusal, without extending an active lockout."""
        for row in self.rows:
            active_lock = row["locked_until"] is not None and row["locked_until"] > self.now
            if not active_lock and (
                row["locked_until"] is not None
                or row["window_start"] + timedelta(minutes=self.rules.window_minutes) <= self.now
            ):
                row.update(window_start=self.now, failures=0, locked_until=None)
            row["failures"] += 1
            limit = self.rules.ip_failures if row["key_kind"] == "ip" else self.rules.email_failures
            if not active_lock and row["failures"] >= limit:
                minutes = self.rules.lockout_minutes
                # Saturate history at the cap; no unbounded exponent or counter.
                repeats = 0
                while minutes < self.rules.max_lockout_minutes and repeats < row["lockouts"]:
                    minutes = min(minutes * self.rules.lockout_multiplier, self.rules.max_lockout_minutes)
                    repeats += 1
                row.update(lockouts=repeats + 1, locked_until=self.now + timedelta(minutes=minutes))
                logger.warning("Authentication lockout scope=%s kind=%s key_hash=%s request_id=%s",
                               self.scope, row["key_kind"], row["key_hash"].hex(), self.request_id)
            await self.session.execute(text("""UPDATE public.auth_throttle SET
                window_start=:window_start,failures=:failures,locked_until=:locked_until,
                lockouts=:lockouts,updated_at=:now WHERE key_kind=:key_kind AND key_hash=:key_hash"""),
                {**row, "now": self.now})
        await self._cleanup_stale()

    async def reset(self) -> None:
        """Successful authentication clears both keys and escalation history."""
        for row in self.rows:
            await self.session.execute(text("DELETE FROM public.auth_throttle WHERE key_kind=:key_kind AND key_hash=:key_hash"), row)
        await self._cleanup_stale()

    async def _cleanup_stale(self) -> None:
        if self.cleanup:
            # All request keys have already been acquired and finalized. Take
            # only nonblocking cleanup locks, with no later key acquisition.
            await self.session.execute(text("""DELETE FROM public.auth_throttle WHERE (key_kind,key_hash) IN (
                SELECT key_kind,key_hash FROM public.auth_throttle
                WHERE updated_at < :cutoff
                ORDER BY key_kind,key_hash FOR UPDATE SKIP LOCKED)"""), {"cutoff": self.now - timedelta(days=1)})


async def check_and_record(
    session: AsyncSession, scope: str, keys: Mapping[str, str], rules: RateLimitRules,
    *, request_id: str | None = None,
) -> ThrottleAttempt:
    """Create/lock scoped rows before checking credentials; record the outcome.

    Consistent key order and row locks cover lookup, verification and outcome
    in the caller's transaction. Concurrent requests cannot lose increments.
    """
    if not keys:
        raise ValueError("Throttle keys are required")
    digests = sorted((kind, key_hash(scope, kind, value)) for kind, value in keys.items())
    rows = []
    for kind, digest in digests:
        # A single upsert acquires and returns the locked row. DO NOTHING then
        # SELECT would race with a successful request deleting that same row.
        row = (await session.execute(text("""INSERT INTO public.auth_throttle
            (key_kind,key_hash,window_start,updated_at) VALUES (:kind,:digest,:now,:now)
            ON CONFLICT (key_kind,key_hash) DO UPDATE SET key_hash=EXCLUDED.key_hash
            RETURNING *"""), {"kind": kind, "digest": digest, "now": utcnow()})).mappings().one()
        rows.append(dict(row))
    # Read the clock after locks: waiting behind another attempt must not shorten
    # a newly created lockout or admit an attempt with an obsolete timestamp.
    return ThrottleAttempt(session, scope, rows, rules, utcnow(), request_id or str(uuid.uuid4()), cleanup_due())


@dataclass(frozen=True)
class ThrottleRequest:
    scope: str
    ip: str
    request_id: str

    async def check(self, session: AsyncSession, email: str) -> ThrottleAttempt:
        return await check_and_record(session, self.scope, {"ip": self.ip, "email": email},
                                      get_deployment_policy().rate_limit_rules, request_id=self.request_id)


def throttled(scope: str):
    """Inject scope/IP metadata; the handler records its outcome in one TX.

    Uvicorn resolves trusted forwarded headers via FORWARDED_ALLOW_IPS. Never
    parse X-Forwarded-For again here, or an untrusted caller could spoof its key.
    """
    async def dependency(request: Request) -> ThrottleRequest:
        peer = request.client.host if request.client else "0.0.0.0"
        return ThrottleRequest(scope, peer, getattr(request.state, "request_id", None) or str(uuid.uuid4()))
    return dependency
