from __future__ import annotations

from collections.abc import Iterable, Sequence

from decimal import Decimal
from datetime import date
from functools import lru_cache
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.core.iban import validate_iban


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeaFraction(StrictModel):
    kind: Literal["MEA"] = "MEA"
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)

    def as_decimal(self) -> Decimal:
        return Decimal(self.numerator) / Decimal(self.denominator)


class TextValue(StrictModel):
    kind: Literal["TEXT"]
    value: str = Field(min_length=1, max_length=4_000)


class DecimalValue(StrictModel):
    kind: Literal["DECIMAL"]
    value: str = Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$")
    unit: str | None = Field(default=None, max_length=80)


class IntegerValue(StrictModel):
    kind: Literal["INTEGER"]
    value: int


class BooleanValue(StrictModel):
    kind: Literal["BOOLEAN"]
    value: bool


class DateValue(StrictModel):
    kind: Literal["DATE"]
    value: date


class FractionValue(StrictModel):
    kind: Literal["FRACTION"]
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)


class PercentageValue(StrictModel):
    kind: Literal["PERCENTAGE"]
    value: str = Field(pattern=r"^(?:100(?:\.0+)?|[0-9]{1,2}(?:\.[0-9]+)?)$")


NormalizedValue = Annotated[
    TextValue
    | DecimalValue
    | IntegerValue
    | BooleanValue
    | DateValue
    | MeaFraction
    | FractionValue
    | PercentageValue,
    Field(discriminator="kind"),
]


class EvidencePayload(StrictModel):
    document_key: str = Field(min_length=1, max_length=120)
    pdf_page: int = Field(ge=1)
    printed_page: str | None = Field(default=None, max_length=40)
    section: str | None = Field(default=None, max_length=160)
    quote: str = Field(min_length=1, max_length=2_000)
    field_path: str | None = Field(default=None, max_length=160)


FIXED_CORE_FIELDS = MappingProxyType({
    "property": frozenset({
        "name", "street", "zip_code", "city", "management_type", "mea_denominator",
    }),
    "unit": frozenset({
        "number", "location", "usage_type", "area", "heated_area", "building_part", "mea",
    }),
    "legal_reference": frozenset({
        "reference_type", "district", "cadastral_district", "field_number", "parcel", "sheet", "details",
    }),
    "assignment": frozenset({
        "assignment_type", "designation", "location", "description", "unit_subject_key", "details",
    }),
})


class FactPayload(StrictModel):
    subject_key: str = Field(min_length=1, max_length=180)
    field: str = Field(min_length=1, max_length=100)
    raw_value: str = Field(min_length=1, max_length=4_000)
    normalized_value: NormalizedValue
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidencePayload] = Field(min_length=1)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def field_and_value_type_match(self) -> "FactPayload":
        prefix = self.subject_key.split(":", 1)[0]
        if prefix not in FIXED_CORE_FIELDS or self.field not in FIXED_CORE_FIELDS[prefix]:
            raise PydanticCustomError('fact_core_field_unsupported', "Value error, unsupported fixed-core subject or field")
        if self.field == "mea" and not isinstance(self.normalized_value, MeaFraction):
            raise PydanticCustomError('fact_mea_type', "Value error, mea requires a MEA value")
        if self.field == "mea_denominator" and not isinstance(
            self.normalized_value, IntegerValue
        ):
            raise PydanticCustomError('fact_denominator_type', "Value error, mea_denominator requires an INTEGER value")
        if self.field in {"area", "heated_area"} and not isinstance(
            self.normalized_value, DecimalValue
        ):
            raise PydanticCustomError('fact_area_type', "Value error, area fields require a DECIMAL value")
        return self


class RuleScope(StrictModel):
    all_units: bool = False
    unit_subject_keys: list[str] = Field(default_factory=list)
    building_parts: list[str] = Field(default_factory=list)
    participant_groups: list[str] = Field(default_factory=list)
    additional_scope: list[str] = Field(default_factory=list)


