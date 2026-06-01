"""Tests for PdfTextExtractor -- M2 real PDF extraction.

All tests are deterministic: synthetic PDFs are generated with reportlab
containing known text, then verified that the extractor correctly pulls
date, amount, VAT, document number, service period, vendor, and category.

Test categories:
  1. Unit tests for individual field extraction (date, amount, VAT, etc.)
  2. Category suggestion from keyword matching
  3. Confidence -> start status mapping
  4. End-to-end: upload synthetic PDF -> cost_entry with pre-filled fields
  5. Graceful handling of non-text PDFs (image-only simulation)
  6. Non-PDF mime types -> empty result
"""

from __future__ import annotations

import io
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.extraction import ExtractionResult, StubExtractor, set_extractor
from app.core.pdf_extraction import (
    PdfTextExtractor,
    _parse_german_amount,
    _parse_german_date,
)
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
def extractor():
    return PdfTextExtractor()


@pytest.fixture
def tmp_store(tmp_path):
    """Provide a temporary LocalFsDocumentStore and register it."""
    store = LocalFsDocumentStore(str(tmp_path))
    set_store(store)
    yield store
    set_store(None)  # type: ignore


# ============================================================
# 1. Helper function tests
# ============================================================
class TestGermanAmountParsing:
    def test_simple_amount(self):
        assert _parse_german_amount("234,56") == Decimal("234.56")

    def test_thousand_separator(self):
        assert _parse_german_amount("1.234,56") == Decimal("1234.56")

    def test_large_amount(self):
        assert _parse_german_amount("12.345.678,90") == Decimal("12345678.90")

    def test_no_thousands(self):
        assert _parse_german_amount("99,99") == Decimal("99.99")

    def test_with_spaces(self):
        assert _parse_german_amount(" 1.234,56 ") == Decimal("1234.56")

    def test_invalid(self):
        assert _parse_german_amount("abc") is None


class TestGermanDateParsing:
    def test_valid_date(self):
        assert _parse_german_date("15", "06", "2025") == "2025-06-15"

    def test_single_digit(self):
        assert _parse_german_date("1", "1", "2025") == "2025-01-01"

    def test_invalid_date(self):
        assert _parse_german_date("32", "13", "2025") is None


# ============================================================
# 2. Invoice date extraction
# ============================================================
class TestInvoiceDateExtraction:
    def test_labeled_rechnungsdatum(self, extractor):
        pdf = _make_pdf("Stadtwerke Musterstadt GmbH\nRechnungsdatum: 15.06.2025\nStrom")
        result = extractor.extract(pdf, "application/pdf")
        assert result.invoice_date == "2025-06-15"
        assert result.field_confidences["invoice_date"] == 0.8

    def test_labeled_datum(self, extractor):
        pdf = _make_pdf("Firma XY\nDatum 01.03.2025\nRechnung Nr. 123")
        result = extractor.extract(pdf, "application/pdf")
        assert result.invoice_date == "2025-03-01"

    def test_fallback_first_date(self, extractor):
        pdf = _make_pdf("Firma ABC\n20.12.2024\nSome invoice text")
        result = extractor.extract(pdf, "application/pdf")
        assert result.invoice_date == "2024-12-20"


