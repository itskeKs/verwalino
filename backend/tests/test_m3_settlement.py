"""M3 settlement tests -- persistence, RLS, immutability, atomic finalize, and
the end-to-end M3 exit (Mischobjekt with Mieterwechsel -> nachrechenbar correct,
formell complete BK-Abrechnung PDF).

Uses the same fixtures as M0/M1/M2 (seed_data, app_rw_session, migrator_session,
client_a_admin/clerk/readonly, client_b_admin).

The engine math itself is proven in tests/engine/ (Golden + property-based);
here we prove the DB invariants and the full create->compute->finalize->PDF path.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.financial_coverage_helpers import attest_fixture
from tests.test_invoice_extraction_worker import worker_tenant  # noqa: F401
import pytest
from sqlalchemy import text

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


@pytest.mark.parametrize('heating_advance,balance', [('1080.00', '20.00'), ('0.00', '1100.00')])
def test_operating_and_heating_costs_credit_their_own_advances(heating_advance, balance):
    from app.engine.settlement import compute_bk_settlement
    from app.engine.types import CostPositionInput, LeasePeriodInput, PropertyInput, SettlementInput, UnitInput
    unit, lease, tenant = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    positions = tuple(CostPositionInput(uuid.uuid4(), None, code, code, Decimal(amount), start, end,
        'WOHNFLAECHE', 'UMLAGEFAEHIG') for code, amount in [('GRUNDSTEUER', '1900'), ('MESSDIENST', '1000')])
    inp = SettlementInput(start, end, PropertyInput(uuid.uuid4(), 'Musterhaus', 'MIET'), lease,
        (UnitInput(unit, '1', 'WOHNUNG', area=Decimal(70)),),
        (LeasePeriodInput(lease, unit, tenant, start, None),), (), positions, Decimal('1800'),
        heating_advance_payments_total=Decimal(heating_advance))
    result = compute_bk_settlement(inp)
    assert result.balance == Decimal(balance)
    assert result.heating_advance_payments_total == Decimal(heating_advance)
    lines = {line.label: line.amount for line in result.lines}
    assert lines['Betriebskosten abzüglich Vorauszahlungen'] == Decimal('100')
    assert lines['Heizkosten abzüglich Vorauszahlungen'] == Decimal('1000') - Decimal(heating_advance)


@pytest.mark.asyncio
@pytest.mark.parametrize('batch', [False, True])
async def test_omitted_heating_total_blocks_finalization_until_derived(client_a_admin, batch):
    from tests.settlement_period_support import create, period_property, statement
    api = client_a_admin
    data = await period_property(api, month=1)
    response = await api.patch(f"/api/leases/{data['lease']['id']}", json={
        'base_rent': '750.00', 'advance_payment': '150.00', 'heating_advance_payment': '90.00'})
    assert response.status_code == 200, response.text
    categories = (await api.get('/api/cost-categories')).json()
    for code, amount in [('GRUNDSTEUER', '1900.00'), ('HEIZUNG_ZENTRAL', '1000.00')]:
        category = next(row for row in categories if row['code'] == code)
        cost = await create(api, '/api/cost-entries', {
            'property_id': data['property']['id'], 'cost_category_id': category['id'],
            'invoice_date': '2026-12-31', 'service_period_from': '2026-01-01',
            'service_period_to': '2026-12-31', 'gross_amount': amount,
            'tenant_distribution_key': 'WOHNFLAECHE', 'tenant_apportionable': 'UMLAGEFAEHIG'})
        response = await api.post(f"/api/cost-entries/{cost['id']}/confirm")
        assert response.status_code == 200, response.text
    for kind, amount in [('NK_VORAUSZAHLUNG', '1800.00'), ('HK_VORAUSZAHLUNG', '1080.00')]:
        payment = await create(api, '/api/payments', {
            'bank_account_id': data['account']['id'], 'direction': 'IN', 'amount': amount,
            'value_date': '2026-12-31', 'payment_type': kind, 'period_year': 2026,
            'tenant_profile_id': data['tenant']['id'], 'lease_agreement_id': data['lease']['id']})
        assert (await api.post(f"/api/payments/{payment['id']}/book")).status_code == 200
    payload = statement(data, '2026-01-01', '2026-12-31')
    if batch:
        payload.pop('lease_agreement_id')
        response = await api.post('/api/settlements/batch', json=payload)
        assert response.status_code == 200, response.text
        run, = response.json()['created']
        response = await api.patch(f"/api/settlements/{run['id']}/advance-payments",
            json={'advance_payments_total': '1800.00'})
        assert response.status_code == 200, response.text
        run = response.json()
    else:
        run = await create(api, '/api/settlements', {**payload, 'advance_payments_total': '1800.00'})
    url = f"/api/settlements/{run['id']}"
    # Reproduce the owner's probe: attest the actual draft values, even if the
    # creation default incorrectly claims that unknown heating payments are zero.
    await api.post('/api/financial-input-coverage', json={
        'subject_kind': 'settlement', 'subject_id': run['id'], 'confirmed_complete': True,
        'reason': 'Synthetic full-year records reviewed', 'source_document_ids': [],
        'values': {'advance_payments_total': '1800.00', 'advance_payments_source': 'MANUAL',
            'heating_advance_payments_total': run['heating_advance_payments_total'],
            'heating_advance_payments_source': 'MANUAL'}})
    refused = await api.post(url + '/finalize', json={})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'financial_inputs_incomplete'
    assert run['heating_advance_payments_total'] is None
    assert (await api.patch(url + '/advance-payments-derived', params={'period_year': 2026})).status_code == 200
    await attest_fixture(api, 'settlement', run['id'], {
        'advance_payments_total': '1800.00', 'advance_payments_source': 'DERIVED',
        'heating_advance_payments_total': '1080.00', 'heating_advance_payments_source': 'DERIVED'})
    finalized = await api.post(url + '/finalize', json={})
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()['balance'] == '20.00'


@pytest.mark.parametrize('heating', ['none', 'non_apportionable_position', 'zero_position', 'position', 'advance'])
def test_statement_and_pdf_keep_single_block_only_without_heating(heating):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.engine.settlement import compute_bk_settlement
    from app.engine.snapshot import build_snapshot_payload
    from app.services.settlement_pdf import build_settlement_html
    from tests.engine.test_golden import TestGolden1FlaecheTrivial, _U
    inp = TestGolden1FlaecheTrivial()._scenario(_U(21))
    inp = replace(inp, heating_advance_payments_total=Decimal('0.00'))
    heating_id = uuid.uuid4()
    split_advances = heating not in ('none', 'non_apportionable_position')
    if heating in ('non_apportionable_position', 'zero_position', 'position'):
        inp = replace(inp, cost_positions=(*inp.cost_positions,
            replace(inp.cost_positions[0], cost_entry_id=heating_id, category_code='MESSDIENST',
                gross_amount=Decimal('0.00' if heating == 'zero_position' else '1000.00'),
                tenant_apportionable='NICHT_UMLAGEFAEHIG' if heating == 'non_apportionable_position' else 'UMLAGEFAEHIG')))
    elif heating == 'advance':
        inp = replace(inp, heating_advance_payments_total=Decimal('90.00'))
    result = compute_bk_settlement(inp)
    labels = [line.label for line in result.lines]
    assert any(line.cost_entry_id == heating_id for line in result.lines) == (heating in ('zero_position', 'position'))
    if not split_advances:
        assert 'Geleistete Vorauszahlungen' in labels
        assert 'Heizkosten-Vorauszahlungen' not in labels
        assert 'Betriebskosten abzüglich Vorauszahlungen' not in labels
    else:
        assert 'Betriebskosten-Vorauszahlungen' in labels
        assert 'Heizkosten-Vorauszahlungen' in labels
    run = SimpleNamespace(total_cost=result.total_cost, period_start=inp.period_start,
        period_end=inp.period_end, snapshot_sha256=None, snapshot_payload={})
    html = build_settlement_html(run, result.lines)
    assert ('Geleistete Vorauszahlungen' in html) == (not split_advances)
    assert ('Heizkosten-Vorauszahlungen' in html) == split_advances
    snapshot = build_snapshot_payload(inp, result)
    assert snapshot['inputs']['heating_advance_payments_total'] == str(inp.heating_advance_payments_total)
    assert snapshot['result']['heating_advance_payments_total'] == str(result.heating_advance_payments_total)


@pytest.mark.asyncio
@pytest.mark.parametrize('amount', ['0.00', '1000.00'])
async def test_heating_in_later_fiscal_window_keeps_two_advance_blocks(client_a_admin, amount):
    import subprocess
    from tests.settlement_period_support import create, period_property, statement

    api = client_a_admin
    data = await period_property(api, month=4)
    categories = (await api.get('/api/cost-categories')).json()
    category = next(row for row in categories if row['code'] == 'MESSDIENST')
    cost = await create(api, '/api/cost-entries', {
        'property_id': data['property']['id'], 'cost_category_id': category['id'],
        'invoice_date': '2026-04-01', 'service_period_from': '2026-04-01',
        'service_period_to': '2027-03-31', 'gross_amount': amount,
        'tenant_distribution_key': 'WOHNFLAECHE', 'tenant_apportionable': 'UMLAGEFAEHIG'})
    assert (await api.post(f"/api/cost-entries/{cost['id']}/confirm")).status_code == 200
    run = await create(api, '/api/settlements', {
        **statement(data, '2026-01-01', '2026-12-31'),
        'advance_payments_total': '0.00', 'heating_advance_payments_total': '0.00'})
    await attest_fixture(api, 'settlement', run['id'], {
        'advance_payments_total': '0.00', 'advance_payments_source': 'MANUAL',
        'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL'})
    response = await api.post(f"/api/settlements/{run['id']}/compute", json={})
    assert response.status_code == 200, response.text
    computed = response.json()
    assert any(line['cost_category_code'] == 'MESSDIENST' for line in computed['lines'])
    advances = [line['label'] for line in computed['lines'] if line['line_type'] == 'ADVANCE_PAYMENT']
    assert advances == ['Betriebskosten-Vorauszahlungen', 'Heizkosten-Vorauszahlungen']
    response = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
    assert response.status_code == 200, response.text
    pdf = await api.get(f"/api/settlements/{run['id']}/pdf")
    assert pdf.status_code == 200, pdf.text
    content = subprocess.run(['pdftotext', '-', '-'], input=pdf.content, capture_output=True, check=True).stdout.decode()
    assert 'Heizkosten-Vorauszahlungen' in content
    assert 'Geleistete Vorauszahlungen' not in content


@pytest.mark.asyncio
async def test_derived_component_totals_have_independent_sources_and_coverage(client_a_admin):
    from tests.settlement_period_support import create, period_property, statement
    api = client_a_admin
    data = await period_property(api, month=1)

    async def payment(kind, amount, book=True):
        row = await create(api, '/api/payments', {'bank_account_id': data['account']['id'],
            'direction': 'IN', 'amount': amount, 'value_date': '2026-12-31', 'payment_type': kind,
            'period_year': 2026, 'tenant_profile_id': data['tenant']['id'], 'lease_agreement_id': data['lease']['id']})
        if book:
            response = await api.post(f"/api/payments/{row['id']}/book")
            assert response.status_code == 200, response.text
        return row

    await payment('NK_VORAUSZAHLUNG', '1800.00')
    await payment('HK_VORAUSZAHLUNG', '1080.00')
    await payment('KALTMIETE', '9000.00')
    await payment('HK_VORAUSZAHLUNG', '999.00', book=False)
    reversed_row = await payment('HK_VORAUSZAHLUNG', '60.00')
    assert (await api.post(f"/api/payments/{reversed_row['id']}/reverse", json={'note': 'Synthetic reversal'})).status_code == 201
    run = await create(api, '/api/settlements', statement(data, '2026-01-01', '2026-12-31'))
    url = f"/api/settlements/{run['id']}"
    response = await api.patch(url + '/advance-payments-derived', params={'period_year': 2026})
    assert response.status_code == 200, response.text
    assert (response.json()['advance_payments_total'], response.json()['heating_advance_payments_total']) == ('1800.00', '1080.00')
    response = await api.patch(url + '/advance-payments', json={'heating_advance_payments_total': '1000.00'})
    assert response.status_code == 200, response.text
    assert response.json()['advance_payments_source'] == 'DERIVED'
    assert response.json()['heating_advance_payments_source'] == 'MANUAL'
    assert response.json()['advance_payments_total'] == '1800.00'
    # A later HK booking invalidates only the corresponding derived total.
    assert (await api.patch(url + '/advance-payments-derived', params={'period_year': 2026})).status_code == 200
    values = {'advance_payments_total': '1800.00', 'advance_payments_source': 'DERIVED',
        'heating_advance_payments_total': '1080.00', 'heating_advance_payments_source': 'DERIVED'}
    await attest_fixture(api, 'settlement', run['id'], values)
    await payment('HK_VORAUSZAHLUNG', '10.00')
    response = await api.post(url + '/compute', json={})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['blockers'][0]['component'] == 'heating_advance_payments'


@pytest.mark.asyncio
async def test_manual_heating_totals_keep_the_same_sign_as_operating_totals(client_a_admin):
    from tests.settlement_period_support import create, period_property, statement
    api = client_a_admin
    data = await period_property(api, month=1)
    run = await create(api, '/api/settlements', {**statement(data, '2026-01-01', '2026-12-31'),
        'advance_payments_total': '0.00', 'heating_advance_payments_total': '-20.00'})
    url = f"/api/settlements/{run['id']}"
    await attest_fixture(api, 'settlement', run['id'], {'advance_payments_total': '0.00',
        'advance_payments_source': 'MANUAL', 'heating_advance_payments_total': '-20.00',
        'heating_advance_payments_source': 'MANUAL'})
    response = await api.post(url + '/finalize', json={})
    assert response.status_code == 200, response.text
    assert response.json()['balance'] == '20.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('heating_key', ['absent', 'json_null'])
async def test_pre_split_finalized_snapshot_keeps_its_digest_and_pdf(client_a_admin, m3_seed, heating_key):
    """Retain the literal pre-split payload: readers must never fill new keys."""
    import json
    from app.engine.canonical import canonical_json, sha256_hex
    coverage_id = str(uuid.uuid4())
    payload = {'engine_version': 'm3.1.0', 'run_type': 'BK_MIETER',
        'period': {'start': '2025-01-01', 'end': '2025-06-30'},
        'inputs': {'advance_payments_total': '600.00', 'advance_payments_source': 'MANUAL'},
        'result': {'total_cost': '700.00', 'advance_payments_total': '600.00', 'balance': '100.00'},
        'financial_input_coverage': {'id': coverage_id, 'source_revision': '0' * 64}}
    if heating_key == 'json_null':
        payload['inputs']['heating_advance_payments_total'] = None
    digest = sha256_hex(canonical_json(payload))
    run_id = str(uuid.uuid4())
    import psycopg2
    with psycopg2.connect(SYNC_URL) as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO settlement_run
            (id,organization_id,property_id,lease_agreement_id,run_type,period_start,period_end,
             advance_payments_total,advance_payments_source,total_cost,balance,status,
             snapshot_payload,snapshot_sha256,engine_version,finalized_at)
            VALUES (%s,%s,%s,%s,'BK_MIETER','2025-01-01','2025-06-30',600,'MANUAL',700,100,
             'finalized',%s,%s,'m3.1.0',now())""",
            (run_id, str(m3_seed['org_a_id']), m3_seed['property'], m3_seed['lease_a'], json.dumps(payload), digest))
        cur.execute("""INSERT INTO coverage_declaration(id,organization_id,settlement_run_id,property_id,
            period_start,period_end,component,revision,state,values,source_document_ids,source_revision,reason,attestor_id)
            VALUES (%s,%s,%s,%s,'2025-01-01','2025-06-30','BK_MIETER',1,'known',%s,'[]',%s,'Legacy fixture',%s)""",
            (coverage_id, str(m3_seed['org_a_id']), run_id, m3_seed['property'], json.dumps(payload['inputs']),
             '0' * 64, str(m3_seed['user_a_admin_id'])))
        for order, kind, label, amount in [(0, 'ADVANCE_PAYMENT', 'Geleistete Vorauszahlungen', '600'),
                (1, 'BALANCE', 'Nachzahlung', '100')]:
            cur.execute("""INSERT INTO settlement_line(id,organization_id,settlement_run_id,line_type,label,amount,sort_order)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (str(uuid.uuid4()), str(m3_seed['org_a_id']), run_id, kind, label, amount, order))
    response = await client_a_admin.get(f'/api/settlements/{run_id}/pdf')
    assert response.status_code == 200, response.text
    assert response.content.startswith(b'%PDF-')
    with psycopg2.connect(SYNC_URL) as conn, conn.cursor() as cur:
        cur.execute('SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=%s', (run_id,))
        saved, sha = cur.fetchone()
        cur.execute("""SELECT financial_computation_state(organization_id,'settlement_run',id)->>'parent_digest',
            bounded_computation_digest(to_jsonb(r)-'updated_at'-ARRAY['heating_advance_payments_total','heating_advance_payments_source'])
            FROM settlement_run r WHERE id=%s""", (run_id,))
        actual_parent, legacy_parent = cur.fetchone()
        assert actual_parent == legacy_parent
    assert saved == payload
    assert sha256_hex(canonical_json(saved)) == sha == digest
    for field, value in [('heating_advance_payments_total', '90'), ('heating_advance_payments_source', "'DERIVED'")]:
        with psycopg2.connect(SYNC_URL) as conn:
            with pytest.raises(psycopg2.Error, match='GoBD'):
                with conn.cursor() as cur:
                    cur.execute(f'UPDATE settlement_run SET {field}={value} WHERE id=%s', (run_id,))
            conn.rollback()


# ===========================================================================
# Seed: a full Mischobjekt for org_a (via migrator -> persists across the
# API client's own transactions). Mirrors engine Golden 5 (the exit scenario).
# ===========================================================================
def _seed_m3(seed_data):
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "u1": uuid.uuid4(),
        "u2": uuid.uuid4(),
        "u3": uuid.uuid4(),
        "u4": uuid.uuid4(),
        "person_owner": uuid.uuid4(),
        "owner_profile": uuid.uuid4(),
        "tp_a": uuid.uuid4(),
        "tp_b": uuid.uuid4(),
        "tp_c": uuid.uuid4(),
        "tp_d": uuid.uuid4(),
        "tp_e": uuid.uuid4(),
        "lease_a": uuid.uuid4(),
        "lease_b": uuid.uuid4(),
        "lease_c": uuid.uuid4(),
        "lease_d": uuid.uuid4(),
        "lease_e": uuid.uuid4(),
    }
    person_ids = {k: uuid.uuid4() for k in ("a", "b", "c", "d", "e")}

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        def cat(code):
            cur.execute(
                "SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,)
            )
            return cur.fetchone()[0]

        # Property (Mischobjekt)
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "Mischhaus M3", "MISCH"),
        )
        # Units: U1 area 80/mea 300, U2 area 60/mea 250, U3 area 50/mea 200, U4 GEWERBE area 100/mea 250
        units = [
            (ids["u1"], "1", "WOHNUNG", 80, 300),
            (ids["u2"], "2", "WOHNUNG", 60, 250),
            (ids["u3"], "3", "WOHNUNG", 50, 200),
            (ids["u4"], "4", "GEWERBE", 100, 250),
        ]
        for uid, num, usage, area, mea in units:
            cur.execute(
                "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uid), org, str(ids["property"]), num, usage, area, area, mea),
            )

        # Owner person + profile; owns all units the whole time.
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_owner"]), org, "Olga", "Owner"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_profile"]), org, str(ids["person_owner"])),
        )
        for uid in (ids["u1"], ids["u2"], ids["u3"], ids["u4"]):
            cur.execute(
                "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), org, str(uid), str(ids["owner_profile"]), date(2000, 1, 1), None),
            )

        # Tenant persons + profiles.
        for key, first in [("a", "Anna"), ("b", "Bert"), ("c", "Cara"), ("d", "Dirk"), ("e", "Emil")]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
                (str(person_ids[key]), org, first, key.upper()),
            )
            cur.execute(
                "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
                (str(ids[f"tp_{key}"]), org, str(person_ids[key])),
            )

        # Leases: U1 A(Jan-Jun)/B(Jul-Dec); U3 C(Jan-Mar)/vacancy/D(Aug-Dec); U4 E(all year).
        #
        # valid_to is stored in the TRUE M1 half-open [) convention: it is the first
        # NON-occupied day (the EXCLUDE constraint and the point-in-time query treat
        # it as an exclusive upper bound). The repository read-edge converts it back
        # to the engine's tag-inclusive last-occupied-day (valid_to - 1 day; ADR-0004
        # #6 / m3 section 3.4). So:
        #   A last day 30.06. -> stored 2025-07-01 ; B is back-to-back from 01.07.
        #     (these two are ADJACENT and non-overlapping in the DB -- exactly the
        #      Auszug-30.06./Einzug-01.07. case that triggered the live 500 before
        #      the read-edge conversion was applied).
        #   C last day 31.03. -> stored 2025-04-01 ; D starts 01.08. -> Apr-Jul vacancy.
        #   open-ended leases (last day = settlement period end) -> NULL.
        # The resulting engine day-counts are 181 (A) / 184 (B) / 90 (C) / 153 (D),
        # matching engine Golden 5.
        leases = [
            (ids["lease_a"], ids["u1"], ids["tp_a"], date(2025, 1, 1), date(2025, 7, 1)),
            (ids["lease_b"], ids["u1"], ids["tp_b"], date(2025, 7, 1), None),
            (ids["lease_c"], ids["u3"], ids["tp_c"], date(2025, 1, 1), date(2025, 4, 1)),
            (ids["lease_d"], ids["u3"], ids["tp_d"], date(2025, 8, 1), None),
            (ids["lease_e"], ids["u4"], ids["tp_e"], date(2025, 1, 1), None),
        ]
        for lid, uid, tp, vf, vt in leases:
            cur.execute(
                "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, valid_from, valid_to, base_rent, advance_payment) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(lid), org, str(uid), str(tp), vf, vt, Decimal("800.00"), Decimal("100.00")),
            )

        # Confirmed cost entries (active chain) for 2025.
        def add_cost(code, gross, key, appo, appo_amount=None):
            ce = uuid.uuid4()
            cur.execute(
                "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
                "invoice_date, service_period_from, service_period_to, gross_amount, tenant_distribution_key, "
                "tenant_apportionable, apportionable_tenant_amount, status, source, confirmed_at, confirmed_by) "
                "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
                (
                    str(ce), org, str(ids["property"]), str(cat(code)),
                    date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                    gross, key, appo, appo_amount, str(seed_data["user_a_admin_id"]),
                ),
            )
            return ce

        ids["ce_grundsteuer"] = add_cost("GRUNDSTEUER", Decimal("2900.00"), "WOHNFLAECHE", "UMLAGEFAEHIG")
        ids["ce_muell"] = add_cost("MUELLBESEITIGUNG", Decimal("480.00"), "EINHEIT", "UMLAGEFAEHIG")
        ids["ce_wasser"] = add_cost("KALTWASSER", Decimal("600.00"), "VERBRAUCH", "UMLAGEFAEHIG")
        ids["ce_heizung"] = add_cost("HEIZUNG_ZENTRAL", Decimal("950.00"), "EXTERNE_ABRECHNUNG", "UMLAGEFAEHIG_EXTERN_PRUEFEN")
        ids["ce_co2"] = add_cost("CO2_KOSTEN", Decimal("500.00"), "WOHNFLAECHE", "TEILWEISE_PRUEFEN", Decimal("350.00"))
        ids["ce_hausmeister"] = add_cost("HAUSMEISTER", Decimal("1000.00"), "WOHNFLAECHE", "GEMISCHT_PRUEFEN", Decimal("720.00"))
        ids["ce_verwalter"] = add_cost("WEG_VERWALTERKOSTEN", Decimal("1200.00"), "MEA", "NICHT_UMLAGEFAEHIG")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


@pytest.fixture(scope="session")
def m3_seed(seed_data):
    return _seed_m3(seed_data)


def _extras_for_exit(m3_seed):
    """The structured external/consumption inputs for the exit scenario."""
    return {
        "external_amounts": [
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u1"], "tenant_amount": "210.00", "period_from": "2025-01-01", "period_to": "2025-06-30"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u1"], "tenant_amount": "190.00", "period_from": "2025-07-01", "period_to": "2025-12-31"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u3"], "tenant_amount": "100.00", "period_from": "2025-01-01", "period_to": "2025-03-31"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u3"], "tenant_amount": "150.00", "period_from": "2025-08-01", "period_to": "2025-12-31"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u4"], "tenant_amount": "300.00", "period_from": "2025-01-01", "period_to": "2025-12-31"},
        ],
        "unit_metrics": [
            {"unit_id": m3_seed["u1"], "consumption": "30"},
            {"unit_id": m3_seed["u2"], "consumption": "20"},
            {"unit_id": m3_seed["u3"], "consumption": "10"},
            {"unit_id": m3_seed["u4"], "consumption": "40"},
        ],
    }


async def _create_run(client, m3_seed, lease_key, advance):
    resp = await client.post("/api/settlements", json={
        'heating_advance_payments_total': '0.00', "property_id": m3_seed["property"],
        "lease_agreement_id": m3_seed[lease_key],
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "advance_payments_total": advance,
    })
    assert resp.status_code == 201, resp.text
    await attest_fixture(client, "settlement", resp.json()["id"],
        {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "MANUAL", "advance_payments_total": advance, "advance_payments_source": "MANUAL"})
    return resp.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["property_id", "management_mandate_id"])
async def test_foreign_and_unknown_parents_create_no_rental_settlement(
    client_a_admin, client_b_admin, m3_seed, seed_data, migrator_session, field,
):
    foreign_property = await client_b_admin.post("/api/properties", json={
        "name": "Foreign settlement parent", "management_type": "MIET",
    })
    assert foreign_property.status_code == 201, foreign_property.text
    foreign = foreign_property.json()["id"] if field == "property_id" else seed_data["mandate_b_id"]
    body = {"property_id": m3_seed["property"], "lease_agreement_id": m3_seed["lease_a"],
            "period_start": "2025-01-01", "period_end": "2025-12-31"}
    before = await migrator_session.scalar(text("SELECT count(*) FROM settlement_run"))
    responses = []
    for parent in (foreign, uuid.uuid4()):
        response = await client_a_admin.post("/api/settlements", json=body | {field: str(parent)})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]
    assert await migrator_session.scalar(text("SELECT count(*) FROM settlement_run")) == before


# ===========================================================================
# Test: RLS fail-closed + cross-org isolation for the new tables
# ===========================================================================
class TestM3RLSFailClosed:
    @pytest.mark.asyncio
    async def test_no_context_zero_runs(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM settlement_run"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_no_context_zero_lines(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM settlement_line"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_bogus_uuid_zero_runs(self, app_rw_session):
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(uuid.uuid4())}
        )
        result = await app_rw_session.execute(text("SELECT * FROM settlement_run"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_insert_foreign_org_rejected(self, app_rw_session, m3_seed):
        """WITH CHECK blocks inserting a run for another org."""
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        with pytest.raises(Exception):
            await app_rw_session.execute(
                text(
                    "INSERT INTO settlement_run (id, organization_id, property_id, run_type, "
                    "period_start, period_end, advance_payments_source, status, engine_version) "
                    "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','draft','m3.1.0')"
                ),
                {"id": str(uuid.uuid4()), "org": str(m3_seed["org_b_id"]), "prop": m3_seed["property"]},
            )

    @pytest.mark.asyncio
    async def test_cross_org_runs_invisible(self, client_a_admin, client_b_admin, m3_seed):
        await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp_b = await client_b_admin.get("/api/settlements")
        assert resp_b.status_code == 200
        assert len(resp_b.json()) == 0

    @pytest.mark.asyncio
    async def test_foreign_run_returns_404(self, client_a_admin, client_b_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp = await client_b_admin.get(f"/api/settlements/{run_id}")
        assert resp.status_code == 404


# ===========================================================================
# Test: RBAC
# ===========================================================================
class TestM3Rbac:
    @pytest.mark.asyncio
    async def test_readonly_cannot_create(self, client_a_readonly, m3_seed):
        resp = await client_a_readonly.post("/api/settlements", json={
            "property_id": m3_seed["property"],
            "lease_agreement_id": m3_seed["lease_a"],
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_can_read(self, client_a_admin, client_a_readonly, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp = await client_a_readonly.get(f"/api/settlements/{run_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_cannot_finalize(self, client_a_admin, client_a_readonly, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp = await client_a_readonly.post(f"/api/settlements/{run_id}/finalize")
        assert resp.status_code == 403


# ===========================================================================
# Test: immutability (finalized run + lines frozen, ADR-0003 trigger)
# ===========================================================================
class TestM3Immutability:
    @pytest.mark.asyncio
    async def test_update_total_cost_on_finalized_rejected(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, total_cost, balance, status, snapshot_sha256, engine_version, finalized_at) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','100.00','0.00','finalized',:sha,'m3.1.0',now())"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"], "sha": "a" * 64},
        )
        with pytest.raises(Exception, match="GoBD.*total_cost.*finalized"):
            await session.execute(
                text("UPDATE settlement_run SET total_cost='999.00' WHERE id=:id"), {"id": str(run_id)}
            )

    @pytest.mark.asyncio
    async def test_update_snapshot_on_finalized_rejected(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, status, snapshot_sha256, engine_version, finalized_at) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','finalized',:sha,'m3.1.0',now())"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"], "sha": "a" * 64},
        )
        with pytest.raises(Exception, match="GoBD.*snapshot_sha256.*finalized"):
            await session.execute(
                text("UPDATE settlement_run SET snapshot_sha256=:s WHERE id=:id"),
                {"id": str(run_id), "s": "b" * 64},
            )

    @pytest.mark.asyncio
    async def test_delete_finalized_run_rejected(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, status, engine_version, finalized_at) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','finalized','m3.1.0',now())"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"]},
        )
        with pytest.raises(Exception, match="GoBD.*DELETE.*finalized"):
            await session.execute(text("DELETE FROM settlement_run WHERE id=:id"), {"id": str(run_id)})

    @pytest.mark.asyncio
    async def test_superseded_by_id_allowed_on_finalized(self, app_rw_session, m3_seed):
        """The ONLY allowed mutation on a finalized run: setting superseded_by_id."""
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        a, b = uuid.uuid4(), uuid.uuid4()
        for rid, status in [(a, "finalized"), (b, "draft")]:
            await session.execute(
                text(
                    "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                    "advance_payments_source, status, engine_version) "
                    "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL',:st,'m3.1.0')"
                ),
                {"id": str(rid), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"], "st": status},
            )
        # Setting superseded_by_id on the finalized run is allowed.
        await session.execute(
            text("UPDATE settlement_run SET superseded_by_id=:b WHERE id=:a"),
            {"a": str(a), "b": str(b)},
        )
        result = await session.execute(
            text("SELECT superseded_by_id FROM settlement_run WHERE id=:a"), {"a": str(a)}
        )
        assert str(result.scalar_one()) == str(b)

    @pytest.mark.asyncio
    async def test_line_on_finalized_run_frozen(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        line_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, status, engine_version) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','draft','m3.1.0')"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"]},
        )
        await session.execute(
            text(
                "INSERT INTO settlement_line (id, organization_id, settlement_run_id, line_type, label, amount, sort_order) "
                "VALUES (:id,:org,:run,'COST','Test','100.00',0)"
            ),
            {"id": str(line_id), "org": str(m3_seed["org_a_id"]), "run": str(run_id)},
        )
        # While draft: line is editable.
        await session.execute(
            text("UPDATE settlement_line SET amount='200.00' WHERE id=:id"), {"id": str(line_id)}
        )
        # Finalize the run.
        await session.execute(
            text("UPDATE settlement_run SET status='finalized', finalized_at=now() WHERE id=:id"),
            {"id": str(run_id)},
        )
        # Now the line is frozen: UPDATE and DELETE both rejected.
        with pytest.raises(Exception, match="GoBD.*settlement_line.*finalized"):
            await session.execute(
                text("UPDATE settlement_line SET amount='300.00' WHERE id=:id"), {"id": str(line_id)}
            )


# ===========================================================================
# Test: compute is repeatable; preview does not persist
# ===========================================================================
class TestM3ComputeRepeatable:
    @pytest.mark.asyncio
    async def test_recompute_replaces_lines(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        r1 = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
        assert r1.status_code == 200
        n1 = len(r1.json()["lines"])
        # Recompute: same input -> same line count, same total.
        r2 = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
        assert r2.status_code == 200
        assert len(r2.json()["lines"]) == n1
        assert r1.json()["total_cost"] == r2.json()["total_cost"]

    @pytest.mark.asyncio
    async def test_missing_external_amounts_escalates(self, client_a_admin, m3_seed):
        """A HEIZUNG (EXTERNE_ABRECHNUNG) position without external amounts -> 422."""
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        # Provide consumption (for WASSER) but NO external amounts (for HEIZUNG).
        resp = await client_a_admin.post(
            f"/api/settlements/{run_id}/compute",
            json={"unit_metrics": _extras_for_exit(m3_seed)["unit_metrics"]},
        )
        assert resp.status_code == 422
        assert "escalate" in resp.json()["detail"].lower() or "extern" in resp.json()["detail"].lower()


# ===========================================================================
# End-to-end M3 EXIT: Mischobjekt with Mieterwechsel -> correct + complete PDF
# ===========================================================================
class TestM3ExitEndToEnd:
    @pytest.mark.asyncio
    async def test_full_bk_settlement_for_tenant_a(self, client_a_admin, m3_seed):
        """Create -> compute -> finalize -> PDF for tenant A (mid-year change).

        Hand-checked Soll (engine Golden 5, tenant A = U1, 181/365):
          GRUNDSTEUER 396.71, MUELL 59.51, WASSER 89.26, HEIZUNG 210.00 (extern),
          CO2 47.88, HAUSMEISTER 98.49; total 901.85; minus 700.00 = 201.85 Nachzahlung.
          WEG_VERWALTERKOSTEN (NICHT_UMLAGEFAEHIG) must NOT appear.
        """
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)

        # Finalize (computes, snapshots, hashes, writes lines -- atomic).
        fin = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        assert fin.status_code == 200, fin.text
        body = fin.json()
        assert body["status"] == "finalized"
        assert body["snapshot_sha256"] is not None and len(body["snapshot_sha256"]) == 64
        assert body["total_cost"] == "901.85"
        assert body["balance"] == "201.85"

        lines = {ln["cost_category_code"]: ln for ln in body["lines"] if ln["line_type"] == "COST"}
        assert "WEG_VERWALTERKOSTEN" not in lines  # not apportionable
        assert lines["GRUNDSTEUER"]["amount"] == "396.71"
        assert lines["GRUNDSTEUER"]["distribution_key"] == "WOHNFLAECHE"
        assert lines["GRUNDSTEUER"]["total_amount"] == "2900.00"
        assert lines["GRUNDSTEUER"]["time_share_days"] == 181
        assert lines["GRUNDSTEUER"]["time_share_total_days"] == 365
        assert lines["MUELLBESEITIGUNG"]["amount"] == "59.51"
        assert lines["KALTWASSER"]["amount"] == "89.26"
        assert lines["HEIZUNG_ZENTRAL"]["amount"] == "210.00"
        assert lines["HEIZUNG_ZENTRAL"]["note"] == "extern abgerechnet (Messdienst)"
        assert lines["CO2_KOSTEN"]["amount"] == "47.88"
        assert lines["CO2_KOSTEN"]["total_amount"] == "350.00"  # effective, not gross 500
        assert lines["HAUSMEISTER"]["amount"] == "98.49"

        # ADVANCE_PAYMENT and BALANCE lines present (BGH).
        types = {ln["line_type"] for ln in body["lines"]}
        assert "ADVANCE_PAYMENT" in types and "BALANCE" in types

        # PDF: only available finalized, contains the values (PDF computes nothing).
        pdf = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content[:5] == b"%PDF-"
        assert len(pdf.content) > 1000

    @pytest.mark.asyncio
    async def test_finalization_committed_before_response(
        self, client_a_admin, m3_seed, app_rw_session
    ):
        """Observe the DB at response.start, before ASGITransport awaits cleanup.

        A real HTTP client can request the PDF as soon as it receives the
        response. Request-scoped dependency cleanup is too late for that read.
        """
        import httpx
        from app.main import app

        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        computed = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
        assert computed.status_code == 200, computed.text
        assert computed.json()["status"] == "draft"
        draft_pdf = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert draft_pdf.status_code == 409

        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(m3_seed["org_a_id"])},
        )
        observed = []

        async def observe_response(scope, receive, send):
            async def observe_send(message):
                if message["type"] == "http.response.start":
                    observed.append(await app_rw_session.scalar(
                        text("SELECT status FROM settlement_run WHERE id=:id"),
                        {"id": run_id},
                    ))
                await send(message)

            await app(scope, receive, observe_send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=observe_response),
            base_url="http://test",
            cookies=client_a_admin.cookies,
        ) as client:
            finalized = await client.post(f"/api/settlements/{run_id}/finalize", json=extras)
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["status"] == "finalized"
        assert observed == ["finalized"], "Finalization must commit before sending HTTP success"
        pdf = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF-")

    @pytest.mark.asyncio
    async def test_two_tenants_sum_to_unit_share(self, client_a_admin, m3_seed):
        """A + B for U1 GRUNDSTEUER == full unit share 800.00 (no cent lost)."""
        extras = _extras_for_exit(m3_seed)
        run_a = await _create_run(client_a_admin, m3_seed, "lease_a", "0.00")
        run_b = await _create_run(client_a_admin, m3_seed, "lease_b", "0.00")
        ra = (await client_a_admin.post(f"/api/settlements/{run_a}/finalize", json=extras)).json()
        rb = (await client_a_admin.post(f"/api/settlements/{run_b}/finalize", json=extras)).json()
        gs_a = Decimal(next(ln["amount"] for ln in ra["lines"] if ln["cost_category_code"] == "GRUNDSTEUER"))
        gs_b = Decimal(next(ln["amount"] for ln in rb["lines"] if ln["cost_category_code"] == "GRUNDSTEUER"))
        assert gs_a + gs_b == Decimal("800.00")

    @pytest.mark.asyncio
    async def test_pdf_only_for_finalized(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        # Draft (not finalized) -> PDF 409.
        resp = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_finalized_run_cannot_recompute(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        # Re-compute a finalized run -> 409 (service guards; correction = new run).
        resp = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_correction_creates_new_run(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        corr = await client_a_admin.post(f"/api/settlements/{run_id}/correction")
        assert corr.status_code == 201
        new_run = corr.json()
        assert new_run["status"] == "draft"
        assert new_run["id"] != run_id
        # Original now points to the new run (forward pointer), stays finalized.
        orig = await client_a_admin.get(f"/api/settlements/{run_id}")
        assert orig.json()["superseded_by_id"] == new_run["id"]
        assert orig.json()["status"] == "finalized"

    @pytest.mark.asyncio
    async def test_identical_results_retain_distinct_attestation_hashes(self, client_a_admin, m3_seed):
        """Same arithmetic, distinct evidence; persisted hashes remain stable."""
        extras = _extras_for_exit(m3_seed)
        r1 = await _create_run(client_a_admin, m3_seed, "lease_e", "1500.00")
        r2 = await _create_run(client_a_admin, m3_seed, "lease_e", "1500.00")
        f1 = (await client_a_admin.post(f"/api/settlements/{r1}/finalize", json=extras)).json()
        f2 = (await client_a_admin.post(f"/api/settlements/{r2}/finalize", json=extras)).json()
        for key in ("total_cost", "balance", "advance_payments_total"):
            assert f1[key] == f2[key]
        assert f1["snapshot_sha256"] != f2["snapshot_sha256"]
        assert (await client_a_admin.get(f"/api/settlements/{r1}")).json()["snapshot_sha256"] == f1["snapshot_sha256"]

    @pytest.mark.asyncio
    async def test_payload_consistent_with_lines(self, client_a_admin, m3_seed, app_rw_session):
        """snapshot_payload.result totals == settlement_line sums (I-G)."""
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        # Read the snapshot payload directly.
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        row = await app_rw_session.execute(
            text("SELECT snapshot_payload, total_cost, balance FROM settlement_run WHERE id=:id"),
            {"id": run_id},
        )
        payload, total_cost, balance = row.first()
        assert Decimal(payload["result"]["total_cost"]) == total_cost
        assert Decimal(payload["result"]["balance"]) == balance
        # Sum of COST lines in payload == total_cost.
        cost_sum = sum(
            Decimal(l["amount"]) for l in payload["result"]["lines"] if l["line_type"] == "COST"
        )
        assert cost_sum == total_cost


# ===========================================================================
# Regression: M1[)<->M3[] read-edge conversion (the live 500 reproduction)
# ===========================================================================
@pytest.fixture(scope="function")
def adjacent_lease_seed(seed_data):
    """One unit, two ADJACENT leases seeded in the TRUE M1 half-open [) convention.

    Weber valid_from=2025-01-01, valid_to=2025-07-01 (exclusive upper bound)
    Klein valid_from=2025-07-01, valid_to=NULL (open-ended)

    These do NOT overlap in the DB: the EXCLUDE uses daterange(.., '[)') and the
    point-in-time query uses valid_to > target, so 01.07. belongs only to Klein.
    Before the read-edge conversion the engine read both valid_to/valid_from as
    tag-inclusive, double-counted 01.07., and raised PeriodOverlapError -> 500.

    Function-scoped (not session) so it is independent of the big m3_seed and the
    DB is clean per test (the autouse run_migrations fixture rebuilds the schema).
    """
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "u1": uuid.uuid4(),
        "person_owner": uuid.uuid4(),
        "owner_profile": uuid.uuid4(),
        "tp_weber": uuid.uuid4(),
        "tp_klein": uuid.uuid4(),
        "lease_weber": uuid.uuid4(),
        "lease_klein": uuid.uuid4(),
        "p_weber": uuid.uuid4(),
        "p_klein": uuid.uuid4(),
    }
    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        def cat(code):
            cur.execute(
                "SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,)
            )
            return cur.fetchone()[0]

        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "Adjacent-Lease Mischhaus", "MISCH"),
        )
        # Single unit, area 80 (the only unit -> it bears 100% of any Flaeche cost).
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["u1"]), org, str(ids["property"]), "1", "WOHNUNG", 80, 80, 300),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_owner"]), org, "Olga", "Owner"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_profile"]), org, str(ids["person_owner"])),
        )
        cur.execute(
            "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), org, str(ids["u1"]), str(ids["owner_profile"]), date(2000, 1, 1), None),
        )
        for pkey, tpkey, first in [("p_weber", "tp_weber", "Walter"), ("p_klein", "tp_klein", "Karla")]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
                (str(ids[pkey]), org, first, "Mieter"),
            )
            cur.execute(
                "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
                (str(ids[tpkey]), org, str(ids[pkey])),
            )

        # The crux: adjacent valid_to = valid_from = 2025-07-01 in DB [) convention.
        leases = [
            (ids["lease_weber"], ids["tp_weber"], date(2025, 1, 1), date(2025, 7, 1)),
            (ids["lease_klein"], ids["tp_klein"], date(2025, 7, 1), None),
        ]
        for lid, tp, vf, vt in leases:
            cur.execute(
                "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, valid_from, valid_to, base_rent, advance_payment) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(lid), org, str(ids["u1"]), str(tp), vf, vt, Decimal("800.00"), Decimal("100.00")),
            )

        # A confirmed, full-year GRUNDSTEUER (WOHNFLAECHE). With a single unit the
        # unit bears the whole 1200.00, split purely by time between the two tenants.
        ce = uuid.uuid4()
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
            "invoice_date, service_period_from, service_period_to, gross_amount, tenant_distribution_key, "
            "tenant_apportionable, status, source, confirmed_at, confirmed_by) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
            (
                str(ce), org, str(ids["property"]), str(cat("GRUNDSTEUER")),
                date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                Decimal("1200.00"), "WOHNFLAECHE", "UMLAGEFAEHIG",
                str(seed_data["user_a_admin_id"]),
            ),
        )
        ids["ce_grundsteuer"] = ce
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


class TestM3AdjacentLeaseReadEdge:
    """The live bug: adjacent leases (M1 [)-convention) must compute, not 500.

    This is the regression test that was missing. It seeds via the DB in the real
    M1 convention and drives the public /compute endpoint, so it exercises the
    repository read-edge conversion (settlement_service.db_exclusive_to_engine_
    inclusive) end-to-end -- exactly where the conversion was missing.
    """

    async def _create_run(self, client, seed, lease_key):
        resp = await client.post("/api/settlements", json={
            'heating_advance_payments_total': '0.00', "property_id": seed["property"],
            "lease_agreement_id": seed[lease_key],
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "advance_payments_total": "0.00",
        })
        assert resp.status_code == 201, resp.text
        await attest_fixture(client, "settlement", resp.json()["id"],
            {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "MANUAL", "advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})
        return resp.json()["id"]

    @pytest.mark.asyncio
    async def test_adjacent_leases_compute_succeeds_no_500(self, client_a_admin, adjacent_lease_seed):
        """Compute for the predecessor tenant succeeds (no false overlap, no 500)."""
        run_id = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_weber")
        resp = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json={})
        assert resp.status_code == 200, resp.text  # before the fix this was 500

    @pytest.mark.asyncio
    async def test_weber_181_days(self, client_a_admin, adjacent_lease_seed):
        """Predecessor Weber occupies 01.01.-30.06. = 181 days (DB 2025-07-01 -> 30.06.)."""
        run_id = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_weber")
        body = (await client_a_admin.post(f"/api/settlements/{run_id}/compute", json={})).json()
        gs = next(ln for ln in body["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        assert gs["time_share_days"] == 181
        assert gs["time_share_total_days"] == 365
        # 1200 * 181/365 = 595.0684... -> 595.07
        assert gs["amount"] == "595.07"

    @pytest.mark.asyncio
    async def test_klein_184_days(self, client_a_admin, adjacent_lease_seed):
        """Successor Klein occupies 01.07.-31.12. = 184 days (open-ended, clamped)."""
        run_id = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_klein")
        body = (await client_a_admin.post(f"/api/settlements/{run_id}/compute", json={})).json()
        gs = next(ln for ln in body["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        assert gs["time_share_days"] == 184
        assert gs["time_share_total_days"] == 365
        # 1200 * 184/365 = 604.9315... -> 604.93
        assert gs["amount"] == "604.93"

    @pytest.mark.asyncio
    async def test_time_shares_sum_to_one_no_vacancy(self, client_a_admin, adjacent_lease_seed):
        """Weber 181 + Klein 184 = 365 (Sigma time shares == 1); no vacancy day.

        The back-to-back boundary (30.06. -> 01.07.) leaves NO gap: the two tenant
        shares of the unit's GRUNDSTEUER add up to the full unit amount (1200.00),
        so no cent is attributed to vacancy/owner. This is the structural invariant
        the read-edge conversion preserves (ADR-0004 #4).
        """
        run_weber = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_weber")
        run_klein = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_klein")
        bw = (await client_a_admin.post(f"/api/settlements/{run_weber}/compute", json={})).json()
        bk = (await client_a_admin.post(f"/api/settlements/{run_klein}/compute", json={})).json()
        gw = next(ln for ln in bw["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        gk = next(ln for ln in bk["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        assert gw["time_share_days"] + gk["time_share_days"] == 365
        # No cent lost to a phantom 1-day vacancy: the two tenant shares == full cost.
        assert Decimal(gw["amount"]) + Decimal(gk["amount"]) == Decimal("1200.00")

    @pytest.mark.asyncio
    async def test_db_exclude_rejects_genuine_same_unit_overlap(
        self, client_a_admin, adjacent_lease_seed, app_rw_session
    ):
        """Primary guard: the DB EXCLUDE rejects a genuinely overlapping lease.

        A lease 2025-03-01..2025-09-01 on U1 overlaps Weber [2025-01-01,2025-07-01)
        even in the half-open DB convention, so excl_lease_no_overlap must reject the
        INSERT (fail-closed) -- a real overlap can never silently reach the engine.
        """
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(adjacent_lease_seed["org_a_id"])},
        )
        with pytest.raises(Exception):  # ExclusionViolation from excl_lease_no_overlap
            await app_rw_session.execute(
                text(
                    "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
                    "valid_from, valid_to, base_rent, advance_payment) "
                    "VALUES (:id,:org,:unit,:tp,'2025-03-01','2025-09-01','800.00','100.00')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org": str(adjacent_lease_seed["org_a_id"]),
                    "unit": adjacent_lease_seed["u1"],
                    "tp": adjacent_lease_seed["tp_weber"],
                },
            )


# ===========================================================================
# Unit test: the read-edge conversion helper itself
# ===========================================================================
class TestReadEdgeConversionHelper:
    def test_exclusive_to_inclusive_subtracts_one_day(self):
        from app.services.settlement_service import db_exclusive_to_engine_inclusive

        # DB stores the first NON-occupied day; engine wants the last occupied day.
        assert db_exclusive_to_engine_inclusive(date(2025, 7, 1)) == date(2025, 6, 30)
        assert db_exclusive_to_engine_inclusive(date(2025, 4, 1)) == date(2025, 3, 31)
        # Month/year boundary correctness.
        assert db_exclusive_to_engine_inclusive(date(2026, 1, 1)) == date(2025, 12, 31)

    def test_none_stays_open_ended(self):
        from app.services.settlement_service import db_exclusive_to_engine_inclusive

        assert db_exclusive_to_engine_inclusive(None) is None


@pytest.fixture
def derived_period_seed(seed_data):
    import psycopg2
    seed = _seed_m3(seed_data)
    bank = str(uuid.uuid4())
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO bank_account (id,organization_id,property_id,account_type,label) VALUES (%s,%s,%s,'LAUFEND','T08')",
                        (bank, str(seed["org_a_id"]), seed["property"]))
            for year in (2025, 2026):
                for month in range(1, 13):
                    for kind, amount in (("NK_VORAUSZAHLUNG", "100.00"), ("KALTMIETE", "500.00")):
                        cur.execute("""INSERT INTO payment
                            (id,organization_id,bank_account_id,property_id,direction,amount,value_date,
                             payment_type,tenant_profile_id,lease_agreement_id,period_year,period_month,
                             status,booked_at,booked_by)
                            VALUES (%s,%s,%s,%s,'IN',%s,%s,%s,%s,%s,%s,%s,'booked',now(),%s)""",
                            (str(uuid.uuid4()), str(seed["org_a_id"]), bank, seed["property"], amount,
                             date(year, month, 1), kind, seed["tp_e"], seed["lease_e"], year, month,
                             str(seed["user_a_admin_id"])))
            cur.execute("""INSERT INTO payment
                (id,organization_id,bank_account_id,property_id,direction,amount,value_date,
                 payment_type,tenant_profile_id,lease_agreement_id,period_year,period_month,status)
                VALUES (%s,%s,%s,%s,'IN',999,'2025-08-01','NK_VORAUSZAHLUNG',%s,%s,2025,8,'draft')""",
                (str(uuid.uuid4()), str(seed["org_a_id"]), bank, seed["property"], seed["tp_e"], seed["lease_e"]))
    return seed


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end,expected", [
    ("2025-07-01", "2026-06-30", "1200.00"),
    ("2025-01-01", "2025-09-30", "900.00"),
])
async def test_derived_advances_for_exact_period(client_a_admin, derived_period_seed, start, end, expected):
    import psycopg2
    seed = derived_period_seed
    if end == "2025-09-30":
        with psycopg2.connect(SYNC_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE lease_agreement SET valid_to='2025-10-01' WHERE id=%s", (seed["lease_e"],))
    resp = await client_a_admin.post("/api/settlements", json={
        'heating_advance_payments_total': '0.00', "property_id": seed["property"], "lease_agreement_id": seed["lease_e"],
        "period_start": start, "period_end": end, "advance_payments_total": "0.00",
    })
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]
    resp = await client_a_admin.patch(f"/api/settlements/{run_id}/advance-payments-derived", params={"period_year": 2025})
    assert resp.status_code == 200, resp.text
    assert resp.json()["advance_payments_total"] == expected
    assert resp.json()["advance_payments_source"] == "DERIVED"


@pytest.mark.asyncio
async def test_batch_creates_three_drafts_and_repeat_skips_same_ids(client_a_admin, m3_seed):
    body = {
        "property_id": m3_seed["property"],
        "period_start": "2037-01-01", "period_end": "2037-12-31",
    }
    response = await client_a_admin.post("/api/settlements/batch", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["skipped"] == []
    assert {r["lease_agreement_id"] for r in result["created"]} == {
        m3_seed["lease_b"], m3_seed["lease_d"], m3_seed["lease_e"],
    }
    assert len({r["unit_id"] for r in result["created"]}) == 3
    for run in result["created"]:
        assert run["status"] == "draft"
        assert run["run_type"] == "BK_MIETER"
        assert run["advance_payments_source"] == "MANUAL"
        assert run["advance_payments_total"] is None
        assert run["total_cost"] is None
        assert run["balance"] is None
        assert run["snapshot_sha256"] is None
        assert run["lines"] == []
    repeated = await client_a_admin.post("/api/settlements/batch", json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] == []
    assert {s["lease_agreement_id"]: s["settlement_run_id"] for s in repeated.json()["skipped"]} == {
        r["lease_agreement_id"]: r["id"] for r in result["created"]
    }


@pytest.fixture
def batch_seed(seed_data):
    return _seed_m3(seed_data)


def _batch_body(seed, start="2038-01-01", end="2038-12-31", **extra):
    return {"property_id": seed["property"], "period_start": start, "period_end": end, **extra}


def _batch_rows(seed, run_ids=None):
    """Observe synthetic rows, including effects hidden from ordinary reads."""
    import psycopg2
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM settlement_run WHERE property_id=%s ORDER BY id", (seed["property"],))
            ids = run_ids if run_ids is not None else [str(r[0]) for r in cur.fetchall()]
            result = []
            for table, predicate in (
                ("settlement_run", "id=ANY(%s::uuid[])"),
                ("settlement_line", "settlement_run_id=ANY(%s::uuid[])"),
                ("audit_event", "entity_id=ANY(%s::uuid[])"),
                ("accounting_scope_binding", "settlement_run_id=ANY(%s::uuid[])"),
            ):
                cur.execute(f"SELECT to_jsonb(t) FROM {table} t WHERE {predicate} ORDER BY id", (ids,))
                result.append(cur.fetchall())
            cur.execute("SELECT to_jsonb(p) FROM payment p WHERE property_id=%s ORDER BY id", (seed["property"],))
            result.append(cur.fetchall())
            return result


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end,leases", [
    ("2025-06-30", "2025-06-30", {"lease_a", "lease_e"}),
    ("2025-07-01", "2025-07-01", {"lease_b", "lease_e"}),
    ("2025-03-31", "2025-04-01", {"lease_a", "lease_c", "lease_e"}),
    ("2025-08-01", "2025-08-01", {"lease_b", "lease_d", "lease_e"}),
    ("2024-01-01", "2024-12-31", set()),
])
async def test_batch_exclusive_lease_end_inclusive_period(client_a_admin, batch_seed, start, end, leases):
    _seed_m3(batch_seed)  # Another property must not contribute its leases.
    response = await client_a_admin.post("/api/settlements/batch", json=_batch_body(batch_seed, start, end))
    assert response.status_code == 200, response.text
    assert response.json()["skipped"] == []
    assert {r["lease_agreement_id"] for r in response.json()["created"]} == {batch_seed[k] for k in leases}


@pytest.mark.asyncio
async def test_batch_existing_draft_and_finalized_untouched(client_a_admin, batch_seed):
    finalized = await _create_run(client_a_admin, batch_seed, "lease_a", "600.00")
    response = await client_a_admin.post(f"/api/settlements/{finalized}/finalize", json=_extras_for_exit(batch_seed))
    assert response.status_code == 200, response.text
    draft = await _create_run(client_a_admin, batch_seed, "lease_b", "700.00")
    before = _batch_rows(batch_seed, [finalized, draft])
    response = await client_a_admin.post("/api/settlements/batch", json=_batch_body(batch_seed, "2025-01-01", "2025-12-31"))
    assert response.status_code == 200, response.text
    assert len(response.json()["created"]) == 3
    assert {s["settlement_run_id"] for s in response.json()["skipped"]} == {finalized, draft}
    assert _batch_rows(batch_seed, [finalized, draft]) == before
    correction = await client_a_admin.post(f"/api/settlements/{finalized}/correction")
    assert correction.status_code == 201, correction.text
    successor = correction.json()["id"]
    before = _batch_rows(batch_seed)
    repeated = await client_a_admin.post("/api/settlements/batch", json=_batch_body(batch_seed, "2025-01-01", "2025-12-31"))
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] == []
    skipped = {r["settlement_run_id"] for r in repeated.json()["skipped"]}
    assert len(skipped) == 5 and successor in skipped and finalized not in skipped
    assert _batch_rows(batch_seed) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"property_id": "invalid"}, {"accounting_scope_id": "invalid"},
    {"period_start": "2025-02-30"}, {"period_end": "invalid"},
    {"period_start": "2039-01-01", "period_end": "2038-12-31"},
    {"advance_payments_total": "100.00"}, {"management_mandate_id": str(uuid.uuid4())},
    {"currency": "EUR"},
])
async def test_batch_invalid_body_rejected(client_a_admin, batch_seed, override):
    response = await client_a_admin.post("/api/settlements/batch", json={**_batch_body(batch_seed), **override})
    assert response.status_code == 422, response.text
    assert _batch_rows(batch_seed) == [[], [], [], [], []]


@pytest.mark.asyncio
async def test_batch_roles_foreign_ids_and_empty_property(client_a_admin, client_a_readonly, client_b_admin, batch_seed):
    import httpx
    from app.main import app
    body = _batch_body(batch_seed)
    assert (await client_a_readonly.post("/api/settlements/batch", json=body)).status_code == 403
    assert (await client_b_admin.post("/api/settlements/batch", json=body)).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.post("/api/settlements/batch", json=body)).status_code == 401
    assert (await client_a_admin.post("/api/settlements/batch", json={**body, "property_id": str(uuid.uuid4())})).status_code == 404
    empty = await client_a_admin.post("/api/properties", json={"name": "Synthetic empty batch property", "management_type": "MIET"})
    assert empty.status_code == 201, empty.text
    body["property_id"] = empty.json()["id"]
    response = await client_a_admin.post("/api/settlements/batch", json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {"created": [], "skipped": []}
    assert (await client_b_admin.post("/api/settlements/batch", json=body)).status_code == 404


@pytest.mark.asyncio
async def test_batch_parallel_requests_create_exactly_three(client_a_admin, batch_seed):
    import asyncio
    responses = await asyncio.gather(*[
        client_a_admin.post("/api/settlements/batch", json=_batch_body(batch_seed)) for _ in range(2)
    ])
    for response in responses:
        assert response.status_code == 200, response.text
    assert sorted(len(r.json()["created"]) for r in responses) == [0, 3]
    created = [run for response in responses for run in response.json()["created"]]
    skipped = [run for response in responses for run in response.json()["skipped"]]
    assert {r["id"] for r in created} == {r["settlement_run_id"] for r in skipped}
    rows = _batch_rows(batch_seed)
    assert len(rows[0]) == len(rows[2]) == 3
    assert len({row[0]["lease_agreement_id"] for row in rows[0]}) == 3


@pytest.mark.asyncio
async def test_batch_second_create_failure_rolls_back_runs_and_audit(client_a_admin, batch_seed, monkeypatch):
    from app.services.settlement_service import SettlementService
    original = SettlementService.create_draft
    created_ids = []

    async def fail_after_second(self, *args, **kwargs):
        run = await original(self, *args, **kwargs)
        created_ids.append(str(run.id))
        if len(created_ids) == 2:
            raise RuntimeError("synthetic failure after second draft and audit")
        return run

    before = _batch_rows(batch_seed)
    with monkeypatch.context() as patch:
        patch.setattr(SettlementService, "create_draft", fail_after_second)
        with pytest.raises(RuntimeError, match="synthetic failure after second"):
            await client_a_admin.post("/api/settlements/batch", json=_batch_body(batch_seed))
    assert len(created_ids) == 2
    assert _batch_rows(batch_seed) == before
    assert _batch_rows(batch_seed, created_ids) == before
    retry = await client_a_admin.post("/api/settlements/batch", json=_batch_body(batch_seed))
    assert retry.status_code == 200, retry.text
    assert len(retry.json()["created"]) == 3 and retry.json()["skipped"] == []


def _batch_scope(seed, *, units=None, authorized=True, scope_type="MIET"):
    import psycopg2
    scope_id, grant_id = str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO accounting_scope
                (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
                VALUES (%s,%s,%s,%s,'Synthetic batch scope','1900-01-01',%s,'REVIEWED',%s)""",
                (scope_id, str(seed["org_a_id"]), seed["property"], scope_type, units is None, str(seed["user_a_admin_id"])))
            for unit in units or []:
                cur.execute("INSERT INTO accounting_scope_unit(organization_id,scope_id,unit_id,property_id) VALUES (%s,%s,%s,%s)",
                    (str(seed["org_a_id"]), scope_id, unit, seed["property"]))
            if authorized:
                cur.execute("""INSERT INTO scope_grant
                    (id,organization_id,scope_id,user_id,capabilities,granted_by,reason)
                    VALUES (%s,%s,%s,%s,ARRAY['read','prepare'],%s,'Synthetic batch preparation')""",
                    (grant_id, str(seed["org_a_id"]), scope_id, str(seed["user_a_admin_id"]), str(seed["user_a_admin_id"])))
    return scope_id, grant_id


@pytest.mark.asyncio
async def test_batch_scope_selection_partial_units_and_revocation(worker_tenant):
    from tests.strict_finance_fixture import classify_property_finance
    client, seed = worker_tenant
    seed = _seed_m3(seed)
    classified = await classify_property_finance(seed, seed["property"], scope_type="MIET")
    scope_a = str(classified["scope"])
    body = _batch_body(seed, accounting_scope_id=scope_a)
    first = await client.post("/api/settlements/batch", json=body)
    assert first.status_code == 200, first.text
    assert len(first.json()["created"]) == 3
    original_ids = {r["id"] for r in first.json()["created"]}
    before = _batch_rows(seed, list(original_ids))
    hidden, _ = _batch_scope(seed, authorized=False)
    denied = await client.post("/api/settlements/batch", json={**body, "accounting_scope_id": hidden})
    assert denied.status_code == 404, denied.text
    assert not any(run_id in denied.text for run_id in original_ids)
    # Admin status must not substitute for a scope grant, even with no leases.
    denied = await client.post("/api/settlements/batch", json=_batch_body(seed, "1901-01-01", "1901-12-31", accounting_scope_id=hidden))
    assert denied.status_code == 404, denied.text
    second_scope, _ = _batch_scope(seed)
    second = await client.post("/api/settlements/batch", json={**body, "accounting_scope_id": second_scope})
    assert second.status_code == 200, second.text
    assert len(second.json()["created"]) == 3 and second.json()["skipped"] == []
    second_ids = {r["id"] for r in second.json()["created"]}
    assert original_ids.isdisjoint(second_ids)
    repeated = await client.post("/api/settlements/batch", json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] == []
    assert {r["settlement_run_id"] for r in repeated.json()["skipped"]} == original_ids
    assert not any(run_id in repeated.text for run_id in second_ids)
    assert _batch_rows(seed, list(original_ids)) == before
    partial, partial_grant = _batch_scope(seed, units=[seed["u1"]])
    partial_body = {**body, "accounting_scope_id": partial}
    response = await client.post("/api/settlements/batch", json=partial_body)
    assert response.status_code == 200, response.text
    assert [r["lease_agreement_id"] for r in response.json()["created"]] == [seed["lease_b"]]
    run = response.json()["created"][0]
    rows = _batch_rows(seed, [run["id"]])
    assert rows[3][0][0]["scope_id"] == partial
    repeated = await client.post("/api/settlements/batch", json=partial_body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == {"created": [], "skipped": [{"lease_agreement_id": seed["lease_b"], "settlement_run_id": run["id"]}]}
    revoked = await client.post(f"/api/accounting-scopes/{partial}/grants/{partial_grant}/revoke")
    assert revoked.status_code == 204, revoked.text
    response = await client.post("/api/settlements/batch", json=partial_body)
    assert response.status_code == 404, response.text
    assert run["id"] not in response.text
    assert _batch_rows(seed, [run["id"]]) == rows
    assert (await client.post("/api/settlements/batch", json=_batch_body(seed))).status_code == 409
    weg, _ = _batch_scope(seed, scope_type="WEG")
    assert (await client.post("/api/settlements/batch", json={**body, "accounting_scope_id": weg})).status_code == 422
    other = _seed_m3(seed)
    wrong_property, _ = _batch_scope(other)
    assert (await client.post("/api/settlements/batch", json={**body, "accounting_scope_id": wrong_property})).status_code == 422


@pytest.mark.asyncio
async def test_batch_source_revocation_never_returns_partial_leases(worker_tenant):
    import psycopg2
    from tests.contact_source_fixtures import grant_property_contacts
    from tests.strict_finance_fixture import classify_property_finance
    from tests.test_property_source_access import grant
    from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
    from app.core.db import get_tenant_session
    client, seed = worker_tenant
    seed = _seed_m3(seed)
    classified = await classify_property_finance(seed, seed["property"], scope_type="MIET")
    await grant_property_contacts(client, seed, seed["property"])
    domain, source_grant = await grant(client, seed, seed["property"], caps=["read", "prepare"])
    body = _batch_body(seed, accounting_scope_id=str(classified["scope"]))
    with psycopg2.connect(SYNC_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT person_id FROM tenant_profile WHERE id=%s", (seed["tp_b"],))
        person = cur.fetchone()[0]
        cur.execute("""SELECT d.id,g.id FROM person_data_domain d JOIN person_data_grant g ON g.domain_id=d.id
            WHERE d.person_id=%s AND g.user_id=%s AND g.revoked_at IS NULL""", (str(person), str(seed["user_a_admin_id"])))
        contact_domain, contact_grant = cur.fetchone()
    async with synthetic_enforcement(seed):
        first = await client.post("/api/settlements/batch", json=body)
        assert first.status_code == 200, first.text
        assert len(first.json()["created"]) == 3
        before = _batch_rows(seed)
        revoked = await client.post(f"/api/person-data-domains/{contact_domain}/grants/{contact_grant}/revoke")
        assert revoked.status_code == 204, revoked.text
        # Contact RLS hides the expected source even though history remains
        # property-readable. Counting lease rows alone cannot authorize a batch.
        async with get_tenant_session(seed["org_a_id"]) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),
                {"actor": str(seed["user_a_admin_id"]), "scope": str(classified["scope"])})
            visible = (await db.execute(text("SELECT id FROM lease_agreement WHERE id IN (:b,:d,:e)"),
                {k: uuid.UUID(seed[f"lease_{k}"]) for k in ("b", "d", "e")})).scalars().all()
            assert set(map(str, visible)) == {seed["lease_b"], seed["lease_d"], seed["lease_e"]}
            assert await db.scalar(text("SELECT count(*) FROM tenant_profile WHERE id=:id"),
                {"id": uuid.UUID(seed["tp_b"])}) == 0
        for start, end in (("2038-01-01", "2038-12-31"), ("2039-01-01", "2039-12-31"), ("1901-01-01", "1901-12-31")):
            response = await client.post("/api/settlements/batch", json={**body, "period_start": start, "period_end": end})
            assert response.status_code == 404, response.text
        assert _batch_rows(seed) == before
        restored = await client.post(f"/api/person-data-domains/{contact_domain}/grants",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"user_id": str(seed["user_a_admin_id"]), "capabilities": ["read", "prepare", "export"], "reason_code": "TEAM_ACCESS"})
        assert restored.status_code == 201, restored.text
        repeated = await client.post("/api/settlements/batch", json=body)
        assert repeated.status_code == 200, repeated.text
        assert len(repeated.json()["skipped"]) == 3
        revoked = await client.post(f"/api/property-data-domains/{domain}/grants/{source_grant}/revoke")
        assert revoked.status_code == 204, revoked.text
        assert (await client.post("/api/settlements/batch", json=body)).status_code == 404
        async with get_tenant_session(seed["org_a_id"]) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
                {"actor": str(seed["user_a_admin_id"])})
            assert await db.scalar(text("SELECT count(*) FROM lease_agreement WHERE id=:id"),
                {"id": uuid.UUID(seed["lease_b"])}) == 0
        assert _batch_rows(seed) == before
        await change_mode(seed, "PAUSED")
        assert (await client.post("/api/settlements/batch", json=body)).status_code == 404


