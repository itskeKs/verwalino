"""Review policy boundary for immutable declaration extraction proposals."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.declarations.policy import CandidateContext, CandidateDecision, classify_candidate
from app.declarations.review_schemas import (
    ApprovalPreviewOut,
    BulkCandidateReviewRequest,
    CandidateOut,
    CandidateReviewRequest,
    EvidenceOut,
    IssueOut,
    ReviewImportOut,
)
from app.declarations.schemas import (
    DocumentRelationPayload,
    EvidencePayload,
    FactPayload,
    NormalizedValue,
    RulePayload,
)
from app.declarations.validation import validate_allocation_formulas, validate_mea_totals
from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.repositories.declaration_review_repo import DeclarationReviewRepository

_REJECTION_COMMENT_TYPES = {
    "UPDATE", "RULE", "DOCUMENT_RELATION", "MEA_RESCALE", "WARNING", "BLOCKER"
}


class DeclarationReviewService:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._organization_id = organization_id
        self._user_id = user_id
        self._reviews = DeclarationReviewRepository(session, organization_id)

    async def list_candidates(self, import_id: uuid.UUID) -> list[CandidateOut]:
        item = await self._reviews.lock_import(import_id)
        if item is None:
            raise _not_found()
        candidates = await self._reviews.list_candidates(import_id)
        return await self._out_candidates(import_id, candidates)

    async def review_one(
        self, import_id: uuid.UUID, candidate_id: uuid.UUID, request: CandidateReviewRequest
    ) -> ReviewImportOut:
        item = await self._lock_reviewable(import_id, request.draft_revision)
        candidates = await self._reviews.lock_candidates(import_id, [candidate_id])
        if not candidates:
            raise _not_found()
        candidate = candidates[0]
        normalized_comment = _normalize_comment(request.comment)
        corrected = self._canonical_correction(candidate, request.corrected_value) if request.decision == "CORRECTED" else None
        evidence, issues = await self._candidate_context(import_id, candidates)
        self._validate_decision(candidate, request.decision, normalized_comment, evidence, issues)
        if self._is_exact_replay(item.draft_revision, request.draft_revision, candidate, request.decision, corrected, normalized_comment):
            return ReviewImportOut(draft_revision=item.draft_revision, candidate=(await self._out_candidates(import_id, [candidate]))[0])
        prior_resolutions = await self._apply_review(import_id, request.draft_revision, [candidate.id],
                                         request.decision, corrected, normalized_comment, bulk=False)
        await self._session.refresh(item)
        await self._session.refresh(candidate)
        await self._revalidate_managed_issues(import_id, prior_resolutions)
        await self._session.flush()
        return ReviewImportOut(draft_revision=item.draft_revision, candidate=(await self._out_candidates(import_id, [candidate]))[0])

    async def review_bulk(self, import_id: uuid.UUID, request: BulkCandidateReviewRequest) -> ReviewImportOut:
        try:
            ids = [uuid.UUID(value) for value in request.candidate_ids]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "INVALID_CANDIDATE_ID"}) from exc
        if len(ids) != len(set(ids)):
            raise HTTPException(status_code=422, detail={"code": "DUPLICATE_CANDIDATE_ID"})
        item = await self._lock_reviewable(import_id, request.draft_revision)
        candidates = await self._reviews.lock_candidates(import_id, ids)
        if not candidates:
            raise _not_found()
        comment = _normalize_comment(request.comment)
        evidence, issues = await self._candidate_context(import_id, candidates)
        policies = {candidate.id: _policy(candidate, evidence.get(candidate.id, []), issues.get(candidate.id, [])) for candidate in candidates}
        if any(policy.requires_individual_review for policy in policies.values()):
            raise HTTPException(status_code=409, detail={"code": "INDIVIDUAL_REVIEW_REQUIRED"})
        for candidate in candidates:
            self._validate_decision(candidate, request.decision, comment, evidence, issues)
        if all(self._is_exact_replay(item.draft_revision, request.draft_revision, candidate, request.decision, None, comment) for candidate in candidates):
            return ReviewImportOut(draft_revision=item.draft_revision, candidates=await self._out_candidates(import_id, candidates))
        prior_resolutions = await self._apply_review(import_id, request.draft_revision, [candidate.id for candidate in candidates],
                                         request.decision, None, comment, bulk=True)
        await self._session.refresh(item)
        for candidate in candidates:
            await self._session.refresh(candidate)
        await self._revalidate_managed_issues(import_id, prior_resolutions)
        await self._session.flush()
        return ReviewImportOut(draft_revision=item.draft_revision, candidates=await self._out_candidates(import_id, candidates))

    async def _apply_review(self, *args, **kwargs):
        try:
            return await self._reviews.apply_review(*args, **kwargs)
        except DBAPIError as exc:
            # Authority/expiry may change after application validation. Preserve
            # actionable HTTP semantics without returning SQL or source values.
            state = getattr(exc.orig, 'sqlstate', None)
            if state == '42501':
                raise HTTPException(status_code=409, detail={'code': 'REVIEW_AUTHORITY_CHANGED'}) from exc
            if state == '40001':
                raise _stale() from exc
            if state == '22023':
                for code in ('IMPORT_NOT_REVIEWABLE', 'REVIEW_CANDIDATE_NOT_FOUND', 'INVALID_REVIEW_COMMAND',
                             'EVIDENCE_REQUIRED', 'COMMENT_REQUIRED', 'INDIVIDUAL_REVIEW_REQUIRED'):
                    if code in str(exc.orig):
                        raise HTTPException(status_code=409 if code in {'IMPORT_NOT_REVIEWABLE', 'INDIVIDUAL_REVIEW_REQUIRED'} else 422,
                                            detail={'code': code}) from exc
            raise

    async def preview(self, import_id: uuid.UUID) -> ApprovalPreviewOut:
        item = await self._reviews.lock_import(import_id)
        if item is None:
            raise _not_found()
        if item.status != "NEEDS_REVIEW":
            raise HTTPException(status_code=409, detail={"code": "IMPORT_NOT_REVIEWABLE"})
        candidates = await self._reviews.list_candidates(import_id)
        evidence, issues = await self._candidate_context(import_id, candidates)
        open_issues = [issue for issue in await self._reviews.issues_for_import(import_id) if issue.resolution_status == "OPEN"]
        blockers = [issue for issue in open_issues if issue.severity == "BLOCKER"]
        pending = [candidate for candidate in candidates if candidate.review_status not in {"ACCEPTED", "CORRECTED", "REJECTED"}]
        missing_evidence = [
            candidate for candidate in candidates
            if candidate.review_status in {"ACCEPTED", "CORRECTED"}
            and _policy(candidate, evidence.get(candidate.id, []), issues.get(candidate.id, [])).risk_class == "CRITICAL"
            and not evidence.get(candidate.id)
        ]
        rule_warnings = [
            _rule_needs_clarification_issue(candidate)
            for candidate in candidates
            if _rule_needs_clarification(candidate)
        ]
        return ApprovalPreviewOut(
            import_id=str(item.id), draft_revision=item.draft_revision,
            import_mode=item.mode,
            can_approve=not blockers and not pending and not missing_evidence and not rule_warnings,
            issues=sorted(
                [
                    *(_issue_out(issue) for issue in open_issues),
                    *rule_warnings,
                ],
                key=lambda value: (value.severity, value.code, value.candidate_id or "", value.id),
            ),
            pending_candidate_ids=[str(candidate.id) for candidate in pending],
        )

    async def approval_not_implemented(self, import_id: uuid.UUID) -> None:
        """Tenant-scoped authorization boundary; approval writes start in Task 4."""
        if await self._reviews.lock_import(import_id) is None:
            raise _not_found()
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_NOT_IMPLEMENTED"})

    async def _lock_reviewable(self, import_id: uuid.UUID, revision: int):
        item = await self._reviews.lock_import(import_id, capability='prepare')
        if item is None:
            raise _not_found()
        if item.status != "NEEDS_REVIEW":
            raise HTTPException(status_code=409, detail={"code": "IMPORT_NOT_REVIEWABLE"})
        if revision != item.draft_revision:
            raise _stale()
        return item

    async def _candidate_context(
        self, import_id: uuid.UUID, candidates: Sequence[DeclarationCandidate]
    ) -> tuple[dict[uuid.UUID, list[DeclarationCandidateEvidence]], dict[uuid.UUID, list[DeclarationCandidateIssue]]]:
        evidence = await self._reviews.evidence_for([candidate.id for candidate in candidates])
        values: dict[uuid.UUID, list[DeclarationCandidateIssue]] = defaultdict(list)
        all_issues = await self._reviews.issues_for_import(import_id)
        for issue in all_issues:
            if issue.candidate_id is not None:
                values[issue.candidate_id].append(issue)
        return evidence, values

    async def _out_candidates(self, import_id: uuid.UUID, candidates: Sequence[DeclarationCandidate]) -> list[CandidateOut]:
        evidence, issues = await self._candidate_context(import_id, candidates)
        return [_candidate_out(candidate, evidence.get(candidate.id, []), issues.get(candidate.id, [])) for candidate in candidates]

    def _canonical_correction(self, candidate: DeclarationCandidate, value: Any) -> dict:
        proposed = candidate.proposed_value_json or {}
        if candidate.candidate_type in {"PROPERTY_FIELD", "UNIT_FIELD", "MEA_RESCALE"}:
            raw = value
            if not isinstance(raw, dict):
                kind = proposed.get("kind") if isinstance(proposed, dict) else None
                kind = kind or _expected_kind(candidate.field_key)
                if kind == "TEXT":
                    raw = {"kind": "TEXT", "value": raw.strip() if isinstance(raw, str) else raw}
                elif kind == "INTEGER":
                    raw = {"kind": "INTEGER", "value": raw}
                elif kind == "DECIMAL":
                    raw = {"kind": "DECIMAL", "value": raw, "unit": proposed.get("unit")}
                else:
                    raise HTTPException(status_code=422, detail={"code": "INVALID_CORRECTION_VALUE"})
            try:
                parsed = TypeAdapter(NormalizedValue).validate_python(raw)
                # The discriminator alone accepts valid values for an unrelated
                # field. FactPayload is the canonical field/type authority.
                parsed = FactPayload(
                    subject_key=_fact_subject(candidate), field=candidate.field_key or "",
                    raw_value="review", normalized_value=parsed, confidence=1,
                    evidence=[EvidencePayload(document_key="review", pdf_page=1, quote="review")],
                ).normalized_value
            except (ValidationError, ValueError) as exc:
                raise HTTPException(status_code=422, detail={"code": "INVALID_CORRECTION_VALUE"}) from exc
            normalized = parsed.model_dump(mode="json")
            expected_kind = _expected_kind(candidate.field_key)
            if expected_kind is not None and normalized.get("kind") != expected_kind:
                raise HTTPException(status_code=422, detail={"code": "INVALID_CORRECTION_VALUE"})
            return normalized
        if not isinstance(value, dict):
            raise HTTPException(status_code=422, detail={"code": "INVALID_CORRECTION_VALUE"})
        evidence = [EvidencePayload(document_key="review", pdf_page=1, quote="review")]
        try:
            if candidate.candidate_type in {"UNIT_CREATE", "LEGAL_REFERENCE", "ASSIGNMENT"}:
                if not value:
                    raise ValueError("empty composite")
                return {
                    field: FactPayload(subject_key=candidate.subject_key, field=field, raw_value="review", normalized_value=raw, confidence=1, evidence=evidence).normalized_value.model_dump(mode="json")
                    for field, raw in sorted(value.items())
                }
            if candidate.candidate_type == "RULE":
                payload = dict(value)
                payload["subject_key"] = candidate.subject_key
                payload["evidence"] = evidence
                payload["confidence"] = 1
                payload.setdefault("clarification_needed", False)
                return RulePayload.model_validate(payload).model_dump(mode="json", exclude={"evidence", "confidence", "subject_key"})
            if candidate.candidate_type == "DOCUMENT_RELATION":
                return DocumentRelationPayload(evidence=evidence, **value).model_dump(mode="json", exclude={"evidence"})
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail={"code": "INVALID_CORRECTION_VALUE"}) from exc
        raise HTTPException(status_code=422, detail={"code": "INVALID_CORRECTION_VALUE"})

    def _validate_decision(self, candidate, decision, comment, evidence, issues) -> None:
        policy = _policy(candidate, evidence.get(candidate.id, []), issues.get(candidate.id, []))
        if decision in {"ACCEPTED", "CORRECTED"} and policy.risk_class == "CRITICAL" and not evidence.get(candidate.id):
            raise HTTPException(status_code=422, detail={"code": "EVIDENCE_REQUIRED"})
        if decision == "NEEDS_CLARIFICATION" and not comment:
            raise HTTPException(status_code=422, detail={"code": "COMMENT_REQUIRED"})
        has_flagged_issue = any(issue.severity in {"WARNING", "BLOCKER"} and issue.resolution_status == "OPEN" for issue in issues.get(candidate.id, []))
        if decision == "REJECTED" and (candidate.operation == "UPDATE" or candidate.candidate_type in _REJECTION_COMMENT_TYPES or has_flagged_issue) and not comment:
            raise HTTPException(status_code=422, detail={"code": "COMMENT_REQUIRED"})

    async def _revalidate_managed_issues(self, import_id: uuid.UUID, prior_resolutions: dict) -> None:
        """Reconcile only invariants provable from the complete effective graph.

        The import row is already locked by the caller.  Every candidate remains
        part of the graph regardless of its decision: a rejection or comment is
        review metadata, not evidence that a structural blocker disappeared.
        A corrected value replaces a proposal only while its decision remains
        ``CORRECTED``.  This makes reopening deterministic when a correction is
        replaced or reverted.
        """
        candidates = await self._reviews.list_candidates(import_id)
        issues = await self._reviews.issues_for_import(import_id, for_update=True)
        effective = _EffectiveGraph.from_candidates(candidates)
        now = datetime.now(timezone.utc)
        self._reconcile_allocation_issues(import_id, issues, effective, now)
        for issue in issues:
            if issue.code not in _REVALIDATED_ISSUE_CODES:
                continue
            if effective.proves(issue):
                if issue.resolution_status == "OPEN":
                    issue.resolution_status = "RESOLVED"
                    issue.resolved_by = self._user_id
                    issue.resolved_at = now
            elif issue.resolution_status == "RESOLVED":
                issue.resolution_status = "OPEN"
                issue.resolved_by = None
                issue.resolved_at = None
        # Preserve historical resolution timestamps only after the complete new
        # graph independently proves the same managed issue resolved again.
        for issue in issues:
            if (issue.code in _REVALIDATED_ISSUE_CODES | _ALLOCATION_ISSUE_CODES
                    and issue.resolution_status == 'RESOLVED' and issue.id in prior_resolutions):
                issue.resolved_by, issue.resolved_at = prior_resolutions[issue.id]

    def _reconcile_allocation_issues(
        self,
        import_id: uuid.UUID,
        issues: Sequence[DeclarationCandidateIssue],
        effective: "_EffectiveGraph",
        now: datetime,
    ) -> None:
        """Synchronize allocation issue rows with the complete current validator result."""
        current_by_candidate = effective.allocation_issues()
        existing: dict[tuple[uuid.UUID, str], list[DeclarationCandidateIssue]] = defaultdict(list)
        for issue in issues:
            if issue.code in _ALLOCATION_ISSUE_CODES and issue.candidate_id is not None:
                existing[(issue.candidate_id, issue.code)].append(issue)

        for (candidate_id, code), rows in existing.items():
            current = current_by_candidate.get(candidate_id)
            if current is None:
                # A malformed historical rule cannot prove that a blocker vanished.
                continue
            still_present = code in current
            for issue in rows:
                if still_present and issue.resolution_status == "RESOLVED":
                    issue.resolution_status = "OPEN"
                    issue.resolved_by = None
                    issue.resolved_at = None
                elif not still_present and issue.resolution_status == "OPEN":
                    issue.resolution_status = "RESOLVED"
                    issue.resolved_by = self._user_id
                    issue.resolved_at = now

        for candidate_id, current in current_by_candidate.items():
            if current is None:
                continue
            for code, result in current.items():
                if (candidate_id, code) in existing:
                    continue
                self._session.add(
                    DeclarationCandidateIssue(
                        organization_id=self._organization_id,
                        import_id=import_id,
                        candidate_id=candidate_id,
                        code=code,
                        severity=result.severity,
                        message=result.message,
                        resolution_status="OPEN",
                    )
                )

    def _is_exact_replay(self, current_revision, request_revision, candidate, decision, corrected, comment) -> bool:
        return (
            request_revision == current_revision
            and candidate.review_status == decision
            and candidate.corrected_value_json == corrected
            and _normalize_comment(candidate.review_comment) == comment
        )

def _policy(candidate: DeclarationCandidate, evidence, issues) -> CandidateDecision:
    return classify_candidate(CandidateContext(
        candidate.candidate_type, candidate.operation, float(candidate.confidence),
        tuple(issue.severity for issue in issues if issue.resolution_status == "OPEN"),
        _has_conflict(candidate.proposed_value_json), candidate.field_key, bool(evidence),
    ))


def _has_conflict(value: object) -> bool:
    if isinstance(value, dict):
        return (isinstance(value.get("alternatives"), list) and len(value["alternatives"]) > 1) or any(_has_conflict(item) for item in value.values())
    return isinstance(value, list) and any(_has_conflict(item) for item in value)


def _normalize_comment(value: str | None) -> str | None:
    normalized = value.strip() if isinstance(value, str) else None
    return normalized or None


def _expected_kind(field_key: str | None) -> str | None:
    if field_key == "mea":
        return "MEA"
    if field_key == "mea_denominator":
        return "INTEGER"
    if field_key in {"area", "heated_area"}:
        return "DECIMAL"
    if field_key in {
        "name", "street", "zip_code", "city", "management_type", "number",
        "location", "usage_type", "type", "building_part",
    }:
        return "TEXT"
    return None


def _fact_subject(candidate: DeclarationCandidate) -> str:
    """Reject schema-incompatible field candidates rather than inventing a type."""
    if candidate.subject_key.startswith(("property:", "unit:")):
        return candidate.subject_key
    raise ValueError("field correction requires a fixed-core subject")


_REVALIDATED_ISSUE_CODES = frozenset(
    {
        "CONFLICTING_ALTERNATIVES",
        "CONFLICTING_FACT_ALTERNATIVES",
        "MEA_DECLARED_DENOMINATOR_INVALID",
        "MIXED_MEA_DENOMINATOR",
        "MEA_TOTAL_INVALID",
        "DUPLICATE_UNIT_NUMBER",
        "MEA_VALUE_TYPE_INVALID",
        "DUPLICATE_RULE_SUBJECT",
    }
)

_ALLOCATION_ISSUE_CODES = frozenset(
    {"ALLOCATION_TOTAL_INVALID", "ALLOCATION_SHARE_TYPE_INVALID"}
)


@dataclass(frozen=True, slots=True)
class _EffectiveFact:
    candidate_id: uuid.UUID
    subject_key: str
    field: str
    value: object


@dataclass(frozen=True, slots=True)
class _EffectiveGraph:
    """The deterministic review graph, except for explicit review-choice blockers."""

    candidates_by_id: dict[uuid.UUID, DeclarationCandidate]
    facts: tuple[_EffectiveFact, ...]
    rules: tuple[tuple[DeclarationCandidate, object], ...]

    @classmethod
    def from_candidates(
        cls, candidates: Sequence[DeclarationCandidate]
    ) -> "_EffectiveGraph":
        facts: list[_EffectiveFact] = []
        rules: list[tuple[DeclarationCandidate, object]] = []
        by_id = {candidate.id: candidate for candidate in candidates}
        for candidate in candidates:
            value = _effective_value(candidate)
            if candidate.candidate_type in {"PROPERTY_FIELD", "UNIT_FIELD", "MEA_RESCALE"}:
                if candidate.field_key is not None:
                    facts.append(_EffectiveFact(candidate.id, candidate.subject_key, candidate.field_key, value))
            elif candidate.candidate_type in {"UNIT_CREATE", "LEGAL_REFERENCE", "ASSIGNMENT"}:
                if isinstance(value, dict):
                    facts.extend(
                        _EffectiveFact(candidate.id, candidate.subject_key, field, field_value)
                        for field, field_value in value.items()
                        if isinstance(field, str)
                    )
            elif candidate.candidate_type == "RULE":
                rules.append((candidate, value))
        return cls(by_id, tuple(facts), tuple(rules))

    def proves(self, issue: DeclarationCandidateIssue) -> bool:
        if issue.code == "CONFLICTING_ALTERNATIVES":
            candidate = self._issue_candidate(issue)
            return candidate is not None and not _has_conflict(_effective_value(candidate))
        if issue.code == "CONFLICTING_FACT_ALTERNATIVES":
            candidate = self._issue_candidate(issue)
            if candidate is None:
                return False
            subject_facts = [
                fact for fact in self.facts if fact.subject_key == candidate.subject_key
            ]
            if not subject_facts:
                return False
            return all(
                not self._fact_group_has_conflict(fact.subject_key, fact.field)
                for fact in subject_facts
            )
        if issue.code == "MEA_VALUE_TYPE_INVALID":
            candidate = self._issue_candidate(issue)
            if candidate is None:
                return False
            values = [
                fact.value for fact in self.facts
                if fact.subject_key == candidate.subject_key and fact.field == "mea"
            ]
            return bool(values) and all(_mea_value(value) is not None for value in values)
        if issue.code in {
            "MEA_DECLARED_DENOMINATOR_INVALID",
            "MIXED_MEA_DENOMINATOR",
            "MEA_TOTAL_INVALID",
        }:
            return self._mea_validator_has_no_blocker(issue)
        if issue.code == "DUPLICATE_UNIT_NUMBER":
            values = self._unit_numbers()
            return len(values) == len(set(values))
        if issue.code == "DUPLICATE_RULE_SUBJECT":
            return self._duplicate_rule_subject_is_resolved(issue)
        return False

    def allocation_issues(self) -> dict[uuid.UUID, dict[str, object] | None]:
        """Return every current deterministic allocation blocker per rule.

        ``None`` means the historical payload is malformed and therefore cannot
        justify resolving an existing blocker.
        """
        values: dict[uuid.UUID, dict[str, object] | None] = {}
        for candidate, value in self.rules:
            rule = _rule_from_effective_value(candidate, value)
            if rule is None:
                values[candidate.id] = None
                continue
            values[candidate.id] = {
                result.code: result
                for result in validate_allocation_formulas([rule])
                if result.severity == "BLOCKER"
                and result.code in _ALLOCATION_ISSUE_CODES
            }
        return values

    def _issue_candidate(
        self, issue: DeclarationCandidateIssue
    ) -> DeclarationCandidate | None:
        return self.candidates_by_id.get(issue.candidate_id) if issue.candidate_id else None

    def _fact_group_has_conflict(self, subject_key: str, field: str) -> bool:
        values = [
            fact.value for fact in self.facts
            if fact.subject_key == subject_key and fact.field == field
        ]
        if not values:
            return True
        if any(_has_conflict(value) or _canonical_fact_value(subject_key, field, value) is None for value in values):
            return True
        canonical = {
            _canonical_fact_value(subject_key, field, value)
            for value in values
        }
        return len(canonical) != 1

    def _declared_denominator(self) -> int | None:
        values = [
            _integer_value(fact.value)
            for fact in self.facts
            if fact.subject_key.startswith("property:") and fact.field == "mea_denominator"
        ]
        if not values or any(value is None or value <= 0 for value in values):
            return None
        unique = set(values)
        return next(iter(unique)) if len(unique) == 1 else None

    def _mea_values(self) -> list[tuple[int, int]]:
        values: list[tuple[int, int]] = []
        seen: set[tuple[str, str, str]] = set()
        for fact in self.facts:
            if fact.field != "mea":
                continue
            parsed = _mea_value(fact.value)
            if parsed is None:
                continue
            key = (fact.subject_key, fact.field, _stable_value_json(fact.value))
            if key in seen:
                continue
            seen.add(key)
            values.append(parsed)
        return values

    def _mea_validator_has_no_blocker(self, issue: DeclarationCandidateIssue) -> bool:
        details = issue.details_json
        if not isinstance(details, dict):
            return False
        mea_set_status = details.get("mea_set_status")
        if mea_set_status not in {"COMPLETE", "PARTIAL", "UNKNOWN"}:
            return False
        results = validate_mea_totals(
            [
                (f"effective:{index}", numerator, denominator)
                for index, (numerator, denominator) in enumerate(self._mea_values())
            ],
            self._declared_denominator(),
            mea_set_status,
        )
        return not any(result.severity == "BLOCKER" for result in results)

    def _unit_numbers(self) -> list[str]:
        values: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for fact in self.facts:
            if not fact.subject_key.startswith("unit:") or fact.field != "number":
                continue
            parsed = _text_value(fact.value)
            if parsed is None:
                continue
            key = (fact.subject_key, fact.field, _stable_value_json(fact.value))
            if key in seen:
                continue
            seen.add(key)
            values.append(parsed.strip().casefold())
        return values

    def _duplicate_rule_subject_is_resolved(
        self, issue: DeclarationCandidateIssue
    ) -> bool:
        candidate = self._issue_candidate(issue)
        if candidate is None or candidate.candidate_type != "RULE":
            return False
        colliding = [
            rule_candidate
            for rule_candidate, _value in self.rules
            if rule_candidate.subject_key == candidate.subject_key
        ]
        if len(colliding) < 2:
            return False
        terminal = {"ACCEPTED", "CORRECTED", "REJECTED"}
        if any(rule_candidate.review_status not in terminal for rule_candidate in colliding):
            return False
        return sum(
            rule_candidate.review_status in {"ACCEPTED", "CORRECTED"}
            for rule_candidate in colliding
        ) <= 1


def _effective_value(candidate: DeclarationCandidate) -> object:
    if candidate.review_status == "CORRECTED" and candidate.corrected_value_json is not None:
        return candidate.corrected_value_json
    return candidate.proposed_value_json


def _stable_value_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _canonical_fact_value(subject_key: str, field: str, value: object) -> str | None:
    try:
        parsed = FactPayload(
            subject_key=subject_key,
            field=field,
            raw_value="review",
            normalized_value=value,
            confidence=1,
            evidence=[EvidencePayload(document_key="review", pdf_page=1, quote="review")],
        )
    except (ValidationError, ValueError, TypeError):
        return None
    return str(parsed.normalized_value.model_dump(mode="json"))


def _integer_value(value: object) -> int | None:
    if not isinstance(value, dict) or value.get("kind") != "INTEGER":
        return None
    raw = value.get("value")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _mea_value(value: object) -> tuple[int, int] | None:
    if not isinstance(value, dict) or value.get("kind") != "MEA":
        return None
    numerator, denominator = value.get("numerator"), value.get("denominator")
    if (
        isinstance(numerator, int) and not isinstance(numerator, bool) and numerator >= 0
        and isinstance(denominator, int) and not isinstance(denominator, bool) and denominator > 0
    ):
        return numerator, denominator
    return None


def _text_value(value: object) -> str | None:
    if not isinstance(value, dict) or value.get("kind") != "TEXT":
        return None
    raw = value.get("value")
    return raw if isinstance(raw, str) and raw.strip() else None


def _rule_from_effective_value(
    candidate: DeclarationCandidate, value: object
) -> RulePayload | None:
    if not isinstance(value, dict):
        return None
    try:
        return RulePayload.model_validate({
            **value,
            "subject_key": candidate.subject_key,
            "evidence": [EvidencePayload(document_key="review", pdf_page=1, quote="review")],
            "confidence": 1,
            "clarification_needed": value.get("clarification_needed", False),
        })
    except (ValidationError, ValueError, TypeError):
        return None


def _rule_needs_clarification(candidate: DeclarationCandidate) -> bool:
    """Keep unresolved rules visible in preview before final approval rejects them."""
    if candidate.candidate_type != "RULE" or candidate.review_status == "REJECTED":
        return False
    value = _effective_value(candidate)
    return isinstance(value, dict) and (
        value.get("clarification_needed") is True
        or value.get("proposed_status") == "NEEDS_CLARIFICATION"
    )


def _rule_needs_clarification_issue(candidate: DeclarationCandidate) -> IssueOut:
    return IssueOut(
        id=f"preview-rule-needs-clarification:{candidate.id}",
        code="RULE_NEEDS_CLARIFICATION",
        severity="WARNING",
        message="Die Regel ist als klärungsbedürftig markiert und kann nicht freigegeben werden.",
        resolution_status="OPEN",
        candidate_id=str(candidate.id),
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Declaration import not found")


def _stale() -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "STALE_DRAFT_REVISION"})


def _evidence_out(item: DeclarationCandidateEvidence) -> EvidenceOut:
    return EvidenceOut(id=str(item.id), document_id=str(item.document_id), field_path=item.field_path, pdf_page=item.pdf_page, printed_page=item.printed_page, section_ref=item.section_ref, quote=item.quote)


def _issue_out(item: DeclarationCandidateIssue) -> IssueOut:
    return IssueOut(id=str(item.id), code=item.code, severity=item.severity, message=item.message, resolution_status=item.resolution_status, candidate_id=str(item.candidate_id) if item.candidate_id else None)


def _candidate_out(candidate, evidence, issues) -> CandidateOut:
    policy = _policy(candidate, evidence, issues)
    return CandidateOut(id=str(candidate.id), stable_key=candidate.stable_key, candidate_type=candidate.candidate_type, subject_key=candidate.subject_key, field_key=candidate.field_key, operation=candidate.operation, previous_value_json=candidate.previous_value_json, proposed_value_json=candidate.proposed_value_json, corrected_value_json=candidate.corrected_value_json, confidence=candidate.confidence, risk_class=policy.risk_class, requires_individual_review=policy.requires_individual_review, review_status=candidate.review_status, review_comment=candidate.review_comment, evidence=[_evidence_out(item) for item in evidence], issues=[_issue_out(item) for item in issues])
