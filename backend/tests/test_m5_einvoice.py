"""M5: E-Rechnung (XRechnung / ZUGFeRD) extraction tests.

Pure-function tests (no DB): synthetic XRechnung UBL + CII XML map correctly to
ExtractionResult; the RoutingExtractor routes XML to the e-invoice parser and
non-e-invoice PDFs to the fallback; ZUGFeRD embedded-XML extraction degrades
gracefully on a plain PDF.
"""

from app.core.einvoice import (
    EInvoiceExtractor,
    RoutingExtractor,
    extract_zugferd_xml,
    parse_en16931_xml,
)
from app.core.extraction import ExtractionResult

UBL = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>RE-2025-0815</cbc:ID>
  <cbc:IssueDate>2025-03-15</cbc:IssueDate>
  <cac:InvoicePeriod><cbc:StartDate>2025-01-01</cbc:StartDate><cbc:EndDate>2025-03-31</cbc:EndDate></cac:InvoicePeriod>
  <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>Stadtwerke Muster GmbH</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">19.00</cbc:TaxAmount>
    <cac:TaxSubtotal><cac:TaxCategory><cbc:Percent>19.0</cbc:Percent></cac:TaxCategory></cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxInclusiveAmount currencyID="EUR">119.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">119.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>"""

CII = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument><ram:ID>ZF-2025-001</ram:ID>
    <ram:IssueDateTime><udt:DateTimeString format="102">20250415</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Hausmeister Service AG</ram:Name></ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:BillingSpecifiedPeriod>
        <ram:StartDateTime><udt:DateTimeString format="102">20250101</udt:DateTimeString></ram:StartDateTime>
        <ram:EndDateTime><udt:DateTimeString format="102">20251231</udt:DateTimeString></ram:EndDateTime>
      </ram:BillingSpecifiedPeriod>
      <ram:ApplicableTradeTax><ram:CalculatedAmount>76.00</ram:CalculatedAmount><ram:RateApplicablePercent>19.0</ram:RateApplicablePercent></ram:ApplicableTradeTax>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxTotalAmount>76.00</ram:TaxTotalAmount><ram:GrandTotalAmount>476.00</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""


class _FakeFallback:
    """Marker fallback to assert the router delegates to it."""

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        return ExtractionResult(confidence=0.42, raw_text="FALLBACK", is_einvoice=False)


def test_parse_ubl():
    r = parse_en16931_xml(UBL)
    assert r is not None and r.is_einvoice
    assert r.source_format == "XRECHNUNG_UBL"
    assert r.document_number == "RE-2025-0815"
    assert r.invoice_date == "2025-03-15"
    assert r.vendor_name == "Stadtwerke Muster GmbH"
    assert r.gross_amount == "119.00"
    assert r.vat_amount == "19.00"
    assert r.vat_rate == "19.0"
    assert r.service_period_from == "2025-01-01"
    assert r.service_period_to == "2025-03-31"
    assert r.confidence >= 0.9


def test_parse_cii_with_102_date_format():
    r = parse_en16931_xml(CII)
    assert r is not None and r.is_einvoice
    assert r.source_format == "XRECHNUNG_CII"
    assert r.document_number == "ZF-2025-001"
    assert r.invoice_date == "2025-04-15"  # 20250415 normalised
    assert r.vendor_name == "Hausmeister Service AG"
    assert r.gross_amount == "476.00"
    assert r.vat_amount == "76.00"
    assert r.vat_rate == "19.0"
    assert r.service_period_from == "2025-01-01"
    assert r.service_period_to == "2025-12-31"


def test_parse_non_invoice_xml_returns_none():
    assert parse_en16931_xml(b"<?xml version='1.0'?><foo><bar/></foo>") is None
    assert parse_en16931_xml(b"not xml at all") is None


def test_router_routes_xml_to_einvoice():
    router = RoutingExtractor(_FakeFallback())
    r = router.extract(UBL, "application/xml")
    assert r.is_einvoice and r.gross_amount == "119.00"


def test_router_falls_back_for_plain_pdf():
    router = RoutingExtractor(_FakeFallback())
    r = router.extract(b"%PDF-1.7\n%plain pdf without embedded e-invoice\n", "application/pdf")
    assert not r.is_einvoice
    assert r.raw_text == "FALLBACK"


def test_router_falls_back_for_non_invoice_xml():
    router = RoutingExtractor(_FakeFallback())
    r = router.extract(b"<?xml version='1.0'?><note>hello</note>", "application/xml")
    assert r.raw_text == "FALLBACK"


def test_zugferd_extraction_graceful_on_plain_pdf():
    # A plain PDF has no EmbeddedFiles name tree -> None (caller falls back).
    assert extract_zugferd_xml(b"%PDF-1.7\nplain\n%%EOF") is None


def test_einvoice_extractor_marks_pdf_without_xml_as_not_einvoice():
    r = EInvoiceExtractor().extract(b"%PDF-1.7\nplain\n", "application/pdf")
    assert not r.is_einvoice and r.confidence == 0.0