@pytest.mark.asyncio
async def test_batch_clerk_uses_exact_period_not_another_year(client_a_clerk, batch_seed):
    existing = await client_a_clerk.post("/api/settlements", json={
        **_batch_body(batch_seed, "2037-01-01", "2037-12-31"),
        "lease_agreement_id": batch_seed["lease_b"],
    })
    assert existing.status_code == 201, existing.text
    run_id = existing.json()["id"]
    before = _batch_rows(batch_seed, [run_id])
    response = await client_a_clerk.post("/api/settlements/batch", json=_batch_body(batch_seed))
    assert response.status_code == 200, response.text
    assert len(response.json()["created"]) == 3 and response.json()["skipped"] == []
    assert all(r["period_start"] == "2038-01-01" and r["period_end"] == "2038-12-31" for r in response.json()["created"])
    assert run_id not in response.text
    assert _batch_rows(batch_seed, [run_id]) == before

@pytest.mark.asyncio
async def test_advance_proposal_finalized_tenant_cost(client_a_admin, m3_seed):
    run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
    finalized = await client_a_admin.post(
        f"/api/settlements/{run_id}/finalize", json=_extras_for_exit(m3_seed)
    )
    assert finalized.status_code == 200, finalized.text
    response = await client_a_admin.get(f"/api/settlements/{run_id}/advance-proposal")
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["lease_agreement_id"] == m3_seed["lease_a"]
    assert proposal["tenant_cost"] == "643.97"  # 901.85 less 210.00 heating and 47.88 CO2.
    assert proposal["heating_tenant_cost"] == "257.88"
    assert proposal["months"] == "6"
    from decimal import ROUND_HALF_UP
    assert Decimal(proposal["monthly_amount"]) == (Decimal(proposal["tenant_cost"]) / 6).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# T28 uses the same isolated live-API contract fixtures as T23.


