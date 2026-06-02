"""M5: E-Rechnung (XRechnung / ZUGFeRD) parsing -- second DocumentExtractor.

Empfangspflicht seit 01.01.2025 (B2B). This module parses structured EN-16931
invoices into the SAME ExtractionResult as the OCR path, so the existing
belegfluss (upload -> draft_extracted -> confirm) and the review UI work
unchanged. Structured data => high confidence; NEVER auto-confirmed (SDD §9).

Two carriers, one EN-16931 semantic model:
  - XRechnung: a standalone XML, either UBL (root <Invoice>) or CII
    (root <CrossIndustryInvoice>).
  - ZUGFeRD / Factur-X: a PDF/A-3 with an embedded CII XML (factur-x.xml /
    zugferd-invoice.xml / xrechnung.xml). We extract the embedded XML and parse
    it as CII.

Dependencies: stdlib xml.etree.ElementTree (namespace-wildcard `{*}` paths,
robust to prefix variation) + pdfminer.six (already a dependency) to walk the
PDF EmbeddedFiles name tree. No new dependencies; ZUGFeRD extraction degrades
gracefully (returns None -> caller falls back to the OCR/text extractor).
"""

from __future__ import annotations

import io
import re
from decimal import Decimal, InvalidOperation

import xml.etree.ElementTree as ET

from app.core.extraction import DocumentExtractor, ExtractionResult

# Embedded-file names used by ZUGFeRD / Factur-X / XRechnung-in-PDF.
_ZUGFERD_XML_NAMES = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "facturx.xml")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _text(root: ET.Element, path: str) -> str | None:
    el = root.find(path)
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _amount(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(value.strip()))
    except (InvalidOperation, ValueError):
        return None


def _norm_date(value: str | None, fmt: str | None = None) -> str | None:
    """Normalise an invoice date to ISO YYYY-MM-DD.

    UBL dates are already ISO. CII DateTimeString uses format '102' = YYYYMMDD.
    """
    if not value:
        return None
    v = value.strip()
    if fmt == "102" or re.fullmatch(r"\d{8}", v):
        return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"
    # already ISO (possibly with time) -> take the date part
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
    return m.group(1) if m else v


# ---------------------------------------------------------------------------
# CII (CrossIndustryInvoice) -- ZUGFeRD / Factur-X / XRechnung-CII
# ---------------------------------------------------------------------------
def _parse_cii(root: ET.Element, source_format: str) -> ExtractionResult:
    number = _text(root, ".//{*}ExchangedDocument/{*}ID")
    date_el = root.find(".//{*}ExchangedDocument/{*}IssueDateTime/{*}DateTimeString")
    invoice_date = None
    if date_el is not None:
        invoice_date = _norm_date(date_el.text, date_el.get("format"))
    seller = _text(root, ".//{*}SellerTradeParty/{*}Name")
    gross = _amount(_text(root, ".//{*}SpecifiedTradeSettlementHeaderMonetarySummation/{*}GrandTotalAmount"))
    vat = _amount(_text(root, ".//{*}SpecifiedTradeSettlementHeaderMonetarySummation/{*}TaxTotalAmount"))
    if vat is None:
        vat = _amount(_text(root, ".//{*}ApplicableTradeTax/{*}CalculatedAmount"))
    rate = _amount(_text(root, ".//{*}ApplicableTradeTax/{*}RateApplicablePercent"))
    sp_from = sp_to = None
    sf = root.find(".//{*}BillingSpecifiedPeriod/{*}StartDateTime/{*}DateTimeString")
    st = root.find(".//{*}BillingSpecifiedPeriod/{*}EndDateTime/{*}DateTimeString")
    if sf is not None:
        sp_from = _norm_date(sf.text, sf.get("format"))
    if st is not None:
        sp_to = _norm_date(st.text, st.get("format"))
    return _build(number, invoice_date, seller, gross, vat, rate, sp_from, sp_to, source_format)


