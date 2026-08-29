"""Bounded, fixed-pass declaration extraction and draft publication.

The module deliberately has no worker loop and no live-provider construction.
It accepts a provider and lease from the worker layer, so long-running parsing
and model calls stay outside database transactions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections import defaultdict
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Generic, Sequence, TypeVar

from sqlalchemy import select

from app.core.config import Settings, settings
from app.core.db import get_tenant_session
from app.core.storage import DocumentStore, get_store
from app.declarations.batching import PageBatch, PageBatcher
from app.declarations.evidence import EvidenceTextIndex
from app.declarations.policy import CandidateContext, classify_candidate
from app.declarations.preflight import (
    DocumentIntegrityError,
    ImportPreflightBudget,
    PdfPreflight,
)
from app.declarations.routing import PageRoutingSignal, route_escalation
from app.declarations.provider import (
    DeclarationExtractionProvider,
    DocumentInput,
    PageInput,
    ProviderCallResult,
    ProviderFailure,
    ProviderReservationEstimate,
    ProviderUsage,
)
from app.declarations.observability import PassLedgerEntry, ProviderPassMetadata
from app.declarations.schemas import (
    AdditionalFactPayload,
    CoreExtractionOutput,
    DocumentInventoryOutput,
    FactPayload,
    ReconciliationOutput,
    RuleExtractionOutput,
    RulePayload,
    TargetedRecheckOutput,
)
from app.declarations.validation import (
    ValidationIssue,
    validate_evidence_items,
    validate_reconciliation,
)
from app.models.declaration_import import DeclarationExtractionRun, DeclarationImport
from app.models.property import Property
from app.models.property_document import PropertyDocument
from app.models.unit import Unit
from app.repositories.declaration_candidate_repo import (
    DeclarationCandidateRepository,
    DraftCandidate,
    DraftEvidence,
    DraftIssue,
)
from app.repositories.declaration_import_repo import DeclarationImportRepository, WorkerLease
from app.repositories.unit_repo import UnitRepository


class PipelineFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class _Reservation:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(slots=True)
class _Ledger:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    attempts: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_usd: Decimal
    configured: Settings
    initial_input_tokens: int = 0
    initial_output_tokens: int = 0
    initial_cost_usd: Decimal = Decimal("0")
    initial_attempts: int = 0
    # Run-local queue, drained only by a successful tenant-bound transaction.
    pass_entries: list[PassLedgerEntry] = field(default_factory=list)

    def output_allowance(self) -> int:
        remaining_output = self.max_output_tokens - self.output_tokens
        return min(self.configured.declaration_provider_max_output_tokens_per_call, remaining_output)

    def reserve(
        self,
        text: str,
        *,
        escalation: bool = False,
        reserved_input_tokens: int | None = None,
    ) -> _Reservation:
        # Generic providers do not have the OpenAI adapter's request wrappers.
        # Their fallback remains conservative: two possible schema attempts.
        reserved_input = (
            _estimate_tokens(text) * 2
            if reserved_input_tokens is None
            else reserved_input_tokens
        )
        if isinstance(reserved_input, bool) or not isinstance(reserved_input, int) or reserved_input < 0:
            raise PipelineFailure("PROVIDER_RESERVATION_ESTIMATE_INVALID")
        output = self.output_allowance()
        if output <= 0:
            raise PipelineFailure("IMPORT_TOKEN_BUDGET_EXCEEDED")
        if self.input_tokens + reserved_input > self.max_input_tokens:
            raise PipelineFailure("IMPORT_TOKEN_BUDGET_EXCEEDED")
        if escalation:
            in_rate = max(
                self.configured.declaration_escalation_input_usd_per_million,
                self.configured.declaration_escalation_cached_input_usd_per_million,
            )
        else:
            primary_input_rate = self.configured.declaration_primary_input_usd_per_million
            in_rate = max(
                primary_input_rate,
                self.configured.declaration_primary_cached_input_usd_per_million,
                primary_input_rate * self.configured.declaration_primary_cache_write_multiplier,
            )
        out_rate = (
            self.configured.declaration_escalation_output_usd_per_million
            if escalation
            else self.configured.declaration_primary_output_usd_per_million
        )
        cost = (Decimal(reserved_input) * in_rate + Decimal(output) * out_rate) / Decimal(1_000_000)
        if self.cost_usd + cost > self.max_cost_usd:
            raise PipelineFailure("IMPORT_COST_BUDGET_EXCEEDED")
        return _Reservation(reserved_input, output, cost)

    def add_usage(self, usage: ProviderUsage, reservation: _Reservation) -> None:
        _validate_usage(usage)
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cost_usd += usage.cost_usd
        self.attempts += usage.attempt_count
        if (
            usage.input_tokens > reservation.input_tokens
            or usage.output_tokens > reservation.output_tokens
            or usage.cost_usd > reservation.cost_usd
        ):
            raise PipelineFailure("PROVIDER_USAGE_EXCEEDED_RESERVATION")
        if (
            self.input_tokens > self.max_input_tokens
            or self.output_tokens > self.max_output_tokens
            or self.cost_usd > self.max_cost_usd
        ):
            raise PipelineFailure("IMPORT_TOKEN_BUDGET_EXCEEDED")

    def settle_reservation(self, usage: ProviderUsage, reservation: _Reservation) -> None:
        """Replace an already durable conservative charge with actual usage."""
        _validate_usage(usage)
        self.input_tokens += usage.input_tokens - reservation.input_tokens
        self.output_tokens += usage.output_tokens - reservation.output_tokens
        self.cost_usd += usage.cost_usd - reservation.cost_usd
        # A reservation is one provider attempt; provider usage is aggregate
        # and can include Task 6's bounded schema-repair attempt.
        self.attempts += usage.attempt_count - 1
        if (
            usage.input_tokens > reservation.input_tokens
            or usage.output_tokens > reservation.output_tokens
            or usage.cost_usd > reservation.cost_usd
        ):
            raise PipelineFailure("PROVIDER_USAGE_EXCEEDED_RESERVATION")
        if (
            self.input_tokens > self.max_input_tokens
            or self.output_tokens > self.max_output_tokens
            or self.cost_usd > self.max_cost_usd
        ):
            raise PipelineFailure("IMPORT_TOKEN_BUDGET_EXCEEDED")

    def retain_reservation_for_incomplete_usage(
        self,
        usage: ProviderUsage | None,
        attempt_count: int,
        reservation: _Reservation,
    ) -> None:
        """Keep the prepaid envelope if any SDK leg omitted trustworthy usage."""
        if isinstance(attempt_count, bool) or not isinstance(attempt_count, int) or attempt_count < 1:
            raise PipelineFailure("PROVIDER_USAGE_INVALID")
        if usage is not None:
            _validate_usage(usage)
            if (
                usage.input_tokens > reservation.input_tokens
                or usage.output_tokens > reservation.output_tokens
                or usage.cost_usd > reservation.cost_usd
            ):
                raise PipelineFailure("PROVIDER_USAGE_EXCEEDED_RESERVATION")
        self.attempts += attempt_count - 1

    def charge_reservation(self, reservation: _Reservation) -> None:
        self.input_tokens += reservation.input_tokens
        self.output_tokens += reservation.output_tokens
        self.cost_usd += reservation.cost_usd
        self.attempts += 1

    @property
    def run_input_tokens(self) -> int:
        return self.input_tokens - self.initial_input_tokens

    @property
    def run_output_tokens(self) -> int:
        return self.output_tokens - self.initial_output_tokens

    @property
    def run_cost_usd(self) -> Decimal:
        return self.cost_usd - self.initial_cost_usd


T = TypeVar("T")


class DeclarationPipeline:
    def __init__(
        self,
        provider: DeclarationExtractionProvider,
        *,
        store: DocumentStore | None = None,
        preflight: PdfPreflight | None = None,
        configured: Settings = settings,
        tenant_session_factory: Callable[[uuid.UUID], AbstractAsyncContextManager] = get_tenant_session,
    ) -> None:
        self._provider = provider
        self._store = store or get_store()
        self._settings = configured
        self._preflight = preflight or PdfPreflight(
            max_pdf_bytes=configured.declaration_max_pdf_bytes,
            max_pages=configured.declaration_max_pages,
            text_min_chars=configured.declaration_text_min_chars,
            ocr_dpi=configured.declaration_ocr_dpi,
            ocr_timeout_seconds=configured.declaration_ocr_timeout_seconds,
            max_pixels_per_page=configured.declaration_ocr_max_pixels_per_page,
            max_temp_bytes=configured.declaration_ocr_max_temp_bytes,
            max_ocr_pages_per_import=configured.declaration_ocr_max_pages_per_import,
            max_pixels_per_import=configured.declaration_ocr_max_pixels_per_import,
            deadline_seconds=configured.declaration_preflight_deadline_seconds,
        )
        self._tenant_session_factory = tenant_session_factory
        # A pipeline owns mutable run identifiers only while this lock is held.
        # Workers may share an injected pipeline instance, but never its state.
        self._run_guard = asyncio.Lock()

    async def run(self, lease: WorkerLease) -> None:
        async with self._run_guard:
            await self._run_unlocked(lease)

    async def _run_unlocked(self, lease: WorkerLease) -> None:
        self._active_lease = lease
        self._active_run_id: uuid.UUID | None = None
        ledger: _Ledger | None = None
        try:
            imported, documents = await self._load(lease)
            ledger = _Ledger(
                input_tokens=int(imported.input_tokens),
                output_tokens=int(imported.output_tokens),
                cost_usd=Decimal(imported.estimated_cost_usd),
                attempts=int(imported.attempt_count),
                max_input_tokens=self._settings.declaration_max_input_tokens,
                max_output_tokens=self._settings.declaration_max_output_tokens,
                max_cost_usd=Decimal(imported.max_cost_usd),
                configured=self._settings,
            )
            ledger.initial_input_tokens = ledger.input_tokens
            ledger.initial_output_tokens = ledger.output_tokens
            ledger.initial_cost_usd = ledger.cost_usd
            ledger.initial_attempts = ledger.attempts
            await self._transition(
                lease,
                {"QUEUED", "PREFLIGHT", "RETRY_WAIT"},
                "PREFLIGHT",
                {
                    "current_stage": "preflight",
                    "next_attempt_at": None,
                    "error_code": None,
                    "error_message": None,
                },
            )
            run_id = await self._start_run(lease)
            self._active_run_id = run_id
            if len(documents) > self._settings.declaration_max_documents:
                raise PipelineFailure("DOCUMENT_LIMIT_REACHED")
            (
                page_inputs,
                page_counts,
                document_ids,
                preflight_issues,
                source_pdf_bytes,
                preflight_budget,
            ) = self._preflight_documents(documents)
            evidence_index = EvidenceTextIndex.from_pages(page_inputs)
            await self._transition(lease, {"PREFLIGHT"}, "EXTRACTING", {"current_stage": "extracting"})
            batches = self._page_batches(page_inputs)
            if not batches:
                raise PipelineFailure("IMPORT_REQUIRES_DOCUMENT")

            inventories: list[DocumentInventoryOutput] = []
            cores: list[CoreExtractionOutput] = []
            rulesets: list[RuleExtractionOutput] = []
            # Fixed global pass order is important: do not interleave a core
            # pass before every inventory batch has completed.
            for batch in batches:
                inventories.append(await self._call("inventory", batch.as_documents(), ledger, DocumentInventoryOutput))
            for batch in batches:
                cores.append(await self._call("extract_core", batch.as_documents(), ledger, CoreExtractionOutput))
            for batch in batches:
                rulesets.append(await self._call("extract_rules", batch.as_documents(), ledger, RuleExtractionOutput))
            inventory = _merge_inventory(inventories)
            _validate_inventory_documents(inventory, document_ids)
            core = _merge_core(cores)
            rules = _merge_rules(rulesets)
            await self._transition(lease, {"EXTRACTING"}, "RECONCILING", {"current_stage": "reconciling"})
            reconciliation = await self._reconcile_bounded(inventory, core, rules, ledger)

            declared_denominator = _declared_denominator(reconciliation)
            issues = [
                *preflight_issues,
                *_validate_inventory_evidence(
                    inventory, page_counts, evidence_index=evidence_index
                ),
                *validate_reconciliation(
                    reconciliation,
                    page_counts,
                    declared_denominator,
                    evidence_index=evidence_index,
                ),
            ]
            if any(issue.code == "UNKNOWN_EVIDENCE_DOCUMENT" for issue in issues):
                raise PipelineFailure("UNKNOWN_EVIDENCE_DOCUMENT")
            recheck, recheck_route_reasons = self._selected_recheck(
                reconciliation, inventory, issues, page_inputs
            )
            if recheck:
                rendered_recheck, render_issue = self._render_recheck_pages(
                    recheck, source_pdf_bytes, preflight_budget
                )
                if render_issue is not None:
                    # Never send a partial selected-page set to the provider.
                    issues.append(render_issue)
                else:
                    try:
                        rechecked, remaining_recheck_pages = await self._call_rechecks(
                            rendered_recheck, issues, ledger, route_reason_codes=recheck_route_reasons
                        )
                    except PipelineFailure as exc:
                        if exc.code not in {
                            "IMPORT_TOKEN_BUDGET_EXCEEDED",
                            "IMPORT_COST_BUDGET_EXCEEDED",
                            "PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED",
                        }:
                            raise
                        issues.append(
                            ValidationIssue(
                                code="RECHECK_BUDGET_UNAVAILABLE",
                                severity="WARNING",
                                message="Eine gezielte Nachprüfung konnte innerhalb des Importbudgets nicht ausgeführt werden.",
                            )
                        )
                    else:
                        reconciliation = _merge_recheck(reconciliation, rechecked)
                        declared_denominator = _declared_denominator(reconciliation)
                        issues = [
                            *preflight_issues,
                            *_validate_inventory_evidence(
                                inventory, page_counts, evidence_index=evidence_index
                            ),
                            *validate_reconciliation(
                                reconciliation,
                                page_counts,
                                declared_denominator,
                                evidence_index=evidence_index,
                            ),
                        ]
                        if any(issue.code == "UNKNOWN_EVIDENCE_DOCUMENT" for issue in issues):
                            raise PipelineFailure("UNKNOWN_EVIDENCE_DOCUMENT")
                        if remaining_recheck_pages:
                            issues.append(
                                ValidationIssue(
                                    code="RECHECK_CALL_LIMIT_REACHED",
                                    severity="WARNING",
                                    message="Weitere gezielte Nachprüfungen benötigen eine fachliche Klärung.",
                                )
                            )

            drafts, draft_issues = await self._convert(
                imported, inventory, reconciliation, issues, document_ids, lease.organization_id
            )
            if len(drafts) > self._settings.declaration_max_candidates:
                raise PipelineFailure("CANDIDATE_LIMIT_EXCEEDED")
            await self._publish(lease, page_counts, drafts, draft_issues, ledger)
        except DocumentIntegrityError as exc:
            await self._fail(lease, exc.code, retryable=False, ledger=ledger)
        except ProviderFailure as exc:
            if ledger is not None:
                # The call boundary charges a failure once before it reaches here.
                pass
            await self._fail(lease, exc.code, retryable=exc.retryable, ledger=ledger)
        except PipelineFailure as exc:
            await self._fail(lease, exc.code, retryable=exc.retryable, ledger=ledger)
        except ValueError:
            await self._fail(lease, "DRAFT_PUBLICATION_FAILED", retryable=False, ledger=ledger)
        except Exception:
            await self._fail(lease, "PIPELINE_UNEXPECTED_FAILURE", retryable=False, ledger=ledger)

    async def _load(self, lease: WorkerLease):
        async with self._tenant_session_factory(lease.organization_id) as session:
            imports = DeclarationImportRepository(session, lease.organization_id)
            item = await imports.lock_current_lease(
                lease, {"QUEUED", "PREFLIGHT", "RETRY_WAIT"}
            )
            if item is None:
                raise PipelineFailure("LEASE_LOST")
            return item, tuple(await imports.list_documents(lease.import_id))

    def _preflight_documents(self, documents):
        budget = self._preflight.new_import_budget()
        pages: list[PageInput] = []
        counts: dict[str, int] = {}
        ids: dict[str, uuid.UUID] = {}
        issues: list[ValidationIssue] = []
        # Kept only in this run's stack-local handoff to the selected-page
        # renderer. Never attach source bytes to a page, evidence, ledger or
        # durable model.
        source_pdf_bytes: dict[str, bytes] = {}
        for _link, document in documents:
            key = _document_key(document)
            try:
                data = self._store.get(document.storage_uri)
            except FileNotFoundError as exc:
                raise DocumentIntegrityError("STORAGE_INTEGRITY_MISMATCH") from exc
            result = self._preflight.run_bytes(
                data,
                expected_sha256=document.content_sha256,
                expected_size=document.content_byte_size,
                budget=budget,
            )
            source_pdf_bytes[key] = data
            counts[key] = result.page_count
            ids[key] = document.id
            pages.extend(
                PageInput(
                    document_key=key,
                    pdf_page=artifact.page_number,
                    text=artifact.text,
                    quality=_page_quality(artifact.source),
                )
                for artifact in result.pages
            )
            for artifact in result.pages:
                for code in artifact.issues:
                    issues.append(ValidationIssue(code=code, severity="WARNING", message="Eine PDF-Seite konnte nicht vollständig maschinell gelesen werden."))
        return tuple(pages), counts, ids, tuple(issues), source_pdf_bytes, budget

    def _render_recheck_pages(
        self,
        pages: Sequence[PageInput],
        source_pdf_bytes: dict[str, bytes],
        budget: ImportPreflightBudget,
    ) -> tuple[tuple[PageInput, ...], ValidationIssue | None]:
        """Attach every selected PNG or fail the full recheck closed."""
        rendered: list[PageInput] = []
        for page in pages:
            source = source_pdf_bytes.get(page.document_key)
            if source is None:
                return (), self._vision_render_issue(page)
            try:
                image_data_url = self._preflight.render_page_data_url(
                    source, page.pdf_page, budget=budget
                )
            except DocumentIntegrityError:
                return (), self._vision_render_issue(page)
            rendered.append(
                PageInput(
                    document_key=page.document_key,
                    pdf_page=page.pdf_page,
                    text=page.text,
                    quality=page.quality,
                    image_data_url=image_data_url,
                )
            )
        return tuple(rendered), None

    @staticmethod
    def _vision_render_issue(page: PageInput) -> ValidationIssue:
        return ValidationIssue(
            code="VISION_RENDER_UNAVAILABLE",
            severity="BLOCKER",
            message="Die ausgewählte PDF-Seite konnte nicht sicher für die Sichtprüfung gerendert werden.",
            document_key=page.document_key,
            pdf_page=page.pdf_page,
        )

    def _page_batches(self, pages: Sequence[PageInput]):
        """Batch pages against the exact logical envelopes of all fixed passes."""
        cap = self._settings.declaration_provider_batch_chars

        def fits(batch_pages) -> bool:
            documents = PageBatch(batch_pages, sum(len(page.text) for page in batch_pages)).as_documents()
            return all(
                len(_serialize_provider_request(method, documents=documents)) <= cap
                for method in ("inventory", "extract_core", "extract_rules")
            )

        try:
            return PageBatcher(
                self._settings.declaration_provider_batch_pages,
                cap,
                fits_batch=fits,
            ).batch(pages)
        except ValueError as exc:
            raise PipelineFailure("PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED") from exc

    async def _call(self, method: str, documents: Sequence[DocumentInput], ledger: _Ledger, expected: type[T]) -> T:
        source = _serialize_provider_request(method, documents=documents)
        reservation = await self._reserve_before_request(
            source, ledger, method=method, documents=documents
        )
        try:
            raw = await getattr(self._provider, method)(documents, max_output_tokens=reservation.output_tokens)
        except ProviderFailure as exc:
            try:
                _append_failure_attempt_metadata(ledger, exc)
            except PipelineFailure:
                await self._checkpoint(ledger)
                raise
            if not exc.usage_complete:
                try:
                    ledger.retain_reservation_for_incomplete_usage(
                        exc.usage if isinstance(exc.usage, ProviderUsage) else None,
                        exc.attempt_count,
                        reservation,
                    )
                except PipelineFailure:
                    await self._checkpoint(ledger)
                    raise
            elif isinstance(exc.usage, ProviderUsage):
                try:
                    ledger.settle_reservation(exc.usage, reservation)
                except PipelineFailure as error:
                    await self._checkpoint(ledger)
                    raise
            elif exc.usage is not None:
                await self._checkpoint(ledger)
                raise PipelineFailure("PROVIDER_USAGE_INVALID")
            await self._checkpoint(ledger)
            raise
        except Exception as exc:
            await self._checkpoint(ledger)
            raise PipelineFailure("PROVIDER_UNEXPECTED_FAILURE") from exc
        try:
            output = _unwrap(raw, expected, ledger, reservation, reservation_already_charged=True)
        except PipelineFailure:
            await self._checkpoint(ledger)
            raise
        await self._checkpoint(ledger)
        return output

    async def _call_reconcile(self, inventory, core, rules, ledger: _Ledger) -> ReconciliationOutput:
        source = _serialize_provider_request(
            "reconcile", inventory=inventory, core=core, rules=rules
        )
        reservation = await self._reserve_before_request(
            source,
            ledger,
            method="reconcile",
            inventory=inventory,
            core=core,
            rules=rules,
        )
        try:
            raw = await self._provider.reconcile(inventory, core, rules, max_output_tokens=reservation.output_tokens)
        except ProviderFailure as exc:
            try:
                _append_failure_attempt_metadata(ledger, exc)
            except PipelineFailure:
                await self._checkpoint(ledger)
                raise
            if not exc.usage_complete:
                try:
                    ledger.retain_reservation_for_incomplete_usage(
                        exc.usage if isinstance(exc.usage, ProviderUsage) else None,
                        exc.attempt_count,
                        reservation,
                    )
                except PipelineFailure:
                    await self._checkpoint(ledger)
                    raise
            elif isinstance(exc.usage, ProviderUsage):
                try:
                    ledger.settle_reservation(exc.usage, reservation)
                except PipelineFailure as error:
                    await self._checkpoint(ledger)
                    raise
            elif exc.usage is not None:
                await self._checkpoint(ledger)
                raise PipelineFailure("PROVIDER_USAGE_INVALID")
            await self._checkpoint(ledger)
            raise
        except Exception as exc:
            await self._checkpoint(ledger)
            raise PipelineFailure("PROVIDER_UNEXPECTED_FAILURE") from exc
        try:
            output = _unwrap(raw, ReconciliationOutput, ledger, reservation, reservation_already_charged=True)
        except PipelineFailure:
            await self._checkpoint(ledger)
            raise
        await self._checkpoint(ledger)
        return output

    async def _reconcile_bounded(self, inventory, core, rules, ledger: _Ledger) -> ReconciliationOutput:
        """Keep reconciliation payloads deterministic and below the text cap."""
        source = _serialize_provider_request(
            "reconcile", inventory=inventory, core=core, rules=rules
        )
        if len(source) <= self._settings.declaration_provider_batch_chars:
            return await self._call_reconcile(inventory, core, rules, ledger)
        entries = (
            [("fact", item) for item in core.facts]
            + [("additional-core", item) for item in core.additional_facts]
            + [("rule", item) for item in rules.rules]
            + [("additional-rule", item) for item in rules.additional_facts]
        )
        if not entries:
            raise PipelineFailure("RECONCILE_PAYLOAD_LIMIT_EXCEEDED")

        def build(
            values: tuple[
                list[FactPayload],
                list[AdditionalFactPayload],
                list[RulePayload],
                list[AdditionalFactPayload],
            ]
        ) -> tuple[CoreExtractionOutput, RuleExtractionOutput]:
            return (
                CoreExtractionOutput(facts=values[0], additional_facts=values[1]),
                RuleExtractionOutput(rules=values[2], additional_facts=values[3]),
            )

        def add(
            values: tuple[
                list[FactPayload],
                list[AdditionalFactPayload],
                list[RulePayload],
                list[AdditionalFactPayload],
            ],
            kind: str,
            item: FactPayload | AdditionalFactPayload | RulePayload,
        ) -> tuple[
            list[FactPayload],
            list[AdditionalFactPayload],
            list[RulePayload],
            list[AdditionalFactPayload],
        ]:
            candidate = tuple([*part] for part in values)
            if kind == "fact":
                candidate[0].append(item)  # type: ignore[arg-type]
            elif kind == "additional-core":
                candidate[1].append(item)  # type: ignore[arg-type]
            elif kind == "rule":
                candidate[2].append(item)  # type: ignore[arg-type]
            else:
                candidate[3].append(item)  # type: ignore[arg-type]
            return candidate

        def fits(values) -> bool:
            candidate_core, candidate_rules = build(values)
            return len(
                _serialize_provider_request(
                    "reconcile",
                    inventory=inventory,
                    core=candidate_core,
                    rules=candidate_rules,
                )
            ) <= self._settings.declaration_provider_batch_chars

        chunks: list[tuple[list[FactPayload], list[AdditionalFactPayload], list[RulePayload], list[AdditionalFactPayload]]] = []
        current: tuple[list[FactPayload], list[AdditionalFactPayload], list[RulePayload], list[AdditionalFactPayload]] = ([], [], [], [])
        for kind, item in entries:
            candidate = add(current, kind, item)
            if fits(candidate):
                current = candidate
                continue
            if not any(current):
                raise PipelineFailure("RECONCILE_PAYLOAD_LIMIT_EXCEEDED")
            chunks.append(current)
            current = add(([], [], [], []), kind, item)
            if not fits(current):
                raise PipelineFailure("RECONCILE_PAYLOAD_LIMIT_EXCEEDED")
        chunks.append(current)
        results: list[ReconciliationOutput] = []
        for facts, core_additional, rules_part, rule_additional in chunks:
            results.append(await self._call_reconcile(
                inventory,
                CoreExtractionOutput(facts=facts, additional_facts=core_additional),
                RuleExtractionOutput(rules=rules_part, additional_facts=rule_additional),
                ledger,
            ))
        return _merge_reconciliations(results)

    async def _call_rechecks(
        self,
        pages: Sequence[PageInput],
        issues: Sequence[ValidationIssue],
        ledger: _Ledger,
        *,
        route_reason_codes: Sequence[str] = (),
    ) -> tuple[TargetedRecheckOutput, bool]:
        issue_codes = tuple(sorted({issue.code for issue in issues}))
        subject_keys = tuple(sorted({issue.subject_key for issue in issues if issue.subject_key}))
        cap = self._settings.declaration_provider_batch_chars

        def fits(batch_pages) -> bool:
            documents = PageBatch(
                batch_pages, sum(len(page.text) for page in batch_pages)
            ).as_documents()
            return len(
                _serialize_provider_request(
                    "recheck",
                    documents=documents,
                    issue_codes=issue_codes,
                    subject_keys=subject_keys,
                )
            ) <= cap

        try:
            batches = PageBatcher(
                self._settings.declaration_provider_batch_pages,
                cap,
                fits_batch=fits,
                overlap=False,
            ).batch(tuple(pages))
        except ValueError as exc:
            if pages and all(page.image_data_url is not None for page in pages):
                return TargetedRecheckOutput(), True
            raise PipelineFailure("PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED") from exc
        limit = self._settings.declaration_max_recheck_calls
        if limit <= 0:
            return TargetedRecheckOutput(), bool(batches)
        if len(batches) > limit:
            # One routed vision decision is atomic: do not disclose only a
            # prefix because the configured call cap is lower than its batch
            # count.
            return TargetedRecheckOutput(), True
        if not self._vision_batches_are_atomic(batches):
            return TargetedRecheckOutput(), True

        planned: list[tuple[tuple[DocumentInput, ...], _Reservation]] = []
        planning = _Ledger(
            ledger.input_tokens,
            ledger.output_tokens,
            ledger.cost_usd,
            ledger.attempts,
            ledger.max_input_tokens,
            ledger.max_output_tokens,
            ledger.max_cost_usd,
            ledger.configured,
        )
        for batch in batches:
            documents = batch.as_documents()
            source = _serialize_provider_request(
                "recheck", documents=documents, issue_codes=issue_codes, subject_keys=subject_keys
            )
            reservation = self._reservation_for_request(
                source,
                planning,
                escalation=True,
                method="recheck",
                documents=documents,
                issue_codes=issue_codes,
                subject_keys=subject_keys,
            )
            planning.charge_reservation(reservation)
            planned.append((documents, reservation))

        # All provider calls have now passed the aggregate budget fence.  Keep
        # that planning ledger separate from the durable ledger: each actual
        # reservation is charged and checkpointed only immediately before its
        # own SDK call.  A failure in batch one must not fabricate charged,
        # durable attempts for later batches that were never sent.
        results: list[TargetedRecheckOutput] = []
        for documents, reservation in planned:
            ledger.charge_reservation(reservation)
            await self._checkpoint(ledger)
            results.append(
                await self._call_recheck_batch(
                    documents,
                    issue_codes,
                    subject_keys,
                    ledger,
                    reservation=reservation,
                    route_reason_codes=route_reason_codes,
                )
            )
        return _merge_rechecks(results), False

    @staticmethod
    def _vision_batches_are_atomic(batches: Sequence[PageBatch]) -> bool:
        """Reject split logical pages before any image can leave the process."""
        page_batches: dict[tuple[str, int], set[int]] = defaultdict(set)
        image_counts: dict[tuple[int, str, int], int] = defaultdict(int)
        for batch_number, batch in enumerate(batches):
            for page in batch.pages:
                key = (page.document_key, page.pdf_page)
                page_batches[key].add(batch_number)
                if page.image_data_url is not None:
                    image_counts[(batch_number, *key)] += 1
        return all(
            len(batch_numbers) == 1
            and image_counts[(next(iter(batch_numbers)), *key)] == 1
            for key, batch_numbers in page_batches.items()
        )

    async def _reserve_before_request(
        self,
        serialized_request: str,
        ledger: _Ledger,
        *,
        escalation: bool = False,
        method: str,
        **request: object,
    ) -> _Reservation:
        """Durably fence a conservative charge before network I/O.

        If the process dies after a provider accepts a request, a later worker
        must see the reservation.  Conversely a lost lease stops here, before
        the provider method is ever invoked.
        """
        reservation = self._reservation_for_request(
            serialized_request, ledger, escalation=escalation, method=method, **request
        )
        ledger.charge_reservation(reservation)
        await self._checkpoint(ledger)
        return reservation

    def _reservation_for_request(
        self,
        serialized_request: str,
        ledger: _Ledger,
        *,
        escalation: bool = False,
        method: str,
        **request: object,
    ) -> _Reservation:
        if len(serialized_request) > self._settings.declaration_provider_batch_chars:
            raise PipelineFailure("PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED")
        if ledger.output_allowance() <= 0:
            raise PipelineFailure("IMPORT_TOKEN_BUDGET_EXCEEDED")
        estimator = getattr(self._provider, "estimate_reservation", None)
        if not callable(estimator):
            raise PipelineFailure("PROVIDER_RESERVATION_ESTIMATE_INVALID")
        try:
            estimate = estimator(
                method,
                max_output_tokens=ledger.output_allowance(),
                **request,
            )
        except Exception as exc:
            raise PipelineFailure("PROVIDER_RESERVATION_ESTIMATE_INVALID") from exc
        if (
            not isinstance(estimate, ProviderReservationEstimate)
            or isinstance(estimate.input_tokens, bool)
            or not isinstance(estimate.input_tokens, int)
            or estimate.input_tokens < 0
            or isinstance(estimate.attempt_count, bool)
            or not isinstance(estimate.attempt_count, int)
            or estimate.attempt_count < 1
            or len(estimate.attempt_input_tokens) != estimate.attempt_count
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in estimate.attempt_input_tokens
            )
            or sum(estimate.attempt_input_tokens) != estimate.input_tokens
        ):
            raise PipelineFailure("PROVIDER_RESERVATION_ESTIMATE_INVALID")
        return ledger.reserve(
            serialized_request,
            escalation=escalation,
            reserved_input_tokens=estimate.input_tokens,
        )

    async def _call_recheck_batch(
        self,
        documents: Sequence[DocumentInput],
        issue_codes: Sequence[str],
        subject_keys: Sequence[str],
        ledger: _Ledger,
        *,
        reservation: _Reservation | None = None,
        route_reason_codes: Sequence[str] = (),
    ) -> TargetedRecheckOutput:
        source = _serialize_provider_request(
            "recheck", documents=documents, issue_codes=issue_codes, subject_keys=subject_keys
        )
        if reservation is None:
            reservation = await self._reserve_before_request(
                source,
                ledger,
                escalation=True,
                method="recheck",
                documents=documents,
                issue_codes=issue_codes,
                subject_keys=subject_keys,
            )
        try:
            raw = await self._provider.recheck(
                documents,
                issue_codes,
                subject_keys,
                max_output_tokens=reservation.output_tokens,
            )
        except ProviderFailure as exc:
            try:
                _validate_provider_failure_contract(exc)
                _apply_recheck_route_metadata(exc, route_reason_codes)
                _append_failure_attempt_metadata(ledger, exc)
            except PipelineFailure:
                await self._checkpoint(ledger)
                raise
            if not exc.usage_complete:
                try:
                    ledger.retain_reservation_for_incomplete_usage(
                        exc.usage if isinstance(exc.usage, ProviderUsage) else None,
                        exc.attempt_count,
                        reservation,
                    )
                except PipelineFailure:
                    await self._checkpoint(ledger)
                    raise
            elif isinstance(exc.usage, ProviderUsage):
                try:
                    ledger.settle_reservation(exc.usage, reservation)
                except PipelineFailure as error:
                    await self._checkpoint(ledger)
                    raise
            elif exc.usage is not None:
                await self._checkpoint(ledger)
                raise PipelineFailure("PROVIDER_USAGE_INVALID")
            await self._checkpoint(ledger)
            raise
        except Exception as exc:
            await self._checkpoint(ledger)
            raise PipelineFailure("PROVIDER_UNEXPECTED_FAILURE") from exc
        try:
            raw = _with_recheck_route_metadata(raw, route_reason_codes)
            output = _unwrap(raw, TargetedRecheckOutput, ledger, reservation, reservation_already_charged=True)
        except PipelineFailure:
            await self._checkpoint(ledger)
            raise
        await self._checkpoint(ledger)
        return output

    def _selected_recheck_pages(
        self,
        output: ReconciliationOutput,
        inventory: DocumentInventoryOutput,
        issues: Sequence[ValidationIssue],
        pages: Sequence[PageInput],
    ) -> tuple[PageInput, ...]:
        return self._selected_recheck(output, inventory, issues, pages)[0]

    def _selected_recheck(
        self,
        output: ReconciliationOutput,
        inventory: DocumentInventoryOutput,
        issues: Sequence[ValidationIssue],
        pages: Sequence[PageInput],
    ) -> tuple[tuple[PageInput, ...], tuple[str, ...]]:
        """Adapt local validation results into the pure escalation router."""
        cited, blockers = _routing_evidence_page_keys(output, inventory, issues)
        decision = route_escalation(
            issues=issues,
            page_inventory=tuple(
                PageRoutingSignal(
                    document_key=page.document_key,
                    pdf_page=page.pdf_page,
                    quality=page.quality,
                    # Structural preflight accepted the page. Only the bounded
                    # renderer decides whether it can become a vision input;
                    # failure there is converted into a human-review blocker.
                    renderable=True,
                    cited=(page.document_key, page.pdf_page) in cited,
                    has_blocker=(page.document_key, page.pdf_page) in blockers,
                )
                for page in pages
            ),
            provider_failure=None,
            max_pages=self._settings.declaration_max_recheck_pages,
        )
        if decision.action != "VISION_RECHECK":
            return (), ()
        by_key = {(page.document_key, page.pdf_page): page for page in pages}
        return tuple(by_key[key] for key in decision.page_keys), decision.reason_codes

    async def _convert(self, imported, inventory, output, issues, document_ids, org_id):
        existing_property: Property | None = None
        existing_units: Sequence[Unit] = ()
        if imported.mode == "EXISTING_PROPERTY" and imported.target_property_id:
            async with self._tenant_session_factory(org_id) as session:
                existing_property = await session.scalar(
                    select(Property)
                    .where(Property.organization_id == org_id)
                    .where(Property.id == imported.target_property_id)
                )
                existing_units = await UnitRepository(session, org_id).list_by_property(imported.target_property_id)
        return _candidate_drafts(imported, inventory, output, issues, document_ids, existing_property, existing_units)

    async def _publish(self, lease, page_counts, drafts, issues, ledger) -> None:
        persisted_entries: tuple[PassLedgerEntry, ...] = ()
        async with self._tenant_session_factory(lease.organization_id) as session:
            imports = DeclarationImportRepository(session, lease.organization_id)
            if await imports.lock_current_lease(lease, {"RECONCILING"}) is None:
                raise PipelineFailure("LEASE_LOST")
            document_rows = await imports.list_documents(lease.import_id)
            for _link, document in document_rows:
                key = _document_key(document)
                if key not in page_counts:
                    raise PipelineFailure("DOCUMENT_PAGE_COUNT_MISSING")
                document.page_count = page_counts[key]
            await DeclarationCandidateRepository(session, lease.organization_id).replace_for_import(
                lease.import_id, drafts, issues
            )
            run = await imports.get_run_for_import_for_update(
                lease.import_id, getattr(self, "_active_run_id", None)
            )
            if run is None:
                raise PipelineFailure("EXTRACTION_RUN_NOT_FOUND")
            persisted_entries = await self._append_pending_pass_entries(
                imports, run, ledger
            )
            run.status = "SUCCEEDED"
            run.input_tokens = ledger.run_input_tokens
            run.output_tokens = ledger.run_output_tokens
            run.estimated_cost_usd = ledger.run_cost_usd
            run.completed_at = datetime.now(timezone.utc)
            if not await imports.transition_with_lease(
                lease,
                {"RECONCILING"},
                "NEEDS_REVIEW",
                {
                    "current_stage": "review",
                    "progress_percent": 100,
                    "error_code": None,
                    "error_message": None,
                    "next_attempt_at": None,
                    "input_tokens": ledger.input_tokens,
                    "output_tokens": ledger.output_tokens,
                    "estimated_cost_usd": ledger.cost_usd,
                    "attempt_count": ledger.attempts,
                    "draft_revision": DeclarationImport.draft_revision + 1,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                },
            ):
                raise PipelineFailure("LEASE_LOST")
        self._clear_persisted_pass_entries(ledger, persisted_entries)

    async def _fail(self, lease, code: str, *, retryable: bool, ledger: _Ledger | None) -> None:
        if code == "LEASE_LOST":
            return
        persisted_entries: tuple[PassLedgerEntry, ...] = ()
        async with self._tenant_session_factory(lease.organization_id) as session:
            repo = DeclarationImportRepository(session, lease.organization_id)
            if ledger is None:
                item = await repo.lock_current_lease(
                    lease, {"PREFLIGHT", "EXTRACTING", "RECONCILING"}
                )
                if item is None:
                    raise PipelineFailure("LEASE_LOST")
                ledger = _Ledger(int(item.input_tokens), int(item.output_tokens), Decimal(item.estimated_cost_usd), int(item.attempt_count), self._settings.declaration_max_input_tokens, self._settings.declaration_max_output_tokens, Decimal(item.max_cost_usd), self._settings)
            elif await repo.lock_current_lease(
                lease, {"PREFLIGHT", "EXTRACTING", "RECONCILING"}
            ) is None:
                raise PipelineFailure("LEASE_LOST")
            next_attempt = datetime.now(timezone.utc) + timedelta(minutes=5) if retryable else None
            run = await repo.get_run_for_import_for_update(
                lease.import_id, getattr(self, "_active_run_id", None)
            )
            if getattr(self, "_active_run_id", None) is not None and run is None:
                raise PipelineFailure("EXTRACTION_RUN_NOT_FOUND")
            if run is not None:
                persisted_entries = await self._append_pending_pass_entries(
                    repo, run, ledger
                )
                run.status = "FAILED"
                run.error_code = code
                run.input_tokens = ledger.run_input_tokens
                run.output_tokens = ledger.run_output_tokens
                run.estimated_cost_usd = ledger.run_cost_usd
                run.completed_at = datetime.now(timezone.utc)
            if not await repo.transition_with_lease(
                lease,
                {"PREFLIGHT", "EXTRACTING", "RECONCILING"},
                "RETRY_WAIT" if retryable else "FAILED",
                {
                    "current_stage": "retry_wait" if retryable else "failed",
                    "error_code": code,
                    "error_message": code,
                    "input_tokens": ledger.input_tokens,
                    "output_tokens": ledger.output_tokens,
                    "estimated_cost_usd": ledger.cost_usd,
                    "attempt_count": ledger.attempts,
                    "next_attempt_at": next_attempt,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                },
            ):
                raise PipelineFailure("LEASE_LOST")
        self._clear_persisted_pass_entries(ledger, persisted_entries)

    async def _transition(self, lease: WorkerLease, expected: set[str], status: str, values: dict[str, object]) -> None:
        async with self._tenant_session_factory(lease.organization_id) as session:
            if not await DeclarationImportRepository(session, lease.organization_id).transition_with_lease(lease, expected, status, values):
                raise PipelineFailure("LEASE_LOST")

    async def _start_run(self, lease: WorkerLease) -> uuid.UUID:
        async with self._tenant_session_factory(lease.organization_id) as session:
            repo = DeclarationImportRepository(session, lease.organization_id)
            if await repo.lock_current_lease(lease, {"PREFLIGHT"}) is None:
                raise PipelineFailure("LEASE_LOST")
            run = DeclarationExtractionRun(
                organization_id=lease.organization_id,
                import_id=lease.import_id,
                status="RUNNING",
                provider=type(self._provider).__name__,
                model_name=self._settings.declaration_primary_model,
                prompt_version=self._settings.declaration_prompt_version,
                schema_version=self._settings.declaration_schema_version,
                harness_version=self._settings.declaration_harness_version,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
            )
            session.add(run)
            await session.flush()
            return run.id

    async def _checkpoint(self, ledger: _Ledger) -> None:
        lease = getattr(self, "_active_lease", None)
        if lease is None:
            return
        persisted_entries: tuple[PassLedgerEntry, ...] = ()
        async with self._tenant_session_factory(lease.organization_id) as session:
            repo = DeclarationImportRepository(session, lease.organization_id)
            item = await repo.lock_current_lease(
                lease, {"PREFLIGHT", "EXTRACTING", "RECONCILING"}
            )
            if item is None:
                raise PipelineFailure("LEASE_LOST")
            run = await repo.get_run_for_import_for_update(
                lease.import_id, getattr(self, "_active_run_id", None)
            )
            if getattr(self, "_active_run_id", None) is not None and run is None:
                raise PipelineFailure("EXTRACTION_RUN_NOT_FOUND")
            if run is not None:
                persisted_entries = await self._append_pending_pass_entries(
                    repo, run, ledger
                )
                run.input_tokens = ledger.run_input_tokens
                run.output_tokens = ledger.run_output_tokens
                run.estimated_cost_usd = ledger.run_cost_usd
            if not await repo.transition_with_lease(
                lease,
                {item.status},
                item.status,
                {
                    "input_tokens": ledger.input_tokens,
                    "output_tokens": ledger.output_tokens,
                    "estimated_cost_usd": ledger.cost_usd,
                    "attempt_count": ledger.attempts,
                },
            ):
                raise PipelineFailure("LEASE_LOST")
        self._clear_persisted_pass_entries(ledger, persisted_entries)

    @staticmethod
    async def _append_pending_pass_entries(
        repo: DeclarationImportRepository,
        run: DeclarationExtractionRun,
        ledger: _Ledger,
    ) -> tuple[PassLedgerEntry, ...]:
        """Stage the queue in the caller's lease-fenced transaction only."""
        pending = tuple(ledger.pass_entries)
        if pending:
            await repo.append_pass_entries(run.id, pending)
        return pending

    @staticmethod
    def _clear_persisted_pass_entries(
        ledger: _Ledger,
        persisted: tuple[PassLedgerEntry, ...],
    ) -> None:
        """Drop only entries whose enclosing DB transaction has committed."""
        if not persisted:
            return
        count = len(persisted)
        if ledger.pass_entries[:count] != list(persisted):
            # A pipeline is single-run guarded.  Treat any unexpected queue
            # mutation as a fail-closed invariant breach instead of dropping
            # potentially unpersisted metadata.
            raise PipelineFailure("PASS_LEDGER_QUEUE_INVALID")
        del ledger.pass_entries[:count]