async def _proposal_run(api, world, db, *, cost="1200.00", start="2026-01-01", end="2026-12-31"):
    await db.execute(text("""INSERT INTO cost_entry
        (id,organization_id,property_id,cost_category_id,cost_category_plan_version,
         invoice_date,service_period_from,service_period_to,gross_amount,tenant_distribution_key,
         tenant_apportionable,status,source,confirmed_at,confirmed_by)
        SELECT :id,:org,:property,id,1,:start,:start,:end,:cost,'WOHNFLAECHE',
         'UMLAGEFAEHIG','confirmed','MANUAL',now(),:actor FROM cost_category
        WHERE code='GRUNDSTEUER' AND plan_version=1"""),
        {"id": uuid.uuid4(), "org": world["org"], "property": world["property"],
         "actor": world["admin"], "start": date.fromisoformat(start),
         "end": date.fromisoformat(end), "cost": Decimal(cost)})
    await db.commit()
    response = await api.post("/api/settlements", json={
        'heating_advance_payments_total': '0.00', "property_id": str(world["property"]), "lease_agreement_id": str(world["lease"]),
        "period_start": start, "period_end": end, "advance_payments_total": "999.00",
    })
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    await attest_fixture(api, "settlement", run_id,
        {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "MANUAL", "advance_payments_total": "999.00", "advance_payments_source": "MANUAL"})
    response = await api.post(f"/api/settlements/{run_id}/finalize")
    assert response.status_code == 200, response.text
    return run_id, response.json()


