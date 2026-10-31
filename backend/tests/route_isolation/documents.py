"""Isolation sweep: probes and seed for the intake and allocation pipeline: intake batches and items, logical documents, document refs, extractions, partitions, invoice reviews, context decisions and mappings, allocation proposals, treatments and adoptions, duplicates, exceptions, visual extracts and disclosures."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.conftest import MIGRATOR_URL
from tests.route_isolation import Probe, row
from tests import test_invoice_reviews as invoice_reviews
from tests.test_allocation_adoptions import confirm_grant
from tests.test_context_candidates import document
from tests.test_context_mappings import activation
from tests.test_correction_followups import corrected_report
from tests.test_duplicate_comparisons import command as duplicate_command
from tests.test_duplicate_comparisons import comparisons
from tests.test_duplicate_comparisons import reviewed as duplicate_reviewed
from tests.test_exception_completions import adopted_context, completion_preview
from tests.test_exception_completions import command as completion_command
from tests.test_financial_scope_boundary import scope_seed, worker_tenant
from tests.test_invoice_source_custody import fixture, store
from tests.test_scope_grants import scope_fixture
from tests.test_scoped_account_statements import client
from tests.test_visual_disclosures import prepared as visual_prepared
from tests.test_visual_disclosures import publish_command

# Imported by the sweep test with *: the seed fixture and every fixture it requests that
# conftest.py does not provide.
__all__ = ["documents_seeded", "scope_seed", "store", "worker_tenant"]


async def more_ids(seed):
    """Another set of properties and scopes in the seed organization; ``fixture`` can commit only once per session."""
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with AsyncSession(engine) as session, session.begin():
            return await scope_fixture(session, seed)
    finally:
        await engine.dispose()


@pytest.fixture
async def documents_seeded(migrator_session, scope_seed, store, monkeypatch):
    """One organization (not B) with the four chains below; each helper commits its own rows."""
    # Scope conflict case, completed, with a context mapping. First: it expects the only exception case of the organization.
    ids = await fixture(migrator_session, scope_seed)
    await confirm_grant(ids)
    await confirm_grant({**ids, "scope": ids["other_scope"]})
    monkeypatch.setattr("tests.test_intake_extraction_worker.pdf_bytes", lambda: document(["Objekt-ID: " + str(ids["property"])]))
    async with client(ids) as api:
        _, _, _, adopted, case = await adopted_context(api, ids)
        options = await api.get("/api/allocation-sets/" + adopted["id"] + "/mapping-options")
        assert options.status_code == 200, options.text
        mapped = await api.post("/api/context-mappings", **activation(adopted, options.json()["options"][0]))
        assert mapped.status_code == 201, mapped.text
        completed = await api.post("/api/exceptions/" + case["id"] + "/completions", **completion_command(await completion_preview(api, case)))
        assert completed.status_code == 201, completed.text

    # Corrected allocation set with a correction impact on a weg report.
    # An adoption is blocked by an adopted document of the same reviewed identity in the organization.
    review_command = invoice_reviews.command
    monkeypatch.setattr(invoice_reviews, "command", lambda *args, **changes: review_command(*args, **{"document_number": "R-2", **changes}))
    monkeypatch.setattr("tests.test_intake_extraction_worker.pdf_bytes", lambda: document(["Isolation: corrected allocation"]))
    ids = await more_ids(scope_seed)
    await confirm_grant(ids)
    async with client(ids) as api:
        await corrected_report(api, ids)

    # Two reviews of identical bytes with a duplicate comparison.
    data = document(["Isolation: duplicate pair"])
    monkeypatch.setattr("tests.test_intake_extraction_worker.pdf_bytes", lambda: data)
    ids = await more_ids(scope_seed)
    async with client(ids) as api:
        *_, first = await duplicate_reviewed(api, ids)
        *_, second = await duplicate_reviewed(api, ids)
        compared = await api.post(comparisons(first), **duplicate_command(second))
        assert compared.status_code == 201, compared.text

    # Visual extract published to a second scope.
    ids = await more_ids(scope_seed)
    async with client(ids) as api:
        artifact, *_ = await visual_prepared(api, ids)
        published = await api.post("/api/visual-extracts/" + artifact["id"] + "/disclosures", **publish_command(artifact, ids))
        assert published.status_code == 201, published.text
        assert published.json()["receipt"]["id"], "Document isolation fixture published no disclosure id"


def pick(columns: str, tables: str, where: str = "") -> str:
    """Like ``row`` for joins: the primary table is aliased ``x``."""
    return f"SELECT {columns} FROM {tables} WHERE x.organization_id IS DISTINCT FROM :b{where} ORDER BY x.id LIMIT 1"


HEX = "0" * 64
UNKNOWN = "00000000-0000-4000-8000-000000000001"  # a reference that exists in no organization, the same in the twin request
REASON = "Isolationstest"

LOGICAL = row("logical_document", logical_id="id")
# A set and the review that would replace its source, as a correction names them.
SET_AND_REVIEW = pick(
    "x.predecessor_id AS set_id, n.treatment_review_id AS treatment_review_id",
    "allocation_correction x JOIN allocation_set n ON n.organization_id = x.organization_id AND n.id = x.successor_id",
)
# Earlier tests can retain reviews/sets in organizations without disclosures.
# Join an existing disclosure before choosing the row used by either probe.
DISCLOSURE_JOIN = " JOIN visual_disclosure_event d ON d.organization_id = x.organization_id"
ADOPTED_REVIEW = pick(
    "x.logical_document_id AS logical_id, x.treatment_review_id AS treatment_review_id", "allocation_set x", " AND x.treatment_review_id IS NOT NULL"
)
ADOPTED_REVIEW_AND_DISCLOSURE = pick(
    "x.logical_document_id AS logical_id, x.treatment_review_id AS treatment_review_id, d.id AS disclosure_id",
    "allocation_set x" + DISCLOSURE_JOIN,
    " AND x.treatment_review_id IS NOT NULL",
)
ARTIFACT_AND_SCOPE = pick(
    "x.artifact_id AS artifact_id, x.scope_id AS scope_id, a.partition_revision_id AS partition_revision_id, a.content_sha256 AS content_sha256",
    "visual_disclosure_event x JOIN visual_extract_artifact a ON a.organization_id = x.organization_id AND a.id = x.artifact_id",
)
ASSIGNMENT = row(
    "assignment_proposal",
    logical_id="logical_document_id",
    partition_revision_id="partition_revision_id",
    extraction_revision_id="extraction_revision_id",
    scope_id="scope_id",
)

CORRECTION = {
    "treatment_review_id": "{treatment_review_id}",
    "preview_digest": HEX,
    "reason": REASON,
    "costs_confirmed": True,
    "whole_original_disclosure_reviewed": True,
    "downstream_followups_reviewed": True,
}
VISUAL_REVIEW = {
    "treatment_review_id": "{treatment_review_id}",
    "visual_content_sha256": HEX,
    "preview_digest": HEX,
    "reason": REASON,
    "costs_confirmed": True,
    "retained_visual_source_reviewed": True,
}
INVOICE_FIELDS = {
    "assignment_proposal_id": "{assignment_proposal_id}",
    "seller_name": REASON,
    "document_number": "R-1",
    "document_kind": "invoice",
    "currency": "EUR",
    "invoice_date": "2026-10-01",
    "service_period_from": "2026-09-01",
    "service_period_to": "2026-09-30",
    "gross_amount": "119.00",
    "fields_reviewed": True,
    "reason": REASON,
}
ALLOCATION = {
    "context_decision_id": "{context_decision_id}",
    "lines": [{"cost_category_id": UNKNOWN, "cost_category_plan_version": 1, "description": REASON, "gross_amount": "119.00"}],
    "reason": REASON,
    "allocation_reviewed": True,
}
TREATMENT = {
    "allocation_proposal_id": "{allocation_proposal_id}",
    "preview_digest": HEX,
    "reason": REASON,
    "treatment_reviewed": True,
    "lines": [
        {
            "allocation_line_id": UNKNOWN,
            "expense_intent": "operating_expense",
            "tax_intent": "gross_without_input_vat",
            "tenant_treatment": "none",
            "tenant_amount": None,
            "tenant_distribution_key": None,
            "owner_distribution_key": "not_applicable",
            "documentary_basis": REASON,
        }
    ],
}
CONTEXT_DECISION = {
    "assignment_proposal_id": "{assignment_proposal_id}",
    "invoice_review_id": "{invoice_review_id}",
    "snapshot_digest": HEX,
    "quotation": REASON,
    "page_number": 1,
    "comparison_note": REASON,
    "context_reviewed": True,
}
MAPPING = {
    "allocation_set_id": "{allocation_set_id}",
    "identifier_kind": "property_id",
    "identifier_value": REASON,
    "page_number": 1,
    "valid_from": "2026-01-01",
    "valid_to": "2026-12-31",
    "reason": REASON,
    "reuse_consented": True,
}

PROBES: dict[str, Probe] = {
    # app/api/allocation_adoptions.py
    "GET /api/allocation-correction-impacts/{impact_id}/follow-up": Probe(row("allocation_correction_impact", impact_id="id")),
    "GET /api/allocation-corrections/{correction_id}": Probe(row("allocation_correction", correction_id="id")),
    "GET /api/allocation-sets/{set_id}": Probe(row("allocation_set", set_id="id")),
    "GET /api/allocation-sets/{set_id}/correction-preview": Probe(SET_AND_REVIEW),
    "GET /api/allocation-sets/{set_id}/replacement-preview": Probe(SET_AND_REVIEW),
    "GET /api/allocation-sets/{set_id}/visual-correction-preview": Probe(SET_AND_REVIEW),
    "GET /api/allocation-treatments/{review_id}/adoption-preview": Probe(row("allocation_treatment_review", review_id="id")),
    "GET /api/allocation-treatments/{review_id}/replacement-candidates": Probe(row("allocation_treatment_review", review_id="id")),
    "GET /api/allocation-treatments/{review_id}/visual-adoption-preview": Probe(
        pick("x.id AS review_id, d.id AS disclosure_id", "allocation_treatment_review x" + DISCLOSURE_JOIN)
    ),
    "GET /api/logical-documents/{logical_id}/allocation-adoptions": Probe(LOGICAL),
    "POST /api/allocation-correction-impacts/{impact_id}/completions": Probe(
        row("allocation_correction_impact", impact_id="id"),
        body={"preview_digest": HEX, "reason": REASON, "source_reviewed": True, "confirmed": True},
    ),
    "POST /api/allocation-sets/{set_id}/corrections": Probe(SET_AND_REVIEW, body=CORRECTION),
    "POST /api/allocation-sets/{set_id}/source-replacements": Probe(SET_AND_REVIEW, body=CORRECTION),
    "POST /api/allocation-sets/{set_id}/visual-corrections": Probe(
        SET_AND_REVIEW, body={**VISUAL_REVIEW, "downstream_followups_reviewed": True}
    ),
    "POST /api/logical-documents/{logical_id}/allocation-adoptions": Probe(
        ADOPTED_REVIEW,
        body={
            "treatment_review_id": "{treatment_review_id}",
            "preview_digest": HEX,
            "reason": REASON,
            "costs_confirmed": True,
            "whole_original_disclosure_reviewed": True,
        },
    ),
    "POST /api/logical-documents/{logical_id}/visual-allocation-adoptions": Probe(
        ADOPTED_REVIEW_AND_DISCLOSURE, body={**VISUAL_REVIEW, "disclosure_id": "{disclosure_id}"}
    ),
    # app/api/allocation_proposals.py
    "GET /api/allocation-proposals/{proposal_id}": Probe(row("allocation_proposal", proposal_id="id")),
    "GET /api/context-decisions/{decision_id}/allocation-preview": Probe(row("context_decision", decision_id="id")),
    "GET /api/logical-documents/{logical_id}/allocation-proposals": Probe(LOGICAL),
    "POST /api/logical-documents/{logical_id}/allocation-proposals": Probe(
        row("allocation_proposal", logical_id="logical_document_id", context_decision_id="context_decision_id"), body=ALLOCATION
    ),
    # app/api/allocation_treatments.py
    "GET /api/allocation-proposals/{proposal_id}/treatment-preview": Probe(row("allocation_proposal", proposal_id="id")),
    "GET /api/allocation-treatments/{review_id}": Probe(row("allocation_treatment_review", review_id="id")),
    "GET /api/logical-documents/{logical_id}/allocation-treatments": Probe(LOGICAL),
    "POST /api/logical-documents/{logical_id}/allocation-treatments": Probe(
        row("allocation_treatment_review", logical_id="logical_document_id", allocation_proposal_id="allocation_proposal_id"),
        body=TREATMENT,
    ),
    # app/api/context_decisions.py
    "GET /api/assignment-proposals/{proposal_id}/context-preview": Probe(row("assignment_proposal", proposal_id="id")),
    "GET /api/context-decisions/{decision_id}": Probe(row("context_decision", decision_id="id")),
    "GET /api/logical-documents/{logical_id}/context-decisions": Probe(LOGICAL),
    "POST /api/logical-documents/{logical_id}/context-decisions": Probe(
        row(
            "context_decision",
            logical_id="logical_document_id",
            assignment_proposal_id="assignment_proposal_id",
            invoice_review_id="invoice_review_id",
        ),
        body=CONTEXT_DECISION,
    ),
    # app/api/context_mappings.py
    "GET /api/allocation-sets/{set_id}/mapping-options": Probe(row("allocation_set", set_id="id")),
    "GET /api/context-mappings/{version_id}": Probe(row("mapping_version", version_id="id")),
    "POST /api/context-mappings": Probe(row("allocation_set", allocation_set_id="id"), body=MAPPING),
    "POST /api/context-mappings/{version_id}/revocations": Probe(row("mapping_version", version_id="id"), body={"reason": REASON}),
    # app/api/document_refs.py
    "GET /api/document-refs/page": Probe(
        pick("x.intake_original_id AS source_id", "document_ref x", " AND x.intake_original_id IS NOT NULL"),
        status=200,
        because="a page filtered by another organization's source is empty, like for a source that exists nowhere",
    ),
    "GET /api/document-refs/{ref_id}": Probe(row("document_ref", ref_id="id")),
    # app/api/duplicate_comparisons.py
    "GET /api/duplicate-comparisons/{comparison_id}": Probe(row("duplicate_comparison", comparison_id="id")),
    "GET /api/invoice-reviews/{review_id}/duplicate-candidates": Probe(row("invoice_review_revision", review_id="id")),
    "GET /api/invoice-reviews/{review_id}/duplicate-comparisons": Probe(
        row("duplicate_comparison", review_id="subject_review_id", reference_review_id="reference_review_id")
    ),
    "POST /api/invoice-reviews/{review_id}/duplicate-comparisons": Probe(
        row("duplicate_comparison", review_id="subject_review_id", reference_review_id="reference_review_id"),
        body={"reference_review_id": "{reference_review_id}", "verdict": "same_document", "reason": REASON, "comparison_reviewed": True},
    ),
    # app/api/exceptions.py
    "GET /api/exceptions": Probe(),
    "GET /api/exceptions/{case_id}": Probe(row("exception_case", case_id="id")),
    "GET /api/exceptions/{case_id}/completion-preview": Probe(row("exception_case", case_id="id")),
    "GET /api/exceptions/{case_id}/completions/{completion_id}": Probe(row("exception_completion", case_id="case_id", completion_id="id")),
    "GET /api/exceptions/{case_id}/dependencies": Probe(row("exception_case", case_id="id")),
    "GET /api/exceptions/{case_id}/duplicate-dependencies": Probe(row("exception_case", case_id="id")),
    "GET /api/exceptions/{case_id}/events": Probe(row("exception_case", case_id="id")),
    "POST /api/exceptions/{case_id}/completions": Probe(
        row("exception_case", case_id="id"), body={"preview_digest": HEX, "reason": REASON, "completion_reviewed": True}
    ),
    "POST /api/exceptions/{case_id}/events": Probe(row("exception_case", case_id="id"), body={"action": "claim", "reason": REASON}),
    # app/api/extraction_revisions.py
    "GET /api/document-refs/{ref_id}/extractions": Probe(
        pick("x.document_ref_id AS ref_id, x.logical_document_id AS logical_document_id",
             "extraction_revision x", " AND x.logical_document_id IS NOT NULL")
    ),
    "GET /api/extraction-revisions/{revision_id}": Probe(row("extraction_revision", revision_id="id")),
    # app/api/intake_batches.py
    "GET /api/intake-batches": Probe(),
    "GET /api/intake-batches/originals/{original_id}/content": Probe(row("intake_original", original_id="id")),
    "GET /api/intake-batches/{batch_id}": Probe(row("intake_batch", batch_id="id")),
    "POST /api/intake-batches": Probe(
        row("accounting_scope", scope_hint_id="id"),
        body={
            "scope_hint_id": "{scope_hint_id}",
            "items": [
                {"client_item_key": "item-1", "filename": "isolation.pdf", "mime_type": "application/pdf", "content_sha256": HEX, "content_byte_size": 10}
            ],
        },
    ),
    "POST /api/intake-batches/retry": Probe(
        row("workflow_job", job_id="id"), body={"job_id": "{job_id}", "expected_fence": UNKNOWN, "reason": REASON}
    ),
    "POST /api/intake-batches/{batch_id}/items": Probe(row("intake_batch", batch_id="id")),
    # app/api/intake_items.py
    "GET /api/logical-documents/{logical_id}/assignment-candidates": Probe(ASSIGNMENT),
    "GET /api/logical-documents/{logical_id}/assignment-proposals": Probe(LOGICAL),
    "POST /api/logical-documents/{logical_id}/assignment-proposals": Probe(
        ASSIGNMENT,
        body={
            "partition_revision_id": "{partition_revision_id}",
            "extraction_revision_id": "{extraction_revision_id}",
            "scope_id": "{scope_id}",
            "reason": REASON,
        },
    ),
    # app/api/invoice_reviews.py
    "GET /api/invoice-reviews/{review_id}": Probe(row("invoice_review_revision", review_id="id")),
    "GET /api/logical-documents/{logical_id}/invoice-reviews": Probe(LOGICAL),
    "POST /api/logical-documents/{logical_id}/invoice-reviews": Probe(
        row("invoice_review_revision", logical_id="logical_document_id", assignment_proposal_id="assignment_proposal_id"),
        body=INVOICE_FIELDS,
    ),
    # app/api/logical_documents.py
    "GET /api/logical-documents": Probe(row("document_ref", document_ref_id="id")),
    "GET /api/logical-documents/{document_id}": Probe(row("logical_document", document_id="id")),
    "GET /api/logical-documents/{document_id}/processing": Probe(row("logical_document", document_id="id")),
    "POST /api/logical-documents": Probe(
        row("document_ref", document_ref_id="id"),
        body={"document_ref_id": "{document_ref_id}", "page_start": 1, "page_end": 1, "reason": REASON},
    ),
    "POST /api/logical-documents/{document_id}/retry-extraction": Probe(
        row("logical_document", document_id="id"), body={"expected_fence": UNKNOWN, "reason": REASON}
    ),
    # app/api/source_partitions.py
    "GET /api/document-refs/{document_ref_id}/partition": Probe(row("document_ref", document_ref_id="id")),
    "GET /api/document-refs/{document_ref_id}/partition/{partition_id}": Probe(
        row("source_partition_revision", document_ref_id="document_ref_id", partition_id="id")
    ),
    "POST /api/document-refs/{document_ref_id}/partition": Probe(
        row("logical_document", document_ref_id="document_ref_id", logical_document_id="id"),
        body={"logical_document_ids": ["{logical_document_id}"], "reason": REASON, "visual_pages_reviewed": True},
    ),
    # app/api/visual_disclosures.py
    "GET /api/accounting-scopes/{scope_id}/visual-disclosures": Probe(row("accounting_scope", scope_id="id")),
    "GET /api/visual-disclosures/outgoing": Probe(),
    "GET /api/visual-disclosures/{disclosure_id}": Probe(row("visual_disclosure_event", disclosure_id="id")),
    "GET /api/visual-disclosures/{disclosure_id}/content": Probe(row("visual_disclosure_event", disclosure_id="id")),
    "GET /api/visual-extracts/{artifact_id}/disclosure-target": Probe(ARTIFACT_AND_SCOPE),
    "GET /api/visual-extracts/{artifact_id}/disclosures": Probe(row("visual_extract_artifact", artifact_id="id")),
    "POST /api/visual-disclosures/{disclosure_id}/withdrawals": Probe(
        row("visual_disclosure_event", disclosure_id="id"), body={"reason": REASON, "withdrawal_confirmed": True}
    ),
    "POST /api/visual-extracts/{artifact_id}/disclosures": Probe(
        ARTIFACT_AND_SCOPE,
        body={
            "scope_id": "{scope_id}",
            "partition_revision_id": "{partition_revision_id}",
            "content_sha256": "{content_sha256}",
            "reason": REASON,
            "visible_content_reviewed": True,
        },
    ),
    # app/api/visual_extracts.py
    "GET /api/logical-documents/{logical_id}/visual-extracts": Probe(LOGICAL),
    "GET /api/visual-extracts/{extract_id}/content": Probe(row("visual_extract_artifact", extract_id="id")),
    "POST /api/logical-documents/{logical_id}/visual-extracts": Probe(
        row("visual_extract_artifact", logical_id="logical_document_id", partition_revision_id="partition_revision_id"),
        body={"partition_revision_id": "{partition_revision_id}", "reason": REASON},
    ),
}