def _unwrap(
    raw: object,
    expected: type[T],
    ledger: _Ledger,
    reservation: _Reservation,
    *,
    reservation_already_charged: bool = False,
) -> T:
    if (
        not isinstance(raw, ProviderCallResult)
        or not isinstance(raw.output, expected)
        or not isinstance(raw.usage, ProviderUsage)
    ):
        if not reservation_already_charged:
            ledger.charge_reservation(reservation)
        raise PipelineFailure("PROVIDER_RESULT_INVALID")
    try:
        _validate_provider_call_contract(raw)
        _append_attempt_metadata(
            ledger,
            raw.attempt_metadata,
            final_metadata=raw.metadata,
            attempt_count=raw.usage.attempt_count,
        )
    except PipelineFailure:
        if not reservation_already_charged:
            ledger.charge_reservation(reservation)
        raise
    try:
        if not raw.usage_complete:
            ledger.retain_reservation_for_incomplete_usage(
                raw.usage,
                raw.usage.attempt_count,
                reservation,
            )
        elif reservation_already_charged:
            ledger.settle_reservation(raw.usage, reservation)
        else:
            ledger.add_usage(raw.usage, reservation)
    except PipelineFailure as error:
        if error.code == "PROVIDER_USAGE_INVALID" and not reservation_already_charged:
            ledger.charge_reservation(reservation)
        raise
    return raw.output