def _proposal_snapshot(cost="1200.00", start="2026-01-01", end="2026-12-31", *, tenancy_start=None, tenancy_end=None):
    from app.models.settlement_run import SettlementRun
    lease = uuid.uuid4()
    return SettlementRun(
        lease_agreement_id=lease, total_cost=Decimal(cost),
        period_start=date.fromisoformat(start), period_end=date.fromisoformat(end),
        snapshot_payload={"run_type": "BK_MIETER", "period": {"start": start, "end": end},
            "inputs": {"lease_periods": [{"lease_agreement_id": str(lease),
                "from": tenancy_start or start, "to": tenancy_end}]},
            "result": {"total_cost": cost, "balance": "-99999.00", 'lines': [
                {'line_type': 'COST', 'category_code': 'GRUNDSTEUER', 'amount': cost}]}},
    )


def test_legacy_advance_proposal_splits_only_the_frozen_cost_lines():
    from app.services.settlement_service import SettlementService
    from copy import deepcopy
    run = _proposal_snapshot('2900.00')
    run.snapshot_payload['result']['lines'] = [
        {'line_type': 'COST', 'category_code': 'GRUNDSTEUER', 'amount': '1900.00'},
        {'line_type': 'COST', 'category_code': 'MESSDIENST', 'amount': '1000.00'}]
    frozen = deepcopy(run.snapshot_payload)
    result = SettlementService._advance_proposal(run)
    assert (result['monthly_amount'], result['heating_monthly_amount']) == ('158.33', '83.33')
    assert run.snapshot_payload == frozen


