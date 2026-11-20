"""Bounded, review-first API for declaration-import drafts."""

from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tenant import AuthContext, require_write_dep
from app.core.live_principal import get_live_writer_db, get_live_publication_db, get_live_review_db
from app.core.uploads import read_upload_limited
from app.declarations.provider import READING_PASS_ID
from app.declarations.review_schemas import (
    ApprovalPreviewOut,
    ApprovalRequest,
    BulkCandidateReviewRequest,
    CandidateOut,
    CandidateReviewRequest,
    ReviewImportOut,
)
from app.models.declaration_import import DeclarationImport
from app.models.property_document import PropertyDocument
from app.services.declaration_import_service import (
    DeclarationImportService,
    require_declaration_import_enabled,
)
from app.services.declaration_approval_service import DeclarationApprovalService
from app.services.declaration_review_service import DeclarationReviewService
from app.services.property_document_service import (
    PropertyDocumentService,
    VerifiedPropertyDocumentContent,
    safe_inline_filename,
)

router = APIRouter(prefix="/api/declaration-imports", tags=["declaration-imports"])


class DeclarationImportCreate(BaseModel):
    mode: Literal["NEW_PROPERTY", "EXISTING_PROPERTY"]
    target_property_id: uuid.UUID | None = None


class DeclarationStageOut(BaseModel):
    """What one stage of a run (reading pages, extracting information) consumed, with the model that did it."""

    stage: Literal["READING", "EXTRACTION"]
    model: str
    # Only what the stage's ledger entries report: some entry reported usage, or none did. A reservation is never here.
    usage_status: Literal["REPORTED", "UNKNOWN"]
    reported_input_tokens: int | None
    reported_output_tokens: int | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal | None


class DeclarationUnassignedOut(BaseModel):
    """What the run's totals hold beyond its stages: a reservation no ledger entry attributes to a stage."""

    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal | None


class DeclarationRunOut(BaseModel):
    usage_status: Literal["REPORTED", "PARTIAL", "RESERVED", "UNKNOWN"]
    reported_input_tokens: int | None
    reported_output_tokens: int | None
    id: str
    model: str
    stages: list[DeclarationStageOut]
    unassigned: DeclarationUnassignedOut | None
    input_tokens: int
    output_tokens: int
    runtime_seconds: float
    closed: bool
    estimated_cost_usd: Decimal | None


class DeclarationImportOut(BaseModel):
    runs: list[DeclarationRunOut] = Field(default_factory=list)
    id: str
    mode: str
    target_property_id: str | None
    applied_property_id: str | None
    status: str
    draft_revision: int
    progress_percent: int
    current_stage: str | None
    error_code: str | None
    error_message: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    attempt_count: int
    max_cost_usd: Decimal
    queue_request_id: str | None = None
    command_receipt_id: str | None = None


class PropertyDocumentOut(BaseModel):
    id: str
    property_id: str | None
    doc_type: str
    title: str
    original_filename: str
    mime_type: str
    content_byte_size: int
    content_sha256: str
    sequence_no: int | None = None
    document_role: str | None = None


class DocumentMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    document_role: Literal["DECLARATION", "AMENDMENT", "ANNEX", "OTHER"] | None = None
    sequence_no: int | None = Field(default=None, ge=1)


@router.post("", response_model=DeclarationImportOut, status_code=201)
async def create_import(
    body: DeclarationImportCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).create(
        body.mode, body.target_property_id
    ))


