"""Tenant-scoped persistence for declaration import drafts and links."""

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.declaration_import import (
    DeclarationExtractionRun,
    DeclarationImport,
    DeclarationImportDocument,
    DeclarationImportPass,
)
from app.declarations.observability import PassLedgerEntry
from app.models.property_document import PropertyDocument
from app.repositories.base import TenantRepository


@dataclass(frozen=True, slots=True)
class WorkerLease:
    organization_id: uuid.UUID
    import_id: uuid.UUID
    lease_generation: int
    lease_token: uuid.UUID
    lease_expires_at: datetime
    queue_request_id: uuid.UUID | None = None


class DeclarationPrincipalDenied(Exception):
    """Unwind effects before committing a separately fenced cancellation."""


class DeclarationImportRepository(TenantRepository[DeclarationImport]):
    model = DeclarationImport

    async def lock_acl(self):
        await self._session.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
            {"key": f"scope-acl:{self._org_id}"})

    async def _set_queue_actor(self, import_id: uuid.UUID, queue_request_id: uuid.UUID | None) -> bool:
        actor = await self._session.scalar(text("SELECT declaration_queue_actor(:org,:job,:request)"),
            {"org": self._org_id, "job": import_id, "request": queue_request_id})
        if actor is None:
            return False
        await self._session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(actor)})
        return True

    async def cancel_unauthorized(self, lease: WorkerLease) -> bool:
        if lease.organization_id != self._org_id:
            return False
        return bool(await self._session.scalar(text("SELECT cancel_unauthorized_declaration(:org,:job,:request,:generation,:token)"),
            {"org": self._org_id, "job": lease.import_id, "request": lease.queue_request_id,
             "generation": lease.lease_generation, "token": lease.lease_token}))

    async def claim_next(
        self,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> WorkerLease | None:
        """Claim one due import using a tenant-local, fenced short transaction."""
        # `now` is retained for caller compatibility; the database owns time.
        row = (await self._session.execute(text("SELECT * FROM claim_declaration_work(:org,:worker,:seconds)"),
            {"org": self._org_id, "worker": worker_id, "seconds": lease_seconds})).mappings().first()
        if row is None:
            return None
        return WorkerLease(self._org_id, row['import_id'], row['lease_generation'], row['lease_token'],
            row['lease_expires_at'], row['queue_request_id'])

    async def heartbeat(
        self,
        lease: WorkerLease,
        now: datetime,
        lease_seconds: int,
    ) -> WorkerLease | DeclarationPrincipalDenied | None:
        """Recheck principal and renew a live fence; denial commits cancellation."""
        if lease.organization_id != self._org_id:
            return None
        expiry = await self._session.scalar(text("SELECT renew_declaration_work(:org,:job,:request,:generation,:token,:seconds)"),
            {"org": self._org_id, "job": lease.import_id, "request": lease.queue_request_id,
             "generation": lease.lease_generation, "token": lease.lease_token, "seconds": lease_seconds})
        if expiry is None:
            cancelled = await self.cancel_unauthorized(lease)
            return DeclarationPrincipalDenied() if cancelled else None
        return replace(lease, lease_expires_at=expiry)

    async def count_extraction_runs(self, import_id: uuid.UUID) -> int:
        """Return the tenant-local worker retry history, never provider usage."""
        result = await self._session.execute(
            select(func.count())
            .select_from(DeclarationExtractionRun)
            .where(DeclarationExtractionRun.organization_id == self._org_id)
            .where(DeclarationExtractionRun.import_id == import_id)
        )
        return int(result.scalar_one())

    async def append_pass_entries(
        self,
        run_id: uuid.UUID,
        entries: Sequence[PassLedgerEntry],
    ) -> tuple[DeclarationImportPass, ...]:
        """Append typed, content-free SDK-attempt metadata for one tenant run.

        The run row is locked before calculating the next number.  This gives
        all checkpoint transactions a deterministic, globally sequential
        ``attempt_no`` without relying on a caller-local provider retry count.
        """
        if not isinstance(run_id, uuid.UUID):
            raise ValueError("invalid extraction run id")
        if not all(isinstance(entry, PassLedgerEntry) for entry in entries):
            raise ValueError("entries must be PassLedgerEntry instances")
        if not entries:
            return ()

        run = await self._session.scalar(
            select(DeclarationExtractionRun)
            .where(DeclarationExtractionRun.organization_id == self._org_id)
            .where(DeclarationExtractionRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise ValueError("extraction run not found")
        last_attempt = await self._session.scalar(
            select(func.max(DeclarationImportPass.attempt_no))
            .where(DeclarationImportPass.organization_id == self._org_id)
            .where(DeclarationImportPass.declaration_extraction_run_id == run_id)
        )
        start = int(last_attempt or 0)
        rows: list[DeclarationImportPass] = []
        for offset, entry in enumerate(entries, start=1):
            global_entry = replace(entry, attempt_no=start + offset)
            row = DeclarationImportPass(
                organization_id=self._org_id,
                declaration_extraction_run_id=run.id,
                attempt_no=global_entry.attempt_no,
                safe_metadata=global_entry.as_safe_dict(),
            )
            self._session.add(row)
            rows.append(row)
        await self._session.flush()
        return tuple(rows)

    async def count_documents(self, import_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(DeclarationImportDocument)
            .where(DeclarationImportDocument.organization_id == self._org_id)
            .where(DeclarationImportDocument.import_id == import_id)
        )
        return int(result.scalar_one())

    async def get_by_id_for_update(
        self, import_id: uuid.UUID
    ) -> DeclarationImport | None:
        result = await self._session.execute(
            self._base_query()
            .where(DeclarationImport.id == import_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_document_link(
        self, import_id: uuid.UUID, document_id: uuid.UUID
    ) -> DeclarationImportDocument | None:
        result = await self._session.execute(
            select(DeclarationImportDocument)
            .where(DeclarationImportDocument.organization_id == self._org_id)
            .where(DeclarationImportDocument.import_id == import_id)
            .where(DeclarationImportDocument.document_id == document_id)
        )
        return result.scalar_one_or_none()

    async def get_active_review_draft_document(
        self, import_id: uuid.UUID, document_id: uuid.UUID
    ) -> PropertyDocument | None:
        """Load one active, unbound PDF from a reviewable import atomically.

        The import status must be part of this final query.  Checking it in a
        preceding SELECT would permit a status transition between that check
        and the link/document lookup under READ COMMITTED.
        """
        result = await self._session.execute(
            select(PropertyDocument)
            .join(
                DeclarationImportDocument,
                (DeclarationImportDocument.organization_id == PropertyDocument.organization_id)
                & (DeclarationImportDocument.document_id == PropertyDocument.id),
            )
            .join(
                DeclarationImport,
                (DeclarationImport.organization_id == DeclarationImportDocument.organization_id)
                & (DeclarationImport.id == DeclarationImportDocument.import_id),
            )
            .where(PropertyDocument.organization_id == self._org_id)
            .where(DeclarationImportDocument.organization_id == self._org_id)
            .where(DeclarationImport.organization_id == self._org_id)
            .where(DeclarationImportDocument.import_id == import_id)
            .where(DeclarationImportDocument.document_id == document_id)
            .where(DeclarationImport.id == import_id)
            .where(DeclarationImport.status.in_({"DRAFT", "NEEDS_REVIEW"}))
            .where(PropertyDocument.id == document_id)
            .where(PropertyDocument.property_id.is_(None))
            .where(PropertyDocument.archived_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_sequence(
        self, import_id: uuid.UUID, sequence_no: int
    ) -> DeclarationImportDocument | None:
        result = await self._session.execute(
            select(DeclarationImportDocument)
            .where(DeclarationImportDocument.organization_id == self._org_id)
            .where(DeclarationImportDocument.import_id == import_id)
            .where(DeclarationImportDocument.sequence_no == sequence_no)
        )
        return result.scalar_one_or_none()

    async def list_documents(
        self, import_id: uuid.UUID
    ) -> Sequence[tuple[DeclarationImportDocument, PropertyDocument]]:
        result = await self._session.execute(
            select(DeclarationImportDocument, PropertyDocument)
            .join(
                PropertyDocument,
                (PropertyDocument.organization_id == DeclarationImportDocument.organization_id)
                & (PropertyDocument.id == DeclarationImportDocument.document_id),
            )
            .where(DeclarationImportDocument.organization_id == self._org_id)
            .where(DeclarationImportDocument.import_id == import_id)
            .order_by(DeclarationImportDocument.sequence_no)
        )
        return result.all()

    async def lock_documents_for_approval(
        self, import_id: uuid.UUID
    ) -> Sequence[tuple[DeclarationImportDocument, PropertyDocument]]:
        """Lock exactly this import's document bindings in a stable order."""
        result = await self._session.execute(
            select(DeclarationImportDocument, PropertyDocument)
            .join(
                PropertyDocument,
                (PropertyDocument.organization_id == DeclarationImportDocument.organization_id)
                & (PropertyDocument.id == DeclarationImportDocument.document_id),
            )
            .where(DeclarationImportDocument.organization_id == self._org_id)
            .where(DeclarationImportDocument.import_id == import_id)
            .order_by(DeclarationImportDocument.sequence_no, DeclarationImportDocument.document_id)
            .with_for_update()
        )
        return result.all()

    async def create_document_link(
        self, link: DeclarationImportDocument
    ) -> DeclarationImportDocument:
        link.organization_id = self._org_id
        self._session.add(link)
        await self._session.flush()
        return link

    async def verify_lease(self, lease: WorkerLease) -> bool:
        return await self.lock_current_lease(lease, {"QUEUED", "RETRY_WAIT", "PREFLIGHT", "EXTRACTING", "RECONCILING"}) is not None

    async def lock_current_lease(
        self,
        lease: WorkerLease,
        expected_statuses: set[str],
    ) -> DeclarationImport | None:
        """Lock and verify a current fence in one statement.

        A separate ``verify`` followed by an insert/update leaves a cancellation
        or reclaim window.  Callers that mutate a run or publish a graph must
        use this helper before making *any* related change in their transaction.
        """
        if lease.organization_id != self._org_id or not expected_statuses:
            return None
        await self.lock_acl()
        current = await self._session.scalar(text(
            "SELECT lock_declaration_lease_metadata(:org,:job,:request,:generation,:token,CAST(:statuses AS text[]))"),
            {"org": self._org_id, "job": lease.import_id, "request": lease.queue_request_id,
             "generation": lease.lease_generation, "token": lease.lease_token, "statuses": sorted(expected_statuses)})
        if not current:
            return None
        if not await self._set_queue_actor(lease.import_id, lease.queue_request_id):
            raise DeclarationPrincipalDenied()
        return await self._session.scalar(self._base_query().where(DeclarationImport.id == lease.import_id))

    async def get_run_for_import_for_update(
        self,
        import_id: uuid.UUID,
        run_id: uuid.UUID | None,
    ) -> DeclarationExtractionRun | None:
        """Tenant- and import-scoped run lookup; never use ``session.get``."""
        if run_id is None:
            return None
        result = await self._session.execute(
            select(DeclarationExtractionRun)
            .where(DeclarationExtractionRun.organization_id == self._org_id)
            .where(DeclarationExtractionRun.import_id == import_id)
            .where(DeclarationExtractionRun.id == run_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def close_running_runs_for_import(
        self,
        import_id: uuid.UUID,
        *,
        error_code: str,
        completed_at: datetime,
    ) -> int:
        """Close active audit rows inside the caller's tenant transaction.

        Cancellation uses this today; Task 9 reclaim can reuse the same
        operation before it installs a new fenced worker lease.
        """
        result = await self._session.execute(
            select(DeclarationExtractionRun)
            .where(DeclarationExtractionRun.organization_id == self._org_id)
            .where(DeclarationExtractionRun.import_id == import_id)
            .where(DeclarationExtractionRun.status == "RUNNING")
            .with_for_update()
        )
        rows = result.scalars().all()
        for run in rows:
            run.status = "FAILED"
            run.error_code = error_code
            run.completed_at = completed_at
        await self._session.flush()
        return len(rows)

    async def transition_with_lease(
        self,
        lease: WorkerLease,
        expected_statuses: set[str],
        new_status: str,
        values: dict[str, object],
    ) -> bool:
        """Fenced state mutation; false means the worker has lost its lease."""
        if lease.organization_id != self._org_id:
            return False
        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty")
        forbidden = {"id", "organization_id", "status", "lease_generation", "queue_request_id", "initiated_by", "draft_revision"}
        if forbidden.intersection(values):
            raise ValueError("values may not override identity, status, or lease generation")
        # A caller may only clear the three lease ownership values together;
        # it cannot install a different token or fabricate a fresh fence.
        lease_fields = {"lease_owner", "lease_token", "lease_expires_at"}
        supplied_lease_fields = lease_fields.intersection(values)
        if supplied_lease_fields and (
            supplied_lease_fields != lease_fields
            or any(values[name] is not None for name in lease_fields)
        ):
            raise ValueError("lease values may only be cleared together")
        if await self.lock_current_lease(lease, expected_statuses) is None:
            return False
        return bool(await self._session.scalar(text(
            "SELECT transition_declaration_work(:org,:job,:request,:generation,:token,CAST(:expected AS text[]),:target,CAST(:patch AS jsonb))"),
            {"org": self._org_id, "job": lease.import_id, "request": lease.queue_request_id,
             "generation": lease.lease_generation, "token": lease.lease_token, "expected": sorted(expected_statuses),
             "target": new_status, "patch": json.dumps(values, default=str)}))
