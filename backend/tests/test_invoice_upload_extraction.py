"""A PDF uploaded as an invoice reaches a proposal through the organization's models (issue #502).

Synthetic PDFs are generated with reportlab. The unstructured PDF is read by the in-process fake providers of the
two model roles: no Tesseract, no pattern matching, no network.
"""

from __future__ import annotations

import io
import os
import uuid

import pytest

from app.declarations.fake_provider import FakeInvoiceExtractionProvider, synthetic_invoice_output
from tests.ai_settings_support import configure_ai
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data
from tests.test_invoice_model_extraction import Harness
from app.core.storage import LocalFsDocumentStore, set_store


# ---------------------------------------------------------------------------
# Helper: create a synthetic PDF with reportlab containing given text
# ---------------------------------------------------------------------------
def _make_pdf(text: str, *, font_size: int = 10) -> bytes:
    """Generate a minimal PDF with the given text using reportlab.

    Returns raw PDF bytes.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Write text line by line
    y = height - 50
    for line in text.split("\n"):
        if y < 50:
            c.showPage()
            y = height - 50
        c.setFont("Helvetica", font_size)
        c.drawString(50, y, line)
        y -= font_size + 4

    c.save()
    return buf.getvalue()


def _make_empty_pdf() -> bytes:
    """Generate a PDF with no text content (simulates image-only PDF)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    # Draw a rectangle but NO text
    c.rect(50, 700, 200, 50)
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_store(tmp_path):
    """Provide a temporary LocalFsDocumentStore and register it."""
    store = LocalFsDocumentStore(str(tmp_path))
    set_store(store)
    yield store
    set_store(None)  # type: ignore


# ============================================================
# End-to-end: upload PDF -> extraction -> cost_entry
# ============================================================
class TestEndToEndPdfUpload:
    @pytest.fixture(scope="session")
    def m2_seed(self, seed_data):
        """Create a property in org_a for M2 cost entries (via migrator)."""
        import psycopg2

        sync_url = os.environ.get(
            "HV_DATABASE_URL_SYNC",
            "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
        )
        property_a_id = uuid.uuid4()

        conn = psycopg2.connect(sync_url)
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO property (id, organization_id, name, management_type) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (str(property_a_id), str(seed_data["org_a_id"]), "Testhaus Extraction", "WEG"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return {**seed_data, "property_a_id": property_a_id}

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("no_invoice_model_data")
    async def test_upload_pdf_extracts_fields(self, client_a_admin, m2_seed, tmp_store):
        """Upload a synthetic PDF invoice -> extraction_payload populated."""
        invoice_text = (
            "Grundsteuer-Amt Musterstadt\n"
            "Rechnungsnummer: GS-2025-001\n"
            "Rechnungsdatum: 10.03.2025\n"
            "Leistungszeitraum: 01.01.2025 bis 31.12.2025\n"
            "Grundsteuerbescheid\n"
            "Gesamtbetrag: 450,00 EUR\n"
            "MwSt. 19% 71,85 EUR\n"
        )
        pdf_bytes = _make_pdf(invoice_text)

        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("grundsteuer.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert upload_resp.status_code == 201
        doc = upload_resp.json()

        from app.core.db import get_worker_session_factory
        from app.workers.invoice_extraction import process_once
        # The unstructured PDF is read by the organization's models (in-process fakes): the extraction model answers
        # with what the invoice says.
        configure_ai(m2_seed["org_a_id"], model="gpt-5.6-luna", document_model="gpt-5.4-mini", import_enabled=True)
        models = Harness(extractor=FakeInvoiceExtractionProvider(output=synthetic_invoice_output(
            vendor_name=("Grundsteuer-Amt Musterstadt", 0.9), invoice_date=("2025-03-10", 0.9), document_number=("GS-2025-001", 0.9),
            gross_amount=("450.00", 0.9), vat_amount=("71.85", 0.9), vat_rate=("19", 0.9),
            service_period_from=("2025-01-01", 0.9), service_period_to=("2025-12-31", 0.9),
            suggested_category_code=("GRUNDSTEUER", 0.9))))
        for _ in range(100):
            await process_once(get_worker_session_factory(), m2_seed["org_a_id"], providers=models)
            doc = (await client_a_admin.get(f"/api/invoice-documents/{doc['id']}")).json()
            if doc["processing_status"] not in {"queued", "running"}:
                break
        assert doc["processing_status"] == "succeeded", doc["processing_error_code"]
        assert "Grundsteuerbescheid" in models.extractor.calls[0].text
        # Check extraction_payload after durable processing.
        payload = doc["extraction_payload"]
        assert payload is not None
        assert payload["invoice_date"] == "2025-03-10"
        assert payload["document_number"] == "GS-2025-001"
        assert payload["gross_amount"] == "450.00"
        assert payload["suggested_category_code"] == "GRUNDSTEUER"
        assert payload["confidence"] > 0.4

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("no_invoice_model_data")
    async def test_upload_pdf_with_cost_entry_prefill(self, client_a_admin, m2_seed, tmp_store):
        """Upload PDF linked to a cost_entry, verify entry gets draft status."""
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        # Create a cost entry in needs_review
        entry_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        assert entry_resp.status_code == 201
        entry_id = entry_resp.json()["id"]

        # Upload a PDF linked to this entry
        pdf_bytes = _make_pdf(
            "Firma Test\nRechnungsdatum: 15.06.2025\nGesamtbetrag: 250,00 EUR"
        )
        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("invoice.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            data={"cost_entry_id": entry_id},
        )
        assert upload_resp.status_code == 201
        doc = upload_resp.json()
        assert doc["cost_entry_id"] == entry_id
        from app.core.db import get_worker_session_factory
        from app.workers.invoice_extraction import process_once
        configure_ai(m2_seed["org_a_id"], model="gpt-5.6-luna", document_model="gpt-5.4-mini", import_enabled=True)
        models = Harness(extractor=FakeInvoiceExtractionProvider(output=synthetic_invoice_output(
            invoice_date=("2025-06-15", 0.9), gross_amount=("250.00", 0.9))))
        for _ in range(100):
            await process_once(get_worker_session_factory(), m2_seed["org_a_id"], providers=models)
            doc = (await client_a_admin.get(f"/api/invoice-documents/{doc['id']}")).json()
            if doc["processing_status"] not in {"queued", "running"}:
                break
        assert doc["processing_status"] == "succeeded", doc["processing_error_code"]
        assert doc["extraction_payload"] is not None
        assert doc["extraction_payload"]["gross_amount"] == "250.00"

        # Entry should still be editable (needs_review), user can update with extracted values
        entry_check = await client_a_admin.get(f"/api/cost-entries/{entry_id}")
        assert entry_check.json()["status"] == "needs_review"

        # User can then update the entry with extracted fields and confirm
        await client_a_admin.patch(f"/api/cost-entries/{entry_id}", json={
            "gross_amount": "250.00",
            "invoice_date": "2025-06-15",
        })
        confirm_resp = await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["status"] == "confirmed"

    @pytest.mark.asyncio
    async def test_empty_pdf_upload_needs_review(self, client_a_admin, m2_seed, tmp_store):
        """Uploading a PDF without text yields extraction with confidence=0."""
        pdf_bytes = _make_empty_pdf()
        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("empty.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert upload_resp.status_code == 201
        doc = upload_resp.json()
        payload = doc["extraction_payload"]
        assert payload is not None
        assert payload["confidence"] == 0.0
