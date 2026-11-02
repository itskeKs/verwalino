"""Reserve funding is cash; budgets and administrator adjustments stay explicit."""
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from app.core.rbac import ScopeCapability
from app.engine.reserve_ledger import money, reserve_totals
from app.models.audit_event import AuditEvent
from app.models.reserve_ledger_entry import ReserveLedgerEntry
from app.repositories.audit_repo import AuditRepository
from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
from app.repositories.settlement_repo import SettlementRunRepository
from app.services.financial_scope_service import FinancialScopeService

ZERO = Decimal('0.00')
OPENING_IMPORT_ONLY = {'code': 'RESERVE_OPENING_IMPORT_ONLY', 'message': 'Ein Import des Anfangsbestands ist nur im ersten Rücklagenjahr vor der Finalisierung zulässig und nur, wenn kein anderer, abweichender Rücklagenzeitraum besteht.'}
OPENING_IMPORT_EXISTS = {'code': 'RESERVE_OPENING_IMPORT_EXISTS', 'message': 'Nach dem Import eines Anfangsbestands können Sie keinen früheren oder abweichenden, überlappenden Abrechnungszeitraum anlegen.'}
DERIVED_TOTAL_READ_ONLY = {'code': 'RESERVE_DERIVED_TOTAL_READ_ONLY', 'message': 'Zuführungen und Entnahmen werden aus Buchungen abgeleitet; Korrekturen erfolgen mit Bezug im Rücklagenbuch.'}
CONTRIBUTION_EXCEEDS_BOOKED = {'code': 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED', 'message': 'Die Zuführungskorrektur darf den Rücklagenanteil des gebuchten Hausgelds der Einheit nicht überschreiten.'}


def _raise_reserve_opening_error(exc: DBAPIError):
    code = getattr(exc.orig, 'sqlstate', None)
    constraint = getattr(getattr(exc.orig, '__cause__', None), 'constraint_name', None)
    if (code, constraint) == ('23514', 'reserve_opening_import_only'):
        raise HTTPException(422, OPENING_IMPORT_ONLY) from None
    if (code, constraint) == ('23514', 'reserve_opening_import_exists'):
        raise HTTPException(422, OPENING_IMPORT_EXISTS) from None
    if (code, constraint) == ('23514', 'reserve_period_finalized'):
        raise HTTPException(409, {'code': 'RESERVE_PERIOD_FINALIZED', 'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'}) from None


class ReserveLedgerService:
    def __init__(self, session, org_id, user_id):
        self.session, self.org_id, self.user_id = session, org_id, user_id
        self.repo = ReserveLedgerRepository(session, org_id)
        self.scope = FinancialScopeService(session, org_id, user_id)

    async def subject(self, run_id, write=False):
        await self.scope.lock_inputs()
        run = await SettlementRunRepository(self.session, self.org_id).get_by_id(run_id)
        if run is None or run.run_type not in ('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT'):
            raise HTTPException(404, {'code': 'RESERVE_NOT_FOUND', 'message': 'Rücklagenabrechnung nicht gefunden.'})
        await self.scope.enter(run, capability=ScopeCapability.PREPARE if write else ScopeCapability.READ,
                               computation=run.status == 'draft')
        if not await self.repo.require_sources(run):
            raise HTTPException(404, {'code': 'RESERVE_SOURCES_NOT_FOUND', 'message': 'Rücklagenquellen nicht zugänglich.'})
        if write and run.status != 'draft':
            raise HTTPException(409, {'code': 'RESERVE_PERIOD_FINALIZED', 'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'})
        return run

    async def require_period_creation(self, run, scope_id):
        if await self.repo.has_later_opening_import(run, scope_id):
            raise HTTPException(422, OPENING_IMPORT_EXISTS)

    async def read(self, run):
        if run.status == 'finalized':
            ledger = (run.snapshot_payload or {}).get('reserve_ledger')
            if ledger is not None:
                return {**ledger, 'frozen': True}
            # Existing immutable snapshots are never rewritten or re-derived.
            dev = (run.snapshot_payload or {}).get('result', {}).get('reserve_development')
            if dev is None:
                raise HTTPException(409, {'code': 'RESERVE_HISTORY_UNAVAILABLE', 'message': 'Dieser historische Snapshot enthält keine Rücklagenentwicklung.'})
            return {**dev, 'overrides_total': '0.00', 'planned_contributions_total': None,
                    'overdrawn': Decimal(dev['closing_balance']) < 0, 'entries': [],
                    'booking_basis': 'LEGACY_SNAPSHOT', 'frozen': True, 'opening_source_run_id': None}
        try:
            finalized = await self.repo.finalized(run)
        except DBAPIError as exc:
            if getattr(exc.orig, 'sqlstate', None) == '42501':
                raise HTTPException(404, {'code': 'RESERVE_NOT_FOUND', 'message': 'Rücklagenabrechnung nicht gefunden.'}) from None
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
                raise HTTPException(409, {'code': 'RESERVE_PERIOD_GAP', 'message': 'Bitte schließen Sie die Lücke zum letzten finalisierten Rücklagenzeitraum.'})
            opening = Decimal((await self.read(prior))['closing_balance'])
        plans = await self.repo.plans(run)
        if run.economic_plan_id:
            plans = [p for p in plans if p.id == run.economic_plan_id]
        if len(plans) > 1 or run.economic_plan_id and not plans:
            raise HTTPException(422, {'code': 'RESERVE_PLAN_REQUIRED', 'message': 'Genau ein aktueller, finalisierter Wirtschaftsplan für diesen Zeitraum ist erforderlich.'})
        plan = plans[0] if plans else None
        rows = await self.repo.plan_rows(plan) if plan else []
        # Frozen ledgers identify consumed bookings, including earlier late cash.
        history = [r for r, _ in compatible if r.period_end < run.period_start]
        frozen_cash = {row['payment_id']: row for r in history
                       for row in (r.snapshot_payload or {}).get('reserve_ledger', {}).get('entries', [])
                       if row.get('payment_id')}
        consumed = set(frozen_cash)
        start = min((r.period_start for r in history), default=run.period_start)
        entries = await self.repo.entries(run)
        funding = {entry.payment_id: entry for entry in entries if entry.component == 'FUNDING'}
        assignments = {entry.payment_id: entry for entry in entries if entry.component == 'CONTRIBUTION' and entry.payment_id}
        contributions, withdrawals, overrides, late_cash = ZERO, ZERO, ZERO, ZERO
        unlinked = []
        payments = await self.repo.payments(run, start)
        payments = [p for p in payments if p.value_date >= run.period_start or (
            str(p.id) not in consumed and any(r.period_start <= p.value_date <= r.period_end
                and ('reserve_ledger' in (r.snapshot_payload or {}) or p.booked_at > r.finalized_at)
                for r in history))]
        payment_ids = {p.id for p in payments}
        owner_units = await self.repo.owner_units(payments)
        cash_plans = await self.repo.cash_plans(run, start) if any(
            p.payment_type == 'HAUSGELD_VORSCHUSS' and p.value_date < run.period_start
            for p in payments) else []
        for payment in payments:
            signed = payment.amount if payment.direction == 'IN' else -payment.amount
            owner = str(payment.owner_profile_id)
            actual = None
            if payment.payment_type == 'RUECKLAGENANTEIL':
                actual = signed
            elif payment.payment_type == 'HAUSGELD_VORSCHUSS':
                assignment = assignments.get(payment.id) or assignments.get(payment.reverses_id)
                original = frozen_cash.get(str(payment.reverses_id))
                if assignment:
                    actual = assignment.amount if assignment.payment_id == payment.id else -assignment.amount
                elif original and (original['kind'] == 'CONTRIBUTION' or original.get('component') == 'CONTRIBUTION'):
                    # Later sales and corrected plans cannot change frozen cash.
                    actual = -Decimal(original['amount'])
                else:
                    cash_plan = plan
                    if payment.value_date >= run.period_start and cash_plan is None:
                        cash_plan = await self.repo.cash_plan(run, payment.value_date)
                    elif payment.value_date < run.period_start:
                        source_id = next(((r.snapshot_payload or {}).get('reserve_ledger', {}).get('economic_plan_id')
                            for r in history if r.period_start <= payment.value_date <= r.period_end
                            and (r.snapshot_payload or {}).get('reserve_ledger', {}).get('economic_plan_id')), None)
                        cash_plan = next((p for p in cash_plans if str(p.id) == source_id), None) if source_id else next(
                            (p for p in cash_plans if p.period_start <= payment.value_date <= p.period_end), None)
                        if source_id and cash_plan is None:
                            raise HTTPException(404, {'code': 'RESERVE_NOT_FOUND', 'message': 'Rücklagenabrechnung nicht gefunden.'})
                    units = owner_units.get(payment.id, set())
                    actual = await self.repo.hausgeld_share(signed, units, cash_plan) if cash_plan else None
                if actual is None:
                    unlinked.append(str(payment.id))
                    rows.append({'kind': 'UNASSIGNED', 'date': str(payment.value_date), 'amount': format(signed, '.2f'),
                                 'payment_id': str(payment.id), 'bank_account_id': str(payment.bank_account_id),
                                 'owner_profile_id': owner})
            late = payment.value_date < run.period_start
            if actual is not None:
                if late:
                    late_cash += actual
                else:
                    contributions += actual
                rows.append({'kind': 'LATE_CASH' if late else 'CONTRIBUTION', **({'component': 'CONTRIBUTION'} if late else {}), 'date': str(payment.value_date), 'amount': format(actual, '.2f'),
                             'payment_id': str(payment.id), 'bank_account_id': str(payment.bank_account_id),
                             'owner_profile_id': owner,
                             **({'reason': assignment.reason, 'id': str(assignment.id)}
                                if payment.payment_type == 'HAUSGELD_VORSCHUSS' and assignment else {})})
            link = funding.get(payment.reverses_id or payment.id)
            if payment.payment_type == 'RUECKLAGENENTNAHME':
                cost_id = link.cost_entry_id if link else payment.reserve_cost_entry_id
                register_id = link.register_entry_id if link else payment.reserve_register_entry_id
                backed = bool(cost_id and register_id)
                amount = -signed
                if late:
                    late_cash -= amount
                else:
                    withdrawals += amount
                rows.append({'kind': 'LATE_CASH' if late else 'WITHDRAWAL', **({'component': 'WITHDRAWAL'} if late else {}), 'date': str(payment.value_date), 'amount': format(-amount if late else amount, '.2f'),
                             'payment_id': str(payment.id), 'cost_entry_id': str(cost_id) if cost_id else None,
                             'register_entry_id': str(register_id) if register_id else None,
                             'reason': (link.reason if link else payment.note) if backed else 'Kostenbeleg und Beschluss noch nicht verknüpft'})
                if not backed and not (payment.reversed_by_id in payment_ids or payment.reverses_id in payment_ids):
                    unlinked.append(str(payment.id))
        for entry in entries:
            if entry.payment_id or not run.period_start <= entry.entry_date <= run.period_end:
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
                         'amount': format(entry.amount, '.2f'), 'reason': entry.reason, 'id': str(entry.id),
                         **({'unit_id': str(entry.unit_id)} if entry.unit_id else {}),
                         **({'cost_entry_id': str(entry.cost_entry_id), 'register_entry_id': str(entry.register_entry_id)} if entry.cost_entry_id else {})})
        created_at = {str(source.id): source.created_at.isoformat() for source in [*entries, *payments]
                      if getattr(source, 'created_at', None) is not None}
        return {**reserve_totals(opening, contributions, withdrawals, overrides, late_cash),
                'planned_contributions_total': format(money(plan.reserve_contribution_total), '.2f') if plan else None,
                'economic_plan_id': str(plan.id) if plan else None, 'economic_plan_sha256': plan.snapshot_sha256 if plan else None,
                'booking_basis': 'CASH', 'frozen': False,
                'unlinked_payment_ids': unlinked,
                'opening_source_run_id': str(prior.id) if prior else None,
                'entries': sorted(rows, key=lambda row: (row['date'], created_at.get(row.get('id') or row.get('payment_id'), ''),
                    row.get('id') or row.get('payment_id', ''), row['kind'], row.get('unit_id', ''), row.get('owner_profile_id', '')))}

    async def read_display(self, run):
        ledger = await self.read(run)
        return {**ledger, 'entries': await self.repo.display_entries(ledger['entries']),
                'contribution_headroom': [] if ledger['frozen'] else await self.repo.contribution_options(run)}

    async def require_contribution_backing(self, run):
        if await self.repo.has_unsupported_contributions(run):
            raise HTTPException(422, CONTRIBUTION_EXCEEDS_BOOKED)

    async def require_evidence(self, property_id, cost_id, register_id, *, complete=True, payment=None, withdrawal_amount=None):
        with self.session.no_autoflush:
            cost, register, result = await self.repo.evidence_sources(cost_id, register_id)
        if (cost_id and (cost is None or cost.property_id != property_id)
                or register_id and (register is None or register.property_id != property_id)):
            raise HTTPException(404, {'code': 'RESERVE_FUNDING_SOURCE_NOT_FOUND', 'message': 'Finanzierungsbeleg nicht gefunden.'})
        if not complete:
            return
        if (cost is None or register is None or cost.status != 'confirmed' or cost.superseded_by_id
                or result != 'ANGENOMMEN'):
            raise HTTPException(422, {'code': 'RESERVE_PAYOUT_EVIDENCE_REQUIRED',
                'message': 'Bitte verknüpfen Sie einen bestätigten Kostenbeleg und einen angenommenen Beschluss aus der Beschluss-Sammlung vor dem Buchen.'})
        if payment:
            withdrawal_amount = payment.amount
        if withdrawal_amount is not None and withdrawal_amount + await self.repo.funded_amount(
                cost_id, payment.id if payment else None) > cost.gross_amount:
            raise HTTPException(422, {'code': 'RESERVE_PAYOUT_EVIDENCE_REQUIRED',
                'message': 'Die aktiven Rücklagenentnahmen dürfen den bestätigten Kostenbetrag nicht überschreiten.'})

    async def add(self, run, component, amount, reason, payment_id=None, cost_entry_id=None, register_entry_id=None, unit_id=None):
        ledger = await self.read(run)
        if ledger['frozen']:
            raise HTTPException(409, {'code': 'RESERVE_PERIOD_FINALIZED', 'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'})
        if not reason.strip():
            raise HTTPException(422, {'code': 'RESERVE_CORRECTION_REASON_REQUIRED', 'message': 'Bitte begründen Sie die Rücklagenkorrektur.'})
        try:
            amount = money(amount)
        except ValueError:
            raise HTTPException(422, {'code': 'RESERVE_AMOUNT_INVALID', 'message': 'Bitte geben Sie einen endlichen Betrag ein.'}) from None
        if component == 'OPENING':
            await self.require_opening_period(run, ledger)
            if amount == ZERO:
                raise HTTPException(422, {'code': 'RESERVE_AMOUNT_INVALID', 'message': 'Keine Änderung des Anfangsbestands.'})
        if unit_id is not None and (component != 'CONTRIBUTION' or payment_id or cost_entry_id or register_entry_id):
            raise HTTPException(422, {'code': 'RESERVE_INPUT_INVALID', 'message': 'Der Einheitenbezug ist nur für eine Zuführungskorrektur zulässig.'})
        if component == 'CONTRIBUTION' and payment_id is None:
            if unit_id is None or cost_entry_id or register_entry_id:
                raise HTTPException(422, {'code': 'RESERVE_CONTRIBUTION_UNIT_REQUIRED', 'message': 'Bitte wählen Sie für die Zuführungskorrektur eine Einheit; Kostenbeleg und Beschluss sind hier nicht zulässig.'})
            unit = await self.repo.unit(run, unit_id)
            if unit is None:
                raise HTTPException(404, {'code': 'RESERVE_NOT_FOUND', 'message': 'Rücklagenabrechnung nicht gefunden.'})
            if amount <= ZERO:
                raise HTTPException(422, {'code': 'RESERVE_AMOUNT_INVALID', 'message': 'Bitte geben Sie für eine Zuführungskorrektur einen Betrag größer als null ein.'})
            if amount > await self.repo.contribution_headroom(run, unit_id):
                raise HTTPException(422, CONTRIBUTION_EXCEEDS_BOOKED)
        elif component == 'FUNDING':
            await self.require_evidence(run.property_id, cost_entry_id, register_entry_id)
            sources = await self.repo.funding_sources(payment_id, cost_entry_id, register_entry_id)
            if any(source is None or source.property_id != run.property_id for source in sources):
                raise HTTPException(404, {'code': 'RESERVE_FUNDING_SOURCE_NOT_FOUND', 'message': 'Finanzierungsbeleg nicht gefunden.'})
            payment, cost, register = sources
            if payment.reserve_cost_entry_id and (payment.reserve_cost_entry_id != cost_entry_id or payment.reserve_register_entry_id != register_entry_id):
                raise HTTPException(422, {'code': 'RESERVE_PAYOUT_EVIDENCE_REQUIRED', 'message': 'Die Verknüpfung muss den unveränderbaren Buchungsbelegen entsprechen.'})
            if (payment.status != 'booked' or payment.direction != 'OUT' or payment.payment_type != 'RUECKLAGENENTNAHME'
                    or payment.reverses_id
                    or cost.status != 'confirmed' or cost.superseded_by_id or cost.gross_amount < payment.amount
                    or not any(row.get('payment_id') == str(payment.id) for row in ledger['entries'])):
                raise HTTPException(422, {'code': 'RESERVE_PAYOUT_EVIDENCE_REQUIRED', 'message': 'Bitte wählen Sie eine aktive Auszahlung und einen bestätigten Kostenbeleg.'})
            if any(e.payment_id == payment_id for e in await self.repo.entries(run)):
                raise HTTPException(409, {'code': 'RESERVE_PAYMENT_ALREADY_ASSIGNED', 'message': 'Diese Auszahlung ist bereits der Rücklage zugeordnet.'})
            amount = ZERO
        elif component == 'CONTRIBUTION' and payment_id and not any((cost_entry_id, register_entry_id)):
            unassigned = next((row for row in ledger['entries']
                if row['kind'] == 'UNASSIGNED' and row.get('payment_id') == str(payment_id)), None)
            if unassigned is None:
                raise HTTPException(422, {'code': 'RESERVE_ASSIGNMENT_REQUIRED', 'message': 'Bitte wählen Sie eine Zahlung ohne Planbezug.'})
            signed = Decimal(unassigned['amount'])
            if not min(ZERO, signed) <= amount <= max(ZERO, signed):
                raise HTTPException(422, {'code': 'RESERVE_ASSIGNMENT_EXCEEDS_PAYMENT',
                    'message': 'Die Rücklagenzuordnung muss zwischen null und dem vorzeichenbehafteten Zahlungsbetrag liegen.'})
            # The immutable organization/payment unique key permits exactly one
            # assignment, so its total cannot grow through another draft or race.
        elif component in ('WITHDRAWAL', 'ADJUSTMENT') and payment_id is None:
            await self.require_evidence(run.property_id, cost_entry_id, register_entry_id,
                withdrawal_amount=amount if component == 'WITHDRAWAL' else abs(amount) if component == 'ADJUSTMENT' else None)
            if component in ('WITHDRAWAL', 'CONTRIBUTION') and amount <= ZERO:
                raise HTTPException(422, {'code': 'RESERVE_AMOUNT_INVALID',
                    'message': 'Bitte geben Sie für eine Entnahme- oder Zuführungskorrektur einen Betrag größer als null ein.'})
        elif any((payment_id, cost_entry_id, register_entry_id)):
            raise HTTPException(422, {'code': 'RESERVE_FUNDING_LINKS_INVALID', 'message': 'Belegverknüpfungen sind nur für eine finanzierte Entnahme zulässig.'})
        entry = await self.repo.create(ReserveLedgerEntry(
            property_id=run.property_id, settlement_run_id=run.id,
            entry_date=run.period_start if component == 'OPENING' else run.period_end,
            component=component, amount=amount, reason=reason.strip(), created_by=self.user_id,
            payment_id=payment_id, cost_entry_id=cost_entry_id, register_entry_id=register_entry_id, unit_id=unit_id))
        await AuditRepository(self.session, self.org_id).create(AuditEvent(
            actor_user_id=self.user_id, entity_type='settlement_run', entity_id=run.id,
            action='reserve.override_added', payload_diff={'entry_id': str(entry.id), 'component': component,
                'amount': format(amount, '.2f'), 'reason': reason.strip(),
                **({'unit_id': str(unit_id)} if unit_id else {})}))
        return entry

    async def require_opening_period(self, run, ledger):
        if await self.repo.has_finalized_opening_period(run):
            raise HTTPException(409, {'code': 'RESERVE_PERIOD_FINALIZED', 'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'})
        if (ledger.get('opening_source_run_id') or await self.repo.has_earlier_ledger(run)
                or await self.repo.has_overlapping_ledger(run)):
            raise HTTPException(422, OPENING_IMPORT_ONLY)

    async def apply_typed_overrides(self, run, values):
        if any(getattr(values, field, None) is not None for field in ('reserve_contributions_total', 'reserve_withdrawals_total')):
            raise HTTPException(422, DERIVED_TOTAL_READ_ONLY)
        ledger = await self.read(run)
        fields = {'reserve_opening_balance': ('OPENING', 'opening_balance')}
        for field, (component, total) in fields.items():
            value = getattr(values, field, None)
            if value is None:
                continue
            delta = money(value) - Decimal(ledger[total])
            if not delta:
                continue
            if ledger['frozen'] and component == 'OPENING':
                raise HTTPException(409, {'code': 'RESERVE_PERIOD_FINALIZED', 'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'})
            reason = values.reserve_override_reason
            if not reason or not reason.strip():
                raise HTTPException(422, {'code': 'RESERVE_CORRECTION_REASON_REQUIRED', 'message': 'Manuelle Rücklagenwerte benötigen eine Begründung.'})
            await self.add(run, component, delta, reason)
            ledger = await self.read(run)
        return ledger