# ---------------------------------------------------------------------------
# UBL (Invoice) -- XRechnung-UBL
# ---------------------------------------------------------------------------
def _parse_ubl(root: ET.Element) -> ExtractionResult:
    number = _text(root, "{*}ID")
    invoice_date = _norm_date(_text(root, "{*}IssueDate"))
    seller = (
        _text(root, ".//{*}AccountingSupplierParty/{*}Party/{*}PartyName/{*}Name")
        or _text(root, ".//{*}AccountingSupplierParty/{*}Party/{*}PartyLegalEntity/{*}RegistrationName")
    )
    gross = _amount(_text(root, ".//{*}LegalMonetaryTotal/{*}TaxInclusiveAmount"))
    if gross is None:
        gross = _amount(_text(root, ".//{*}LegalMonetaryTotal/{*}PayableAmount"))
    vat = _amount(_text(root, ".//{*}TaxTotal/{*}TaxAmount"))
    rate = _amount(_text(root, ".//{*}TaxTotal/{*}TaxSubtotal/{*}TaxCategory/{*}Percent"))
    sp_from = _norm_date(_text(root, ".//{*}InvoicePeriod/{*}StartDate"))
    sp_to = _norm_date(_text(root, ".//{*}InvoicePeriod/{*}EndDate"))
    return _build(number, invoice_date, seller, gross, vat, rate, sp_from, sp_to, "XRECHNUNG_UBL")


def _build(number, invoice_date, seller, gross, vat, rate, sp_from, sp_to, source_format) -> ExtractionResult:
    conf = {}
    for k, v in (
        ("document_number", number), ("invoice_date", invoice_date), ("vendor_name", seller),
        ("gross_amount", gross), ("vat_amount", vat), ("vat_rate", rate),
        ("service_period_from", sp_from), ("service_period_to", sp_to),
    ):
        if v is not None:
            conf[k] = 0.99  # structured data: high confidence
    overall = 0.95 if gross is not None and invoice_date is not None else 0.6
    return ExtractionResult(
        vendor_name=seller,
        invoice_date=invoice_date,
        document_number=number,
        gross_amount=gross,
        vat_amount=vat,
        vat_rate=rate,
        service_period_from=sp_from,
        service_period_to=sp_to,
        suggested_category_code=None,  # user picks; financial facts are the point
        confidence=overall,
        field_confidences=conf,
        raw_text=None,
        is_einvoice=True,
        source_format=source_format,
    )


