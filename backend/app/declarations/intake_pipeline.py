"""Model-assisted extraction of an unstructured inbox document (issue #501, ADR 0017).

The selected pages of one verified logical document (a PDF, PNG or JPEG original that carries no structured invoice) are
read by the organization's document reading model, a page with a text layer of its own keeps that text, and the
organization's extraction model fills the ten fields of the proposal, exactly as for an invoice (issue #500): this is
``InvoiceModelPipeline`` with the few things that differ for the inbox. Only the pages of this logical document reach a
provider, with the page numbers of the original, and never another document of the same original. Every request is
reserved before it leaves, settled with its usage in the shared ledger and re-checked against the organization's opt-in,
destination and monthly limit, and the claim's fence is renewed in the same transaction: a lost fence sends nothing
more. The calls of one document are bounded by the constants of the invoice path (``declarations/invoice_pipeline.py``):
a logical document is one voucher, the same kind of thing as an invoice.

The outcome has the shape the inbox's database function records (``complete_intake_extraction``): a proposal with the
original's page numbers as its source pages, the text each page was read as and how, and evidence for every stated
field. The model says where it found each value (a page and a passage); the evidence is located at page level only when
that page is one of this document's selected pages and the passage occurs in its text, and unlocated otherwise.
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from sqlalchemy import text

from app.core.extraction_evidence import FIELD_TYPES
from app.core.intake_extraction import MODEL_VERSION, model_evidence, outcome
from app.declarations.invoice_pipeline import InvoiceModelFailure, InvoiceModelPipeline, InvoicePage, InvoiceProviders
from app.declarations.pipeline import _Reservation
from app.declarations.provider import InvoiceCategory
from app.declarations.schemas import InvoiceExtractionOutput
from app.repositories.invoice_model_call_repo import InvoiceModelCallRepository


class IntakeModelPipeline(InvoiceModelPipeline):
    """Read the selected pages of one claimed inbox extraction, extract its fields and meter every request."""

    _inbox = True

    def __init__(
        self,
        *,
        session_factory,
        org_id: uuid.UUID,
        claim: dict[str, Any],
        data: bytes,
        mime: str,
        effective,
        providers: InvoiceProviders,
        categories: Sequence[InvoiceCategory],
    ) -> None:
        # The identity of the one document inside the requests is its logical document: it carries no meaning
        # beyond telling the documents of one original apart.
        super().__init__(session_factory=session_factory, org_id=org_id, claim=claim, data=data, effective=effective,
                         providers=providers, categories=categories, document_key=str(claim["logical_document_id"]))
        self._mime = mime
        # Set when the claim is still the live one and a request was refused because the division of the original no longer
        # lists this document: the run did not fail, the document waits for a decision again (``workers/intake_extraction.py``).
        self.division_changed = False

    def _outcome(self, status: str, error: str | None, payload: dict | None = None, evidence: dict | None = None,
                 pages: list | None = None) -> dict:
        claim = self._claim
        return outcome(self._data, self._mime, claim["page_start"], claim["page_end"], claim["verified_page_count"],
                       status, error, payload, evidence, pages, version=MODEL_VERSION)

    def _failure_outcome(self, failure: InvoiceModelFailure) -> dict:
        return self._outcome(failure.status, failure.code)

    def _proposal_outcome(self, extracted: InvoiceExtractionOutput, pages: Sequence[InvoicePage], texts: Sequence[str]) -> dict:
        proposal = super()._proposal_outcome(extracted, pages, texts)
        payload = proposal["payload"]
        stored = [{"number": page.number, "text": text_, "method": method}
                  for page, text_, method in zip(pages, texts, self._methods, strict=True)]
        # Where the model says it found each value: verified against the pages of this document before it is recorded.
        sources = {key: (field.source_page, field.source_quote) for key in FIELD_TYPES
                   if (field := getattr(extracted, key)).value is not None}
        return self._outcome(proposal["status"], proposal["error_code"], payload, model_evidence(payload, sources, stored), stored)

    def _render(self, page: InvoicePage, budget) -> bytes:
        if self._mime == "application/pdf":
            return super()._render(page, budget)
        return self._preflight.render_image_for_reading(self._data, budget=budget)

    async def _renew(self, session) -> bool:
        claim = {"org": self._org_id, "job": uuid.UUID(str(self._claim["job_id"])), "fence": uuid.UUID(str(self._claim["fence"]))}
        if await session.scalar(text("SELECT renew_intake_extraction(:org,:job,:fence,:seconds)"), {**claim, "seconds": self._lease_seconds}):
            return True
        # Refused: the claim is lost, or it is still the live one and the latest division of the original leaves this document
        # out. Only the second is a decision of a person; the context answers while the fence is live.
        context = await session.scalar(text("SELECT intake_extraction_context(:org,:job,:fence)"), claim)
        self.division_changed = context is not None and context["split_state"] != "decided"
        return False

    async def _record_reservation(self, session, stage, pdf_page, model, reservation: _Reservation) -> uuid.UUID:
        return await InvoiceModelCallRepository(session, self._org_id).reserve(
            intake_attempt_id=uuid.UUID(str(self._claim["attempt_id"])), stage=stage, pdf_page=pdf_page, model_name=model,
            input_tokens=reservation.input_tokens, output_tokens=reservation.output_tokens, cost_usd=reservation.cost_usd)
