"""Strict transport models for human review of declaration draft candidates."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.property_data_domain import DataGrantCreate


Decision = Literal["ACCEPTED", "CORRECTED", "REJECTED", "NEEDS_CLARIFICATION"]


class CandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_revision: int = Field(ge=0)
    decision: Decision
    corrected_value: Any | None = None
    comment: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "CandidateReviewRequest":
        if self.decision == "CORRECTED" and self.corrected_value is None:
            raise ValueError("CORRECTED requires corrected_value")
        if self.decision != "CORRECTED" and self.corrected_value is not None:
            raise ValueError("corrected_value is only valid for CORRECTED")
        if self.decision == "NEEDS_CLARIFICATION" and not (self.comment or "").strip():
            raise ValueError("NEEDS_CLARIFICATION requires comment")
        return self


class BulkCandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_revision: int = Field(ge=0)
    candidate_ids: list[str] = Field(min_length=1, max_length=250)
    decision: Literal["ACCEPTED", "REJECTED"]
    comment: str | None = Field(default=None, max_length=2_000)


class EvidenceOut(BaseModel):
    id: str
    document_id: str
    field_path: str | None
    pdf_page: int
    printed_page: str | None
    section_ref: str | None
    quote: str


class IssueOut(BaseModel):
    id: str
    code: str
    severity: str
    message: str
    resolution_status: str
    candidate_id: str | None


class CandidateOut(BaseModel):
    id: str
    stable_key: str
    candidate_type: str
    subject_key: str
    field_key: str | None
    operation: str
    previous_value_json: dict | None
    proposed_value_json: dict | None
    corrected_value_json: dict | None
    confidence: Decimal
    risk_class: str
    requires_individual_review: bool
    review_status: str
    review_comment: str | None
    evidence: list[EvidenceOut] = Field(default_factory=list)
    issues: list[IssueOut] = Field(default_factory=list)


class ReviewImportOut(BaseModel):
    draft_revision: int
    candidate: CandidateOut | None = None
    candidates: list[CandidateOut] = Field(default_factory=list)


class ApprovalPreviewOut(BaseModel):
    import_id: str
    draft_revision: int
    can_approve: bool
    issues: list[IssueOut]
    pending_candidate_ids: list[str]
    import_mode: Literal["NEW_PROPERTY", "EXISTING_PROPERTY"]


class NewPropertyAudience(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)
    grants: list[DataGrantCreate] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_users(self):
        if len({grant.user_id for grant in self.grants}) != len(self.grants):
            raise ValueError("Each audience member needs exactly one explicit grant")
        return self


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=16, max_length=128)
    new_property_audience: NewPropertyAudience | None = None