def parse_en16931_xml(xml_bytes: bytes) -> ExtractionResult | None:
    """Parse an EN-16931 XML (UBL or CII). Returns None if it is not one."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    local = root.tag.rsplit("}", 1)[-1]  # strip namespace
    if local == "CrossIndustryInvoice":
        return _parse_cii(root, "XRECHNUNG_CII")
    if local == "Invoice":
        # Could be UBL invoice; confirm it has UBL-ish content.
        return _parse_ubl(root)
    return None


# ---------------------------------------------------------------------------
# ZUGFeRD: extract the embedded CII XML from a PDF/A-3 (pdfminer.six)
# ---------------------------------------------------------------------------
def extract_zugferd_xml(pdf_bytes: bytes) -> bytes | None:
    """Return the embedded ZUGFeRD/Factur-X CII XML bytes, or None.

    Walks the PDF catalog's Names/EmbeddedFiles name tree via pdfminer.six.
    Degrades gracefully (returns None) on any structural problem.
    """
    try:
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdftypes import resolve1, PDFStream
    except ImportError:
        return None

    try:
        doc = PDFDocument(PDFParser(io.BytesIO(pdf_bytes)))
        names = resolve1(doc.catalog.get("Names")) if doc.catalog else None
        if not names:
            return None
        ef = resolve1(names.get("EmbeddedFiles"))
        if not ef:
            return None
        candidates: list[tuple[str, bytes]] = []

        def walk(node) -> None:
            node = resolve1(node)
            if not isinstance(node, dict):
                return
            kids = node.get("Kids")
            if kids:
                for kid in resolve1(kids) or []:
                    walk(kid)
            arr = resolve1(node.get("Names"))
            if arr:
                # flat list: [name1, filespec1, name2, filespec2, ...]
                for i in range(0, len(arr) - 1, 2):
                    try:
                        fname = arr[i]
                        if isinstance(fname, bytes):
                            fname = fname.decode("latin-1", "replace")
                        spec = resolve1(arr[i + 1])
                        ef_dict = resolve1(spec.get("EF")) if isinstance(spec, dict) else None
                        stream = None
                        if isinstance(ef_dict, dict):
                            stream = resolve1(ef_dict.get("F") or ef_dict.get("UF"))
                        if isinstance(stream, PDFStream):
                            candidates.append((str(fname), stream.get_data()))
                    except Exception:
                        continue

        walk(ef)
        if not candidates:
            return None
        # Prefer the well-known ZUGFeRD/Factur-X names; else the first XML-ish one.
        for fname, data in candidates:
            if fname.lower() in _ZUGFERD_XML_NAMES:
                return data
        for fname, data in candidates:
            if fname.lower().endswith(".xml") or data.lstrip()[:5].lower() == b"<?xml":
                return data
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------
def _looks_like_xml(file_bytes: bytes, mime_type: str) -> bool:
    if "xml" in (mime_type or "").lower():
        return True
    head = file_bytes.lstrip()[:512].lower()
    return head.startswith(b"<?xml") or b"crossindustryinvoice" in head or b"<invoice" in head


def _looks_like_pdf(file_bytes: bytes, mime_type: str) -> bool:
    return file_bytes[:5] == b"%PDF-" or "pdf" in (mime_type or "").lower()


class EInvoiceExtractor:
    """Parses XRechnung (UBL/CII XML) and ZUGFeRD (PDF/A-3 embedded CII).

    Returns an ExtractionResult with is_einvoice=True on success. For a PDF
    without an embedded e-invoice XML it returns a low-confidence,
    is_einvoice=False result so the router can fall back to OCR.
    """

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        if _looks_like_xml(file_bytes, mime_type):
            result = parse_en16931_xml(file_bytes)
            if result is not None:
                return result
        if _looks_like_pdf(file_bytes, mime_type):
            xml = extract_zugferd_xml(file_bytes)
            if xml is not None:
                result = parse_en16931_xml(xml)
                if result is not None:
                    # mark carrier as ZUGFeRD regardless of inner UBL/CII flavour
                    return _with_format(result, "ZUGFERD")
        return ExtractionResult(confidence=0.0, is_einvoice=False)


def _with_format(r: ExtractionResult, fmt: str) -> ExtractionResult:
    return ExtractionResult(
        vendor_name=r.vendor_name, invoice_date=r.invoice_date, document_number=r.document_number,
        gross_amount=r.gross_amount, vat_amount=r.vat_amount, vat_rate=r.vat_rate,
        service_period_from=r.service_period_from, service_period_to=r.service_period_to,
        suggested_category_code=r.suggested_category_code, confidence=r.confidence,
        field_confidences=r.field_confidences, raw_text=r.raw_text,
        is_einvoice=r.is_einvoice, source_format=fmt,
    )


class RoutingExtractor:
    """Routes each upload to the right extractor (M5).

    XML or PDF-with-embedded-XML -> EInvoiceExtractor (structured, high confidence).
    Everything else -> the fallback (OCR/text) extractor. One active extractor
    keeps the single-extractor architecture (extraction.get_extractor) intact.
    """

    def __init__(self, fallback: DocumentExtractor):
        self._einvoice = EInvoiceExtractor()
        self._fallback = fallback

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        if _looks_like_xml(file_bytes, mime_type):
            r = self._einvoice.extract(file_bytes, mime_type)
            # XML that is not a valid e-invoice still should not crash; fall back.
            return r if r.is_einvoice else self._fallback.extract(file_bytes, mime_type)
        if _looks_like_pdf(file_bytes, mime_type):
            r = self._einvoice.extract(file_bytes, mime_type)
            if r.is_einvoice:
                return r
        return self._fallback.extract(file_bytes, mime_type)
