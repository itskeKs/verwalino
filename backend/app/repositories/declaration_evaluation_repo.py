"""Bounded, tenant-scoped reads for a recorded declaration evaluation."""
from dataclasses import dataclass
import uuid

from sqlalchemy import select, text

from app.models.audit_event import AuditEvent
from app.models.declaration_candidate import DeclarationCandidate, DeclarationCandidateEvidence, DeclarationCandidateIssue
from app.models.declaration_import import DeclarationImport, DeclarationImportDocument, DeclarationExtractionRun, DeclarationImportPass, DeclarationQueueRequest
from app.models.property_document import PropertyDocument


class RecordedCaptureUnavailable(ValueError):
    """Only fixed diagnostic codes may leave the capture boundary."""


@dataclass(frozen=True)
class RecordedImportSnapshot:
    item: DeclarationImport
    documents: tuple
    runs: tuple
    queues: tuple
    passes: tuple
    candidates: tuple
    evidence: tuple
    issues: tuple
    reviewed: bool


class DeclarationEvaluationRepository:
    def __init__(self, session, organization_id: uuid.UUID):
        self.session = session
        self.org = organization_id

    async def read(self, import_id: uuid.UUID, input_digest: str) -> RecordedImportSnapshot:
        principal = (await self.session.execute(text("SELECT current_user, current_setting('transaction_read_only')"))).one()
        if principal != ('app_rw', 'on'):
            raise RecordedCaptureUnavailable('READ_ONLY_RUNTIME_REQUIRED')
        if await self.session.scalar(text('SELECT scope_actor_role(:org)'), {'org': self.org}) is None:
            raise RecordedCaptureUnavailable('IMPORT_NOT_VISIBLE')
        if not await self.session.scalar(text("SELECT property_import_runtime_access(:org,:job,'read')"), {'org': self.org, 'job': import_id}):
            raise RecordedCaptureUnavailable('IMPORT_NOT_VISIBLE')
        item = await self.session.scalar(select(DeclarationImport).where(DeclarationImport.organization_id == self.org, DeclarationImport.id == import_id))
        if item is None:
            raise RecordedCaptureUnavailable('IMPORT_NOT_VISIBLE')
        documents = tuple((await self.session.scalars(select(PropertyDocument).join(DeclarationImportDocument,
            (DeclarationImportDocument.organization_id == PropertyDocument.organization_id)
            & (DeclarationImportDocument.document_id == PropertyDocument.id)).where(
                DeclarationImportDocument.organization_id == self.org, DeclarationImportDocument.import_id == import_id,
                PropertyDocument.organization_id == self.org).limit(2))).all())
        if len(documents) != 1 or documents[0].content_sha256 != input_digest:
            raise RecordedCaptureUnavailable('SYNTHETIC_INPUT_MISMATCH')
        runs = tuple((await self.session.scalars(select(DeclarationExtractionRun).where(
            DeclarationExtractionRun.organization_id == self.org, DeclarationExtractionRun.import_id == import_id).limit(2))).all())
        queues = tuple((await self.session.scalars(select(DeclarationQueueRequest).where(
            DeclarationQueueRequest.organization_id == self.org, DeclarationQueueRequest.import_id == import_id).limit(2))).all())
        passes = () if len(runs) != 1 else tuple((await self.session.scalars(select(DeclarationImportPass).where(
            DeclarationImportPass.organization_id == self.org, DeclarationImportPass.declaration_extraction_run_id == runs[0].id
        ).order_by(DeclarationImportPass.attempt_no).limit(65))).all())
        candidates = tuple((await self.session.scalars(select(DeclarationCandidate).where(
            DeclarationCandidate.organization_id == self.org, DeclarationCandidate.import_id == import_id).limit(1001))).all())
        evidence = tuple((await self.session.scalars(select(DeclarationCandidateEvidence).where(
            DeclarationCandidateEvidence.organization_id == self.org, DeclarationCandidateEvidence.import_id == import_id).limit(10001))).all())
        issues = tuple((await self.session.scalars(select(DeclarationCandidateIssue).where(
            DeclarationCandidateIssue.organization_id == self.org, DeclarationCandidateIssue.import_id == import_id).limit(1001))).all())
        reviewed = bool(await self.session.scalar(select(AuditEvent.id).where(
            AuditEvent.organization_id == self.org, AuditEvent.entity_id == import_id,
            AuditEvent.action == 'declaration_import.candidate_reviewed').limit(1)))
        if len(candidates) > 1000 or len(evidence) > 10000 or len(issues) > 1000 or len(passes) > 64:
            raise RecordedCaptureUnavailable('CAPTURE_LIMIT_EXCEEDED')
        return RecordedImportSnapshot(item, documents, runs, queues, passes, candidates, evidence, issues, reviewed)