# ============================================================
# 3. Gross amount extraction
# ============================================================
class TestGrossAmountExtraction:
    def test_labeled_gesamtbetrag(self, extractor):
        pdf = _make_pdf(
            "Firma ABC\nRechnung\n"
            "Netto: 100,00 EUR\n"
            "MwSt. 19%: 19,00 EUR\n"
            "Gesamtbetrag: 119,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.gross_amount == "119.00"

    def test_labeled_rechnungsbetrag(self, extractor):
        pdf = _make_pdf(
            "Firma ABC\nRechnungsbetrag 1.234,56 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.gross_amount == "1234.56"

    def test_labeled_brutto(self, extractor):
        pdf = _make_pdf(
            "Pos 1: 500,00\nPos 2: 250,00\nBrutto 750,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.gross_amount == "750.00"

    def test_zahlbetrag(self, extractor):
        pdf = _make_pdf(
            "Zahlbetrag 2.500,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.gross_amount == "2500.00"

    def test_fallback_largest_amount(self, extractor):
        pdf = _make_pdf(
            "Firma ABC\n"
            "Posten 1: 100,00 EUR\n"
            "Posten 2: 200,00 EUR\n"
            "EUR 350,00"
        )
        result = extractor.extract(pdf, "application/pdf")
        # Without a gross label, takes the largest
        assert result.gross_amount == "350.00"


# ============================================================
# 4. VAT extraction
# ============================================================
class TestVatExtraction:
    def test_mwst_19_percent(self, extractor):
        pdf = _make_pdf(
            "Netto: 100,00\nMwSt. 19% 19,00 EUR\nBrutto 119,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.vat_rate == "19"
        assert result.vat_amount == "19.00"

    def test_ust_7_percent(self, extractor):
        pdf = _make_pdf(
            "Netto: 100,00\nUSt. 7% 7,00 EUR\nBrutto 107,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.vat_rate == "7"
        assert result.vat_amount == "7.00"


# ============================================================
# 5. Document number extraction
# ============================================================
class TestDocumentNumberExtraction:
    def test_rechnungsnummer(self, extractor):
        pdf = _make_pdf("Rechnungsnummer: RE-2025-00042\nDatum: 01.06.2025")
        result = extractor.extract(pdf, "application/pdf")
        assert result.document_number == "RE-2025-00042"

    def test_beleg_nr(self, extractor):
        pdf = _make_pdf("Beleg-Nr. BLG-99887\nSome text")
        result = extractor.extract(pdf, "application/pdf")
        assert result.document_number == "BLG-99887"

    def test_rechnung_nr(self, extractor):
        pdf = _make_pdf("Rechnung Nr. 12345\nMore text here for extraction")
        result = extractor.extract(pdf, "application/pdf")
        assert result.document_number == "12345"


# ============================================================
# 6. Service period extraction
# ============================================================
class TestServicePeriodExtraction:
    def test_leistungszeitraum_bis(self, extractor):
        pdf = _make_pdf(
            "Firma XY\nLeistungszeitraum: 01.01.2025 bis 31.12.2025\n"
            "Rechnungsbetrag 1.000,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.service_period_from == "2025-01-01"
        assert result.service_period_to == "2025-12-31"

    def test_abrechnungszeitraum_dash(self, extractor):
        pdf = _make_pdf(
            "Abrechnungszeitraum 01.07.2024 - 30.06.2025\n"
            "Brutto 500,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.service_period_from == "2024-07-01"
        assert result.service_period_to == "2025-06-30"

    def test_vom_bis(self, extractor):
        pdf = _make_pdf(
            "Zeitraum vom 01.04.2025 bis 30.06.2025\n"
            "Betrag 300,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.service_period_from == "2025-04-01"
        assert result.service_period_to == "2025-06-30"


# ============================================================
# 7. Vendor extraction
# ============================================================
class TestVendorExtraction:
    def test_first_line_vendor(self, extractor):
        pdf = _make_pdf(
            "Stadtwerke Musterstadt GmbH\n"
            "Musterweg 1, 12345 Musterstadt\n"
            "Rechnungsdatum: 01.06.2025\n"
            "Rechnungsbetrag 234,56 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.vendor_name == "Stadtwerke Musterstadt GmbH"


# ============================================================
# 8. Category suggestion
# ============================================================
class TestCategorySuggestion:
    def test_grundsteuer(self, extractor):
        pdf = _make_pdf(
            "Stadt Musterstadt\nGrundsteuerbescheid 2025\n"
            "Datum: 15.01.2025\nBetrag: 450,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "GRUNDSTEUER"

    def test_muellbeseitigung(self, extractor):
        pdf = _make_pdf(
            "Abfallwirtschaft GmbH\n"
            "Abfallentsorgung Jahresgebuehr 2025\n"
            "Datum: 01.02.2025\nRechnungsbetrag 180,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "MUELLBESEITIGUNG"

    def test_gebaeudeversicherung(self, extractor):
        pdf = _make_pdf(
            "Allianz Versicherungs AG\n"
            "Gebaeudeversicherung Praemienrechnung\n"
            "Datum: 01.03.2025\nRechnungsbetrag 1.200,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "GEBAEUDEVERSICHERUNG"

    def test_heizung_gas(self, extractor):
        pdf = _make_pdf(
            "Energieversorger AG\nGasabrechnung 2024/2025\n"
            "Abrechnungszeitraum 01.01.2024 - 31.12.2024\n"
            "Brutto 3.456,78 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "HEIZUNG_ZENTRAL"

    def test_hausmeister(self, extractor):
        pdf = _make_pdf(
            "Hausmeister-Service Mueller\n"
            "Rechnung Nr. HM-2025-003\nHausmeisterservice Monat Juni 2025\n"
            "Brutto 850,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "HAUSMEISTER"

    def test_kabel_breitband(self, extractor):
        pdf = _make_pdf(
            "Vodafone Deutschland GmbH\n"
            "Breitband Kabelanschluss\n"
            "Rechnungsbetrag 199,99 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "KABEL_BREITBAND"

    def test_no_match_returns_none(self, extractor):
        pdf = _make_pdf(
            "Rechnung fuer diverse Dienstleistungen\n"
            "Datum: 01.06.2025\nBetrag: 100,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code is None

    def test_gartenpflege(self, extractor):
        pdf = _make_pdf(
            "Gaertner Meier GbR\n"
            "Gartenpflege Fruehling 2025\n"
            "Rechnungsbetrag 320,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "GARTENPFLEGE"

    def test_aufzug(self, extractor):
        pdf = _make_pdf(
            "OTIS Aufzuege GmbH\n"
            "Aufzugswartung Quartal 2/2025\n"
            "Rechnungsbetrag 450,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "AUFZUG"

    def test_winterdienst(self, extractor):
        pdf = _make_pdf(
            "Winterdienst Schmidt\n"
            "Schneeraeumung Dezember 2024\n"
            "Brutto 280,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "WINTERDIENST"


# ============================================================
# 9. Confidence -> start status mapping
# ============================================================
class TestConfidenceToStatus:
    """Test the logic that maps extraction confidence to cost_entry start status.

    Per m2-gobd-belegfluss.md section 3.5:
      - High confidence (critical fields present) -> draft_extracted
      - Low confidence or *_PRUEFEN category -> needs_review
      - NEVER automatically confirmed
    """

    def test_complete_extraction_high_confidence(self, extractor):
        """A well-structured invoice yields draft_extracted-worthy confidence."""
        pdf = _make_pdf(
            "Stadtwerke Musterstadt GmbH\n"
            "Rechnungsnummer: RE-2025-001\n"
            "Rechnungsdatum: 15.06.2025\n"
            "Leistungszeitraum: 01.01.2025 bis 31.12.2025\n"
            "Grundsteuerbescheid\n"
            "Netto: 500,00 EUR\n"
            "MwSt. 19% 95,00 EUR\n"
            "Gesamtbetrag: 595,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")

        assert result.invoice_date == "2025-06-15"
        assert result.gross_amount == "595.00"
        assert result.vat_rate == "19"
        assert result.vat_amount == "95.00"
        assert result.document_number == "RE-2025-001"
        assert result.service_period_from == "2025-01-01"
        assert result.service_period_to == "2025-12-31"
        assert result.suggested_category_code == "GRUNDSTEUER"
        # Overall confidence should be reasonably high (> 0.5)
        assert result.confidence > 0.5, f"Expected >0.5, got {result.confidence}"

    def test_incomplete_extraction_low_confidence(self, extractor):
        """A sparse document should yield low confidence."""
        pdf = _make_pdf("Some text without any recognizable invoice fields or patterns at all")
        result = extractor.extract(pdf, "application/pdf")
        assert result.confidence < 0.3

    def test_review_category_lowers_confidence(self, extractor):
        """A PRUEFEN-category suggestion has lower field confidence."""
        pdf = _make_pdf(
            "Hausmeister-Service\nDatum: 01.06.2025\nBrutto 500,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        assert result.suggested_category_code == "HAUSMEISTER"
        # HAUSMEISTER is in _REVIEW_CATEGORIES -> lower confidence
        assert result.field_confidences["suggested_category_code"] == 0.4

    def _determine_start_status(self, result: ExtractionResult) -> str:
        """Replicate the status determination logic from the upload path.

        Per spec: low overall confidence OR critical *_PRUEFEN category -> needs_review.
        High confidence and non-critical category -> draft_extracted.
        NEVER confirmed.
        """
        CONFIDENCE_THRESHOLD = 0.5
        REVIEW_CATEGORIES = {
            "HAUSMEISTER", "SONSTIGE_BK", "KABEL_BREITBAND",
            "MESSDIENST", "CO2_KOSTEN",
        }

        if result.confidence < CONFIDENCE_THRESHOLD:
            return "needs_review"
        if result.suggested_category_code in REVIEW_CATEGORIES:
            return "needs_review"
        return "draft_extracted"

    def test_status_high_confidence_normal_category(self, extractor):
        """Full extraction + normal category -> draft_extracted."""
        pdf = _make_pdf(
            "Stadtwerke GmbH\nRechnungsnummer: RE-001\nRechnungsdatum: 01.06.2025\n"
            "Leistungszeitraum: 01.01.2025 bis 31.12.2025\n"
            "Grundsteuerbescheid\nGesamtbetrag: 500,00 EUR\nMwSt. 19% 80,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        status = self._determine_start_status(result)
        assert status == "draft_extracted"

    def test_status_low_confidence(self, extractor):
        """Incomplete extraction -> needs_review."""
        pdf = _make_pdf("Hallo Welt, some unrelated text here that has no invoice data")
        result = extractor.extract(pdf, "application/pdf")
        status = self._determine_start_status(result)
        assert status == "needs_review"

    def test_status_review_category(self, extractor):
        """Even with good extraction, PRUEFEN category -> needs_review."""
        pdf = _make_pdf(
            "Hausmeister Mueller GmbH\nRechnungsnummer: HM-001\n"
            "Rechnungsdatum: 01.06.2025\n"
            "Leistungszeitraum: 01.01.2025 bis 31.12.2025\n"
            "Hausmeisterservice\nGesamtbetrag: 1.000,00 EUR\nMwSt. 19% 160,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        status = self._determine_start_status(result)
        assert status == "needs_review"

    def test_never_confirmed(self, extractor):
        """No extraction result should ever lead to 'confirmed'."""
        for text in [
            "Best possible invoice ever with all fields\nRechnungsdatum: 01.06.2025\n"
            "Grundsteuerbescheid\nGesamtbetrag: 500,00 EUR\nMwSt. 19% 80,00 EUR\n"
            "Leistungszeitraum: 01.01.2025 bis 31.12.2025\nRechnungsnummer: RE-001",
            "Barely any text",
        ]:
            pdf = _make_pdf(text)
            result = extractor.extract(pdf, "application/pdf")
            status = self._determine_start_status(result)
            assert status != "confirmed"


# ============================================================
# 10. PDF without text layer (graceful degradation)
# ============================================================
class TestNoTextLayer:
    def test_empty_pdf_no_crash(self, extractor):
        """A PDF without text should return confidence=0, not crash."""
        pdf = _make_empty_pdf()
        result = extractor.extract(pdf, "application/pdf")
        assert result.confidence == 0.0
        assert result.invoice_date is None
        assert result.gross_amount is None

    def test_corrupted_bytes_no_crash(self, extractor):
        """Garbage bytes should not crash the extractor."""
        result = extractor.extract(b"NOT A PDF AT ALL", "application/pdf")
        assert result.confidence == 0.0

    def test_non_pdf_mime_type(self, extractor):
        """Non-PDF mime type should return empty result immediately."""
        result = extractor.extract(b"some bytes", "text/plain")
        assert result.confidence == 0.0
        assert result.raw_text is None


# ============================================================
# 11. StubExtractor still works (backward compatibility)
# ============================================================
class TestStubExtractorStillWorks:
    def test_stub_returns_zero_confidence(self):
        stub = StubExtractor()
        result = stub.extract(b"anything", "application/pdf")
        assert result.confidence == 0.0
        assert result.invoice_date is None


# ============================================================
# 12. Comprehensive German invoice (full field extraction)
# ============================================================
class TestComprehensiveExtraction:
    def test_full_german_invoice(self, extractor):
        """A realistic German invoice text with all fields."""
        invoice_text = (
            "Stadtwerke Musterstadt GmbH\n"
            "Musterweg 1\n"
            "12345 Musterstadt\n"
            "\n"
            "Rechnung\n"
            "\n"
            "Rechnungsnummer: RE-2025-00042\n"
            "Rechnungsdatum: 15.06.2025\n"
            "Leistungszeitraum: 01.01.2025 bis 31.12.2025\n"
            "\n"
            "Grundsteuer Vorauszahlung 2025\n"
            "\n"
            "Netto                     378,15 EUR\n"
            "MwSt. 19%                  71,85 EUR\n"
            "Gesamtbetrag              450,00 EUR\n"
            "\n"
            "Zahlbar bis 30.06.2025\n"
            "IBAN: DE89 3704 0044 0532 0130 00\n"
        )
        pdf = _make_pdf(invoice_text)
        result = extractor.extract(pdf, "application/pdf")

        assert result.vendor_name == "Stadtwerke Musterstadt GmbH"
        assert result.invoice_date == "2025-06-15"
        assert result.document_number == "RE-2025-00042"
        assert result.gross_amount == "450.00"
        assert result.vat_rate == "19"
        assert result.vat_amount == "71.85"
        assert result.service_period_from == "2025-01-01"
        assert result.service_period_to == "2025-12-31"
        assert result.suggested_category_code == "GRUNDSTEUER"
        assert result.confidence > 0.5

        # Verify raw_text is populated
        assert result.raw_text is not None
        assert "Stadtwerke" in result.raw_text

    def test_serialization_roundtrip(self, extractor):
        """ExtractionResult.to_dict() produces valid JSONB-compatible dict."""
        pdf = _make_pdf(
            "Firma XY\nDatum: 01.06.2025\nGesamtbetrag: 100,00 EUR"
        )
        result = extractor.extract(pdf, "application/pdf")
        d = result.to_dict()

        assert isinstance(d, dict)
        assert d["invoice_date"] == result.invoice_date
        assert d["gross_amount"] == result.gross_amount
        assert d["confidence"] == result.confidence
        assert isinstance(d["field_confidences"], dict)


# ============================================================
# 13. End-to-end: upload PDF -> extraction -> cost_entry
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
    async def test_upload_pdf_extracts_fields(self, client_a_admin, m2_seed, tmp_store):
        """Upload a synthetic PDF invoice -> extraction_payload populated."""
        # Register PdfTextExtractor for this test
        set_extractor(PdfTextExtractor())

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

        # Check extraction_payload
        payload = doc["extraction_payload"]
        assert payload is not None
        assert payload["invoice_date"] == "2025-03-10"
        assert payload["document_number"] == "GS-2025-001"
        assert payload["gross_amount"] == "450.00"
        assert payload["suggested_category_code"] == "GRUNDSTEUER"
        assert payload["confidence"] > 0.4

        # Restore stub to not affect other tests
        set_extractor(StubExtractor())

    @pytest.mark.asyncio
    async def test_upload_pdf_with_cost_entry_prefill(self, client_a_admin, m2_seed, tmp_store):
        """Upload PDF linked to a cost_entry, verify entry gets draft status."""
        set_extractor(PdfTextExtractor())

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
            "source": "PDF_IMPORT",
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

        set_extractor(StubExtractor())

    @pytest.mark.asyncio
    async def test_empty_pdf_upload_needs_review(self, client_a_admin, m2_seed, tmp_store):
        """Uploading a PDF without text yields extraction with confidence=0."""
        set_extractor(PdfTextExtractor())

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

        set_extractor(StubExtractor())