AllocationShare = Annotated[
    FractionValue | PercentageValue | DecimalValue | TextValue,
    Field(discriminator="kind"),
]


class AllocationPart(StrictModel):
    label: str = Field(min_length=1, max_length=255)
    basis: str = Field(min_length=1, max_length=255)
    share: AllocationShare
    applies_to: list[str] = Field(default_factory=list)


class AllocationFormula(StrictModel):
    declared_complete: bool
    parts: list[AllocationPart] = Field(min_length=1)


class RuleEffect(StrictModel):
    obligation: str | None = None
    entitlement: str | None = None
    responsibility: list[str] = Field(default_factory=list)
    allocation_formula: AllocationFormula | None = None
    exceptions: list[str] = Field(default_factory=list)
    consequence: str | None = None
    administrative_note: str | None = None


class RulePayload(StrictModel):
    subject_key: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=1, max_length=255)
    topic: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=4_000)
    scope: RuleScope
    effect: RuleEffect
    proposed_status: Literal[
        "EFFECTIVE", "SUPERSEDED", "NEEDS_CLARIFICATION"
    ] = "EFFECTIVE"
    supersedes_subject_keys: list[str] = Field(default_factory=list)
    conflicts_with_subject_keys: list[str] = Field(default_factory=list)
    evidence: list[EvidencePayload] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    clarification_needed: bool


class AdditionalFactPayload(StrictModel):
    subject_key: str = Field(min_length=1, max_length=180)
    label: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=4_000)
    management_relevance: str = Field(min_length=1, max_length=1_000)
    evidence: list[EvidencePayload] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    clarification_needed: bool


# What the inventory schema tells the model about the two keys (issue #876); the instruction says the same in prose.
_ENTRY_KEY_DESCRIPTION = (
    "The document_key of exactly one supplied file, copied from the page labels. "
    "One entry per file: a deed inside a file has no entry of its own."
)
_RELATION_TARGET_DESCRIPTION = (
    "The document_key of a supplied document, copied exactly, when the relation points at one. "
    "A document that was not supplied is named by the words of the text, never by an invented key."
)


class DocumentRelationPayload(StrictModel):
    relation_type: Literal["AMENDS", "SUPPLEMENTS", "REFERENCES"]
    target_document_key: str = Field(min_length=1, max_length=120, description=_RELATION_TARGET_DESCRIPTION)
    evidence: list[EvidencePayload] = Field(min_length=1)


class DocumentInventoryItem(StrictModel):
    document_key: str = Field(min_length=1, max_length=120, description=_ENTRY_KEY_DESCRIPTION)
    document_type: Literal["DECLARATION", "AMENDMENT", "ANNEX", "OTHER"]
    title: str = Field(min_length=1, max_length=255)
    document_date: str | None = None
    deed_reference: str | None = Field(default=None, max_length=255)
    relations: list[DocumentRelationPayload] = Field(default_factory=list)


class DocumentInventoryOutput(StrictModel):
    documents: list[DocumentInventoryItem] = Field(min_length=1)


def inventory_output_type(document_keys: Iterable[str]) -> type[DocumentInventoryOutput]:
    """The answer type of one inventory request: an entry's ``document_key`` is one of the keys of that request.

    The request schema and the local validation of the answer both come from this type, so a model that cannot
    be held to the schema by its server is still held to it here. The keys are request data: the fingerprints of
    the pass hash the static contract (``static_output_type``), never this type. ``target_document_key`` stays
    free text. Without keys there is nothing to restrict.
    """
    keys = tuple(sorted(set(document_keys)))
    return _restricted_inventory_output(keys) if keys else DocumentInventoryOutput


@lru_cache(maxsize=32)
def _restricted_inventory_output(keys: tuple[str, ...]) -> type[DocumentInventoryOutput]:
    # The classes keep the names of the base types, so the wire schema differs from the base one by the allowed keys.
    entry = create_model(
        "DocumentInventoryItem",
        __base__=DocumentInventoryItem,
        document_key=(Literal[keys], Field(description=_ENTRY_KEY_DESCRIPTION)),  # type: ignore[valid-type]
    )
    return create_model(
        "DocumentInventoryOutput",
        __base__=DocumentInventoryOutput,
        documents=(list[entry], Field(min_length=1)),  # type: ignore[valid-type]
    )