@pytest.mark.parametrize("cost,start,end,tenancy_start,tenancy_end,months,amount", [
    ("1200.00", "2026-01-01", "2026-12-31", None, None, Decimal(12), "100.00"),
    ("720.00", "2026-01-01", "2026-12-31", None, "2026-06-30", Decimal(6), "120.00"),
    ("80.00", "2026-01-01", "2026-01-31", "2026-01-16", None, Decimal(16)/31, "155.00"),
    ("0.08", "2026-01-01", "2026-01-31", "2026-01-16", None, Decimal(16)/31, "0.16"),
    ("29.00", "2024-02-01", "2024-02-29", "2024-02-16", None, Decimal(14)/29, "60.07"),
    ("0.00", "2026-01-01", "2026-12-31", None, None, Decimal(12), "0.00"),
    ("12.06", "2026-01-01", "2026-12-31", None, None, Decimal(12), "1.01"),
    ("63.00", "2026-01-16", "2026-02-14", "2025-01-01", None, Decimal(63)/62, "62.00"),
    ("0.17", "2024-01-01", "2024-02-05", None, None, Decimal(34)/29, "0.15"),
])
def test_proposal_frozen_calendar_month_basis(cost, start, end, tenancy_start, tenancy_end, months, amount):
    from app.services.settlement_service import SettlementService
    run = _proposal_snapshot(cost, start, end, tenancy_start=tenancy_start, tenancy_end=tenancy_end)
    result = SettlementService._advance_proposal(run)
    assert abs(Decimal(result["months"]) - months) < Decimal("1e-26")
    assert result["monthly_amount"] == amount
    assert result["tenant_cost"] == cost
    assert result["period_start"] == date.fromisoformat(start)
    assert result["period_end"] == date.fromisoformat(end)


@pytest.mark.parametrize("problem", ["missing", "lease", "end", "negative", "nan", "infinity", "overflow", "mismatch", "period", "empty"])
def test_proposal_invalid_frozen_basis_fails_closed(problem):
    from fastapi import HTTPException
    from app.services.settlement_service import SettlementService
    run = _proposal_snapshot()
    if problem == "missing":
        run.snapshot_payload = {}
    elif problem == "lease":
        run.snapshot_payload["inputs"]["lease_periods"] = []
    elif problem == "end":
        del run.snapshot_payload["inputs"]["lease_periods"][0]["to"]
    elif problem in {"negative", "nan", "infinity", "overflow"}:
        value = {"negative": "-1", "nan": "NaN", "infinity": "Infinity", "overflow": "99999999999999"}[problem]
        run.total_cost = Decimal(value)
        run.snapshot_payload["result"]["total_cost"] = value
    elif problem == "mismatch":
        run.total_cost = Decimal("1")
    elif problem == "period":
        run.snapshot_payload["period"]["start"] = "2027-01-01"
    else:
        run.snapshot_payload["inputs"]["lease_periods"][0]["from"] = "2027-01-01"
    with pytest.raises(HTTPException) as error:
        SettlementService._advance_proposal(run)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_proposal_accept_preserves_history_and_frozen_rows(rent_world, rent_db):
    world = rent_world
    async with api_client(world) as api:
        bank = uuid.uuid4()
        await rent_db.execute(text("""INSERT INTO bank_account
            (id,organization_id,property_id,account_type,label)
            VALUES (:id,:org,:property,'LAUFEND','T28 booked advances')"""), {**world, "id":bank})
        await rent_db.commit()
        response = await api.post("/api/payments", json={
            "bank_account_id":str(bank), "direction":"IN", "amount":"150.00",
            "value_date":"2026-03-05", "payment_type":"NK_VORAUSZAHLUNG",
            "tenant_profile_id":str(world["tenant"]), "lease_agreement_id":str(world["lease"]),
            "period_year":2026, "period_month":3,
        })
        assert response.status_code == 201, response.text
        assert (await api.post(f"/api/payments/{response.json()['id']}/book")).status_code == 200
        run_id, frozen = await _proposal_run(api, world, rent_db)
        url = f"/api/settlements/{run_id}/advance-proposal"
        original = (await api.get(f"/api/leases/{world['lease']}")).json()
        history = f"/api/leases/{world['lease']}/rent-changes"
        for valid_from, base in [("2026-02-01", "750.00"), ("2026-10-01", "900.00")]:
            result = await api.post(history, json={"valid_from": valid_from, "base_rent": base, "advance_payment": "160.00"})
            assert result.status_code == 201, result.text
        proposal = (await api.get(url)).json()
        assert proposal == {"lease_agreement_id": str(world["lease"]), "tenant_cost": "1200.00",
            "months": "12", "monthly_amount": "100.00", "heating_monthly_amount": "0.00",
            "heating_tenant_cost": "0.00", "period_start": "2026-01-01", "period_end": "2026-12-31"}
        assert frozen["balance"] == "201.00"  # Never balance / months or paid advances.
        before = (await rent_db.execute(text("SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=:id"), {"id":uuid.UUID(run_id)})).one()
        payments = (await rent_db.execute(text("SELECT to_jsonb(p) FROM payment p WHERE lease_agreement_id=:lease ORDER BY id"), {"lease":world["lease"]})).all()
        accepted = await api.post(url, json={"valid_from": "2026-05-01"})
        assert accepted.status_code == 201, accepted.text
        row = accepted.json()
        assert row["base_rent"] == "750.00" and row["advance_payment"] == "100.00"
        assert row["lease_agreement_id"] == str(world["lease"])
        assert (await api.post(url, json={"valid_from": "2026-05-01"})).status_code == 409
        assert (await api.post(url, json={"valid_from": "2026-02-01"})).status_code == 409
        assert (await api.get(f"/api/leases/{world['lease']}")).json() == original
        assert (await api.get(f"/api/settlements/{run_id}")).json() == frozen
        assert (await rent_db.execute(text("SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=:id"), {"id":uuid.UUID(run_id)})).one() == before
        assert (await rent_db.execute(text("SELECT to_jsonb(p) FROM payment p WHERE lease_agreement_id=:lease ORDER BY id"), {"lease":world["lease"]})).all() == payments
        events = (await rent_db.execute(text("SELECT payload_diff FROM audit_event WHERE entity_id=:lease AND action='lease.rent_change_created'"), {"lease":world["lease"]})).scalars().all()
        assert sum(event["rent_change_id"] == row["id"] for event in events) == 1
        assert len((await api.get(history)).json()) == 3


@pytest.mark.asyncio
async def test_proposal_bounds_validation_and_concurrent_acceptance(rent_world, rent_db):
    import asyncio
    world = rent_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2027-01-01' WHERE id=:id"), {"id":world["lease"]})
    await rent_db.commit()
    async with api_client(world) as api, api_client(world) as other:
        run_id, _ = await _proposal_run(api, world, rent_db)
        url = f"/api/settlements/{run_id}/advance-proposal"
        for body in [{}, {"valid_from":"invalid"}, {"valid_from":"2025-12-31"}, {"valid_from":"2027-01-01"},
                     {"valid_from":"2026-05-01", "monthly_amount":"1.00"}, {"valid_from":"2026-05-01", "base_rent":"1.00"}]:
            assert (await api.post(url, json=body)).status_code == 422
        responses = await asyncio.gather(api.post(url, json={"valid_from":"2026-12-31"}),
                                         other.post(url, json={"valid_from":"2026-12-31"}))
        assert sorted(response.status_code for response in responses) == [201,409]
        # Historical dates are allowed, not future-only.
        assert (await api.post(url, json={"valid_from":"2026-01-01"})).status_code == 201


@pytest.mark.asyncio
async def test_proposal_roles_org_and_lifecycle(rent_world, rent_db):
    import httpx
    from app.main import app
    world = rent_world
    async with api_client(world) as api, api_client(world, "reader") as reader, api_client(world, foreign=True) as foreign:
        run_id, _ = await _proposal_run(api, world, rent_db)
        url = f"/api/settlements/{run_id}/advance-proposal"
        assert (await reader.get(url)).status_code == 200
        assert (await reader.post(url, json={"valid_from":"2026-05-01"})).status_code == 403
        for client in [foreign]:
            assert (await client.get(url)).status_code == 404
            assert (await client.post(url, json={"valid_from":"2026-05-01"})).status_code == 404
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anonymous:
            assert (await anonymous.get(url)).status_code == 401
            assert (await anonymous.post(url, json={"valid_from":"2026-05-01"})).status_code == 401
        async with api_client(world, "clerk") as clerk:
            result = await clerk.post(url, json={"valid_from":"2026-04-01"})
            assert result.status_code == 201, result.text
        correction = await api.post(f"/api/settlements/{run_id}/correction")
        assert correction.status_code == 201, correction.text
        for target in [url, f"/api/settlements/{correction.json()['id']}/advance-proposal"]:
            assert (await api.get(target)).status_code == 409
            assert (await api.post(target, json={"valid_from":"2026-05-01"})).status_code == 409


@pytest.mark.asyncio
async def test_proposal_accept_audit_failure_rolls_back(rent_world, rent_db, monkeypatch):
    from app.repositories.audit_repo import AuditRepository
    world = rent_world
    async with api_client(world) as api:
        run_id, _ = await _proposal_run(api, world, rent_db)
        original = AuditRepository.create
        async def fail(self, event):
            if event.action == "lease.rent_change_created":
                raise RuntimeError("synthetic audit write failure")
            return await original(self, event)
        with monkeypatch.context() as patch:
            patch.setattr(AuditRepository, "create", fail)
            with pytest.raises(RuntimeError, match="synthetic audit"):
                await api.post(f"/api/settlements/{run_id}/advance-proposal", json={"valid_from":"2026-05-01"})
        assert (await api.get(f"/api/leases/{world['lease']}/rent-changes")).json() == []
        assert (await api.post(f"/api/settlements/{run_id}/advance-proposal", json={"valid_from":"2026-05-01"})).status_code == 201


