"""Unknown is never zero. Coverage is explicit and invalidated by source changes."""
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
import re
import uuid
from fastapi import HTTPException
from app.core.config import settings
from app.core.rbac import ScopeCapability
from app.services.financial_scope_service import FinancialScopeService
from app.models.coverage_declaration import CoverageDeclaration
from app.models.audit_event import AuditEvent
from app.repositories.audit_repo import AuditRepository
from app.repositories.coverage_declaration_repo import CoverageDeclarationRepository
from app.repositories.invoice_document_repo import InvoiceDocumentRepository


def normalize_values(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: normalize_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_values(item) for item in value]
    if isinstance(value, Decimal) or isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        number = Decimal(value)
        if not number.is_finite():
            raise HTTPException(422, "Financial amounts must be finite")
        return format(number.normalize(), "f")
    if isinstance(value, (uuid.UUID, date)):
        return str(value)
    return value


def rental_values(run):
    return {"advance_payments_total": run.advance_payments_total, "advance_payments_source": run.advance_payments_source}


def incomplete(code, component):
    raise HTTPException(422, {"code": "financial_inputs_incomplete", "blockers": [
        {"code": code, "component": component, "action": "Review complete period inputs and record a coverage declaration"}]})


class FinancialInputPolicy:
    def __init__(self, session, org_id, user_id):
        self.repo = CoverageDeclarationRepository(session, org_id)
        self.documents = InvoiceDocumentRepository(session, org_id)
        self.user_id = user_id
        self.session, self.org_id = session, org_id
        self.audit = AuditRepository(session, org_id)

    @staticmethod
    def component(subject, kind):
        return "budget_inputs" if kind == "economic_plan" else subject.run_type

    def validate_values(self, subject, values, kind):
        values = normalize_values(values)
        reserve_fields = {'reserve_opening_balance', 'reserve_withdrawals_total', 'reserve_contributions_total'}
        if any(value is None for key, value in values.items() if key not in reserve_fields):
            incomplete("missing_input", self.component(subject, kind))
        if any(values.get(key) is not None for key in reserve_fields) and not values.get('reserve_override_reason', '').strip():
            raise HTTPException(422, {'code': 'RESERVE_CORRECTION_REASON_REQUIRED', 'message': 'Manuelle Rücklagenwerte benötigen eine Begründung.'})
        if kind != "economic_plan" and subject.run_type == "BK_MIETER":
            if subject.advance_payments_total is None:
                incomplete("unknown_advance_payments", "advance_payments")
        return values

    async def source_revision(self, subject):
        context = {key: str(getattr(subject, key, None)) for key in (
            "property_id", "management_mandate_id", "unit_id", "lease_agreement_id",
            "tenant_profile_id", "economic_plan_id", "plan_year", "period_start", "period_end")}
        context["sources"] = await self.repo.source_revision(subject.property_id,
            reserve=getattr(subject, 'run_type', None) in ('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT'))
        if getattr(subject, "economic_plan_id", None):
            plan = await self.repo.linked_plan(subject.economic_plan_id)
            context["economic_plan_revision"] = plan["revision"] if plan else None
        return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()

    async def validate_scope_coverage(self, subject, kind, values):
        if kind == "settlement" and subject.run_type == "BK_MIETER" and subject.advance_payments_source == "DERIVED":
            from app.services.payment_service import PaymentService
            current = await PaymentService(self.session, self.org_id, self.user_id).derive_nk_advance_total_for_period(
                subject.lease_agreement_id, subject.period_start, subject.period_end)
            if current != subject.advance_payments_total:
                incomplete("derived_total_stale", "advance_payments")
        if kind == "settlement" and subject.run_type == "WEG_JAHRESABRECHNUNG":
            plan = await self.repo.linked_plan(subject.economic_plan_id)
            if not plan or plan["status"] != "finalized" or plan["superseded_by_id"] is not None:
                incomplete("linked_plan_not_current", "plan_advances")
            if plan["property_id"] != subject.property_id or plan["period_start"] != subject.period_start or plan["period_end"] != subject.period_end:
                incomplete("linked_plan_scope_mismatch", "plan_advances")
            provided = [item["owner_profile_id"] for item in values["paid_advances"]]
            if set(provided) != await self.repo.owner_ids(subject) or len(provided) != len(set(provided)):
                incomplete("owner_payment_coverage_missing", "paid_advances")
        if kind == "settlement" and subject.run_type == "VERMOEGENSBERICHT":
            expected = await self.repo.bank_accounts(subject.property_id)
            provided = [item["bank_account_id"] for item in values["bank_balances"]]
            if set(provided) != set(expected) or len(provided) != len(set(provided)):
                incomplete("bank_balance_coverage_missing", "bank_balances")
            if any(expected[item["bank_account_id"]] != item["account_type"] for item in values["bank_balances"]):
                incomplete("bank_account_type_mismatch", "bank_balances")

    async def attest(self, subject, kind, values, reason, source_document_ids):
        await FinancialScopeService(self.session, self.org_id, self.user_id).enter(
            subject, capability=ScopeCapability.CONFIRM, computation=True)
        if subject.status != "draft":
            if (kind == 'settlement' and subject.run_type in ('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT')
                    and any(normalize_values(values).get(field) is not None for field in (
                        'reserve_opening_balance', 'reserve_withdrawals_total', 'reserve_contributions_total'))):
                raise HTTPException(409, {'code': 'RESERVE_PERIOD_FINALIZED', 'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'})
            raise HTTPException(409, "Only draft inputs can be attested")
        values = self.validate_values(subject, values, kind)
        if not reason.strip():
            raise HTTPException(422, "A coverage reason is required, including for zero")
        for doc_id in source_document_ids:
            if await self.documents.get_by_id(uuid.UUID(doc_id)) is None:
                raise HTTPException(404, "Source document not found")
        await self.repo.lock_sources()
        await self.validate_scope_coverage(subject, kind, values)
        if kind == 'settlement' and subject.run_type in ('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT'):
            from types import SimpleNamespace
            from app.services.reserve_ledger_service import ReserveLedgerService
            typed = SimpleNamespace(**{key: Decimal(value) if key in (
                'reserve_opening_balance', 'reserve_withdrawals_total', 'reserve_contributions_total') and value is not None else value
                for key, value in values.items()})
            await ReserveLedgerService(self.session, self.org_id, self.user_id).apply_typed_overrides(subject, typed)
        revision = await self.source_revision(subject)
        previous = await self.repo.latest(subject.id, kind)
        if previous and previous.values == values and previous.source_revision == revision and previous.reason == reason and previous.attestor_id == self.user_id and previous.source_document_ids == source_document_ids:
            return previous
        declaration = await self.repo.create(CoverageDeclaration(
            settlement_run_id=subject.id if kind == "settlement" else None,
            economic_plan_id=subject.id if kind == "economic_plan" else None,
            property_id=subject.property_id, period_start=subject.period_start, period_end=subject.period_end,
            component=self.component(subject, kind), revision=previous.revision + 1 if previous else 1,
            state="known", values=values, source_document_ids=source_document_ids,
            source_revision=revision, reason=reason, attestor_id=self.user_id))
        await self.audit.create(AuditEvent(actor_user_id=self.user_id,
            entity_type="coverage_declaration", entity_id=declaration.id,
            action="financial_inputs.attested", payload_diff={"subject_id": str(subject.id),
                "subject_kind": kind, "revision": declaration.revision, "source_revision": revision}))
        return declaration

    async def require(self, subject, kind, values):
        await FinancialScopeService(self.session, self.org_id, self.user_id).enter(subject, computation=True)
        if not settings.financial_computation_enabled:
            raise HTTPException(503, "Financial computation is temporarily disabled; existing records remain available")
        values = self.validate_values(subject, values, kind)
        await self.repo.lock_sources()
        await self.validate_scope_coverage(subject, kind, values)
        declaration = await self.repo.latest(subject.id, kind)
        if declaration is None:
            incomplete("coverage_missing", self.component(subject, kind))
        if declaration.values != values or declaration.source_revision != await self.source_revision(subject) or declaration.period_start != subject.period_start or declaration.period_end != subject.period_end:
            incomplete("coverage_stale", self.component(subject, kind))
        return {"id": str(declaration.id), "revision": declaration.revision, "component": declaration.component,
                "state": declaration.state, "values": declaration.values,
                "source_revision": declaration.source_revision, "source_document_ids": declaration.source_document_ids,
                "reason": declaration.reason, "attestor_id": str(declaration.attestor_id),
                "attested_at": declaration.created_at.isoformat(),
                "period_start": str(declaration.period_start), "period_end": str(declaration.period_end)}