_VISION_ROUTE_REASONS = frozenset({"EVIDENCE_QUOTE_NOT_FOUND", "LOW_PAGE_QUALITY"})


def _recheck_route_reason_codes(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value in _VISION_ROUTE_REASONS}))


def _route_metadata_for_recheck(
    metadata: ProviderPassMetadata, reasons: Sequence[str]
) -> ProviderPassMetadata:
    return replace(
        metadata,
        route_action="VISION_RECHECK",
        route_reason_codes=_recheck_route_reason_codes(reasons),
    )


def _with_recheck_route_metadata(
    raw: object, reasons: Sequence[str]
) -> object:
    if not isinstance(raw, ProviderCallResult):
        return raw
    _validate_provider_call_contract(raw)
    entries = tuple(_route_metadata_for_recheck(item, reasons) for item in raw.attempt_metadata)
    metadata = _route_metadata_for_recheck(raw.metadata, reasons) if isinstance(raw.metadata, ProviderPassMetadata) else None
    return replace(raw, metadata=metadata, attempt_metadata=entries)


def _apply_recheck_route_metadata(error: ProviderFailure, reasons: Sequence[str]) -> None:
    _validate_provider_failure_contract(error)
    entries = tuple(
        _route_metadata_for_recheck(item, reasons) for item in error.attempt_metadata
    )
    if entries:
        error.set_attempt_metadata(entries)
    elif isinstance(error.metadata, ProviderPassMetadata):
        error.metadata = _route_metadata_for_recheck(error.metadata, reasons)


