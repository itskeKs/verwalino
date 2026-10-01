"""Durable ordinary-invoice extraction, synthetic sources only."""
import io
import uuid
import pytest
import pytest_asyncio
from reportlab.pdfgen import canvas
from app.core.invoice_ocr import extract_invoice, run_isolated_extraction


@pytest_asyncio.fixture
async def worker_tenant():
    """A dedicated queue, independent of queued originals in other test modules."""
    import os
    import psycopg2
    import httpx
    from app.core.config import settings
    from app.core.security import create_session_token
    from app.main import app
    org, user = uuid.uuid4(), uuid.uuid4()
    with psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"]) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO organization (id,name) VALUES (%s,'Isolated invoice queue')", (str(org),))
        cur.execute('INSERT INTO "user" (id,organization_id,email,password_hash,display_name) VALUES (%s,%s,%s,%s,%s)',
                    (str(user), str(org), f"{user}@invoice-test.invalid", "unused-fixture-password", "Invoice worker test"))
        cur.execute("INSERT INTO membership (id,organization_id,user_id,role) VALUES (%s,%s,%s,'OWNER_ADMIN')",
                    (str(uuid.uuid4()), str(org), str(user)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                cookies={settings.session_cookie_name: create_session_token(user, org)}) as client:
        yield client, {"org_a_id": org, "user_a_admin_id": user}


def pdf_bytes(pages=1, *, encrypted=False):
    out = io.BytesIO()
    c = canvas.Canvas(out, encrypt="secret" if encrypted else None)
    for _ in range(pages):
        c.drawString(60, 750, "Stadtwerke Test GmbH")
        c.drawString(60, 710, "Rechnungsdatum: 01.06.2025")
        c.drawString(60, 670, "Gesamtbetrag: 119,00 EUR")
        c.showPage()
    c.save()
    return out.getvalue()


def test_text_and_xml_bypass_ocr(monkeypatch):
    from tests.test_m5_einvoice import UBL
    def forbidden(*args, **kwargs):
        raise AssertionError("OCR must not run for text/XML")
    monkeypatch.setattr("app.core.invoice_ocr.ocr_page", forbidden)
    for data, mime in [(pdf_bytes(), "application/pdf"), (UBL, "application/xml")]:
        outcome = extract_invoice(data, mime)
        assert outcome["status"] == "succeeded"
        assert outcome["payload"]["gross_amount"] == "119.00"


@pytest.mark.parametrize("data,error", [(b"broken", "invalid_pdf"), (pdf_bytes(encrypted=True), "encrypted_pdf"), (pdf_bytes(201), "pdf_page_limit")], ids=["corrupt", "encrypted", "page-limit"])
def test_invalid_documents_keep_explicit_outcome(data, error):
    result = extract_invoice(data, "application/pdf")
    assert result["error_code"] == error
    assert result["payload"] is None


def test_real_image_only_invoice():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(image)
    # Use a platform-independent font shipped with reportlab.
    import reportlab
    from pathlib import Path
    font = ImageFont.truetype(str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"), 36)
    for y, line in enumerate(["Stadtwerke Test GmbH", "Rechnungsdatum: 01.06.2025", "Gesamtbetrag: 119,00 EUR"]):
        draw.text((100, 150+y*90), line, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, format="PDF", resolution=200)
    result = run_isolated_extraction(out.getvalue(), "application/pdf")
    assert result["status"] == "succeeded", result
    assert result["payload"]["gross_amount"] == "119.00"
    assert result["payload"]["source_pages"] == [1]


@pytest.mark.asyncio
async def test_upload_is_durable_before_processing(worker_tenant, tmp_path):
    client_a_admin, seed_data = worker_tenant
    from app.core.storage import LocalFsDocumentStore, set_store
    from app.core.db import get_session_factory
    from app.workers.invoice_extraction import process_once
    set_store(LocalFsDocumentStore(str(tmp_path)))
    try:
        uploaded = await client_a_admin.post("/api/invoice-documents/upload", files={"file": ("scan.pdf", pdf_bytes(), "application/pdf")})
        assert uploaded.status_code == 201
        doc = uploaded.json()
        assert doc["processing_status"] == "queued"
        assert doc["cost_entry_id"] is None
        called = []
        def processor(data, mime):
            called.append(data)
            return extract_invoice(data, mime)
        assert await process_once(get_session_factory(), seed_data["org_a_id"], processor=processor)
        ready = (await client_a_admin.get(f"/api/invoice-documents/{doc['id']}")).json()
        assert ready["processing_status"] == "succeeded"
        assert ready["extraction_payload"]["gross_amount"] == "119.00"
        assert ready["cost_entry_id"] is None
        assert len(called) == 1
        assert not await process_once(get_session_factory(), seed_data["org_a_id"], processor=processor)
    finally:
        set_store(None)


def empty_pdf(pages=1):
    output = io.BytesIO()
    c = canvas.Canvas(output)
    for _ in range(pages):
        c.showPage()
    c.save()
    return output.getvalue()


@pytest.mark.parametrize("failure,code", [(FileNotFoundError(), "ocr_unavailable"), (__import__('subprocess').TimeoutExpired('ocr', 10), "ocr_timeout")], ids=["dependency", "timeout"])
def test_ocr_failure_discards_proposals(monkeypatch, failure, code):
    def fail(*args):
        raise failure
    monkeypatch.setattr("app.core.invoice_ocr.ocr_page", fail)
    result = extract_invoice(empty_pdf(), "application/pdf")
    assert result["error_code"] == code
    assert result["payload"] is None


def test_page_limit_never_truncates():
    result = extract_invoice(empty_pdf(21), "application/pdf")
    assert result["error_code"] == "ocr_page_limit"
    assert result["payload"] is None


def test_process_timeout_is_bounded():
    result = run_isolated_extraction(pdf_bytes(), "application/pdf", timeout=0.001)
    assert result["error_code"] == "extraction_timeout"


@pytest.mark.asyncio
async def test_restart_fencing_and_retry_access(worker_tenant, client_b_admin, tmp_path):
    client_a_admin, seed_data = worker_tenant
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text
    from app.core.storage import LocalFsDocumentStore, set_store
    from app.core.db import get_session_factory, set_tenant_context
    from app.services.invoice_processing_service import InvoiceProcessingService
    set_store(LocalFsDocumentStore(str(tmp_path)))
    try:
        doc = (await client_a_admin.post("/api/invoice-documents/upload", files={"file": ("invoice.pdf", pdf_bytes(), "application/pdf")})).json()
        factory = get_session_factory()
        async def claim():
            async with factory() as s, s.begin():
                await set_tenant_context(s, seed_data["org_a_id"])
                return await InvoiceProcessingService(s, seed_data["org_a_id"]).claim()
        first = await claim()
        assert first["document_id"] == uuid.UUID(doc["id"])
        async with factory() as s, s.begin():
            await set_tenant_context(s, seed_data["org_a_id"])
            await s.execute(text("UPDATE invoice_processing_attempt SET lease_until=:past WHERE id=:id"),
                            {"past": datetime.now(timezone.utc)-timedelta(seconds=1), "id": first["attempt_id"]})
        second = await claim()
        assert second["token"] != first["token"]
        result = extract_invoice(pdf_bytes(), "application/pdf")
        async with factory() as s, s.begin():
            await set_tenant_context(s, seed_data["org_a_id"])
            svc = InvoiceProcessingService(s, seed_data["org_a_id"])
            assert not await svc.finish(first, result)
            assert await svc.finish(second, result)
        attempts = (await client_a_admin.get(f"/api/invoice-documents/{doc['id']}/processing-attempts")).json()
        assert [a["status"] for a in attempts] == ["succeeded", "timeout"]
        assert (await client_b_admin.post(f"/api/invoice-documents/{doc['id']}/retry")).status_code == 404
        assert (await client_b_admin.get(f"/api/invoice-documents/{doc['id']}/processing-attempts")).status_code == 404
    finally:
        set_store(None)


@pytest.mark.asyncio
async def test_queue_limit_preserves_accepted_original(client_b_admin, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.core.storage import LocalFsDocumentStore, set_store
    monkeypatch.setattr(settings, "invoice_queue_limit", 1)
    set_store(LocalFsDocumentStore(str(tmp_path)))
    try:
        first = await client_b_admin.post("/api/invoice-documents/upload", files={"file": ("first.pdf", pdf_bytes(), "application/pdf")})
        assert first.status_code == 201
        second = await client_b_admin.post("/api/invoice-documents/upload", files={"file": ("second.pdf", pdf_bytes(), "application/pdf")})
        assert second.status_code == 201
        assert second.json()["processing_error_code"] == "queue_full"
        assert second.json()["processing_status"] == "needs_review"
        assert (await client_b_admin.get(f"/api/invoice-documents/{second.json()['id']}/content")).status_code == 200
        original = await client_b_admin.get(f"/api/invoice-documents/{first.json()['id']}/content")
        assert original.status_code == 200
        assert original.content.startswith(b"%PDF")
        version = await client_b_admin.post(f"/api/invoice-documents/{first.json()['id']}/version",
            files={"file": ("replacement.pdf", pdf_bytes(), "application/pdf")})
        assert version.status_code == 201
        assert version.json()["processing_status"] == "needs_review"
        assert (await client_b_admin.get(f"/api/invoice-documents/{version.json()['id']}/content")).status_code == 200
    finally:
        set_store(None)


@pytest.mark.asyncio
async def test_disabled_worker_still_stores_manual_review_source(client_a_admin, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.core.storage import LocalFsDocumentStore, set_store
    monkeypatch.setattr(settings, "invoice_extraction_enabled", False)
    set_store(LocalFsDocumentStore(str(tmp_path)))
    try:
        saved = await client_a_admin.post("/api/invoice-documents/upload", files={"file": ("manual.pdf", pdf_bytes(), "application/pdf")})
        assert saved.status_code == 201
        assert saved.json()["processing_error_code"] == "processing_disabled"
        assert (await client_a_admin.get(f"/api/invoice-documents/{saved.json()['id']}/content")).status_code == 200
        assert (await client_a_admin.get(f"/api/invoice-documents/{saved.json()['id']}/processing-attempts")).json() == []
    finally:
        set_store(None)


def test_embedded_xml_bypasses_ocr(monkeypatch):
    from tests.test_m5_einvoice import CII
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [(factur-x.xml) 4 0 R] >> >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << >> >>",
        b"<< /Type /Filespec /F (factur-x.xml) /EF << /F 5 0 R >> >>",
        b"<< /Type /EmbeddedFile /Length " + str(len(CII)).encode() + b" >>\nstream\n" + CII + b"\nendstream",
    ]
    data = b"%PDF-1.7\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    data += f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    def forbidden(*args):
        raise AssertionError("Embedded XML must bypass OCR")
    monkeypatch.setattr("app.core.invoice_ocr.ocr_page", forbidden)
    result = extract_invoice(data, "application/pdf")
    assert result["status"] == "succeeded"
    assert result["payload"]["is_einvoice"]
    assert result["payload"]["gross_amount"] == "476.00"