def static_output_type(output_type: type[StrictModel]) -> type[StrictModel]:
    """The type whose schema the fingerprints of a pass hash: for a request-restricted inventory type, the base one."""
    return DocumentInventoryOutput if issubclass(output_type, DocumentInventoryOutput) else output_type


class CoreExtractionOutput(StrictModel):
    facts: list[FactPayload]
    additional_facts: list[AdditionalFactPayload] = Field(default_factory=list)


class RuleExtractionOutput(StrictModel):
    rules: list[RulePayload]
    additional_facts: list[AdditionalFactPayload] = Field(default_factory=list)


class ReconciliationOutput(StrictModel):
    facts: list[FactPayload]
    rules: list[RulePayload]
    additional_facts: list[AdditionalFactPayload] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    mea_set_status: Literal["COMPLETE", "PARTIAL", "UNKNOWN"] = "UNKNOWN"


class TargetedRecheckOutput(StrictModel):
    facts: list[FactPayload] = Field(default_factory=list)
    rules: list[RulePayload] = Field(default_factory=list)
    additional_facts: list[AdditionalFactPayload] = Field(default_factory=list)
    resolved_issue_codes: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)


# ---- document reading stage (issue #499, ADR 0017) ---------------------------------------------

READING_SCHEMA_VERSION = "reading-1"
# Where a word, number or table cell cannot be read, the transcript says so with this literal instead of a guess.
UNREADABLE_MARKER = "[unreadable]"


class ReadingBlock(StrictModel):
    """One block of a page, in reading order: copied text, a table as rows of cells, or an unreadable region."""

    kind: Literal["TEXT", "TABLE", "UNREADABLE"]
    text: str | None = Field(default=None, max_length=50_000)
    rows: list[list[str]] | None = None

    @model_validator(mode="after")
    def content_matches_kind(self) -> "ReadingBlock":
        if self.kind == "TEXT" and (not self.text or self.rows is not None):
            raise ValueError("a TEXT block carries text and no rows")
        if self.kind == "TABLE" and (not self.rows or self.text is not None):
            raise ValueError("a TABLE block carries rows and no text")
        if self.kind == "UNREADABLE" and (self.text is not None or self.rows is not None):
            raise ValueError("an UNREADABLE block carries neither text nor rows")
        return self


class PageReadingOutput(StrictModel):
    """What the reading model returns for one page; it names no page, the caller owns the identity."""

    blocks: list[ReadingBlock]


class PageTranscript(StrictModel):
    """The typed transcript of one rendered page with its source identity and original page number."""

    document_key: str = Field(min_length=1, max_length=120)
    pdf_page: int = Field(ge=1)
    blocks: list[ReadingBlock]


def transcript_text(blocks: "Sequence[ReadingBlock]") -> str:
    """The page text the extraction passes read: a transcript serialized deterministically, block by block.

    A text block is its text. A table is one line per row with the cells joined by `` | `` between the lines
    ``[table]`` and ``[/table]``. An unreadable block is the marker line; the markers inside text stay as the
    reading model wrote them, so the extraction model sees every gap.
    """
    lines: list[str] = []
    for block in blocks:
        if block.kind == "TEXT":
            lines.append(block.text or "")
        elif block.kind == "TABLE":
            lines.append("[table]")
            lines.extend(" | ".join(row) for row in block.rows or [])
            lines.append("[/table]")
        else:
            lines.append(UNREADABLE_MARKER)
    return "\n".join(lines)


# ---- invoice extraction stage (issue #500, ADR 0017) -------------------------------------------

INVOICE_SCHEMA_VERSION = "invoice-2"
# The value formats the review boundary of an invoice proposal accepts (``core/extraction_evidence.py``): a decimal
# number with a dot and at most 16 integer and 6 fractional digits, and an ISO date.
_INVOICE_AMOUNT = r"^-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,6})?$"
_INVOICE_DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


