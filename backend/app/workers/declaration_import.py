"""RLS-safe, fenced dispatcher for declaration-import extraction work."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import secrets
import shutil
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.core.db import get_tenant_session, get_unscoped_session
from app.models.organization import Organization
from app.repositories.declaration_import_repo import (
    DeclarationImportRepository,
    DeclarationPrincipalDenied,
    WorkerLease,
)

logger = logging.getLogger(__name__)

_PIPELINE_ENTRY_STATUSES = {"QUEUED", "RETRY_WAIT"}
_REQUIRED_LEASE_COLUMNS = {"lease_token", "lease_generation", "lease_expires_at", "queue_request_id"}
_HEALTHCHECK_TESSERACT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class WorkerResult:
    code: str
    import_id: uuid.UUID | None = None


class DeclarationImportWorker:
    """Claim organization-local jobs and fence all processing with a lease."""

    def __init__(
        self,
        pipeline: Any | None = None,
        *,
        configured: Settings = settings,
        tenant_session_factory: Callable[
            [uuid.UUID], AbstractAsyncContextManager[AsyncSession]
        ] = get_tenant_session,
        unscoped_session_factory: Callable[
            [], AbstractAsyncContextManager[AsyncSession]
        ] = get_unscoped_session,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        heartbeat_seconds: float | None = None,
    ) -> None:
        self._settings = configured
        self._pipeline = pipeline
        self._tenant_session_factory = tenant_session_factory
        self._unscoped_session_factory = unscoped_session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._last_claimed_organization_id: uuid.UUID | None = None
        self._validate_settings(heartbeat_seconds)
        self._heartbeat_seconds = (
            float(heartbeat_seconds)
            if heartbeat_seconds is not None
            else float(self._settings.declaration_lease_seconds) / 3
        )

    def _validate_settings(self, heartbeat_seconds: float | None) -> None:
        worker_id = self._settings.declaration_worker_id
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("declaration_worker_id must be nonblank")
        poll = self._settings.declaration_worker_poll_seconds
        if (
            isinstance(poll, bool)
            or not isinstance(poll, (int, float))
            or not math.isfinite(float(poll))
            or poll <= 0
        ):
            raise ValueError("declaration_worker_poll_seconds must be finite and positive")
        lease = self._settings.declaration_lease_seconds
        if isinstance(lease, bool) or not isinstance(lease, int) or lease < 30:
            raise ValueError("declaration_lease_seconds must be an integer of at least 30")
        attempts = self._settings.declaration_max_attempts
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            raise ValueError("declaration_max_attempts must be a positive integer")
        interval = lease / 3 if heartbeat_seconds is None else heartbeat_seconds
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(float(interval))
            or interval <= 0
            or interval > lease / 3
        ):
            raise ValueError("heartbeat interval must be positive and no greater than one third of the lease")

    async def schema_ready(self) -> bool:
        """Check only PostgreSQL catalog metadata before touching tenant tables."""
        statement = text(
            """
            SELECT a.attname
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
            WHERE n.nspname = 'public'
              AND c.relname = 'declaration_import'
              AND c.relkind IN ('r', 'p')
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND a.attname IN ('lease_token', 'lease_generation', 'lease_expires_at', 'queue_request_id')
            """
        )
        async with self._unscoped_session_factory() as session:
            found = set((await session.execute(statement)).scalars().all())
            if not _REQUIRED_LEASE_COLUMNS.issubset(found):
                return False
            return bool(await session.scalar(text("""
                SELECT to_regprocedure('public.next_declaration_queue_metadata(uuid,timestamp with time zone)') IS NOT NULL
                   AND to_regprocedure('public.lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[])') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute
                       WHERE attrelid=to_regclass('public.declaration_candidate_evidence')
                         AND attname='import_id' AND attnum>0 AND NOT attisdropped AND attnotnull)
            """)))

    async def _organization_ids(self) -> tuple[uuid.UUID, ...]:
        """Use the global table only; tenant scans always happen below."""
        async with self._unscoped_session_factory() as session:
            result = await session.execute(select(Organization.id).order_by(Organization.id))
            return tuple(result.scalars().all())

    async def claim_one(self) -> WorkerLease | None:
        now = self._clock()
        organization_ids = await self._organization_ids()
        if self._last_claimed_organization_id in organization_ids:
            start = organization_ids.index(self._last_claimed_organization_id) + 1
            organization_ids = organization_ids[start:] + organization_ids[:start]
        for organization_id in organization_ids:
            async with self._tenant_session_factory(organization_id) as session:
                lease = await DeclarationImportRepository(
                    session, organization_id
                ).claim_next(
                    self._settings.declaration_worker_id.strip(),
                    now,
                    self._settings.declaration_lease_seconds,
                )
            if lease is not None:
                self._last_claimed_organization_id = organization_id
                return lease
        return None

    async def _attempt_limit_reached(self, lease: WorkerLease) -> bool:
        async with self._tenant_session_factory(lease.organization_id) as session:
            repo = DeclarationImportRepository(session, lease.organization_id)
            if await repo.lock_current_lease(lease, _PIPELINE_ENTRY_STATUSES) is None:
                return False
            return (
                await repo.count_extraction_runs(lease.import_id)
                >= self._settings.declaration_max_attempts
            )

    async def _fail_attempt_limit(self, lease: WorkerLease) -> bool:
        async with self._tenant_session_factory(lease.organization_id) as session:
            return await DeclarationImportRepository(session, lease.organization_id).transition_with_lease(
                lease,
                _PIPELINE_ENTRY_STATUSES,
                "FAILED",
                {
                    "current_stage": "failed",
                    "error_code": "WORKER_ATTEMPT_LIMIT_REACHED",
                    "error_message": "WORKER_ATTEMPT_LIMIT_REACHED",
                    "next_attempt_at": None,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                },
            )

    async def _heartbeat(self, initial_lease: WorkerLease) -> bool | str:
        """Distinguish committed permission cancellation from ordinary lease loss."""
        lease = initial_lease
        while True:
            try:
                await self._sleep(self._heartbeat_seconds)
                async with self._tenant_session_factory(lease.organization_id) as session:
                    renewed = await DeclarationImportRepository(
                        session, lease.organization_id
                    ).heartbeat(
                        lease,
                        self._clock(),
                        self._settings.declaration_lease_seconds,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                return True
            if isinstance(renewed, DeclarationPrincipalDenied):
                return "JOB_PERMISSION_CHANGED"
            if renewed is None:
                return True
            lease = renewed

    async def _run_claimed(self, lease: WorkerLease) -> WorkerResult:
        if self._pipeline is None:
            raise RuntimeError("DeclarationImportWorker requires a pipeline")
        pipeline_task = asyncio.create_task(self._pipeline.run(lease))
        heartbeat_task = asyncio.create_task(self._heartbeat(lease))
        try:
            done, _pending = await asyncio.wait(
                {pipeline_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if pipeline_task in done:
                # Completion wins even when a final heartbeat observes the
                # resulting status in the same event-loop turn.
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
                try:
                    await pipeline_task
                except asyncio.CancelledError:
                    raise
                except DeclarationPrincipalDenied:
                    return WorkerResult("JOB_PERMISSION_CHANGED", lease.import_id)
                except Exception:
                    # Do not log arbitrary exception text: providers and parsers
                    # may attach untrusted document fragments to it.
                    logger.warning(
                        "declaration worker result=PIPELINE_ERROR import_id=%s",
                        lease.import_id,
                    )
                    return WorkerResult("PIPELINE_ERROR", lease.import_id)
                return WorkerResult("PROCESSED", lease.import_id)
            if heartbeat_task in done:
                lease_lost = await heartbeat_task
                if lease_lost:
                    pipeline_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await pipeline_task
                    return WorkerResult(lease_lost if isinstance(lease_lost, str) else "LEASE_LOST", lease.import_id)
            raise RuntimeError("worker wait completed without a task result")
        finally:
            for task in (pipeline_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pipeline_task, heartbeat_task, return_exceptions=True)

    async def run_once(self) -> WorkerResult:
        # A disabled import feature must not acquire a lease or invoke external
        # parsing. The independent healthcheck still probes worker readiness.
        if not self._settings.declaration_import_enabled:
            return WorkerResult("DECLARATION_IMPORT_DISABLED")
        if not await self.schema_ready():
            return WorkerResult("SCHEMA_NOT_READY")
        lease = await self.claim_one()
        if lease is None:
            return WorkerResult("IDLE")
        try:
            if await self._attempt_limit_reached(lease):
                if await self._fail_attempt_limit(lease):
                    return WorkerResult("WORKER_ATTEMPT_LIMIT_REACHED", lease.import_id)
                return WorkerResult("LEASE_LOST", lease.import_id)
            return await self._run_claimed(lease)
        except DeclarationPrincipalDenied:
            async with self._tenant_session_factory(lease.organization_id) as session:
                await DeclarationImportRepository(session, lease.organization_id).cancel_unauthorized(lease)
            return WorkerResult("JOB_PERMISSION_CHANGED", lease.import_id)

    async def run_forever(self) -> None:
        delay = float(self._settings.declaration_worker_poll_seconds)
        while True:
            try:
                result = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("declaration worker result=UNEXPECTED")
                await self._sleep(min(max(delay, 0.01), 30.0))
                continue
            logger.info("declaration worker result=%s import_id=%s", result.code, result.import_id)
            # Keep retrying missing migrations/empty queues, but bound any idle
            # wait to avoid an unresponsive process after configuration changes.
            await self._sleep(min(max(delay, 0.01), 30.0))


async def run_healthcheck(
    *,
    configured: Settings = settings,
    unscoped_session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = get_unscoped_session,
    binary_finder: Callable[[str], str | None] = shutil.which,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    random_name: Callable[[], str] = lambda: secrets.token_hex(16),
    fsync: Callable[[int], None] = os.fsync,
) -> bool:
    """Probe only global infrastructure required by the worker healthcheck."""
    try:
        async with unscoped_session_factory() as session:
            await session.execute(select(Organization.id).limit(1))
        if any(binary_finder(name) is None for name in ("qpdf", "pdftoppm", "tesseract")):
            return False
        languages = command_runner(
            ["tesseract", "--list-langs"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_HEALTHCHECK_TESSERACT_TIMEOUT_SECONDS,
        )
        if languages.returncode != 0 or "deu" not in (languages.stdout or "").splitlines():
            return False

        healthcheck_directory = Path(configured.document_storage_root) / ".healthcheck"
        healthcheck_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        probe_path = healthcheck_directory / f"{random_name()}.tmp"
        probe_bytes = secrets.token_bytes(32)
        try:
            with probe_path.open("xb") as probe_file:
                probe_file.write(probe_bytes)
                probe_file.flush()
                fsync(probe_file.fileno())
            if probe_path.read_bytes() != probe_bytes:
                return False
        finally:
            try:
                probe_path.unlink()
            except FileNotFoundError:
                pass
    except Exception:
        return False
    return True


async def _main() -> None:
    """Production-only construction; import and tests never perform I/O."""
    from app.declarations.openai_provider import OpenAiDeclarationProvider
    from app.declarations.pipeline import DeclarationPipeline

    provider = OpenAiDeclarationProvider()
    await DeclarationImportWorker(DeclarationPipeline(provider)).run_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the declaration import worker.")
    parser.add_argument("--healthcheck", action="store_true")
    options = parser.parse_args(argv)
    if options.healthcheck:
        return 0 if asyncio.run(run_healthcheck()) else 1
    asyncio.run(_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
