from __future__ import annotations

from decimal import Decimal
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        allowed = {
            "property": {
                "name",
                "street",
                "zip_code",
                "city",
                "management_type",
                "mea_denominator",
            },
            "unit": {
                "number",
                "location",
                "usage_type",
                "area",
                "heated_area",
                "building_part",
                "mea",
            },
            "legal_reference": {
                "reference_type",
                "district",
                "cadastral_district",
                "field_number",
                "parcel",
                "sheet",
                "details",
            },
            "assignment": {
                "assignment_type",
                "designation",
                "location",
                "description",
                "unit_subject_key",
                "details",
            },
        }
        if prefix not in allowed or self.field not in allowed[prefix]:
            raise ValueError("unsupported fixed-core subject or field")
        if self.field == "mea" and not isinstance(self.normalized_value, MeaFraction):
            raise ValueError("mea requires a MEA value")
        if self.field == "mea_denominator" and not isinstance(
            self.normalized_value, IntegerValue
        ):
            raise ValueError("mea_denominator requires an INTEGER value")
        if self.field in {"area", "heated_area"} and not isinstance(
            self.normalized_value, DecimalValue
        ):
            raise ValueError("area fields require a DECIMAL value")
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


class DocumentRelationPayload(StrictModel):
    relation_type: Literal["AMENDS", "SUPPLEMENTS", "REFERENCES"]
    target_document_key: str = Field(min_length=1, max_length=120)
    evidence: list[EvidencePayload] = Field(min_length=1)


class DocumentInventoryItem(StrictModel):
    document_key: str = Field(min_length=1, max_length=120)
    document_type: Literal["DECLARATION", "AMENDMENT", "ANNEX", "OTHER"]
    title: str = Field(min_length=1, max_length=255)
    document_date: str | None = None
    deed_reference: str | None = Field(default=None, max_length=255)
    relations: list[DocumentRelationPayload] = Field(default_factory=list)


class DocumentInventoryOutput(StrictModel):
    documents: list[DocumentInventoryItem] = Field(min_length=1)


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