@router.post("/{import_id}/documents", response_model=PropertyDocumentOut, status_code=201)
async def upload_document(
    import_id: uuid.UUID,
    file: UploadFile = File(...),
    document_role: str = Form(...),
    sequence_no: int = Form(...),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    # Fail before consuming the multipart stream when imports are disabled,
    # then enforce the deploy-time byte limit during the bounded read.
    await require_declaration_import_enabled(db, auth.organization_id)
    file_bytes = await read_upload_limited(file, settings.declaration_max_pdf_bytes)
    service = DeclarationImportService(db, auth.organization_id, auth.user_id)
    document = await service.upload_document(
        import_id,
        file_bytes,
        original_filename=file.filename or "document.pdf",
        mime_type=file.content_type or "application/octet-stream",
        document_role=document_role,
        sequence_no=sequence_no,
    )
    return _document_out(document, sequence_no=sequence_no, document_role=document_role)


@router.patch("/{import_id}/documents/{document_id}", response_model=PropertyDocumentOut)
async def update_document_metadata(
    import_id: uuid.UUID,
    document_id: uuid.UUID,
    body: DocumentMetadataUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    document, link = await DeclarationImportService(
        db, auth.organization_id, auth.user_id
    ).update_document_metadata(
        import_id,
        document_id,
        title=body.title,
        document_role=body.document_role,
        sequence_no=body.sequence_no,
    )
    return _document_out(
        document,
        sequence_no=link.sequence_no,
        document_role=link.document_role,
    )


@router.get("/{import_id}/documents", response_model=list[PropertyDocumentOut])
async def list_import_documents(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    rows = await DeclarationImportService(
        db, auth.organization_id, auth.user_id
    ).list_documents(import_id)
    return [
        _document_out(
            document,
            sequence_no=link.sequence_no,
            document_role=link.document_role,
        )
        for link, document in rows
    ]


@router.post("/{import_id}/start", response_model=DeclarationImportOut)
async def start_import(
    import_id: uuid.UUID,
    idempotency_key: uuid.UUID = Header(alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).start(import_id, idempotency_key))


@router.get("/{import_id}", response_model=DeclarationImportOut)
async def get_import(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    from app.repositories.declaration_import_repo import DeclarationImportRepository
    item = await DeclarationImportService(db, auth.organization_id, auth.user_id).get(import_id)
    repository = DeclarationImportRepository(db, auth.organization_id)
    runs = await repository.list_runs(import_id)
    passes = await repository.list_run_passes(import_id)
    result = _import_out(item)
    result.runs = [_run_out(run, [entry.safe_metadata for entry in passes if entry.declaration_extraction_run_id == run.id]) for run in runs]
    return result


@router.get("/{import_id}/documents/{document_id}/content")
async def get_import_document_content(
    import_id: uuid.UUID,
    document_id: uuid.UUID,
    page: int | None = Query(default=None, gt=0),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    # Accepted for PDF-viewer compatibility only; storage always uses the
    # persisted content-addressed URI from the authorized document row.
    del page
    verified = await PropertyDocumentService(
        db, auth.organization_id, auth.user_id
    ).get_review_draft_content(import_id, document_id)
    return _pdf_response(verified)


@router.get("/{import_id}/candidates", response_model=list[CandidateOut])
async def list_candidates(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).list_candidates(import_id)


@router.patch("/{import_id}/candidates/{candidate_id}", response_model=ReviewImportOut)
async def review_candidate(
    import_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: CandidateReviewRequest,
    idempotency_key: uuid.UUID | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_review_db, scope="function"),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).review_one(import_id, candidate_id, body, idempotency_key)


@router.post("/{import_id}/candidates/bulk-review", response_model=ReviewImportOut)
async def bulk_review_candidates(
    import_id: uuid.UUID,
    body: BulkCandidateReviewRequest,
    idempotency_key: uuid.UUID | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_review_db, scope="function"),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).review_bulk(import_id, body, idempotency_key)


@router.get("/{import_id}/approval-preview", response_model=ApprovalPreviewOut)
async def approval_preview(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).preview(import_id)


@router.post("/{import_id}/retry", response_model=DeclarationImportOut)
async def retry_import(
    import_id: uuid.UUID,
    idempotency_key: uuid.UUID = Header(alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).retry(import_id, idempotency_key))


@router.post("/{import_id}/approve")
async def approve_import_boundary(
    import_id: uuid.UUID,
    body: ApprovalRequest,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_publication_db),
):
    return await DeclarationApprovalService(
        db, auth.organization_id, auth.user_id
    ).approve(import_id, body)

@router.post("/{import_id}/cancel", response_model=DeclarationImportOut)
async def cancel_import(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).cancel(import_id))


def _is_reported(entry) -> bool:
    return entry["failure_class"] is None or entry["input_tokens"] > 0 or entry["output_tokens"] > 0


def _run_out(run, passes=()) -> DeclarationRunOut:
    from app.core.ai_models import model_price
    provider = {"OpenAiDeclarationProvider": "openai", "AnthropicDeclarationProvider": "anthropic"}.get(run.provider)

    def priced(model: str) -> bool:
        return provider is not None and model_price(model, provider) is not None

    reported = [entry for entry in passes if _is_reported(entry)]
    input_tokens = sum(entry["input_tokens"] for entry in reported)
    output_tokens = sum(entry["output_tokens"] for entry in reported)
    complete = bool(reported) and len(reported) == len(passes) and (input_tokens, output_tokens) == (run.input_tokens, run.output_tokens)
    usage_status = "REPORTED" if complete else "PARTIAL" if reported else "RESERVED" if run.input_tokens or run.output_tokens else "UNKNOWN"
    end = run.completed_at or datetime.now(timezone.utc)
    stages, unassigned = _run_stages(run, passes, priced)
    # The total is priced only when every line that consumed something is; an unpriced model that made no call
    # does not hide a known cost, and a stage with unknown usage has no cost to price (its reservation is in the
    # unassigned line, which carries the price rule).
    lines = [*(stage for stage in stages if stage.usage_status != "UNKNOWN"), *([unassigned] if unassigned else [])]
    total_priced = all(line.estimated_cost_usd is not None for line in lines) if lines else priced(run.model_name)
    return DeclarationRunOut(id=str(run.id), model=run.model_name,
        usage_status=usage_status,
        reported_input_tokens=input_tokens if reported else None,
        reported_output_tokens=output_tokens if reported else None,
        stages=stages, unassigned=unassigned,
        input_tokens=run.input_tokens, output_tokens=run.output_tokens,
        runtime_seconds=max(0, (end - run.started_at).total_seconds()), closed=run.completed_at is not None,
        estimated_cost_usd=run.estimated_cost_usd if total_priced else None)


def _run_stages(run, passes, is_priced) -> tuple[list[DeclarationStageOut], DeclarationUnassignedOut | None]:
    """The two stages of a run from the durable pass ledger, and what the ledger does not attribute (ADR 0017).

    A stage shows only what its ledger entries report: the ledger tags every call with its pass, and a stage with
    no entry made no call and has no block. The run row carries the totals including every reservation that is
    still held (a call whose usage the provider did not report keeps its reservation, and a worker that stopped
    between a reservation and its entry leaves one without an entry). Nothing in the ledger says which stage held
    that remainder, and the import's status does not either, so it is returned apart and never assigned by
    inference. The stages and the remainder add up to the run's tokens and cost.
    """
    reading_model = run.document_model_name or next(
        (entry["configured_model"] for entry in passes if entry["pass_id"] == READING_PASS_ID), None)
    models = {"READING": reading_model, "EXTRACTION": run.model_name}
    entries = {name: [entry for entry in passes if (entry["pass_id"] == READING_PASS_ID) == (name == "READING")] for name in models}
    reported = {name: [entry for entry in items if _is_reported(entry)] for name, items in entries.items()}
    sums = {name: (sum(entry["input_tokens"] for entry in items), sum(entry["output_tokens"] for entry in items),
                   sum((Decimal(entry["estimated_cost_usd"]) for entry in items), Decimal("0")))
            for name, items in reported.items()}
    stages = []
    for name, model in models.items():
        if not entries[name]:
            continue
        has_reported = bool(reported[name])
        stages.append(DeclarationStageOut(
            stage=name, model=model, usage_status="REPORTED" if has_reported else "UNKNOWN",
            reported_input_tokens=sums[name][0] if has_reported else None,
            reported_output_tokens=sums[name][1] if has_reported else None,
            input_tokens=sums[name][0], output_tokens=sums[name][1],
            # A stage whose calls reported nothing has no cost either: its reservation is not shown here, and a
            # stage line of 0,00 USD would read as a known zero.
            estimated_cost_usd=sums[name][2].quantize(Decimal("0.000001")) if has_reported and is_priced(model) else None))
    held_input = max(0, run.input_tokens - sum(total[0] for total in sums.values()))
    held_output = max(0, run.output_tokens - sum(total[1] for total in sums.values()))
    held_cost = max(Decimal("0"), Decimal(run.estimated_cost_usd) - sum(total[2] for total in sums.values()))
    held_cost = held_cost.quantize(Decimal("0.000001"))
    if not (held_input or held_output or held_cost):
        return stages, None
    # Which model's price the remainder was reserved at is as unknown as its stage: it is priced only when both are.
    known_price = all(is_priced(model) for model in (run.model_name, run.document_model_name) if model)
    return stages, DeclarationUnassignedOut(
        input_tokens=held_input, output_tokens=held_output, estimated_cost_usd=held_cost if known_price else None)


def _import_out(item: DeclarationImport) -> DeclarationImportOut:
    return DeclarationImportOut(
        id=str(item.id), mode=item.mode,
        target_property_id=str(item.target_property_id) if item.target_property_id else None,
        applied_property_id=str(item.applied_property_id) if item.applied_property_id else None,
        status=item.status, draft_revision=item.draft_revision,
        progress_percent=item.progress_percent, current_stage=item.current_stage,
        error_code=item.error_code, error_message=item.error_message,
        input_tokens=item.input_tokens, output_tokens=item.output_tokens,
        estimated_cost_usd=item.estimated_cost_usd, attempt_count=item.attempt_count,
        max_cost_usd=item.max_cost_usd,
        queue_request_id=str(item.queue_request_id) if item.queue_request_id else None,
        command_receipt_id=str(item.command_receipt_id) if getattr(item, "command_receipt_id", None) else None,
    )


def _document_out(
    item: PropertyDocument, *, sequence_no: int | None, document_role: str | None
) -> PropertyDocumentOut:
    return PropertyDocumentOut(
        id=str(item.id), property_id=str(item.property_id) if item.property_id else None,
        doc_type=item.doc_type, title=item.title, original_filename=item.original_filename,
        mime_type=item.mime_type, content_byte_size=item.content_byte_size,
        content_sha256=item.content_sha256, sequence_no=sequence_no,
        document_role=document_role,
    )


def _pdf_response(verified: VerifiedPropertyDocumentContent) -> Response:
    filename = safe_inline_filename(verified.document.original_filename)
    return Response(
        content=verified.data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(len(verified.data)),
            "ETag": f'"{verified.document.content_sha256}"',
        },
    )