def _validate_usage(usage: ProviderUsage) -> None:
    for value in (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.cache_write_input_tokens,
        usage.output_tokens,
        usage.attempt_count,
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PipelineFailure("PROVIDER_USAGE_INVALID")
    if usage.attempt_count < 1 or not isinstance(usage.cost_usd, Decimal) or not usage.cost_usd.is_finite() or usage.cost_usd < 0:
        raise PipelineFailure("PROVIDER_USAGE_INVALID")
    if usage.cached_input_tokens + usage.cache_write_input_tokens > usage.input_tokens:
        raise PipelineFailure("PROVIDER_USAGE_INVALID")


def _append_attempt_metadata(
    ledger: _Ledger,
    metadata: tuple[ProviderPassMetadata, ...],
    *,
    final_metadata: ProviderPassMetadata | None,
    attempt_count: int,
) -> None:
    _validate_attempt_metadata_contract(
        metadata,
        final_metadata=final_metadata,
        attempt_count=attempt_count,
    )
    ledger.pass_entries.extend(
        PassLedgerEntry.from_metadata(item) for item in metadata
    )


def _validate_attempt_metadata_contract(
    metadata: tuple[ProviderPassMetadata, ...],
    *,
    final_metadata: ProviderPassMetadata | None,
    attempt_count: int,
) -> None:
    if (
        not isinstance(metadata, tuple)
        or isinstance(attempt_count, bool)
        or not isinstance(attempt_count, int)
        or attempt_count < 0
        or len(metadata) != attempt_count
        or (attempt_count == 0 and final_metadata is not None)
        or (attempt_count > 0 and final_metadata != metadata[-1])
    ):
        raise PipelineFailure("PROVIDER_RESULT_INVALID")
    for expected_attempt_no, item in enumerate(metadata, start=1):
        if (
            not isinstance(item, ProviderPassMetadata)
            or item.attempt_no != expected_attempt_no
        ):
            raise PipelineFailure("PROVIDER_RESULT_INVALID")


def _validate_provider_call_contract(result: ProviderCallResult[object]) -> None:
    if type(result.usage_complete) is not bool or not isinstance(
        result.usage, ProviderUsage
    ):
        raise PipelineFailure("PROVIDER_RESULT_INVALID")
    _validate_usage(result.usage)
    _validate_attempt_metadata_contract(
        result.attempt_metadata,
        final_metadata=result.metadata,
        attempt_count=result.usage.attempt_count,
    )


def _append_failure_attempt_metadata(
    ledger: _Ledger, error: ProviderFailure
) -> None:
    _validate_provider_failure_contract(error)
    _append_attempt_metadata(
        ledger,
        error.attempt_metadata,
        final_metadata=error.metadata,
        attempt_count=error.attempt_count,
    )


def _validate_provider_failure_contract(error: ProviderFailure) -> None:
    if (
        type(error.usage_complete) is not bool
        or isinstance(error.attempt_count, bool)
        or not isinstance(error.attempt_count, int)
        or error.attempt_count < 0
        or (not error.usage_complete and error.attempt_count < 1)
    ):
        raise PipelineFailure("PROVIDER_RESULT_INVALID")
    if error.usage is not None:
        if not isinstance(error.usage, ProviderUsage):
            raise PipelineFailure("PROVIDER_USAGE_INVALID")
        _validate_usage(error.usage)
        if error.usage.attempt_count != error.attempt_count:
            raise PipelineFailure("PROVIDER_RESULT_INVALID")
    _validate_attempt_metadata_contract(
        error.attempt_metadata,
        final_metadata=error.metadata,
        attempt_count=error.attempt_count,
    )


def _estimate_tokens(value: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("o200k_base").encode(value))
    except Exception:
        return max(1, (len(value) + 3) // 4)


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _serialize_documents(documents: Sequence[DocumentInput]) -> list[dict[str, object]]:
    return [
        {
            "document_key": item.document_key,
            "pages": [
                {
                    "document_key": page.document_key,
                    "pdf_page": page.pdf_page,
                    "text": page.text,
                    "quality": page.quality,
                    # Base64 is never part of the text estimate or cap.  A
                    # provider-owned estimate reserves documented vision input.
                    "image_data_url": "<image-data-url-omitted>" if page.image_data_url else None,
                }
                for page in item.pages
            ],
        }
        for item in documents
    ]


def _serialize_provider_request(method: str, **payload: object) -> str:
    """Exact stable logical request envelope used for cap and token estimate."""
    normalized = dict(payload)
    documents = normalized.get("documents")
    if documents is not None:
        normalized["documents"] = _serialize_documents(documents)  # type: ignore[arg-type]
    for key in ("inventory", "core", "rules"):
        value = normalized.get(key)
        if hasattr(value, "model_dump"):
            normalized[key] = value.model_dump(mode="json")  # type: ignore[union-attr]
    return _stable_json({"method": method, **normalized})


def _document_key(document: PropertyDocument) -> str:
    return str(document.id)


def _page_quality(source: str) -> float:
    return {"TEXT_LAYER": 1.0, "OCR": 0.85}.get(source, 0.0)


def _routing_evidence_page_keys(
    output: ReconciliationOutput,
    inventory: DocumentInventoryOutput,
    issues: Sequence[ValidationIssue],
) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    """Build the router's page-only signals without carrying quote or page text."""
    cited: set[tuple[str, int]] = set()
    blockers: set[tuple[str, int]] = {
        (issue.document_key, issue.pdf_page)
        for issue in issues
        if issue.severity == "BLOCKER"
        and isinstance(issue.document_key, str)
        and issue.document_key
        and isinstance(issue.pdf_page, int)
        and not isinstance(issue.pdf_page, bool)
        and issue.pdf_page >= 1
    }
    blocked_subjects = {
        issue.subject_key
        for issue in issues
        if issue.severity == "BLOCKER" and issue.subject_key
    }

    def add_evidence(items, *, blocker_subjects: set[str] | None = None) -> None:
        for item in items:
            is_blocked = blocker_subjects is not None and item.subject_key in blocker_subjects
            for evidence in item.evidence:
                key = (evidence.document_key, evidence.pdf_page)
                cited.add(key)
                if is_blocked:
                    blockers.add(key)

    reconciliation_items = [
        *output.facts,
        *output.rules,
        *output.additional_facts,
    ]
    add_evidence(reconciliation_items, blocker_subjects=blocked_subjects)
    for document in inventory.documents:
        for relation in document.relations:
            for evidence in relation.evidence:
                cited.add((evidence.document_key, evidence.pdf_page))
    return cited, blockers


def _merge_inventory(values: Sequence[DocumentInventoryOutput]) -> DocumentInventoryOutput:
    items = {}
    for output in values:
        for item in output.documents:
            key = _stable_json(item.model_dump(mode="json"))
            items.setdefault((item.document_key, key), item)
    # A conflicting inventory entry remains distinguishable instead of first-win.
    return DocumentInventoryOutput(documents=list(sorted(items.values(), key=lambda item: (item.document_key, _stable_json(item.model_dump(mode="json"))))))


def _validate_inventory_documents(inventory: DocumentInventoryOutput, document_ids: dict[str, uuid.UUID]) -> None:
    seen: set[str] = set()
    for item in inventory.documents:
        if item.document_key not in document_ids:
            raise PipelineFailure("UNKNOWN_INVENTORY_DOCUMENT")
        seen.add(item.document_key)
        for relation in item.relations:
            if relation.target_document_key not in document_ids:
                raise PipelineFailure("UNKNOWN_INVENTORY_DOCUMENT")
    if set(document_ids) != seen:
        raise PipelineFailure("MISSING_INVENTORY_DOCUMENT")


def _validate_inventory_evidence(
    inventory: DocumentInventoryOutput,
    page_counts: dict[str, int],
    *,
    evidence_index: EvidenceTextIndex | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for item in inventory.documents:
        for relation in item.relations:
            issues.extend(
                validate_evidence_items(
                    item.document_key,
                    relation.evidence,
                    page_counts,
                    evidence_index=evidence_index,
                )
            )
    return issues


def _merge_core(values: Sequence[CoreExtractionOutput]) -> CoreExtractionOutput:
    facts, additional = _merge_facts([output.facts for output in values]), _merge_additional([output.additional_facts for output in values])
    return CoreExtractionOutput(facts=facts, additional_facts=additional)


def _merge_rules(values: Sequence[RuleExtractionOutput]) -> RuleExtractionOutput:
    rules = _merge_models([output.rules for output in values])
    additional = _merge_additional([output.additional_facts for output in values])
    return RuleExtractionOutput(rules=rules, additional_facts=additional)


def _merge_facts(groups: Sequence[Sequence[FactPayload]]) -> list[FactPayload]:
    merged: dict[tuple[str, str, str], FactPayload] = {}
    for group in groups:
        for item in group:
            key = (item.subject_key, item.field, _normalized_json(item))
            existing = merged.get(key)
            if existing is None:
                merged[key] = item.model_copy(deep=True)
            else:
                existing.evidence[:] = _distinct_evidence([*existing.evidence, *item.evidence])
                existing.confidence = min(existing.confidence, item.confidence)
                existing.issues[:] = sorted(set(existing.issues).union(item.issues))
    return [merged[key] for key in sorted(merged)]


def _merge_additional(groups: Sequence[Sequence[AdditionalFactPayload]]) -> list[AdditionalFactPayload]:
    return _merge_models(groups)


def _merge_models(groups):
    merged: dict[str, Any] = {}
    for group in groups:
        for item in group:
            key = _stable_json(item.model_dump(mode="json", exclude={"evidence", "confidence"}))
            existing = merged.get(key)
            if existing is None:
                merged[key] = item.model_copy(deep=True)
            else:
                existing.evidence[:] = _distinct_evidence([*existing.evidence, *item.evidence])
                existing.confidence = min(existing.confidence, item.confidence)
    return [merged[key] for key in sorted(merged)]


def _distinct_evidence(evidence):
    values = {}
    for item in evidence:
        values.setdefault(_stable_json(item.model_dump(mode="json")), item)
    return [values[key] for key in sorted(values)]


def _merge_recheck(output: ReconciliationOutput, recheck: TargetedRecheckOutput) -> ReconciliationOutput:
    facts = _merge_facts([output.facts, recheck.facts])
    rules = _merge_models([output.rules, recheck.rules])
    additional = _merge_additional([output.additional_facts, recheck.additional_facts])
    return ReconciliationOutput(
        facts=facts,
        rules=rules,
        additional_facts=additional,
        unresolved_issues=sorted(set(output.unresolved_issues).difference(recheck.resolved_issue_codes).union(recheck.unresolved_issues)),
        mea_set_status=output.mea_set_status,
    )


def _merge_rechecks(values: Sequence[TargetedRecheckOutput]) -> TargetedRecheckOutput:
    return TargetedRecheckOutput(
        facts=_merge_facts([item.facts for item in values]),
        rules=_merge_models([item.rules for item in values]),
        additional_facts=_merge_additional([item.additional_facts for item in values]),
        resolved_issue_codes=sorted({code for item in values for code in item.resolved_issue_codes}),
        unresolved_issues=sorted({code for item in values for code in item.unresolved_issues}),
    )


def _merge_reconciliations(values: Sequence[ReconciliationOutput]) -> ReconciliationOutput:
    statuses = {item.mea_set_status for item in values}
    return ReconciliationOutput(
        facts=_merge_facts([item.facts for item in values]),
        rules=_merge_models([item.rules for item in values]),
        additional_facts=_merge_additional([item.additional_facts for item in values]),
        unresolved_issues=sorted({issue for item in values for issue in item.unresolved_issues}),
        mea_set_status=next(iter(statuses)) if len(statuses) == 1 else "UNKNOWN",
    )


def _declared_denominator(output: ReconciliationOutput) -> int | None:
    values = {
        fact.normalized_value.value
        for fact in output.facts
        if fact.subject_key.startswith("property:") and fact.field == "mea_denominator" and hasattr(fact.normalized_value, "value")
    }
    return next(iter(values)) if len(values) == 1 and isinstance(next(iter(values)), int) and not isinstance(next(iter(values)), bool) else None


def _candidate_drafts(imported, inventory, output, validation_issues, document_ids, property_row, units):
    grouped: dict[tuple[str, str], list[FactPayload]] = defaultdict(list)
    for fact in output.facts:
        grouped[(fact.subject_key, fact.field)].append(fact)
    subject_issues: dict[str, list[ValidationIssue]] = defaultdict(list)
    for issue in validation_issues:
        if issue.subject_key:
            subject_issues[issue.subject_key].append(issue)
    drafts: list[DraftCandidate] = []
    draft_issues: list[DraftIssue] = []
    existing_by_number: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        existing_by_number[_normalize_unit_number(unit.number)].append(unit)

    unit_groups: dict[str, dict[str, list[FactPayload]]] = defaultdict(dict)
    special_subjects: dict[str, dict[str, list[FactPayload]]] = defaultdict(dict)
    for (subject, field), facts in grouped.items():
        if subject.startswith("unit:"):
            unit_groups[subject][field] = facts
            continue
        if subject.startswith(("legal_reference:", "assignment:")):
            special_subjects[subject][field] = facts
            continue
        draft = (
            _property_fact_candidate(subject, field, facts, property_row, subject_issues[subject], document_ids)
            if subject.startswith("property:") and property_row is not None
            else _fact_candidate(subject, field, facts, subject_issues[subject], document_ids)
        )
        drafts.append(draft)
    for subject, fields in sorted(special_subjects.items()):
        facts = [fact for values in fields.values() for fact in values]
        candidate_type = "LEGAL_REFERENCE" if subject.startswith("legal_reference:") else "ASSIGNMENT"
        stable = f"legal-reference:{subject}" if candidate_type == "LEGAL_REFERENCE" else f"assignment:{subject}"
        if candidate_type == "ASSIGNMENT" and "unit_subject_key" in fields:
            reference_values = {
                getattr(fact.normalized_value, "value", None)
                for fact in fields["unit_subject_key"]
            }
            if len(reference_values) != 1 or next(iter(reference_values)) not in unit_groups:
                subject_issues[subject].append(
                    ValidationIssue(
                        code="ASSIGNMENT_UNIT_REFERENCE_INVALID",
                        severity="BLOCKER",
                        message="Die Zuordnung verweist nicht eindeutig auf eine extrahierte Einheit.",
                        subject_key=subject,
                    )
                )
        proposed = {field: _value_or_alternatives(values) for field, values in sorted(fields.items())}
        has_conflict = any(len({_normalized_json(item) for item in values}) > 1 for values in fields.values())
        candidate = _draft(stable, candidate_type, subject, None, "CREATE", None, proposed, min(Decimal(str(item.confidence)) for item in facts), _evidence(facts, document_ids), subject_issues[subject], has_conflict)
        drafts.append(candidate)
        for issue in subject_issues[subject]:
            draft_issues.append(DraftIssue(issue.code, issue.severity, issue.message, candidate.stable_key))
    for subject, fields in sorted(unit_groups.items()):
        number_facts = fields.get("number", [])
        numbers = {_normalized_json(fact) for fact in number_facts}
        number = None
        if len(numbers) == 1 and number_facts and isinstance(number_facts[0].normalized_value.value, str):
            number = _normalize_unit_number(number_facts[0].normalized_value.value)
        matches = existing_by_number.get(number or "", []) if imported.mode == "EXISTING_PROPERTY" else []
        if imported.mode == "EXISTING_PROPERTY" and len(matches) > 1:
            code = "AMBIGUOUS_UNIT_MATCH"
            issue = ValidationIssue(code, "BLOCKER", "Die vorhandene Einheit konnte nicht eindeutig zugeordnet werden.", subject)
            subject_issues[subject].append(issue)
            candidate = _unit_create_candidate(subject, fields, subject_issues[subject], document_ids)
            drafts.append(candidate)
            draft_issues.append(DraftIssue(code, "BLOCKER", issue.message, candidate.stable_key))
        elif imported.mode == "EXISTING_PROPERTY" and not matches:
            # A zero match is a reviewed, explicit new-unit proposal.  It is
            # not a failed lookup and must not carry an unresolvable blocker.
            drafts.append(_unit_create_candidate(subject, fields, subject_issues[subject], document_ids))
        elif matches:
            for field, facts in sorted(fields.items()):
                candidate = _matched_unit_candidate(subject, field, facts, matches[0], property_row, subject_issues[subject], document_ids)
                drafts.append(candidate)
                for issue in subject_issues[subject]:
                    if field == "mea" and issue.code == "MEA_CANONICAL_SCALE_UNCONFIRMED":
                        draft_issues.append(DraftIssue(issue.code, issue.severity, issue.message, candidate.stable_key))
        else:
            drafts.append(_unit_create_candidate(subject, fields, subject_issues[subject], document_ids))
    rule_groups: dict[str, list[tuple[dict[str, object], float, list[object]]]] = defaultdict(list)
    for rule in output.rules:
        rule_groups[rule.subject_key].append(
            (_semantic_model_payload(rule), rule.confidence, rule.evidence)
        )
    additional_groups: dict[str, list[tuple[dict[str, object], float, list[object]]]] = defaultdict(list)
    for fact in output.additional_facts:
        additional_groups[fact.subject_key].append(
            (
                {
                    "topic": "sonstige_relevante_information",
                    "title": fact.label,
                    "summary": fact.value,
                    # Additional facts do not establish that a rule applies to
                    # every unit. Keep their model-supplied subject as a narrow,
                    # explicit scope until a reviewer can refine it.
                    "scope": {"additional_scope": [fact.subject_key]},
                    "effect": {"administrative_note": fact.management_relevance},
                    "clarification_needed": fact.clarification_needed,
                },
                fact.confidence,
                fact.evidence,
            )
        )
    rule_candidates_by_subject: dict[str, list[DraftCandidate]] = defaultdict(list)
    for subject in sorted(rule_groups):
        rule_candidates_by_subject[subject].extend(
            _rule_variant_candidates(
                "rule", subject, rule_groups[subject], subject_issues[subject], document_ids
            )
        )
    for subject in sorted(additional_groups):
        rule_candidates_by_subject[subject].extend(
            _rule_variant_candidates(
                "additional-rule", subject, additional_groups[subject], subject_issues[subject], document_ids
            )
        )
    for subject, candidates in sorted(rule_candidates_by_subject.items()):
        drafts.extend(candidates)
        if len(candidates) < 2:
            continue
        message = "Zwei Regelvorschläge betreffen denselben Gegenstand und müssen getrennt geprüft werden."
        for candidate in candidates:
            draft_issues.append(
                DraftIssue("DUPLICATE_RULE_SUBJECT", "BLOCKER", message, candidate.stable_key)
            )
    relation_groups: dict[str, list[tuple[object, object]]] = defaultdict(list)
    for source in inventory.documents:
        for relation in source.relations:
            stable = f"relation:{source.document_key}:{relation.relation_type}:{relation.target_document_key}"
            relation_groups[stable].append((relation, source))
    for stable, pairs in sorted(relation_groups.items()):
        relation_values = [(_relation_payload(pair[0]), 1.0, pair[0].evidence) for pair in pairs]
        subject = pairs[0][1].document_key
        candidate = _grouped_payload_candidate(stable, "DOCUMENT_RELATION", subject, relation_values, subject_issues[subject], document_ids)
        drafts.append(candidate)
        for issue in subject_issues[subject]:
            if issue.code in {"UNKNOWN_EVIDENCE_DOCUMENT", "EVIDENCE_PAGE_OUT_OF_RANGE"}:
                draft_issues.append(DraftIssue(issue.code, issue.severity, issue.message, candidate.stable_key))
        if len({_stable_json(item[0]) for item in relation_values}) > 1:
            draft_issues.append(DraftIssue("CONFLICTING_ALTERNATIVES", "BLOCKER", "Für dieselbe Dokumentbeziehung liegen widersprüchliche Alternativen vor.", candidate.stable_key))
    if property_row is not None and property_row.mea_scale_status == "CONFIRMED" and property_row.mea_denominator:
        target = _declared_denominator(output)
        if target is not None and target != property_row.mea_denominator:
            denominator_facts = [
                fact for fact in output.facts
                if fact.subject_key.startswith("property:") and fact.field == "mea_denominator"
            ]
            drafts.append(
                _draft(
                    f"mea-rescale:{property_row.id}:{target}",
                    "MEA_RESCALE",
                    f"property:{property_row.id}",
                    "mea_denominator",
                    "UPDATE",
                    {"kind": "INTEGER", "value": property_row.mea_denominator},
                    {"kind": "INTEGER", "value": target},
                    min(Decimal(str(fact.confidence)) for fact in denominator_facts),
                    _evidence(denominator_facts, document_ids),
                    (),
                    False,
                )
            )
    first_by_subject: dict[str, str] = {}
    known_conflict_stables = {
        issue.candidate_stable_key
        for issue in draft_issues
        if issue.code == "CONFLICTING_ALTERNATIVES"
    }
    for draft in drafts:
        first_by_subject.setdefault(draft.subject_key, draft.stable_key)
        # A conflict is a property of this exact stable candidate, not merely
        # of its broad subject.  One property can legitimately contain several
        # independently conflicting fields, each requiring review.
        if (
            _contains_conflicting_alternatives(draft.proposed_value_json)
            and draft.stable_key not in known_conflict_stables
        ):
            draft_issues.append(
                DraftIssue(
                    "CONFLICTING_ALTERNATIVES",
                    "BLOCKER",
                    "Für denselben Vorschlag liegen widersprüchliche Alternativen vor.",
                    draft.stable_key,
                )
            )
            known_conflict_stables.add(draft.stable_key)
    if (
        property_row is not None
        and _declared_denominator(output) is not None
        and property_row.mea_scale_status != "CONFIRMED"
    ):
        property_subject = next((subject for subject in first_by_subject if subject.startswith("property:")), None)
        if property_subject is not None:
            draft_issues.append(
                DraftIssue(
                    "MEA_CANONICAL_SCALE_UNCONFIRMED",
                    "BLOCKER",
                    "Die vorhandene Miteigentumsskala ist nicht bestätigt.",
                    first_by_subject[property_subject],
                )
            )
    known_issue_bindings = {(item.code, item.candidate_stable_key) for item in draft_issues}
    for issue in validation_issues:
        stable_key = first_by_subject.get(issue.subject_key) if issue.subject_key else None
        if (issue.code, stable_key) in known_issue_bindings:
            continue
        details_json: dict[str, object] | None = None
        if issue.document_key is not None and issue.pdf_page is not None:
            details_json = {
                "document_key": issue.document_key,
                "pdf_page": issue.pdf_page,
            }
        elif issue.subject_key is None and issue.code in {
            "MEA_DECLARED_DENOMINATOR_INVALID",
            "MIXED_MEA_DENOMINATOR",
            "MEA_TOTAL_INVALID",
        }:
            details_json = {"mea_set_status": output.mea_set_status}
        draft_issues.append(
            DraftIssue(
                issue.code,
                issue.severity,
                issue.message,
                stable_key,
                details_json,
            )
        )
    unique_issues: dict[tuple[str, str, str, str | None], DraftIssue] = {}
    for issue in draft_issues:
        unique_issues.setdefault(
            (issue.code, issue.severity, issue.message, issue.candidate_stable_key), issue
        )
    return (
        tuple(sorted(drafts, key=lambda item: item.stable_key)),
        tuple(unique_issues[key] for key in sorted(unique_issues, key=str)),
    )


def _fact_candidate(subject, field, facts, issues, document_ids):
    candidate_type = "PROPERTY_FIELD" if subject.startswith("property:") else "LEGAL_REFERENCE" if subject.startswith("legal_reference:") else "ASSIGNMENT"
    stable = f"fact:{subject}:{field}"
    if candidate_type == "LEGAL_REFERENCE": stable = f"legal-reference:{subject}"
    if candidate_type == "ASSIGNMENT": stable = f"assignment:{subject}"
    values = {_normalized_json(fact): fact for fact in facts}
    has_conflict = len(values) > 1
    proposed: dict[str, Any] = {"alternatives": [json.loads(key) for key in sorted(values)]} if has_conflict else json.loads(next(iter(values)))
    evidence = _evidence([fact for fact in facts], document_ids)
    confidence = min(Decimal(str(fact.confidence)) for fact in facts)
    return _draft(stable, candidate_type, subject, field if candidate_type == "PROPERTY_FIELD" else None, "CREATE", None, proposed, confidence, evidence, issues, has_conflict)


def _property_fact_candidate(subject, field, facts, property_row, issues, document_ids):
    values = {_normalized_json(fact): fact for fact in facts}
    has_conflict = len(values) > 1
    proposed = {"alternatives": [json.loads(key) for key in sorted(values)]} if has_conflict else json.loads(next(iter(values)))
    previous = _property_value(property_row, field)
    operation = "UPDATE" if has_conflict or not _values_semantically_equal(proposed, previous) else "KEEP"
    evidence = _evidence(facts, document_ids)
    return _draft(f"fact:{subject}:{field}", "PROPERTY_FIELD", subject, field, operation, previous, proposed, min(Decimal(str(fact.confidence)) for fact in facts), evidence, issues, has_conflict)


def _unit_create_candidate(subject, fields, issues, document_ids):
    facts = [fact for values in fields.values() for fact in values]
    proposed = {field: _value_or_alternatives(values) for field, values in sorted(fields.items())}
    return _draft(f"unit-create:{subject}", "UNIT_CREATE", subject, None, "CREATE", None, proposed, min(Decimal(str(item.confidence)) for item in facts), _evidence(facts, document_ids), issues, any(len({_normalized_json(item) for item in values}) > 1 for values in fields.values()))


def _matched_unit_candidate(subject, field, facts, unit, property_row, issues, document_ids):
    proposed = _value_or_alternatives(facts)
    previous = _unit_value(unit, field, property_row)
    if field == "mea" and previous is None:
        issues.append(
            ValidationIssue(
                code="MEA_CANONICAL_SCALE_UNCONFIRMED",
                severity="BLOCKER",
                message="Der vorhandene Miteigentumsanteil hat keine bestätigte Vergleichsskala.",
                subject_key=subject,
            )
        )
    has_conflict = isinstance(proposed, dict) and "alternatives" in proposed
    operation = "UPDATE" if has_conflict or not _values_semantically_equal(proposed, previous) else "KEEP"
    return _draft(f"fact:{subject}:{field}", "UNIT_FIELD", subject, field, operation, previous, proposed, min(Decimal(str(item.confidence)) for item in facts), _evidence(facts, document_ids), issues, has_conflict)


def _model_candidate(stable, candidate_type, subject, field, operation, model, confidence, evidence_items, issues, document_ids):
    proposed = model if isinstance(model, dict) else model.model_dump(mode="json")
    return _draft(stable, candidate_type, subject, field, operation, None, proposed, Decimal(str(confidence)), _evidence_from_payload(evidence_items, document_ids), issues, False)


def _grouped_model_candidate(stable, candidate_type, subject, values, issues, document_ids):
    payloads = [(_semantic_model_payload(item), item.confidence, item.evidence) for item in values]
    return _grouped_payload_candidate(stable, candidate_type, subject, payloads, issues, document_ids)


def _rule_variant_candidates(prefix, subject, values, issues, document_ids):
    """Keep semantically different rules independently reviewable and citable."""
    variants: dict[str, list[tuple[dict[str, object], float, list[object]]]] = defaultdict(list)
    for value in values:
        variants[_stable_json(value[0])].append(value)
    multiple = len(variants) > 1
    return tuple(
        _grouped_payload_candidate(
            _rule_variant_stable_key(prefix, subject, semantic_key, multiple),
            "RULE",
            subject,
            variants[semantic_key],
            issues,
            document_ids,
        )
        for semantic_key in sorted(variants)
    )


def _rule_variant_stable_key(prefix: str, subject: str, semantic_key: str, multiple: bool) -> str:
    base = f"{prefix}:{subject}"
    if not multiple:
        return base
    return f"{base}:{hashlib.sha256(semantic_key.encode('utf-8')).hexdigest()[:16]}"


def _grouped_payload_candidate(stable, candidate_type, subject, values, issues, document_ids):
    semantic: dict[str, tuple[dict, list[object], Decimal]] = {}
    for payload, confidence, evidence in values:
        key = _stable_json(payload)
        if key not in semantic:
            semantic[key] = (payload, list(evidence), Decimal(str(confidence)))
        else:
            current, current_evidence, current_confidence = semantic[key]
            semantic[key] = (current, [*current_evidence, *evidence], min(current_confidence, Decimal(str(confidence))))
    has_conflict = len(semantic) > 1
    candidate_issues = list(issues)
    if has_conflict:
        candidate_issues.append(ValidationIssue("CONFLICTING_ALTERNATIVES", "BLOCKER", "Für denselben Vorschlag liegen widersprüchliche Alternativen vor.", subject))
    payload = {"alternatives": [semantic[key][0] for key in sorted(semantic)]} if has_conflict else next(iter(semantic.values()))[0]
    evidence = _evidence_from_payload(
        [item for _, evidence_items, _ in semantic.values() for item in evidence_items],
        document_ids,
    )
    if candidate_type == "RULE":
        evidence = _approval_safe_rule_evidence(evidence)
    confidence = min(item[2] for item in semantic.values())
    return _draft(stable, candidate_type, subject, None, "CREATE", None, payload, confidence, evidence, candidate_issues, has_conflict)


def _draft(stable, candidate_type, subject, field, operation, previous, proposed, confidence, evidence, issues, conflict):
    decision = classify_candidate(CandidateContext(candidate_type, operation, float(confidence), tuple(issue.severity for issue in issues), conflict, field, bool(evidence)))
    return DraftCandidate(stable, candidate_type, subject, field, operation, previous, proposed, confidence, decision.risk_class, decision.requires_individual_review, tuple(evidence))


def _evidence(facts, document_ids):
    return _evidence_from_payload([item for fact in facts for item in fact.evidence], document_ids)


def _evidence_from_payload(items, document_ids):
    values = {}
    for item in items:
        if item.document_key not in document_ids:
            raise PipelineFailure("UNKNOWN_EVIDENCE_DOCUMENT")
        draft = DraftEvidence(document_ids[item.document_key], item.pdf_page, item.printed_page, item.section, item.quote, item.field_path)
        values.setdefault((draft.document_id, draft.pdf_page, draft.printed_page, draft.section_ref, draft.quote, draft.field_path), draft)
    return tuple(values[key] for key in sorted(values, key=str))


def _approval_safe_rule_evidence(evidence):
    """Retain one deterministic genuine citation per persisted rule-source slot.

    The canonical schema permits one source per document/page/field-path. If
    identical semantic extractions cite different text at that same location,
    selecting a deterministic supplied citation avoids inventing a synthetic
    field path or treating a rejected alternative as a source of the rule.
    """
    selected: dict[tuple[uuid.UUID, int, str | None], DraftEvidence] = {}
    for item in evidence:
        slot = (item.document_id, item.pdf_page, item.field_path)
        current = selected.get(slot)
        if current is None or _draft_evidence_key(item) < _draft_evidence_key(current):
            selected[slot] = item
    return tuple(sorted(selected.values(), key=_draft_evidence_key))


def _draft_evidence_key(item: DraftEvidence):
    return (
        str(item.document_id),
        item.pdf_page,
        item.field_path or "",
        item.printed_page or "",
        item.section_ref or "",
        item.quote,
    )


def _normalized_json(fact):
    return _stable_json(fact.normalized_value.model_dump(mode="json"))


def _value_or_alternatives(facts):
    values = {_normalized_json(item) for item in facts}
    return json.loads(next(iter(values))) if len(values) == 1 else {"alternatives": [json.loads(value) for value in sorted(values)]}


def _contains_conflicting_alternatives(value: object) -> bool:
    """Find alternatives even when grouped candidates retain typed field maps."""
    if isinstance(value, dict):
        alternatives = value.get("alternatives")
        if isinstance(alternatives, list) and len(alternatives) > 1:
            return True
        return any(_contains_conflicting_alternatives(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_conflicting_alternatives(item) for item in value)
    return False


def _property_value(property_row, field):
    value = getattr(property_row, field, None)
    if isinstance(value, str):
        return {"kind": "TEXT", "value": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"kind": "INTEGER", "value": value}
    return None


def _unit_value(unit, field, property_row):
    value = getattr(unit, field, None)
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    if field == "mea":
        if property_row is None or property_row.mea_scale_status != "CONFIRMED" or not property_row.mea_denominator:
            return None
        if value is None:
            return None
        if Decimal(value) != Decimal(value).to_integral_value():
            return None
        return {"kind": "MEA", "numerator": int(value), "denominator": property_row.mea_denominator}
    if isinstance(value, Decimal):
        return {"kind": "DECIMAL", "value": str(value), "unit": "m²"}
    return {"kind": "TEXT", "value": value} if isinstance(value, str) else None


def _semantic_model_payload(item: object) -> dict[str, object]:
    return item.model_dump(mode="json", exclude={"evidence", "confidence"})


def _semantic_model_json(item: object) -> str:
    return _stable_json(_semantic_model_payload(item))


def _relation_payload(relation: object) -> dict[str, object]:
    return relation.model_dump(mode="json", exclude={"evidence"})


def _values_semantically_equal(proposed: object, previous: object) -> bool:
    if not isinstance(proposed, dict) or not isinstance(previous, dict):
        return proposed == previous
    if proposed.get("kind") != previous.get("kind"):
        return False
    if proposed.get("kind") == "DECIMAL":
        try:
            return (
                Decimal(str(proposed["value"])) == Decimal(str(previous["value"]))
                and proposed.get("unit") == previous.get("unit")
            )
        except Exception:
            return False
    return proposed == previous


def _normalize_unit_number(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()