@pytest.mark.asyncio
async def test_proposal_uses_frozen_not_current_dates_or_costs(rent_world, rent_db):
    world = rent_world
    async with api_client(world) as api:
        run_id, _ = await _proposal_run(api, world, rent_db)
        url = f"/api/settlements/{run_id}/advance-proposal"
        before = (await api.get(url)).json()
        # A later legitimate master-data edit cannot redefine the finalized basis.
        changed = await api.patch(f"/api/leases/{world['lease']}", json={"valid_to":"2026-06-30"})
        assert changed.status_code == 200, changed.text
        await rent_db.execute(text("""INSERT INTO cost_entry
            (id,organization_id,property_id,cost_category_id,cost_category_plan_version,
             invoice_date,service_period_from,service_period_to,gross_amount,tenant_distribution_key,
             tenant_apportionable,status,source,confirmed_at,confirmed_by)
            SELECT :id,:org,:property,id,1,'2026-01-01','2026-01-01','2026-12-31',9000,
             'WOHNFLAECHE','UMLAGEFAEHIG','confirmed','MANUAL',now(),:admin
            FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""), {**world,"id":uuid.uuid4()})
        await rent_db.commit()
        assert (await api.get(url)).json() == before
        assert (await api.post(url, json={"valid_from":"2026-07-01"})).status_code == 422
        result = await api.post(url, json={"valid_from":"2026-04-01"})
        assert result.status_code == 201 and result.json()["advance_payment"] == "100.00"


@pytest.mark.asyncio
async def test_proposal_financial_scope_prepare_and_revocation(rent_world, rent_db):
    from tests.test_financial_source_disclosure import bind_output
    world = rent_world
    seed = {"org_a_id":world["org"], "user_a_admin_id":world["admin"]}
    async with api_client(world) as api:
        run_id, _ = await _proposal_run(api, world, rent_db)
        ids = await bind_output(seed, str(world["property"]), run_id,
            kind="settlement_run", scope_type="MIET", caps=["read"])
        url = f"/api/settlements/{run_id}/advance-proposal"
        assert (await api.get(url)).status_code == 200
        assert (await api.post(url, json={"valid_from":"2026-05-01"})).status_code == 404
        await rent_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:sphere"), ids)
        await rent_db.execute(text("""INSERT INTO scope_grant
            (organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (:org,:sphere,:actor,ARRAY['read','prepare'],:actor,'Explicit T28 prepare')"""), ids)
        await rent_db.commit()
        response = await api.post(url, json={"valid_from":"2026-05-01"})
        assert response.status_code == 201, response.text
        await rent_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:sphere AND revoked_at IS NULL"), ids)
        await rent_db.commit()
        assert (await api.get(url)).status_code == 404
        assert (await api.post(url, json={"valid_from":"2026-06-01"})).status_code == 404


@pytest.mark.asyncio
async def test_proposal_source_read_history_prepare_and_revocation(rent_world, rent_db):
    from tests.test_source_security_lifecycle import status, transition, transition_body
    from tests.test_property_source_access import grant
    from tests.contact_source_fixtures import grant_property_contacts
    from tests.test_financial_source_disclosure import bind_output
    world = rent_world
    seed = {"org_a_id":world["org"], "user_a_admin_id":world["admin"]}
    async with api_client(world) as api:
        run_id, _ = await _proposal_run(api, world, rent_db)
        await grant_property_contacts(api, seed, str(world["property"]))
        await grant_property_contacts(api, seed, str(world["hidden_property"]))
        await grant(api, seed, world["hidden_property"], caps=["read"])
        domain, receipt = await grant(api, seed, world["property"], caps=["read"])
        ids = await bind_output(seed, str(world["property"]), run_id,
            kind="settlement_run", scope_type="MIET", caps=["read","prepare"])
        await rent_db.execute(text("""INSERT INTO accounting_scope_binding
            (organization_id,property_id,scope_id,cost_entry_id,origin,reason,reviewed_by)
            SELECT :org,:property,:sphere,id,'REVIEWED','T28 complete synthetic cost scope',:actor
            FROM cost_entry WHERE organization_id=:org AND property_id=:property"""), ids)
        await rent_db.commit()
        from tests.test_access_disposition import reviewed, assessment, decisions, acceptance_body
        report = await reviewed(api)
        state = await assessment(api, report)
        assert state["acceptance_available"], (state, await decisions(api, report))
        response = await api.post(f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",
            json=acceptance_body(report, state), headers={"Idempotency-Key":str(uuid.uuid4())})
        assert response.status_code == 201, response.text
        decision = response.json()
        activated = await transition(api, transition_body(await status(api), "ENFORCED", decision))
        assert activated.status_code == 201, activated.text
        url = f"/api/settlements/{run_id}/advance-proposal"
        response = await api.get(url)
        assert response.status_code == 200, response.text
        # Financial PREPARE alone cannot mutate a read-only relationship source.
        assert (await api.post(url, json={"valid_from":"2026-05-01"})).status_code == 404
        assert (await api.post(f"/api/property-data-domains/{domain}/grants/{receipt}/revoke")).status_code == 204
        assert (await api.get(url)).status_code == 404
        assert (await api.post(url, json={"valid_from":"2026-06-01"})).status_code == 404


@pytest.mark.asyncio
async def test_proposal_zero_cost_accepts_zero(rent_world, rent_db):
    async with api_client(rent_world) as api:
        run_id, _ = await _proposal_run(api, rent_world, rent_db, cost="0.00")
        url = f"/api/settlements/{run_id}/advance-proposal"
        assert (await api.get(url)).json()["monthly_amount"] == "0.00"
        result = await api.post(url, json={"valid_from":"2026-01-01"})
        assert result.status_code == 201, result.text
        assert result.json()["advance_payment"] == "0.00"
        assert result.json()["base_rent"] == "700.00"


@pytest.mark.asyncio
async def test_proposal_acceptance_committed_before_response(rent_world, rent_db):
    """Observe committed history at response.start, not after ASGI cleanup."""
    import httpx
    from app.main import app

    async with api_client(rent_world) as api:
        run_id, _ = await _proposal_run(api, rent_world, rent_db)
        observed = []

        async def observe_response(scope, receive, send):
            async def observe_send(message):
                if message["type"] == "http.response.start":
                    observed.append(await rent_db.scalar(text("""SELECT count(*)
                        FROM lease_rent_change WHERE lease_agreement_id=:lease
                        AND valid_from=:valid_from"""),
                        {"lease":rent_world["lease"], "valid_from":date(2026, 5, 1)}))
                await send(message)
            await app(scope, receive, observe_send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=observe_response),
            base_url="http://test", cookies=api.cookies,
        ) as client:
            result = await client.post(f"/api/settlements/{run_id}/advance-proposal",
                json={"valid_from":"2026-05-01"})
        assert result.status_code == 201, result.text
        assert observed == [1], "Rent history must commit before sending HTTP 201"


@pytest.fixture
def metered_m3(seed_data):
    """Independent four-unit annual water scenario; synthetic test DB only."""
    from urllib.parse import urlparse
    import psycopg2

    target = urlparse(SYNC_URL)
    assert target.hostname == "127.0.0.1" and target.port == 55432
    world = _seed_m3(seed_data)
    conn = psycopg2.connect(SYNC_URL)
    try:
        with conn, conn.cursor() as cur:
            for key, quantity in [("u1", "30"), ("u2", "20"), ("u3", "10"), ("u4", "40")]:
                meter_id = str(uuid.uuid4())
                cur.execute("""INSERT INTO meter
                    (id,organization_id,property_id,unit_id,meter_number,meter_type,location)
                    VALUES (%s,%s,%s,%s,%s,'KALTWASSER','synthetisch')""",
                    (meter_id, str(world["org_a_id"]), world["property"], world[key], key))
                for day, value in [(date(2025, 1, 1), Decimal("100.0000")),
                                   (date(2025, 12, 31), Decimal("100.0000") + Decimal(quantity))]:
                    cur.execute("""INSERT INTO meter_reading
                        (id,organization_id,meter_id,reading_date,value,period)
                        VALUES (%s,%s,%s,%s,%s,'2025')""",
                        (str(uuid.uuid4()), str(world["org_a_id"]), meter_id, day, value))
    finally:
        conn.close()
    return world


def _water_line(body):
    return next(row for row in body["lines"]
                if row["line_type"] == "COST" and row["cost_category_code"] == "KALTWASSER")


@pytest.mark.asyncio
async def test_meter_consumption_automatically_supplies_all_unit_weights(client_a_admin, metered_m3):
    run_id = await _create_run(client_a_admin, metered_m3, "lease_e", "0.00")
    extras = _extras_for_exit(metered_m3)
    extras["unit_metrics"] = []
    result = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
    assert result.status_code == 200, result.text
    water = _water_line(result.json())
    assert Decimal(water["total_amount"]) == Decimal("600.00")
    assert Decimal(water["amount"]) == Decimal("240.00")
    assert Decimal(water["distribution_base_total"]) == Decimal("100")


@pytest.mark.asyncio
async def test_meter_consumption_explicit_manual_zero_takes_precedence(client_a_admin, metered_m3):
    run_id = await _create_run(client_a_admin, metered_m3, "lease_e", "0.00")
    extras = _extras_for_exit(metered_m3)
    extras["unit_metrics"] = [{"unit_id": metered_m3["u1"], "consumption": "0"}]
    result = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
    assert result.status_code == 200, result.text
    assert Decimal(_water_line(result.json())["amount"]) == Decimal("342.86")
    assert Decimal(_water_line(result.json())["distribution_base_total"]) == Decimal("70")


@pytest.mark.asyncio
async def test_meter_consumption_missing_one_unit_never_silently_redistributes(client_a_admin, seed_data):
    from urllib.parse import urlparse
    assert urlparse(SYNC_URL).hostname == "127.0.0.1" and urlparse(SYNC_URL).port == 55432
    world = _seed_m3(seed_data)
    run_id = await _create_run(client_a_admin, world, "lease_e", "0.00")
    extras = _extras_for_exit(world)
    extras["unit_metrics"] = extras["unit_metrics"][:-1]
    result = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
    assert result.status_code == 422, result.text
    assert "Handeingabe" in result.json()["detail"]


@pytest.mark.asyncio
async def test_meter_consumption_suggestions_show_all_units_and_actual_readings(client_a_admin, client_a_readonly, client_b_admin, metered_m3):
    run_id = await _create_run(client_a_admin, metered_m3, "lease_e", "0.00")
    url = f"/api/settlements/{run_id}/consumption-suggestions"
    response = await client_a_readonly.get(url)
    assert response.status_code == 200, response.text
    rows = {row["unit_id"]: row for row in response.json()}
    assert set(rows) == {metered_m3[key] for key in ("u1", "u2", "u3", "u4")}
    for key, quantity, finish in [("u1", "30.0000", "130.0000"), ("u2", "20.0000", "120.0000"),
                                   ("u3", "10.0000", "110.0000"), ("u4", "40.0000", "140.0000")]:
        row = rows[metered_m3[key]]
        assert row["consumption"] == quantity
        if key in {"u1", "u3"}:
            assert "Tagen" in row["reason"]
        else:
            assert row["reason"] is None
        assert row["meter_id"]
        assert row["unit_label"] == key[-1]
        assert row["start_reading"]["reading_date"] == "2025-01-01"
        assert row["start_reading"]["value"] == "100.0000"
        assert row["end_reading"]["reading_date"] == "2025-12-31"
        assert row["end_reading"]["value"] == finish
        assert row["start_reading"]["id"] != row["end_reading"]["id"]
    assert (await client_b_admin.get(url)).status_code == 404
    unchanged = (await client_a_admin.get(f"/api/settlements/{run_id}")).json()
    assert unchanged["lines"] == []
    assert unchanged["total_cost"] is None
    assert unchanged["snapshot_sha256"] is None


@pytest.mark.asyncio
async def test_meter_consumption_suggestions_explain_missing_values_without_invented_zero(client_a_admin, seed_data):
    from urllib.parse import urlparse
    assert urlparse(SYNC_URL).hostname == "127.0.0.1" and urlparse(SYNC_URL).port == 55432
    world = _seed_m3(seed_data)
    run_id = await _create_run(client_a_admin, world, "lease_e", "0.00")
    response = await client_a_admin.get(f"/api/settlements/{run_id}/consumption-suggestions")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 4
    for row in response.json():
        assert row["consumption"] is None
        assert row["meter_id"] is None
        assert row["start_reading"] is None and row["end_reading"] is None
        assert "Handeingabe" in row["reason"]


@pytest.mark.asyncio
async def test_meter_consumption_common_meter_is_ignored_and_multiple_unit_meters_require_manual(client_a_admin, metered_m3):
    run_id = await _create_run(client_a_admin, metered_m3, "lease_e", "0.00")
    url = f"/api/settlements/{run_id}/consumption-suggestions"
    common = await client_a_admin.post("/api/meters", json={
        "property_id": metered_m3["property"], "meter_number": "COMMON", "meter_type": "KALTWASSER"})
    assert common.status_code == 201, common.text
    for day, value in [("2025-01-01", "0.0000"), ("2025-12-31", "9999999.0000")]:
        added = await client_a_admin.post(f"/api/meters/{common.json()['id']}/readings", json={"reading_date": day, "value": value})
        assert added.status_code == 201, added.text
    rows = {row["unit_id"]: row for row in (await client_a_admin.get(url)).json()}
    assert rows[metered_m3["u1"]]["consumption"] == "30.0000"
    duplicate = await client_a_admin.post("/api/meters", json={
        "property_id": metered_m3["property"], "unit_id": metered_m3["u1"],
        "meter_number": "UNCLASSIFIED-REPLACEMENT", "meter_type": "KALTWASSER"})
    assert duplicate.status_code == 201, duplicate.text
    rows = {row["unit_id"]: row for row in (await client_a_admin.get(url)).json()}
    assert rows[metered_m3["u1"]]["consumption"] is None
    assert "Handeingabe" in rows[metered_m3["u1"]]["reason"]
    assert rows[metered_m3["u4"]]["consumption"] == "40.0000"
    extras = _extras_for_exit(metered_m3)
    extras["unit_metrics"] = []
    rejected = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
    assert rejected.status_code == 422, rejected.text


@pytest.mark.asyncio
async def test_meter_consumption_final_snapshot_keeps_actual_reading_basis_unchanged(client_a_admin, metered_m3):
    import psycopg2
    from urllib.parse import urlparse
    assert urlparse(SYNC_URL).hostname == "127.0.0.1" and urlparse(SYNC_URL).port == 55432
    run_id = await _create_run(client_a_admin, metered_m3, "lease_e", "0.00")
    extras = _extras_for_exit(metered_m3)
    extras["unit_metrics"] = []
    finalized = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
    assert finalized.status_code == 200, finalized.text
    frozen = finalized.json()
    assert frozen["status"] == "finalized" and frozen["snapshot_sha256"]
    assert Decimal(_water_line(frozen)["amount"]) == Decimal("240.00")

    def stored():
        conn = psycopg2.connect(SYNC_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=%s", (run_id,))
                return cur.fetchone()
        finally:
            conn.close()
    before = stored()
    sources = before[0]["inputs"]["meter_consumption_sources"]
    assert len(sources) == 4
    target = next(row for row in sources if row["unit_id"] == metered_m3["u4"])
    assert target["consumption"] == "40.0000"
    assert target["start_reading"]["value"] == "100.0000"
    assert target["end_reading"]["value"] == "140.0000"
    added = await client_a_admin.post(f"/api/meters/{target['meter_id']}/readings", json={
        "reading_date": "2025-12-31", "value": "999.0000"})
    assert added.status_code == 201, added.text
    blocked = await client_a_admin.get(f"/api/settlements/{run_id}/consumption-suggestions")
    assert blocked.status_code == 409, blocked.text
    assert (await client_a_admin.get(f"/api/settlements/{run_id}")).json() == frozen
    assert stored() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_meter_consumption_scope_classification_unit_boundary_and_revocation(client_a_admin, metered_m3, partial):
    import psycopg2
    world = metered_m3
    scope, grant_id = _batch_scope(world, units=[world["u4"]] if partial else None)
    created = await client_a_admin.post("/api/settlements", json={
        'heating_advance_payments_total': '0.00', "property_id": world["property"], "lease_agreement_id": world["lease_e"],
        "period_start": "2025-01-01", "period_end": "2025-12-31",
        "advance_payments_total": "0.00", "accounting_scope_id": scope})
    assert created.status_code == 201, created.text
    url = f"/api/settlements/{created.json()['id']}/consumption-suggestions"
    unclassified = await client_a_admin.get(url)
    assert unclassified.status_code == 409, unclassified.text
    conn = psycopg2.connect(SYNC_URL)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO accounting_scope_binding
                (organization_id,property_id,scope_id,cost_entry_id,origin,reason,reviewed_by)
                SELECT organization_id,property_id,%s,id,'REVIEWED','Synthetic reviewed water scope',%s
                FROM cost_entry WHERE organization_id=%s AND property_id=%s""",
                (scope, str(world["user_a_admin_id"]), str(world["org_a_id"]), world["property"]))
    finally:
        conn.close()
    response = await client_a_admin.get(url)
    assert response.status_code == 200, response.text
    expected = {world["u4"]} if partial else {world[key] for key in ("u1", "u2", "u3", "u4")}
    assert {row["unit_id"] for row in response.json()} == expected
    assert all(row["consumption"] is not None for row in response.json())
    revoked = await client_a_admin.post(f"/api/accounting-scopes/{scope}/grants/{grant_id}/revoke")
    assert revoked.status_code == 204, revoked.text
    assert (await client_a_admin.get(url)).status_code == 404

# T46: independent, fixed-oracle heating integration (all amounts in EUR).
import pytest_asyncio


@pytest_asyncio.fixture
async def heiz_world(rent_world, rent_db, request):
    w = dict(rent_world)
    w['second_unit'] = uuid.uuid4()
    await rent_db.execute(text("UPDATE unit SET area=40,heated_area=40 WHERE id=:unit"), w)
    await rent_db.execute(text("""INSERT INTO unit
        (id,organization_id,property_id,number,usage_type,area,heated_area,mea)
        VALUES (:second_unit,:org,:property,'2','WOHNUNG',60,60,600)"""), w)
    for code, amount, key in [('HEIZUNG_ZENTRAL','1000.00','heat'),
                              ('WARMWASSER_ZENTRAL','400.00','water')]:
        w[key] = uuid.uuid4()
        await rent_db.execute(text("""INSERT INTO cost_entry
            (id,organization_id,property_id,cost_category_id,cost_category_plan_version,
             invoice_date,service_period_from,service_period_to,gross_amount,tenant_distribution_key,
             tenant_apportionable,status,source,confirmed_at,confirmed_by)
            SELECT :id,:org,:property,id,1,'2026-12-31',:service_start,'2026-12-31',:amount,
             'EXTERNE_ABRECHNUNG','UMLAGEFAEHIG_EXTERN_PRUEFEN','confirmed','MANUAL',now(),:admin
            FROM cost_category WHERE code=:code AND plan_version=1"""),
            {**w, 'id':w[key], 'code':code, 'amount':Decimal(amount),
             'service_start': date.fromisoformat(getattr(request, 'param', '2026-01-01')) if key=='heat' else date(2026,1,1)})
    w['meters'] = []
    for kind, quantities in [('HEIZUNG',('100','300')), ('WARMWASSER',('3','1'))]:
        for unit, quantity in zip((w['unit'],w['second_unit']), quantities):
            meter = uuid.uuid4()
            w['meters'].append(meter)
            await rent_db.execute(text("""INSERT INTO meter
                (id,organization_id,property_id,unit_id,meter_number,meter_type)
                VALUES (:id,:org,:property,:unit,:number,:kind)"""),
                {**w,'id':meter,'unit':unit,'number':str(meter),'kind':kind})
            for day, value in [(date(2026,1,1),Decimal('100')),
                               (date(2026,12,31),Decimal('100')+Decimal(quantity))]:
                await rent_db.execute(text("""INSERT INTO meter_reading
                    (id,organization_id,meter_id,reading_date,value,period)
                    VALUES (:id,:org,:meter,:day,:value,'2026')"""),
                    {**w,'id':uuid.uuid4(),'meter':meter,'day':day,'value':value})
    await rent_db.commit()
    async with api_client(w) as api:
        lease = await api.post('/api/leases',json={'unit_id':str(w['second_unit']),
            'tenant_profile_id':str(w['tenant']),'valid_from':'2026-01-01',
            'base_rent':'600.00','advance_payment':'0.00'})
        assert lease.status_code == 201, lease.text
        w['second_lease'] = uuid.UUID(lease.json()['id'])
        run = await api.post('/api/settlements',json={'heating_advance_payments_total': '0.00', 'property_id':str(w['property']),
            'lease_agreement_id':str(w['lease']),'period_start':'2026-01-01',
            'period_end':'2026-12-31','advance_payments_total':'0.00'})
        assert run.status_code == 201, run.text
        w['run'] = run.json()['id']
        await attest_fixture(api,'settlement',w['run'],
            {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL', 'advance_payments_total':'0.00','advance_payments_source':'MANUAL'})
    return w


def _heiz_body(w):
    return {'heating_inputs':[
        {'cost_entry_id':str(w[key]),'kind':kind,'consumption_fraction':fraction,
         'base_key':'AREA','consumption_recorded':True,'mandatory_seventy_percent':False,
         'meter_type':meter_type,'slices':[]}
        for key,kind,fraction,meter_type in [('heat','HEATING','0.70','HEIZUNG'),
                                             ('water','HOT_WATER','0.50','WARMWASSER')]]}


def _heiz_line(response, code='HEIZUNG_ZENTRAL'):
    return next(row for row in response['lines'] if row['cost_category_code']==code)


@pytest.mark.asyncio
async def test_heiz_meter_split_persists_intent_finalize_and_frozen_pdf(heiz_world, rent_db):
    w = heiz_world
    url = f"/api/settlements/{w['run']}"
    async with api_client(w) as api:
        preview = await api.post(url+'/preview',json=_heiz_body(w))
        assert preview.status_code == 200, preview.text
        assert _heiz_line(preview.json())['amount'] == '295.00'
        assert _heiz_line(preview.json(),'WARMWASSER_ZENTRAL')['amount'] == '230.00'
        assert (await api.get(url)).json()['total_cost'] is None
        result = await api.post(url+'/compute',json=_heiz_body(w))
        assert result.status_code == 200, result.text
        assert result.json()['total_cost'] == '525.00'
        assert (await api.get(url)).json()['extra_inputs']['heating_inputs'] == _heiz_body(w)['heating_inputs']
        # Omitting body reuses saved intent, but derives actual readings again.
        final = await api.post(url+'/finalize')
        assert final.status_code == 200, final.text
        frozen = final.json()
        assert frozen['total_cost'] == '525.00' and frozen['snapshot_sha256']
        payload = await rent_db.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=:id'),{'id':uuid.UUID(w['run'])})
        breakdown = payload['heating_costs']
        heat = next(row for row in breakdown if row['cost_entry_id']==str(w['heat']))
        target = next(row for row in heat['rows'] if row['lease_agreement_id']==str(w['lease']))
        assert target['base_amount']=='120.00' and target['consumption_amount']=='175.00'
        assert target['gross_amount']=='295.00' and target['reduction_amount']=='0.00'
        assert target['meter_source']['start_reading']['value']=='100.0000'
        pdf = await api.get(url+'/pdf')
        assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
        import subprocess
        content = subprocess.run(['pdftotext','-','-'],input=pdf.content,capture_output=True,check=True).stdout.decode()
        assert 'Grundanteil' in content and 'Verbrauchsanteil' in content
        assert 'externe Abrechnung' not in content
        assert '120,00' in content and '175,00' in content and '295,00' in content
        changed = await api.post(f"/api/meters/{w['meters'][0]}/readings",json={'reading_date':'2026-12-31','value':'999'})
        assert changed.status_code==201,changed.text
        assert (await api.get(url)).json()==frozen
        assert (await api.get(url+'/pdf')).content==pdf.content
        assert (await api.post(url+'/compute',json=_heiz_body(w))).status_code==409


@pytest.mark.asyncio
async def test_heiz_sources_separate_manual_zero_and_live_rights(heiz_world):
    w=heiz_world
    url=f"/api/settlements/{w['run']}"
    params={'cost_entry_id':str(w['heat']),'meter_type':'HEIZUNG','base_key':'AREA'}
    async with api_client(w) as api,api_client(w,'reader') as reader,api_client(w,foreign=True) as foreign:
        candidates=await api.get(url+'/heating-consumption',params=params)
        assert candidates.status_code==200,candidates.text
        assert {r['consumption'] for r in candidates.json()}=={'100.0000','300.0000'}
        assert (await reader.get(url+'/heating-consumption',params=params)).status_code==200
        assert (await foreign.get(url+'/heating-consumption',params=params)).status_code==404
        assert (await reader.post(url+'/compute',json=_heiz_body(w))).status_code==403
        body=_heiz_body(w)
        body['unit_metrics']=[{'unit_id':str(w['unit']),'consumption':'999999'}]
        body['heating_inputs'][0]['slices']=[{'unit_id':str(w['unit']),
            'period_from':'2026-01-01','period_to':'2026-12-31','consumption':'0'}]
        result=await api.post(url+'/compute',json=body)
        assert result.status_code==200,result.text
        assert _heiz_line(result.json())['amount']=='120.00'
        assert _heiz_line(result.json(),'WARMWASSER_ZENTRAL')['amount']=='230.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('problem',['unknown','duplicate','kind','fraction','nan','negative',
    'special70','external','slice','duplicate_slice','volume','hot_base','unrecorded_reading','missing','ambiguous','precision','infinite_quantity','boolean'])
async def test_heiz_invalid_inputs_atomic(heiz_world,rent_db,problem):
    w=heiz_world
    url=f"/api/settlements/{w['run']}"
    async with api_client(w) as api:
        baseline=await api.post(url+'/compute',json=_heiz_body(w))
        assert baseline.status_code==200,baseline.text
        before=(await api.get(url)).json()
    body=_heiz_body(w)
    heat=body['heating_inputs'][0]
    if problem=='unknown': heat['cost_entry_id']=str(uuid.uuid4())
    elif problem=='duplicate': body['heating_inputs'].append(dict(heat))
    elif problem=='kind': heat['kind']='HOT_WATER'
    elif problem=='fraction': heat['consumption_fraction']='0.71'
    elif problem=='nan': heat['consumption_fraction']='NaN'
    elif problem=='negative': heat['slices']=[{'unit_id':str(w['unit']),'period_from':'2026-01-01','period_to':'2026-12-31','consumption':'-1'}]
    elif problem=='special70': heat.update(mandatory_seventy_percent=True,consumption_fraction='0.50')
    elif problem=='external': body['external_amounts']=[{'cost_entry_id':str(w['heat']),'unit_id':str(w['unit']),'tenant_amount':'20'}]
    elif problem in {'slice','duplicate_slice'}:
        row={'unit_id':str(w['unit']),'period_from':'2026-01-01','period_to':'2026-12-31','consumption':'1'}
        heat['slices']=[row,row] if problem=='duplicate_slice' else [{**row,'unit_id':str(w['foreign_unit'])}]
    elif problem=='volume': heat['base_key']='VOLUME'
    elif problem=='hot_base': body['heating_inputs'][1]['base_key']='HEATED_AREA'
    elif problem=='unrecorded_reading':
        heat['consumption_recorded']=False
        heat['slices']=[{'unit_id':str(w['unit']),'period_from':'2026-01-01','period_to':'2026-12-31','consumption':'1'}]
    elif problem in {'precision','infinite_quantity'}:
        heat['slices']=[{'unit_id':str(w['unit']),'period_from':'2026-01-01','period_to':'2026-12-31',
            'consumption':'0.0000001' if problem=='precision' else 'Infinity'}]
    elif problem=='boolean': heat['consumption_recorded']=1
    elif problem=='missing': heat['meter_type']='ABSENT'
    else:
        await rent_db.execute(text('''INSERT INTO meter(id,organization_id,property_id,unit_id,meter_number,meter_type)
            VALUES (:id,:org,:property,:unit,'duplicate-heiz','HEIZUNG')'''),{**w,'id':uuid.uuid4()})
        await rent_db.commit()
    url=f"/api/settlements/{w['run']}"
    async with api_client(w) as api:
        failed=await api.post(url+'/finalize',json=body)
        assert failed.status_code==422,failed.text
        unchanged=(await api.get(url)).json()
        assert unchanged==before
        assert unchanged['snapshot_sha256'] is None and unchanged['total_cost']=='525.00'


@pytest.mark.asyncio
async def test_heiz_explicit_noncapture_deducts_only_heating(heiz_world):
    w=heiz_world
    body=_heiz_body(w)
    body['heating_inputs'][0]['consumption_recorded']=False
    async with api_client(w) as api:
        result=await api.post(f"/api/settlements/{w['run']}/compute",json=body)
        assert result.status_code==200,result.text
        assert _heiz_line(result.json())['amount']=='340.00'
        assert _heiz_line(result.json(),'WARMWASSER_ZENTRAL')['amount']=='230.00'
        assert result.json()['total_cost']=='570.00'


@pytest.mark.asyncio
@pytest.mark.parametrize("interim_day", ["2026-06-30", "2026-07-01"])
async def test_heiz_nutzerwechsel_uses_intermediate_reading_not_day_estimate(heiz_world,rent_db,interim_day):
    w=heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2026-07-01' WHERE id=:lease"),w)
    for meter,value in [(w['meters'][0],'140'),(w['meters'][2],'101')]:
        await rent_db.execute(text("""INSERT INTO meter_reading
            (id,organization_id,meter_id,reading_date,value,period)
            VALUES (:id,:org,:meter,:day,:value,'2026')"""),
            {**w,'id':uuid.uuid4(),'meter':meter,'day':date.fromisoformat(interim_day),'value':Decimal(value)})
    await rent_db.commit()
    async with api_client(w) as api:
        incoming=await api.post('/api/leases',json={'unit_id':str(w['unit']),
            'tenant_profile_id':str(w['tenant']),'valid_from':'2026-07-01',
            'base_rent':'700.00','advance_payment':'150.00'})
        assert incoming.status_code==201,incoming.text
        stale=await api.post(f"/api/settlements/{w['run']}/compute",json=_heiz_body(w))
        assert stale.status_code==422 and 'coverage_stale' in stale.text
        await attest_fixture(api,'settlement',w['run'],
            {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL', 'advance_payments_total':'0.00','advance_payments_source':'MANUAL'})
        result=await api.post(f"/api/settlements/{w['run']}/compute",json=_heiz_body(w))
        assert result.status_code==200,result.text
        # 120 × 181/365 = 59.51 base; 700 × 40/400 = 70 consumption.
        assert _heiz_line(result.json())['amount']=='129.51'
        assert _heiz_line(result.json(),'WARMWASSER_ZENTRAL')['amount']=='89.67'
        payload=await rent_db.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=:id'),{'id':uuid.UUID(w['run'])})
        pool=next(p for p in payload['heating_costs'] if p['kind']=='HEATING')
        own=[r for r in pool['rows'] if r['unit_id']==str(w['unit'])]
        assert {r['consumption'] for r in own}=={'40.000000','60.000000'}
        assert sum(Decimal(r['gross_amount']) for r in own)==Decimal('295.00')


@pytest.mark.asyncio
async def test_heiz_vacancy_keeps_gross_owner_share(heiz_world,rent_db):
    w=heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2026-07-01' WHERE id=:second_lease"),w)
    await rent_db.commit()
    body=_heiz_body(w)
    for config in body['heating_inputs']: config['consumption_recorded']=False
    async with api_client(w) as api:
        stale=await api.post(f"/api/settlements/{w['run']}/compute",json=_heiz_body(w))
        assert stale.status_code==422 and 'coverage_stale' in stale.text
        await attest_fixture(api,'settlement',w['run'],
            {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL', 'advance_payments_total':'0.00','advance_payments_source':'MANUAL'})
        result=await api.post(f"/api/settlements/{w['run']}/compute",json=body)
        assert result.status_code==200,result.text
        assert result.json()['total_cost']=='476.00'
        payload=await rent_db.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=:id'),{'id':uuid.UUID(w['run'])})
        pool=next(p for p in payload['heating_costs'] if p['kind']=='HEATING')
        vacant=next(r for r in pool['rows'] if r['lease_agreement_id'] is None)
        assert vacant['gross_amount']==vacant['amount_due']=='302.47'
        assert vacant['reduction_amount']=='0.00'
        owner=next(r for r in payload['vacancy_attribution'] if r['cost_entry_id']==str(w['heat']))
        assert owner['amount']=='302.47'
        assert sum(Decimal(r['amount_due'])+Decimal(r['reduction_amount']) for r in pool['rows'])==Decimal('1000.00')


@pytest.mark.asyncio
async def test_heiz_compute_waits_for_financial_lock(heiz_world,rent_db):
    import asyncio
    w=heiz_world
    async with api_client(w) as api:
        await rent_db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {'key':f"financial-inputs:{w['org']}"})
        request=asyncio.create_task(api.post(f"/api/settlements/{w['run']}/compute",json=_heiz_body(w)))
        waiting=False
        try:
            for _ in range(200):
                waiting=bool(await rent_db.scalar(text("""SELECT EXISTS(
                    SELECT 1 FROM pg_stat_activity WHERE pid<>pg_backend_pid()
                    AND pg_backend_pid()=ANY(pg_blocking_pids(pid)))""")))
                if waiting: break
                await asyncio.sleep(.01)
        finally:
            await rent_db.rollback()
        result=await asyncio.wait_for(request,15)
        assert result.status_code==200,result.text
        assert waiting,'Heating compute must share the financial-input lock'


@pytest.mark.asyncio
async def test_heiz_live_role_downgrade_blocks_existing_session(heiz_world,rent_db):
    w=heiz_world
    async with api_client(w) as api:
        await rent_db.execute(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:admin"),w)
        await rent_db.commit()
        response=await api.post(f"/api/settlements/{w['run']}/compute",json=_heiz_body(w))
        assert response.status_code==403,response.text
        unchanged=(await api.get(f"/api/settlements/{w['run']}")).json()
        assert unchanged['total_cost'] is None and unchanged['lines']==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('partial',[False,True])
async def test_heiz_scoped_sources_and_revocation(client_a_admin,metered_m3,partial):
    import psycopg2
    w=metered_m3
    scope,grant_id=_batch_scope(w,units=[w['u4']] if partial else None)
    run=await client_a_admin.post('/api/settlements',json={
        'heating_advance_payments_total': '0.00', 'property_id':w['property'],'lease_agreement_id':w['lease_e'],
        'period_start':'2025-01-01','period_end':'2025-12-31',
        'advance_payments_total':'0.00','accounting_scope_id':scope})
    assert run.status_code==201,run.text
    url=f"/api/settlements/{run.json()['id']}/heating-consumption"
    params={'cost_entry_id':w['ce_heizung'],'meter_type':'HEIZUNG','base_key':'AREA'}
    assert (await client_a_admin.get(url,params=params)).status_code==409
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO accounting_scope_binding
                (organization_id,property_id,scope_id,cost_entry_id,origin,reason,reviewed_by)
                SELECT organization_id,property_id,%s,id,'REVIEWED','Synthetic T46 classified heating',%s
                FROM cost_entry WHERE organization_id=%s AND property_id=%s""",
                (scope,str(w['user_a_admin_id']),str(w['org_a_id']),w['property']))
    candidates=await client_a_admin.get(url,params=params)
    assert candidates.status_code==200,candidates.text
    assert {r['unit_id'] for r in candidates.json()}==({w['u4']} if partial else {w[k] for k in ('u1','u2','u3','u4')})
    assert all(r['consumption'] is None and r['reason'] for r in candidates.json())
    revoked=await client_a_admin.post(f'/api/accounting-scopes/{scope}/grants/{grant_id}/revoke')
    assert revoked.status_code==204,revoked.text
    assert (await client_a_admin.get(url,params=params)).status_code==404


@pytest.mark.asyncio
async def test_clipped_water_run_uses_annual_readings_of_unchanged_other_units(client_a_admin, metered_m3):
    run_id = await _create_run(client_a_admin, metered_m3, "lease_a", "0.00")
    extras = _extras_for_exit(metered_m3)
    extras["unit_metrics"] = []
    result = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
    assert result.status_code == 200, result.text
    water = _water_line(result.json())
    assert Decimal(water["distribution_base_total"]) == Decimal("100")
    assert Decimal(water["amount"]) == Decimal("89.26")


@pytest.mark.asyncio
@pytest.mark.parametrize("interim,expected", [(False, "146.29"), (True, "129.51")])
async def test_clipped_heating_keeps_building_period_pool_and_uses_only_own_interim(heiz_world, rent_db, interim, expected):
    w = heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2026-07-01' WHERE id=:lease"), w)
    if interim:
        for meter, value in [(w['meters'][0], '140'), (w['meters'][2], '101')]:
            await rent_db.execute(text("""INSERT INTO meter_reading
                (id,organization_id,meter_id,reading_date,value,period)
                VALUES (:id,:org,:meter,'2026-06-30',:value,'2026')"""),
                {**w, 'id':uuid.uuid4(), 'meter':meter, 'value':Decimal(value)})
    await rent_db.commit()
    async with api_client(w) as api:
        incoming = await api.post('/api/leases', json={'unit_id':str(w['unit']),
            'tenant_profile_id':str(w['tenant']), 'valid_from':'2026-07-01',
            'base_rent':'700.00', 'advance_payment':'150.00'})
        assert incoming.status_code == 201, incoming.text
        created = await api.post('/api/settlements', json={'heating_advance_payments_total': '0.00', 'property_id':str(w['property']),
            'lease_agreement_id':str(w['lease']), 'period_start':'2026-01-01',
            'period_end':'2026-12-31', 'advance_payments_total':'0.00'})
        assert created.status_code == 201, created.text
        run = created.json()['id']
        assert created.json()['period_end'] == '2026-06-30'
        await attest_fixture(api, 'settlement', run,
            {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL', 'advance_payments_total':'0.00', 'advance_payments_source':'MANUAL'})
        result = await api.post(f'/api/settlements/{run}/compute', json=_heiz_body(w))
        assert result.status_code == 200, result.text
        assert _heiz_line(result.json())['total_amount'] == '1000.00'
        assert _heiz_line(result.json())['amount'] == expected
        final = await api.post(f'/api/settlements/{run}/finalize')
        assert final.status_code == 200, final.text
        pdf = await api.get(f'/api/settlements/{run}/pdf')
        assert pdf.status_code == 200, pdf.text


@pytest.mark.asyncio
@pytest.mark.parametrize('heiz_world', ['2026-12-01'], indirect=True)
async def test_december_heating_source_keeps_annual_weights_and_excludes_departed_tenant(heiz_world, rent_db):
    w = heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2026-10-01' WHERE id=:lease"), w)
    await rent_db.commit()
    async with api_client(w) as api:
        incoming = await api.post('/api/leases', json={'unit_id':str(w['unit']),
            'tenant_profile_id':str(w['tenant']), 'valid_from':'2026-10-01',
            'base_rent':'700.00', 'advance_payment':'150.00'})
        assert incoming.status_code == 201, incoming.text
        amounts = []
        for lease in (str(w['lease']), incoming.json()['id'], str(w['second_lease'])):
            run = await api.post('/api/settlements', json={'heating_advance_payments_total': '0.00', 'property_id':str(w['property']),
                'lease_agreement_id':lease, 'period_start':'2026-01-01',
                'period_end':'2026-12-31', 'advance_payments_total':'0.00'})
            assert run.status_code == 201, run.text
            run_id = run.json()['id']
            await attest_fixture(api, 'settlement', run_id,
                {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL', 'advance_payments_total':'0.00', 'advance_payments_source':'MANUAL'})
            result = await api.post(f'/api/settlements/{run_id}/compute', json=_heiz_body(w))
            assert result.status_code == 200, result.text
            amounts.append(sum((Decimal(row['amount']) for row in result.json()['lines']
                if row['cost_category_code']=='HEIZUNG_ZENTRAL'), Decimal(0)))
            final = await api.post(f'/api/settlements/{run_id}/finalize')
            assert final.status_code == 200, final.text
        assert amounts == [Decimal('0.00'), Decimal('295.00'), Decimal('705.00')]


@pytest.mark.asyncio
async def test_invalid_interim_water_reading_returns_manual_reason_and_422(client_a_admin, metered_m3):
    import psycopg2
    w = metered_m3
    with psycopg2.connect(SYNC_URL) as conn, conn.cursor() as cursor:
        cursor.execute("""INSERT INTO meter_reading
            (id,organization_id,meter_id,reading_date,value,period)
            SELECT %s,organization_id,id,'2025-06-30',140,'2025'
            FROM meter WHERE unit_id=%s AND meter_type='KALTWASSER'""",
            (str(uuid.uuid4()), w['u1']))
    run = await _create_run(client_a_admin, w, 'lease_a', '0.00')
    suggestions = await client_a_admin.get(f'/api/settlements/{run}/consumption-suggestions')
    assert suggestions.status_code == 200, suggestions.text
    row = next(r for r in suggestions.json() if r['unit_id']==w['u1'])
    assert row['consumption'] is None
    assert 'rückläufig' in row['reason']
    extras = _extras_for_exit(w)
    extras['unit_metrics'] = []
    result = await client_a_admin.post(f'/api/settlements/{run}/compute', json=extras)
    assert result.status_code == 422, result.text
    assert 'rückläufig' in result.text


@pytest.mark.asyncio
async def test_free_window_uses_each_overlapping_property_year(client_a_admin, metered_m3):
    w = metered_m3
    prop = await client_a_admin.put(f"/api/properties/{w['property']}", json={'fiscal_year_start_month':4})
    assert prop.status_code == 200, prop.text
    created = await client_a_admin.post('/api/settlements', json={
        'heating_advance_payments_total': '0.00', 'property_id':w['property'], 'lease_agreement_id':w['lease_e'],
        'period_start':'2025-01-01', 'period_end':'2025-12-31', 'advance_payments_total':'0.00'})
    assert created.status_code == 201, created.text
    run = created.json()['id']
    await attest_fixture(client_a_admin, 'settlement', run,
        {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL', 'advance_payments_total':'0.00', 'advance_payments_source':'MANUAL'})
    result = await client_a_admin.post(f'/api/settlements/{run}/compute', json=_extras_for_exit(w))
    assert result.status_code == 200, result.text
    water = [row for row in result.json()['lines'] if row['cost_category_code']=='KALTWASSER']
    # The Jan–Dec source contributes 40% once across the two property years.
    assert sum(Decimal(row['amount']) for row in water) == Decimal('240.00')
    external = [row for row in result.json()['lines'] if row['cost_category_code']=='HEIZUNG_ZENTRAL']
    assert len(external) == 1 and external[0]['amount'] == '300.00'


@pytest.mark.asyncio
@pytest.mark.parametrize("interim_day", ["2025-06-30", "2025-07-01"])
async def test_water_interim_reading_changes_only_the_units_lease_split(client_a_admin, metered_m3, interim_day):
    import psycopg2
    w = metered_m3
    with psycopg2.connect(SYNC_URL) as conn, conn.cursor() as cursor:
        cursor.execute("""INSERT INTO meter_reading
            (id,organization_id,meter_id,reading_date,value,period)
            SELECT %s,organization_id,id,%s,110,'2025'
            FROM meter WHERE unit_id=%s AND meter_type='KALTWASSER'""",
            (str(uuid.uuid4()), interim_day, w['u1']))
    extras = _extras_for_exit(w)
    extras['unit_metrics'] = []
    amounts = []
    for lease_key, expected in [('lease_a','60.00'), ('lease_b','120.00')]:
        run = await _create_run(client_a_admin, w, lease_key, '0.00')
        result = await client_a_admin.post(f'/api/settlements/{run}/compute', json=extras)
        assert result.status_code == 200, result.text
        water = _water_line(result.json())
        assert water['amount'] == expected
        assert Decimal(water['distribution_base_total']) == Decimal('100')
        assert water['time_share_days'] is None
        amounts.append(Decimal(water['amount']))
    assert sum(amounts) == Decimal('180.00')


@pytest.mark.asyncio
async def test_clipped_manual_water_quantity_is_the_property_year_weight(client_a_admin, metered_m3):
    w = metered_m3
    run = await _create_run(client_a_admin, w, 'lease_a', '0.00')
    result = await client_a_admin.post(f'/api/settlements/{run}/compute', json=_extras_for_exit(w))
    assert result.status_code == 200, result.text
    assert result.json()['period_end'] == '2025-06-30'
    water = _water_line(result.json())
    assert water['amount'] == '89.26'
    assert Decimal(water['distribution_base_share']) == Decimal(30)
    assert Decimal(water['distribution_base_total']) == Decimal(100)


@pytest.mark.asyncio
@pytest.mark.parametrize("second_day", ["2026-06-30", "2026-07-01"])
@pytest.mark.parametrize("consistent", [False, True])
async def test_heating_suggestions_refuse_only_inconsistent_occupant_boundary_readings(heiz_world, rent_db, second_day, consistent):
    w = heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2026-07-01' WHERE id=:lease"), w)
    for day, value in (("2026-06-30", "140"), (second_day, "140" if consistent else "150")):
        await rent_db.execute(text("""INSERT INTO meter_reading
            (id,organization_id,meter_id,reading_date,value,period)
            VALUES (:id,:org,:meter,:day,:value,'2026')"""),
            {**w, 'id':uuid.uuid4(), 'meter':w['meters'][0], 'day':date.fromisoformat(day), 'value':Decimal(value)})
    await rent_db.commit()
    async with api_client(w) as api:
        incoming = await api.post('/api/leases', json={'unit_id':str(w['unit']),
            'tenant_profile_id':str(w['tenant']), 'valid_from':'2026-07-01',
            'base_rent':'700.00', 'advance_payment':'150.00'})
        assert incoming.status_code == 201, incoming.text
        candidates = await api.get(f"/api/settlements/{w['run']}/heating-consumption", params={
            'cost_entry_id':str(w['heat']), 'meter_type':'HEIZUNG', 'base_key':'AREA'})
        assert candidates.status_code == 200, candidates.text
        own = [r for r in candidates.json() if r['unit_id'] == str(w['unit'])]
        assert len(own) == 2
        if consistent:
            assert [Decimal(r['consumption']) for r in own] == [Decimal(40), Decimal(60)]
            assert all(r['reason'] is None for r in own)
        else:
            assert all(r['consumption'] is None and 'mehrdeutig' in r['reason'] for r in own)


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_start,lease_end,rows", [
    ("2025-01-05", None, [("2025-01-05", "10"), ("2025-12-31", "1000")]),
    ("2020-01-01", "2025-12-21", [("2025-01-01", "0"), ("2025-12-20", "990")]),
    ("2025-01-02", None, [("2025-01-01", "0"), ("2025-01-02", "5"), ("2025-12-31", "1000")]),
])
async def test_water_suggestions_keep_period_readings_at_vacancy_boundaries(client_a_admin, seed_data, lease_start, lease_end, rows):
    import psycopg2
    w = _seed_m3(seed_data)
    with psycopg2.connect(SYNC_URL) as conn, conn.cursor() as cursor:
        cursor.execute("UPDATE lease_agreement SET valid_from=%s, valid_to=%s WHERE id=%s",
                       (lease_start, lease_end, w['lease_e']))
    meter = await client_a_admin.post('/api/meters', json={
        'property_id':w['property'], 'unit_id':w['u4'], 'meter_number':'VACANCY', 'meter_type':'KALTWASSER'})
    assert meter.status_code == 201, meter.text
    for day, value in rows:
        added = await client_a_admin.post(f"/api/meters/{meter.json()['id']}/readings",
                                         json={'reading_date':day, 'value':value})
        assert added.status_code == 201, added.text
    run = await _create_run(client_a_admin, w, 'lease_e', '0.00')
    response = await client_a_admin.get(f'/api/settlements/{run}/consumption-suggestions')
    assert response.status_code == 200, response.text
    row = next(r for r in response.json() if r['unit_id'] == w['u4'])
    assert Decimal(row['consumption']) == Decimal(1000 if len(rows) == 3 else 990)
    assert row['reason'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_start,lease_end,rows", [
    ("2026-01-05", None, [("2026-01-05", "10"), ("2026-12-31", "1000")]),
    ("2026-01-01", "2026-12-21", [("2026-01-01", "0"), ("2026-12-20", "990")]),
])
async def test_heating_suggestions_keep_measured_slices_at_vacancy_boundaries(heiz_world, rent_db, lease_start, lease_end, rows):
    w = heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_from=:start,valid_to=:end WHERE id=:lease"),
                          {**w, 'start':date.fromisoformat(lease_start), 'end':date.fromisoformat(lease_end) if lease_end else None})
    await rent_db.commit()
    async with api_client(w) as api:
        ignored = await api.patch(f"/api/meters/{w['meters'][0]}", json={'meter_type':'IGNORED'})
        assert ignored.status_code == 200, ignored.text
        meter = await api.post('/api/meters', json={'property_id':str(w['property']),
            'unit_id':str(w['unit']), 'meter_number':'VACANCY', 'meter_type':'HEIZUNG'})
        assert meter.status_code == 201, meter.text
        for day, value in rows:
            added = await api.post(f"/api/meters/{meter.json()['id']}/readings",
                                   json={'reading_date':day, 'value':value})
            assert added.status_code == 201, added.text
        response = await api.get(f"/api/settlements/{w['run']}/heating-consumption", params={
            'cost_entry_id':str(w['heat']), 'meter_type':'HEIZUNG', 'base_key':'AREA'})
        assert response.status_code == 200, response.text
        row = next(r for r in response.json() if r['lease_agreement_id'] == str(w['lease']))
        assert Decimal(row['consumption']) == Decimal(990)
        assert row['reason'] is None
        assert (row['start_reading']['reading_date'], row['end_reading']['reading_date']) == (rows[0][0], rows[-1][0])


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_period_end", [False, True])
async def test_heating_preserves_exact_slice_when_a_later_slice_cannot_be_resolved(heiz_world, rent_db, missing_period_end):
    w = heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2026-07-01' WHERE id=:lease"), w)
    await rent_db.commit()
    async with api_client(w) as api:
        meter = str(w['meters'][0])
        if missing_period_end:
            ignored = await api.patch(f'/api/meters/{meter}', json={'meter_type':'IGNORED'})
            assert ignored.status_code == 200, ignored.text
            created = await api.post('/api/meters', json={'property_id':str(w['property']),
                'unit_id':str(w['unit']), 'meter_number':'MISSING-END', 'meter_type':'HEIZUNG'})
            assert created.status_code == 201, created.text
            meter = created.json()['id']
        for start, end in [('2026-07-01','2026-09-30'), ('2026-10-01',None)]:
            lease = await api.post('/api/leases', json={'unit_id':str(w['unit']),
                'tenant_profile_id':str(w['tenant']), 'valid_from':start, 'valid_to':end,
                'base_rent':'700.00', 'advance_payment':'0.00'})
            assert lease.status_code == 201, lease.text
        readings = [('2026-01-01','100'), ('2026-06-30','140')] if missing_period_end else [
            ('2026-06-30','140'), ('2026-09-30','170'), ('2026-10-01','180')]
        for day, value in readings:
            added = await api.post(f'/api/meters/{meter}/readings', json={'reading_date':day, 'value':value})
            assert added.status_code == 201, added.text
        response = await api.get(f"/api/settlements/{w['run']}/heating-consumption", params={
            'cost_entry_id':str(w['heat']), 'meter_type':'HEIZUNG', 'base_key':'AREA'})
        assert response.status_code == 200, response.text
        own = sorted((r for r in response.json() if r['unit_id'] == str(w['unit'])), key=lambda r:r['period_from'])
        assert Decimal(own[0]['consumption']) == Decimal(40)
        assert own[0]['reason'] is None
        assert own[0]['end_reading']['reading_date'] == '2026-06-30'
        assert all(r['consumption'] is None and ('Periodenablesung' if missing_period_end else 'mehrdeutig') in r['reason'] for r in own[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("interim", [False, True])
async def test_heating_day_fallback_labels_its_enclosing_readings_and_day_share(heiz_world, rent_db, interim):
    w = heiz_world
    await rent_db.execute(text("UPDATE lease_agreement SET valid_to='2026-07-01' WHERE id=:lease"), w)
    await rent_db.commit()
    async with api_client(w) as api:
        if interim:
            lease = await api.post('/api/leases', json={'unit_id':str(w['unit']),
                'tenant_profile_id':str(w['tenant']), 'valid_from':'2026-07-01', 'valid_to':'2026-09-30',
                'base_rent':'700.00', 'advance_payment':'0.00'})
            assert lease.status_code == 201, lease.text
            added = await api.post(f"/api/meters/{w['meters'][0]}/readings", json={'reading_date':'2026-06-30', 'value':'140'})
            assert added.status_code == 201, added.text
        response = await api.get(f"/api/settlements/{w['run']}/heating-consumption", params={
            'cost_entry_id':str(w['heat']), 'meter_type':'HEIZUNG', 'base_key':'AREA'})
        assert response.status_code == 200, response.text
        own = sorted((r for r in response.json() if r['unit_id'] == str(w['unit'])), key=lambda r:r['period_from'])
        for row, days in zip(own[1:] if interim else own, [92, 92] if interim else [181, 184]):
            total, denominator = (60, 184) if interim else (100, 365)
            assert abs(Decimal(row['consumption']) - Decimal(total) * days / denominator) < Decimal('1e-25')
            assert row['start_reading']['reading_date'] == ('2026-06-30' if interim else '2026-01-01')
            assert row['end_reading']['reading_date'] == '2026-12-31'
            assert ('Abschnittsbasis' if interim else 'Periodenbasis') in row['reason']
            assert f'{days}/{denominator}' in row['reason']
