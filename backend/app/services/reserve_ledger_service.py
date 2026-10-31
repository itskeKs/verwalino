"""Reserve funding is cash; budgets and administrator adjustments stay explicit."""
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from app.core.rbac import ScopeCapability
from app.engine.reserve_ledger import money, plan_reserve_entries, hausgeld_reserve_share, reserve_totals
from app.models.audit_event import AuditEvent
from app.models.reserve_ledger_entry import ReserveLedgerEntry
from app.repositories.audit_repo import AuditRepository
from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
from app.repositories.settlement_repo import SettlementRunRepository
from app.services.financial_scope_service import FinancialScopeService

ZERO = Decimal('0.00')


class ReserveLedgerService:
    def __init__(self, session, org_id, user_id):
        self.session, self.org_id, self.user_id = session, org_id, user_id
        self.repo = ReserveLedgerRepository(session, org_id)
        self.scope = FinancialScopeService(session, org_id, user_id)

    async def subject(self, run_id, write=False):
        await self.scope.lock_inputs()
        run = await SettlementRunRepository(self.session, self.org_id).get_by_id(run_id)
        if run is None or run.run_type not in ('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT'):
            raise HTTPException(404, 'Rücklagenabrechnung nicht gefunden.')
        await self.scope.enter(run, capability=ScopeCapability.PREPARE if write else ScopeCapability.READ,
                               computation=run.status == 'draft')
        if not await self.repo.require_sources(run):
            raise HTTPException(404, 'Rücklagenquellen nicht zugänglich.')
        if write and run.status != 'draft':
            raise HTTPException(409, 'Die Rücklage dieses Zeitraums ist finalisiert.')
        return run

    async def read(self, run):
        if run.status == 'finalized':
            ledger = (run.snapshot_payload or {}).get('reserve_ledger')
            if ledger is not None:
                return {**ledger, 'frozen': True}
            # Existing immutable snapshots are never rewritten or re-derived.
            dev = (run.snapshot_payload or {}).get('result', {}).get('reserve_development')
            if dev is None:
                raise HTTPException(409, 'Dieser historische Snapshot enthält keine Rücklagenentwicklung.')
            return {**dev, 'overrides_total': '0.00', 'planned_contributions_total': None,
                    'overdrawn': Decimal(dev['closing_balance']) < 0, 'entries': [],
                    'booking_basis': 'LEGACY_SNAPSHOT', 'frozen': True, 'opening_source_run_id': None}
        try:
            finalized = await self.repo.finalized(run)
        except DBAPIError as exc:
            if getattr(exc.orig, 'sqlstate', None) == '42501':
                raise HTTPException(404, 'Rücklagenabrechnung nicht gefunden.') from None
            raise
        current_scope = await self.repo.current_scope(run)
        current_id = current_scope.id if current_scope else None
        same = [r for r, sphere in finalized if (sphere.id if sphere else None) == current_id
                and r.period_start == run.period_start and r.period_end == run.period_end]
        if same:
            return await self.read(same[0])
        compatible = [(r, sphere) for r, sphere in finalized if (sphere.id if sphere else None) == current_id or (
            sphere is not None and current_scope is not None and sphere.whole_property and current_scope.whole_property
            and sphere.scope_type == current_scope.scope_type == 'WEG' and r.management_mandate_id == run.management_mandate_id)]
        prior = next((r for r, sphere in compatible if r.period_end < run.period_start), None)
        opening = ZERO
        if prior:
            if prior.period_end != run.period_start - timedelta(days=1):
                raise HTTPException(409, 'Bitte schließen Sie die Lücke zum letzten finalisierten Rücklagenzeitraum.')
            opening = Decimal((await self.read(prior))['closing_balance'])
        plans = await self.repo.plans(run)
        if run.economic_plan_id:
            plans = [p for p in plans if p.id == run.economic_plan_id]
        if len(plans) > 1 or run.economic_plan_id and not plans:
            raise HTTPException(422, 'Genau ein aktueller, finalisierter Wirtschaftsplan für diesen Zeitraum ist erforderlich.')
        plan = plans[0] if plans else None
        rows = plan_reserve_entries(plan.snapshot_payload, plan.reserve_contribution_total, plan.period_start) if plan else []
        owner_units = await self.repo.owner_units(run)
        # Frozen ledgers identify consumed bookings, including earlier late cash.
        history = [r for r, _ in compatible if r.period_end < run.period_start]
        frozen_cash = {row['payment_id']: row for r in history
                       for row in (r.snapshot_payload or {}).get('reserve_ledger', {}).get('entries', [])
                       if row.get('payment_id')}
        consumed = set(frozen_cash)
        start = min((r.period_start for r in history), default=run.period_start)
        entries = await self.repo.entries(run)
        funding = {entry.payment_id: entry for entry in entries if entry.component == 'FUNDING'}
        contributions, withdrawals, overrides, late_cash = ZERO, ZERO, ZERO, ZERO
        unlinked = []
        payments = await self.repo.payments(run, start)
        payments = [p for p in payments if p.value_date >= run.period_start or (
            str(p.id) not in consumed and any(r.period_start <= p.value_date <= r.period_end
                and ('reserve_ledger' in (r.snapshot_payload or {}) or p.booked_at > r.finalized_at)
                for r in history))]
        payment_ids = {p.id for p in payments}
        cash_plans = await self.repo.cash_plans(run, start) if any(
            p.payment_type == 'HAUSGELD_VORSCHUSS' and (plan is None or p.value_date < run.period_start)
            for p in payments) else []
        plan_rows = {plan.id: tuple(rows)} if plan else {}
        for payment in payments:
            signed = payment.amount if payment.direction == 'IN' else -payment.amount
            owner = str(payment.owner_profile_id)
            actual = None
            if payment.payment_type == 'RUECKLAGENANTEIL':
                actual = signed
            elif payment.payment_type == 'HAUSGELD_VORSCHUSS':
                original = frozen_cash.get(str(payment.reverses_id))
                if original and (original['kind'] == 'CONTRIBUTION' or original.get('component') == 'CONTRIBUTION'):
                    # Later sales and corrected plans cannot change frozen cash.
                    actual = -Decimal(original['amount'])
                else:
                    cash_plan = plan
                    if cash_plan is None or payment.value_date < run.period_start:
                        source_id = next(((r.snapshot_payload or {}).get('reserve_ledger', {}).get('economic_plan_id')
                            for r in history if r.period_start <= payment.value_date <= r.period_end
                            and (r.snapshot_payload or {}).get('reserve_ledger', {}).get('economic_plan_id')), None)
                        cash_plan = next((p for p in cash_plans if str(p.id) == source_id), None) if source_id else next(
                            (p for p in cash_plans if p.period_start <= payment.value_date <= p.period_end), None)
                        if source_id and cash_plan is None:
                            raise HTTPException(404, 'Rücklagenabrechnung nicht gefunden.')
                    if cash_plan and cash_plan.id not in plan_rows:
                        plan_rows[cash_plan.id] = plan_reserve_entries(
                            cash_plan.snapshot_payload, cash_plan.reserve_contribution_total, cash_plan.period_start)
                    planned = plan_rows[cash_plan.id] if cash_plan else []
                    payload = cash_plan.snapshot_payload if cash_plan else {'inputs': {'owner_periods': []}, 'result': {'owners': []}}
                    units = owner_units.get(owner, set()) | {o['unit_id'] for o in payload['inputs']['owner_periods']
                                                           if o['owner_profile_id'] == owner}
                    actual = hausgeld_reserve_share(signed, units, [r for r in planned if r['kind'] == 'PLANNED'], payload['result']['owners'])
            late = payment.value_date < run.period_start
            if actual is not None:
                if late:
                    late_cash += actual
                else:
                    contributions += actual
                rows.append({'kind': 'LATE_CASH' if late else 'CONTRIBUTION', **({'component': 'CONTRIBUTION'} if late else {}), 'date': str(payment.value_date), 'amount': format(actual, '.2f'),
                             'payment_id': str(payment.id), 'bank_account_id': str(payment.bank_account_id),
                             'owner_profile_id': owner})
            link = funding.get(payment.reverses_id or payment.id)
            if payment.payment_type == 'RUECKLAGENENTNAHME':
                amount = -signed
                if late:
                    late_cash -= amount
                else:
                    withdrawals += amount
                rows.append({'kind': 'LATE_CASH' if late else 'WITHDRAWAL', **({'component': 'WITHDRAWAL'} if late else {}), 'date': str(payment.value_date), 'amount': format(-amount if late else amount, '.2f'),
                             'payment_id': str(payment.id), 'cost_entry_id': str(link.cost_entry_id) if link else None,
                             'register_entry_id': str(link.register_entry_id) if link else None,
                             'reason': link.reason if link else 'Kostenbeleg und Beschluss noch nicht verknüpft'})
                if link is None and not (payment.reversed_by_id in payment_ids or payment.reverses_id in payment_ids):
                    unlinked.append(str(payment.id))
        for entry in entries:
            if entry.component == 'FUNDING' or not run.period_start <= entry.entry_date <= run.period_end:
                continue
            if entry.component == 'OPENING':
                opening += entry.amount
            elif entry.component == 'CONTRIBUTION':
                contributions += entry.amount
            elif entry.component == 'WITHDRAWAL':
                withdrawals += entry.amount
            else:
                overrides += entry.amount
            rows.append({'kind': 'OVERRIDE', 'component': entry.component, 'date': str(entry.entry_date),
                         'amount': format(entry.amount, '.2f'), 'reason': entry.reason, 'id': str(entry.id)})
        return {**reserve_totals(opening, contributions, withdrawals, overrides, late_cash),
                'planned_contributions_total': format(money(plan.reserve_contribution_total), '.2f') if plan else None,
                'economic_plan_id': str(plan.id) if plan else None, 'economic_plan_sha256': plan.snapshot_sha256 if plan else None,
                'booking_basis': 'CASH', 'frozen': False,
                'unlinked_payment_ids': unlinked,
                'opening_source_run_id': str(prior.id) if prior else None,
                'entries': sorted(rows, key=lambda row: (row['date'], row['kind'], row.get('id', ''), row.get('payment_id', ''), row.get('unit_id', ''), row.get('owner_profile_id', '')))}

    async def add(self, run, component, amount, reason, payment_id=None, cost_entry_id=None, register_entry_id=None):
        ledger = await self.read(run)
        if ledger['frozen']:
            raise HTTPException(409, 'Die Rücklage dieses Zeitraums ist finalisiert.')
        if not reason.strip():
            raise HTTPException(422, 'Bitte begründen Sie die Rücklagenkorrektur.')
        try:
            amount = money(amount)
        except ValueError:
            raise HTTPException(422, 'Bitte geben Sie einen endlichen Betrag ein.') from None
        if component == 'FUNDING':
            sources = await self.repo.funding_sources(payment_id, cost_entry_id, register_entry_id)
            if any(source is None or source.property_id != run.property_id for source in sources):
                raise HTTPException(404, 'Finanzierungsbeleg nicht gefunden.')
            payment, cost, register = sources
            if (payment.status != 'booked' or payment.direction != 'OUT' or payment.payment_type != 'RUECKLAGENENTNAHME'
                    or payment.reverses_id
                    or cost.status != 'confirmed' or cost.superseded_by_id or cost.gross_amount < payment.amount
                    or not any(row.get('payment_id') == str(payment.id) for row in ledger['entries'])):
                raise HTTPException(422, 'Bitte wählen Sie eine aktive Auszahlung und einen bestätigten Kostenbeleg.')
            if any(e.payment_id == payment_id for e in await self.repo.entries(run)):
                raise HTTPException(409, 'Diese Auszahlung ist bereits der Rücklage zugeordnet.')
            amount = ZERO
        elif any((payment_id, cost_entry_id, register_entry_id)):
            raise HTTPException(422, 'Belegverknüpfungen sind nur für eine finanzierte Entnahme zulässig.')
        entry = await self.repo.create(ReserveLedgerEntry(
            property_id=run.property_id, settlement_run_id=run.id,
            entry_date=run.period_start if component == 'OPENING' else run.period_end,
            component=component, amount=amount, reason=reason.strip(), created_by=self.user_id,
            payment_id=payment_id, cost_entry_id=cost_entry_id, register_entry_id=register_entry_id))
        await AuditRepository(self.session, self.org_id).create(AuditEvent(
            actor_user_id=self.user_id, entity_type='settlement_run', entity_id=run.id,
            action='reserve.override_added', payload_diff={'entry_id': str(entry.id), 'component': component,
                'amount': format(amount, '.2f'), 'reason': reason.strip()}))
        return entry

    async def apply_typed_overrides(self, run, values):
        ledger = await self.read(run)
        fields = {'reserve_opening_balance': ('OPENING', 'opening_balance'),
                  'reserve_withdrawals_total': ('WITHDRAWAL', 'withdrawals_total'),
                  'reserve_contributions_total': ('CONTRIBUTION', 'contributions_total')}
        for field, (component, total) in fields.items():
            value = getattr(values, field, None)
            if value is None:
                continue
            reason = values.reserve_override_reason
            if not reason or not reason.strip():
                raise HTTPException(422, 'Manuelle Rücklagenwerte benötigen eine Begründung.')
            delta = money(value) - Decimal(ledger[total])
            if delta:
                await self.add(run, component, delta, reason)
                ledger = await self.read(run)
        return ledger
