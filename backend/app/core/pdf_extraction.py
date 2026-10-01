"""PdfTextExtractor -- real PDF text-layer extraction for German invoices.

Implements the DocumentExtractor protocol (extraction.py) using pdfplumber
for text-layer extraction of digital PDFs.

Design decisions (from task spec + m2-gobd-belegfluss.md section 3.5):
  - pdfplumber as default (text layer of digital PDFs).
  - Tesseract as OPTIONAL fallback behind try-import + config flag.
    If Tesseract/poppler is not installed: graceful degradation to
    confidence=0 / needs_review, no crash.
  - German field heuristics for date, amounts, VAT, document number,
    service period, vendor hint.
  - Category suggestion via keyword mapping to seeded cost_category.code.
  - Per-field confidence; overall confidence = weighted average.
  - This is a SUGGESTION tool -- the human always confirms (SDD section 9).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.core.extraction import DocumentExtractor, ExtractionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# German date patterns
# ---------------------------------------------------------------------------
# DD.MM.YYYY (most common German format)
_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})\b"
)

# ---------------------------------------------------------------------------
# German amount patterns: "1.234,56" or "1234,56" or "234,56"
# Optionally followed/preceded by EUR/euro sign
# ---------------------------------------------------------------------------
_AMOUNT_PATTERN = re.compile(
    r"(?:EUR\s*|€\s*)?"  # optional leading currency
    r"(-?\s*\d{1,3}(?:\.\d{3})*,\d{2})"  # German number format
    r"(?:\s*(?:EUR|€))?"  # optional trailing currency
)

# Specifically for total/gross amounts (look for these labels)
_GROSS_LABELS = re.compile(
    r"(?:Gesamt|Brutto|Rechnungsbetrag|Gesamtbetrag|Endbetrag|Summe(?:\s+brutto)?|Zahlbetrag)"
    r"\s*(?:[\:\s]|inkl\.?\s*(?:MwSt|USt|Ust)\.?)*"
    r"\s*(?:EUR\s*|€\s*)?"
    r"(-?\s*\d{1,3}(?:\.\d{3})*,\d{2})"
    r"(?:\s*(?:EUR|€))?",
    re.IGNORECASE,
)

# Netto pattern (to help identify gross vs net)
_NET_LABELS = re.compile(
    r"(?:Netto|Nettobetrag|Summe\s+netto)"
    r"\s*(?:[\:\s])*"
    r"\s*(?:EUR\s*|€\s*)?"
    r"(-?\s*\d{1,3}(?:\.\d{3})*,\d{2})"
    r"(?:\s*(?:EUR|€))?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# VAT patterns
# ---------------------------------------------------------------------------
_VAT_RATE_PATTERN = re.compile(
    r"(?:MwSt|USt|Ust|Mehrwertsteuer|Umsatzsteuer)"
    r"[\.\s:]*"
    r"(\d{1,2})\s*(?:[,%]|Prozent)",
    re.IGNORECASE,
)

_VAT_AMOUNT_PATTERN = re.compile(
    r"(?:MwSt|USt|Ust|Mehrwertsteuer|Umsatzsteuer)"
    r"[\.\s:]*"
    r"(?:\d{1,2}\s*[%,]\s*)?"
    r"(?:EUR\s*|€\s*)?"
    r"(-?\s*\d{1,3}(?:\.\d{3})*,\d{2})"
    r"(?:\s*(?:EUR|€))?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Document number patterns
# ---------------------------------------------------------------------------
_DOC_NUMBER_PATTERNS = [
    # "Rechnungsnummer: XXX" or "Rechnung Nr. XXX" or "Beleg-Nr. XXX"
    re.compile(
        r"(?:Rechnungs?(?:nummer|[-\s]*Nr\.?)|Rechnung\s+Nr\.?|Beleg(?:nummer|[-\s]*Nr\.?)|Invoice\s*(?:No\.?|Number))"
        r"\s*[:\s#]*\s*"
        r"([^\s\n]+(?:[ \t][^\s\n]+)?)",
        re.IGNORECASE,
    ),
    # "RE-2025-001" or "RG-12345" standalone patterns
    re.compile(
        r"\b((?:RE|RG|INV|BLG|R)[-/]?\d{4,}(?:[-/]\d+)*)\b",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Service period (Leistungszeitraum / Abrechnungszeitraum)
# ---------------------------------------------------------------------------
_SERVICE_PERIOD_LABELS = re.compile(
    r"(?:Leistungszeitraum|Abrechnungszeitraum|Lieferzeitraum|Zeitraum|Periode)"
    r"\s*[:\s]*",
    re.IGNORECASE,
)

# "vom DD.MM.YYYY bis DD.MM.YYYY" or "DD.MM.YYYY - DD.MM.YYYY"
_PERIOD_RANGE = re.compile(
    r"(?:vom?\s+)?"
    r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})"
    r"\s*(?:bis|[-–])\s*"
    r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})"
)

# ---------------------------------------------------------------------------
# Category keyword mapping -> cost_category.code
# Maps keywords found in the extracted text to the seeded category codes.
# Priority: more specific patterns first (checked in order).
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    # Specific categories first
    ("GRUNDSTEUER", [
        r"grundsteuer", r"grundsteuer(?:bescheid|vorauszahlung)",
        r"oeffentliche\s+lasten", r"öffentliche\s+lasten",
    ]),
    ("KALTWASSER", [
        r"kaltwasser", r"wasserversorgung", r"frischwasser",
        r"trinkwasser", r"wasserwerk",
    ]),
    ("ENTWAESSERUNG", [
        r"entwässerung", r"entwaesserung", r"abwasser",
        r"kanalgebühr", r"kanalgebuehr", r"sielbau",
    ]),
    ("HEIZUNG_ZENTRAL", [
        r"heizung", r"heizkosten", r"fernwärme", r"fernwaerme",
        r"gasversorgung", r"gas(?:abrechnung|lieferung|rechnung)",
        r"erdgas", r"heizöl", r"heizoel", r"brennstoff",
        r"heizkostenabrechnung",
    ]),
    ("WARMWASSER_ZENTRAL", [
        r"warmwasser", r"warm\s*wasser",
    ]),
    ("MUELLBESEITIGUNG", [
        r"\bmüll(?:abfuhr|beseitigung|entsorgung|gebühr|gebuehr|tonne)?\b",
        r"\bmuell(?:abfuhr|beseitigung|entsorgung|gebühr|gebuehr|tonne)?\b",
        r"\babfall\b", r"\babfallentsorgung\b",
        r"\babfallgebühr\b", r"\babfallgebuehr\b",
        r"\bentsorgung\b", r"\bwertstoff\b",
    ]),
    ("STRASSENREINIGUNG", [
        r"straßenreinigung", r"strassenreinigung",
    ]),
    ("WINTERDIENST", [
        r"winterdienst", r"schneeräumung", r"schneeraeumung",
        r"streudienst", r"räumdienst", r"raeumdienst",
    ]),
    ("GEBAEUDEREINIGUNG", [
        r"gebäudereinigung", r"gebaeudereinigung",
        r"hausreinigung", r"treppenhausreinigung",
    ]),
    ("GARTENPFLEGE", [
        r"gartenpflege", r"grünflächenpflege", r"gruenflaechenpflege",
        r"grünanlage", r"gruenanlage", r"gärtner", r"gaertner",
        r"landschaftspflege", r"baumschnitt", r"rasenpflege",
    ]),
    ("ALLGEMEINSTROM", [
        r"allgemeinstrom", r"beleuchtung(?:\s+allgemein)?",
        r"hausstrom", r"treppenhausbeleuchtung",
        r"gemeinschaftsstrom",
    ]),
    ("SCHORNSTEINREINIGUNG", [
        r"schornstein", r"schornsteinfeger", r"kamin(?:kehrer|feger)",
        r"feuerungsstätte", r"feuerungsstaette",
    ]),
    ("GEBAEUDEVERSICHERUNG", [
        r"gebäudeversicherung", r"gebaeudeversicherung",
        r"wohngebäudeversicherung", r"wohngebaeudeversicherung",
        r"gebäudesachversicherung", r"gebaeudesachversicherung",
    ]),
    ("HAFTPFLICHTVERSICHERUNG", [
        r"haftpflicht", r"haus-?\s*und\s*grundbesitzer.*haftpflicht",
    ]),
    ("AUFZUG", [
        r"aufzug", r"aufzugsanlage", r"lift", r"fahrstuhl",
        r"aufzugswartung",
    ]),
    ("WASCHRAUM", [
        r"waschraum", r"waschküche", r"waschkueche",
        r"waschmaschine", r"gemeinschaftswasch",
    ]),
    ("HAUSMEISTER", [
        r"hausmeister", r"hauswart", r"facility\s*management",
        r"objektbetreuung",
    ]),
    ("SONSTIGE_BK", [
        r"sonstige\s+betriebskosten",
    ]),
    ("KABEL_BREITBAND", [
        r"kabel(?:anschluss|fernsehen|gebühr|gebuehr)?",
        r"breitband", r"antenne", r"gemeinschaftsantenne",
    ]),
    ("MESSDIENST", [
        r"messdienst", r"abrechnungsdienst", r"ablesedienst",
        r"ista\b", r"techem\b", r"brunata\b", r"minol\b",
    ]),
    ("CO2_KOSTEN", [
        r"co2", r"kohlendioxid",
    ]),
    ("WEG_VERWALTERKOSTEN", [
        r"verwaltergebühr", r"verwaltergebuehr", r"verwaltervergütung",
        r"verwalterverguetunga", r"hausverwaltung(?:sgebühr|sgebuehr)?",
    ]),
    ("BUCHHALTUNG", [
        r"buchhaltung", r"jahresabrechnung", r"wirtschaftsprüf",
        r"wirtschaftspruef",
    ]),
    ("EIGENTUEMERVERSAMMLUNG", [
        r"eigentümerversammlung", r"eigentuemerversammlung",
        r"weg-versammlung",
    ]),
    ("BANKGEBUEHREN", [
        r"bankgebühr", r"bankgebuehr", r"kontoführung",
        r"kontofuehrung",
    ]),
    ("RECHTSBERATUNG", [
        r"rechtsberatung", r"rechtsanwalt", r"anwaltskosten",
        r"prozesskosten",
    ]),
    ("INSTANDHALTUNG", [
        r"instandhaltung",
    ]),
    ("INSTANDSETZUNG", [
        r"instandsetzung", r"reparatur", r"sanierung",
    ]),
    ("ERHALTUNGSRUECKLAGE", [
        r"erhaltungsrücklage", r"erhaltungsruecklage",
        r"instandhaltungsrücklage", r"instandhaltungsruecklage",
        r"rücklage", r"ruecklage",
    ]),
    ("SONDERUMLAGE", [
        r"sonderumlage",
    ]),
]

# Precompile category patterns
_CATEGORY_COMPILED: list[tuple[str, list[re.Pattern[str]]]] = [
    (code, [re.compile(kw, re.IGNORECASE) for kw in keywords])
    for code, keywords in CATEGORY_KEYWORDS
]

# Categories where auto-assignment needs human review (cost_group = *_PRUEFEN)
_REVIEW_CATEGORIES = {
    "HAUSMEISTER", "SONSTIGE_BK", "KABEL_BREITBAND", "MESSDIENST", "CO2_KOSTEN",
}


# ---------------------------------------------------------------------------
# Helper: parse German amount string to Decimal
# ---------------------------------------------------------------------------
def _parse_german_amount(s: str) -> Decimal | None:
    """Parse '1.234,56' or '234,56' to Decimal. Returns None on failure."""
    try:
        cleaned = s.strip().replace(" ", "")
        # Remove thousand separators (dots before the comma)
        cleaned = cleaned.replace(".", "").replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _parse_german_date(day: str, month: str, year: str) -> str | None:
    """Parse German date components to ISO date string. Returns None if invalid."""
    try:
        d = date(int(year), int(month), int(day))
        return d.isoformat()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# PdfTextExtractor -- implements DocumentExtractor protocol
# ---------------------------------------------------------------------------
class PdfTextExtractor:
    """Extract structured data from German invoices using pdfplumber text layer.

    This is the default extractor for M2. It handles digital PDFs with embedded
    text. For scanned PDFs (image-only), it degrades gracefully to confidence=0.

    Tesseract OCR is available as an OPTIONAL fallback if:
      1. The `ocr_fallback` parameter is True.
      2. The `pytesseract` and `pdf2image` packages are installed.
      3. Tesseract/poppler binaries are available on the system.

    If any of those conditions are not met, the extractor returns confidence=0
    and the document goes to needs_review -- no crash.
    """

    def __init__(self, *, ocr_fallback: bool = False):
        self._ocr_fallback = ocr_fallback

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        """Extract structured data from a PDF document.

        Returns ExtractionResult with detected fields and confidence scores.
        Non-PDF mime types return empty result (confidence=0).
        """
        if mime_type not in ("application/pdf", "application/x-pdf"):
            return ExtractionResult(confidence=0.0, raw_text=None)

        text = self._extract_text(file_bytes)

        if not text or len(text.strip()) < 20:
            # No meaningful text extracted -- possibly a scanned PDF
            return ExtractionResult(
                confidence=0.0,
                raw_text=text or None,
            )

        return self._parse_german_invoice(text)

    def _extract_text(self, file_bytes: bytes) -> str | None:
        """Extract text from PDF using pdfplumber; optionally fall back to Tesseract."""
        import io

        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber not installed; cannot extract text from PDF")
            return None

        text_parts: list[str] = []
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
        except Exception:
            logger.exception("pdfplumber failed to read PDF")

        full_text = "\n".join(text_parts).strip()

        if full_text:
            return full_text

        # Text layer empty -- try Tesseract fallback if enabled
        if self._ocr_fallback:
            return self._tesseract_fallback(file_bytes)

        return None

    def _tesseract_fallback(self, file_bytes: bytes) -> str | None:
        """Use the same bounded subprocess as ordinary invoice processing."""
        from app.core.invoice_ocr import run_isolated_extraction
        result = run_isolated_extraction(file_bytes, "application/pdf")
        return (result.get("payload") or {}).get("raw_text")

    def _parse_german_invoice(self, text: str) -> ExtractionResult:
        """Parse extracted text using German invoice heuristics."""
        field_confidences: dict[str, float] = {}

        # --- Invoice date ---
        invoice_date = self._extract_invoice_date(text)
        field_confidences["invoice_date"] = 0.8 if invoice_date else 0.0

        # --- Document number ---
        document_number = self._extract_document_number(text)
        field_confidences["document_number"] = 0.7 if document_number else 0.0

        # --- Gross amount ---
        gross_amount = self._extract_gross_amount(text)
        field_confidences["gross_amount"] = 0.85 if gross_amount else 0.0

        # --- VAT ---
        vat_rate, vat_amount = self._extract_vat(text)
        field_confidences["vat_rate"] = 0.8 if vat_rate else 0.0
        field_confidences["vat_amount"] = 0.7 if vat_amount else 0.0

        # --- Service period ---
        period_from, period_to = self._extract_service_period(text)
        field_confidences["service_period_from"] = 0.75 if period_from else 0.0
        field_confidences["service_period_to"] = 0.75 if period_to else 0.0

        # --- Vendor ---
        vendor_name = self._extract_vendor(text)
        field_confidences["vendor_name"] = 0.5 if vendor_name else 0.0

        # --- Category suggestion ---
        suggested_category = self._suggest_category(text)
        field_confidences["suggested_category_code"] = (
            0.6 if suggested_category else 0.0
        )
        # Lower confidence for review-mandatory categories
        if suggested_category in _REVIEW_CATEGORIES:
            field_confidences["suggested_category_code"] = 0.4

        # --- Overall confidence ---
        # Weighted average: critical fields (date, amount) weigh more
        weights = {
            "invoice_date": 0.20,
            "gross_amount": 0.30,
            "document_number": 0.10,
            "vat_rate": 0.05,
            "vat_amount": 0.05,
            "service_period_from": 0.10,
            "service_period_to": 0.05,
            "vendor_name": 0.05,
            "suggested_category_code": 0.10,
        }
        overall = sum(
            field_confidences.get(f, 0.0) * w for f, w in weights.items()
        )

        return ExtractionResult(
            vendor_name=vendor_name,
            invoice_date=invoice_date,
            document_number=document_number,
            gross_amount=str(gross_amount) if gross_amount is not None else None,
            vat_amount=str(vat_amount) if vat_amount is not None else None,
            vat_rate=str(vat_rate) if vat_rate is not None else None,
            service_period_from=period_from,
            service_period_to=period_to,
            suggested_category_code=suggested_category,
            confidence=round(overall, 4),
            field_confidences=field_confidences,
            raw_text=text,
        )

    # --- Individual field extractors ---

    def _extract_invoice_date(self, text: str) -> str | None:
        """Extract invoice date from German text.

        Strategy: look for 'Rechnungsdatum', 'Datum', 'Ausstellungsdatum' labels
        near a DD.MM.YYYY date. Fall back to the first date found.
        """
        # Labeled date (highest priority)
        label_pattern = re.compile(
            r"(?:Rechnungsdatum|Belegdatum|Ausstellungsdatum|Datum\s*(?:der\s+Rechnung)?)"
            r"\s*[:\s]*"
            r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})",
            re.IGNORECASE,
        )
        m = label_pattern.search(text)
        if m:
            return _parse_german_date(m.group(1), m.group(2), m.group(3))

        # Fall back to first date in the document
        m = _DATE_PATTERN.search(text)
        if m:
            return _parse_german_date(m.group(1), m.group(2), m.group(3))

        return None

    def _extract_document_number(self, text: str) -> str | None:
        """Extract document/invoice number."""
        for pattern in _DOC_NUMBER_PATTERNS:
            m = pattern.search(text)
            if m:
                num = m.group(1).strip()
                # Sanitize: remove trailing punctuation, limit length
                num = re.sub(r"[,\.\s]+$", "", num)
                if len(num) <= 50:
                    return num
        return None

    def _extract_gross_amount(self, text: str) -> Decimal | None:
        """Extract the gross (total) amount from German invoice text.

        Strategy:
        1. Look for explicitly labeled gross/total amounts.
        2. If not found, take the largest amount in the document as a heuristic.
        """
        # Explicitly labeled
        m = _GROSS_LABELS.search(text)
        if m:
            parsed = _parse_german_amount(m.group(1))
            if parsed is not None:
                return parsed

        # Fallback: largest amount in the document
        all_amounts = _AMOUNT_PATTERN.findall(text)
        if all_amounts:
            parsed_amounts = [
                _parse_german_amount(a) for a in all_amounts
            ]
            valid = [a for a in parsed_amounts if a is not None and a > 0]
            if valid:
                return max(valid)

        return None

    def _extract_vat(self, text: str) -> tuple[Decimal | None, Decimal | None]:
        """Extract VAT rate and amount."""
        vat_rate: Decimal | None = None
        vat_amount: Decimal | None = None

        # VAT rate
        m = _VAT_RATE_PATTERN.search(text)
        if m:
            try:
                rate = Decimal(m.group(1))
                if rate in (Decimal("7"), Decimal("19")):
                    vat_rate = rate
            except InvalidOperation:
                pass

        # VAT amount
        m = _VAT_AMOUNT_PATTERN.search(text)
        if m:
            vat_amount = _parse_german_amount(m.group(1))

        return vat_rate, vat_amount

    def _extract_service_period(self, text: str) -> tuple[str | None, str | None]:
        """Extract service period (Leistungszeitraum).

        Looks for labeled period ranges like:
          'Leistungszeitraum: 01.01.2025 - 31.12.2025'
          'Abrechnungszeitraum vom 01.01.2025 bis 31.12.2025'
        """
        # Look for labeled period
        label_match = _SERVICE_PERIOD_LABELS.search(text)
        if label_match:
            # Search for the range pattern after the label
            remaining = text[label_match.start():]
            range_match = _PERIOD_RANGE.search(remaining)
            if range_match:
                from_date = _parse_german_date(
                    range_match.group(1), range_match.group(2), range_match.group(3)
                )
                to_date = _parse_german_date(
                    range_match.group(4), range_match.group(5), range_match.group(6)
                )
                if from_date and to_date:
                    return from_date, to_date

        # Try without label -- any date range in the text
        range_match = _PERIOD_RANGE.search(text)
        if range_match:
            from_date = _parse_german_date(
                range_match.group(1), range_match.group(2), range_match.group(3)
            )
            to_date = _parse_german_date(
                range_match.group(4), range_match.group(5), range_match.group(6)
            )
            if from_date and to_date:
                return from_date, to_date

        return None, None

    def _extract_vendor(self, text: str) -> str | None:
        """Extract vendor name from the first few lines (letterhead heuristic).

        Simple heuristic: take the first non-empty, non-date, non-numeric line
        that looks like a company name (from the header area).
        """
        lines = text.split("\n")
        for line in lines[:8]:  # Only check first 8 lines (header area)
            line = line.strip()
            if not line or len(line) < 3 or len(line) > 120:
                continue
            # Skip lines that are just dates or amounts
            if _DATE_PATTERN.fullmatch(line):
                continue
            if re.fullmatch(r"[\d\s.,€EUR]+", line):
                continue
            # Skip common non-vendor lines
            if re.match(
                r"^(Seite|Rechnung|Beleg|Datum|Steuer|Tel|Fax|E-?Mail|www\.|http|IBAN|BIC|USt)",
                line,
                re.IGNORECASE,
            ):
                continue
            # This looks like it could be a vendor/company name
            return line

        return None

    def _suggest_category(self, text: str) -> str | None:
        """Suggest a cost category code based on keyword matching.

        Returns the code of the first matching category, checked in priority
        order (CATEGORY_KEYWORDS). Returns None if no match found.
        """
        text_lower = text.lower()
        for code, patterns in _CATEGORY_COMPILED:
            for pattern in patterns:
                if pattern.search(text_lower):
                    return code
        return None