# Where the model found a stated value (issue #501): the number of the page it was given, and a short passage copied from
# that page's text. The worker checks both against the pages of the one document before it records a location; the
# invoice path ignores them. The source is advisory and never costs the paid answer: a page that is not a whole number
# from 1 to 200, a passage that is not text of 1 to 300 characters, or one half of the pair alone is no source, and
# the field is read as if the answer had stated none (the value fields keep their strict validation). The limits stay
# in the fields below, so that the wire schema names them for the transports that carry them; the repair of a source
# happens before they apply. A value that is not stated has neither, and an answer that leaves them out states no source,
# which records the field as unlocated and never fails the proposal. The strict wire schema lists every property as
# required whatever the default. (No docstrings below: they would become descriptions of the wire schema.)
_SOURCE_PAGE_MAX = 200
_SOURCE_QUOTE_MAX = 300
_SOURCE_PAGE = Field(default=None, ge=1, le=_SOURCE_PAGE_MAX)
_SOURCE_QUOTE = Field(default=None, min_length=1, max_length=_SOURCE_QUOTE_MAX)


class _Sourced(StrictModel):
    @model_validator(mode="before")
    @classmethod
    def a_source_that_cannot_be_used_is_no_source(cls, data: object) -> object:
        if not isinstance(data, dict) or ("source_page" not in data and "source_quote" not in data):
            return data
        page, quote = data.get("source_page"), data.get("source_quote")
        if (type(page) is int and 1 <= page <= _SOURCE_PAGE_MAX) and (isinstance(quote, str) and 1 <= len(quote) <= _SOURCE_QUOTE_MAX):
            return data
        return {**data, "source_page": None, "source_quote": None}


class InvoiceTextField(_Sourced):
    value: str | None = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0, le=1)
    source_page: int | None = _SOURCE_PAGE
    source_quote: str | None = _SOURCE_QUOTE

    @field_validator("value")
    @classmethod
    def no_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("a text value holds no NUL character")
        return value


class InvoiceDateField(_Sourced):
    value: str | None = Field(pattern=_INVOICE_DATE)
    confidence: float = Field(ge=0, le=1)
    source_page: int | None = _SOURCE_PAGE
    source_quote: str | None = _SOURCE_QUOTE

    @field_validator("value")
    @classmethod
    def a_real_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value


class InvoiceAmountField(_Sourced):
    value: str | None = Field(pattern=_INVOICE_AMOUNT)
    confidence: float = Field(ge=0, le=1)
    source_page: int | None = _SOURCE_PAGE
    source_quote: str | None = _SOURCE_QUOTE


# The vendor's IBAN as the invoice prints it. A wrong IBAN is worse than none: a value that is not a valid IBAN
# (country, length, check digits) is not stated, which leaves the rest of the answer as it is; a valid one is kept
# without spaces and in upper case. The wire schema allows any text, so that a misread value costs no repair request.
# Nothing here logs or raises the value. (No docstring: it would become a description of the wire schema.)
class InvoiceIbanField(_Sourced):
    value: str | None
    confidence: float = Field(ge=0, le=1)
    source_page: int | None = _SOURCE_PAGE
    source_quote: str | None = _SOURCE_QUOTE

    @field_validator("value")
    @classmethod
    def a_valid_iban_or_not_stated(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_iban(value)
        except ValueError:
            return None


class InvoiceExtractionOutput(StrictModel):
    # The ten fields of the invoice proposal, each with its value (null when the invoice does not state it) and the
    # model's certainty. The wire schema carries no description: every request pays for it twice.
    vendor_name: InvoiceTextField
    invoice_date: InvoiceDateField
    document_number: InvoiceTextField
    gross_amount: InvoiceAmountField
    vat_amount: InvoiceAmountField
    vat_rate: InvoiceAmountField
    service_period_from: InvoiceDateField
    service_period_to: InvoiceDateField
    suggested_category_code: InvoiceTextField
    vendor_iban: InvoiceIbanField
