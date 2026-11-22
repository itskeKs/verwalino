"""M5: E-Rechnung (XRechnung / ZUGFeRD) extraction tests.

Pure-function tests (no DB): synthetic XRechnung UBL + CII XML map correctly to
ExtractionResult; the RoutingExtractor routes XML to the e-invoice parser and
non-e-invoice PDFs to the fallback; ZUGFeRD embedded-XML extraction degrades
gracefully on a plain PDF.
"""

import pytest

from app.core.einvoice import (
    EInvoiceExtractor,
    RoutingExtractor,
    extract_zugferd_xml,
    parse_en16931_xml,
)
from app.core.extraction import ExtractionResult, StubExtractor
from app.core.invoice_pdf import inspect_invoice

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


# The payee's account as the payment data of the two syntaxes state it (EN 16931 BT-84). The IBAN is the one of the
# insurer's line in the journey's bank statement (docs/acceptance/data), printed the way an invoice prints it.
PAYEE_IBAN = "DE02120300000000202051"
UBL_PAYEE = b"""<cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode>
    <cac:PayeeFinancialAccount><cbc:ID>de02 1203 0000 0000 2020 51</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>"""
UBL_PAYER = b"""<cac:PaymentMeans><cbc:PaymentMeansCode>59</cbc:PaymentMeansCode>
    <cac:PaymentMandate><cac:PayerFinancialAccount><cbc:ID>DE89370400440532013000</cbc:ID></cac:PayerFinancialAccount></cac:PaymentMandate></cac:PaymentMeans>"""
CII_PAYEE = b"""<ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>58</ram:TypeCode>
        <ram:PayeePartyCreditorFinancialAccount><ram:IBANID>DE02 1203 0000 0000 2020 51</ram:IBANID></ram:PayeePartyCreditorFinancialAccount>
      </ram:SpecifiedTradeSettlementPaymentMeans>"""
CII_PAYER = b"""<ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>59</ram:TypeCode>
        <ram:PayerPartyDebtorFinancialAccount><ram:IBANID>DE89370400440532013000</ram:IBANID></ram:PayerPartyDebtorFinancialAccount>
      </ram:SpecifiedTradeSettlementPaymentMeans>"""


def with_payment(syntax_xml: bytes, payment: bytes) -> bytes:
    """The fixture invoice with a payment-means block placed where its syntax holds it."""
    if b"</Invoice>" in syntax_xml:
        return syntax_xml.replace(b"</Invoice>", payment + b"</Invoice>")
    return syntax_xml.replace(b"<ram:ApplicableTradeTax>", payment + b"<ram:ApplicableTradeTax>", 1)


UBL_WITH_IBAN = with_payment(UBL, UBL_PAYEE)
CII_WITH_IBAN = with_payment(CII, CII_PAYEE)


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


# ---- the payee's IBAN (issue #500: the vendor's IBAN is a field of the invoice proposal) ---------------------

@pytest.mark.parametrize("xml,fmt", [(UBL_WITH_IBAN, "XRECHNUNG_UBL"), (CII_WITH_IBAN, "XRECHNUNG_CII")], ids=["ubl", "cii"])
def test_the_payees_iban_is_read_from_the_payment_data_and_normalised(xml, fmt):
    r = parse_en16931_xml(xml)
    assert r is not None and r.source_format == fmt
    assert r.vendor_iban == PAYEE_IBAN


def test_the_zugferd_carrier_gives_the_payees_iban_of_its_embedded_data():
    from tests.test_invoice_extraction_worker import zugferd_pdf
    r = EInvoiceExtractor().extract(zugferd_pdf(CII_WITH_IBAN), "application/pdf")
    assert r.is_einvoice and r.source_format == "ZUGFERD" and r.vendor_iban == PAYEE_IBAN


@pytest.mark.parametrize("xml", [UBL, CII, with_payment(UBL, UBL_PAYER), with_payment(CII, CII_PAYER)],
                         ids=["ubl-states-none", "cii-states-none", "ubl-only-the-payers-account", "cii-only-the-payers-account"])
def test_an_invoice_without_a_payee_account_has_no_vendor_iban_and_the_payers_account_is_never_taken(xml):
    assert parse_en16931_xml(xml).vendor_iban is None


@pytest.mark.parametrize("stated", ["DE02120300000000202052", "DE0212030000000020205", "0123456789", "Sparkasse Musterstadt"],
                         ids=["check-digits", "too-short", "account-number", "text"])
def test_a_payee_account_that_is_not_a_valid_iban_is_not_stated(stated):
    r = parse_en16931_xml(UBL_WITH_IBAN.replace(b"de02 1203 0000 0000 2020 51", stated.encode()))
    assert r is not None and r.is_einvoice and r.vendor_iban is None and r.gross_amount == "119.00"


def test_the_shared_result_keeps_the_shape_the_inboxs_database_function_fixes():
    """The IBAN is a field of the invoice worker's proposal only: ``to_dict()`` feeds the inbox too."""
    r = parse_en16931_xml(UBL_WITH_IBAN)
    assert "vendor_iban" not in r.to_dict() and "vendor_iban" not in r.to_dict()["field_confidences"]


@pytest.mark.parametrize("kind", ["xrechnung", "zugferd"])
def test_the_invoice_proposal_of_a_structured_invoice_carries_the_iban_and_its_certainty(kind):
    from tests.test_invoice_extraction_worker import zugferd_pdf
    data, mime = (UBL_WITH_IBAN, "application/xml") if kind == "xrechnung" else (zugferd_pdf(CII_WITH_IBAN), "application/pdf")
    outcome = inspect_invoice(data, mime)
    payload = outcome["payload"]
    assert outcome["status"] == "succeeded" and payload["is_einvoice"] is True
    assert payload["vendor_iban"] == PAYEE_IBAN and payload["field_confidences"]["vendor_iban"] == 0.99
    assert payload["gross_amount"] in ("119.00", "476.00")


def test_the_invoice_proposal_of_a_structured_invoice_without_an_iban_says_so_and_claims_no_certainty():
    payload = inspect_invoice(UBL, "application/xml")["payload"]
    assert payload["vendor_iban"] is None and "vendor_iban" not in payload["field_confidences"]


def test_the_stub_extractor_returns_an_empty_result():
    result = StubExtractor().extract(b"anything", "application/pdf")
    assert result.confidence == 0.0
    assert result.invoice_date is None
