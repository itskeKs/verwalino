"""Bind the maintenance reserve happy path through the HTTP API."""
from pathlib import Path

import pytest
from pytest_bdd import given, when, then, scenarios

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person
from tests.financial_coverage_helpers import attest_fixture

scenarios(str(Path(__file__).resolve().parents[3] / 'features/maintenance_reserve_ledger.feature'))


@given('Anna and Bernd have a 7000 euro cost plan and a 6000 euro reserve plan', target_fixture='split_reserve')
def split_reserve(world, seed_data):
    w = reserve_correction_property(world, seed_data)
    w['plan'] = reserve_correction_plan(world, w)
    report = create(world, '/api/weg-settlements', {'property_id': w['property'],
        'accounting_scope_id': w['scope'], 'economic_plan_id': w['plan'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    w['run'] = report['id']
    return w


@when('Anna pays 4200 euros Hausgeld and 3600 euros reserve and Bernd pays 5200 euros Hausgeld')
def split_reserve_receipts(world, split_reserve):
    from tests.test_reserve_ledger import payment, paid
    w = split_reserve
    world.run(payment(world.actor, w, '4200.00', kind='HAUSGELD_VORSCHUSS'))
    world.run(payment(world.actor, w, '3600.00'))
    world.run(payment(world.actor, {**w, 'owner_a': w['owner_b']}, '5200.00', kind='HAUSGELD_VORSCHUSS'))
    values = paid(w)
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert response.status_code == 200, response.text
    w['result'] = response.json()


@then('Anna has 3600 euros reserve and Bernd has 2400 euros reserve with nothing payable')
def split_reserve_owner_amounts(split_reserve):
    w = split_reserve
    for key, amount in [('owner_a', '3600.00'), ('owner_b', '2400.00')]:
        line = next(row for row in w['result']['lines'] if row['line_type'] == 'RESERVE_CONTRIBUTION'
                    and row['addressee_owner_profile_id'] == w[key])
        assert line['amount'] == amount
        owner = next(row for row in w['result']['owner_results'] if row['owner_profile_id'] == w[key])
        assert owner['payment_amount'] == '0.00'


@then('the total reserve contribution and closing balance are both 6000 euros')
def split_reserve_total(world, split_reserve):
    ledger = world.act('GET', f"/api/weg-settlements/{split_reserve['run']}/reserve-ledger").json()
    assert ledger['contributions_total'] == ledger['closing_balance'] == '6000.00'


def privacy_export(world):
    import asyncio
    import json
    from tests.bdd.world import target_url
    from tests.test_organization_export import unpack
    response = world.act('POST', '/api/organizations/export')
    if target_url() and response.status_code == 429:
        # The public endpoint's cooldown is one minute. Do not patch a remote
        # process or retry unrelated refusals; one bounded wait must suffice.
        delay = response.headers.get('Retry-After', '')
        assert delay.isascii() and delay.isdecimal() and 0 < int(delay) <= 60, response.text
        world.run(asyncio.sleep(int(delay)))
        response = world.act('POST', '/api/organizations/export')
    assert response.status_code == 200, response.text
    files = unpack(response.content)
    return {entity: {row['id']: row for row in
        (json.loads(line) for line in files[f'entities/{entity}.jsonl'].splitlines())}
        for entity in ('person', 'payment', 'reserve_ledger_entry', 'audit_event', 'settlement_run')}


@given('a retained repayment to Anna and an incidental 300 euro reserve note about Eva', target_fixture='reserve_privacy_case')
def reserve_privacy_case(world, seed_data, reserve_retention_context, monkeypatch):
    from datetime import timedelta
    from app.services import organization_export_service
    from tests.test_reserve_ledger import reserve_erasure_case, retained_anna, finalize
    from tests.bdd.world import target_url
    source = reserve_retention_context
    seed = {**seed_data, 'org_a_id': source['org'], 'user_a_admin_id': source['admin']}
    if target_url():
        w = reserve_correction_property(world, seed, actor=source['client'])
        w['plan'] = reserve_correction_plan(world, w)
        report = create(world, '/api/weg-settlements', {'property_id': w['property'],
            'accounting_scope_id': w['scope'], 'economic_plan_id': w['plan'],
            'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        w['run'] = report['id']
        w['person'] = create(world, '/api/persons', {'first_name': 'Eva', 'last_name': 'Muster'})['id']
        entry = create(world, f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", {
            'component': 'OPENING', 'amount': '300.00', 'reason': 'Abstimmung mit Eva Muster'})
        w['entry'] = entry['id']
    else:
        monkeypatch.setattr(organization_export_service, 'EXPORT_INTERVAL', timedelta(0))
        w = world.run(reserve_erasure_case(world.actor, seed))
    w['anna'], w['payment'] = world.run(retained_anna(world.actor, source['property']))
    w['frozen'] = world.run(finalize(world.actor, w))
    w['before_export'] = privacy_export(world)
    return w


@when('I request deletion of the actual repayment recipient Anna')
def request_retained_anna(world, reserve_privacy_case):
    person = reserve_privacy_case['anna']['id']
    response = world.act('POST', f'/api/dsgvo/persons/{person}/request-deletion')
    assert response.status_code == 200, response.text
    assert response.json()['applied'] == 'RESTRICTED'
    assert response.json()['bindings'] == [{'kind': 'BOOKED_PAYMENT', 'entity_type': 'payment',
        'entity_id': reserve_privacy_case['payment']['id'],
        'retention_until': reserve_privacy_case['payment']['retention_until']}]


@then('Anna remains identifiable and the repayment evidence and reserve rows stay unchanged')
def retained_repayment_export(world, reserve_privacy_case):
    w = reserve_privacy_case
    after = privacy_export(world)
    person = after['person'][w['anna']['id']]
    assert (person['first_name'], person['last_name']) == ('Anna', 'Beispiel')
    assert person['processing_restricted'] and person['anonymized_at'] is None
    assert after['payment'] == w['before_export']['payment']
    assert after['reserve_ledger_entry'] == w['before_export']['reserve_ledger_entry']
    w['after_export'] = after


@when('I request deletion of the genuinely unbound incidental contact Eva')
def request_incidental_eva(world, reserve_privacy_case):
    response = world.act('POST', f"/api/dsgvo/persons/{reserve_privacy_case['person']}/request-deletion")
    assert response.status_code == 200, response.text
    assert response.json()['applied'] == 'ANONYMIZED'
    assert response.json()['bindings'] == []


@then('only Eva is removed from the live reserve reason and its exact audit copy for 300 euros')
def incidental_reason_export(world, reserve_privacy_case):
    from copy import deepcopy
    from decimal import Decimal
    w = reserve_privacy_case
    after = privacy_export(world)
    expected = deepcopy(w['before_export'])
    expected['reserve_ledger_entry'][w['entry']]['reason'] = 'Abstimmung mit Gelöscht'
    for event in expected['audit_event'].values():
        if (event.get('payload_diff') or {}).get('entry_id') == w['entry']:
            event['payload_diff']['reason'] = 'Abstimmung mit Gelöscht'
    assert after['reserve_ledger_entry'] == expected['reserve_ledger_entry']
    assert Decimal(str(after['reserve_ledger_entry'][w['entry']]['amount'])) == Decimal('300.00')
    for identifier, event in expected['audit_event'].items():
        if event['action'].startswith('reserve.'):
            assert after['audit_event'][identifier] == event
    assert after['person'][w['anna']['id']] == expected['person'][w['anna']['id']]
    assert after['payment'] == expected['payment']
    w['after_export'] = after


@then('the complete finalized statement and its original digest remain identical')
def preserved_privacy_snapshot(world, reserve_privacy_case):
    from app.engine.canonical import canonical_sha256
    w = reserve_privacy_case
    before = w['before_export']['settlement_run'][w['run']]
    after = w['after_export']['settlement_run'][w['run']]
    assert after == before
    assert canonical_sha256(after['snapshot_payload']) == before['snapshot_sha256'] == w['frozen']['snapshot_sha256']


def reserve_correction_property(world, seed_data, *, scoped=True, actor=None):
    """Build the two-owner reserve fixture through the selected HTTP target."""
    world.actor = actor or world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {'name': 'WEG Hauptstrasse 5',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Reserve corrections', 'reason': 'Synthetic reserve corrections',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'],
                'reason': 'Synthetic reserve corrections'}]}}, keyed=True)
    w = {'property': home['id']}
    for number, mea, name, suffix in [('1', '600', 'Anton', 'a'), ('2', '400', 'Berta', 'b')]:
        unit = create(world, '/api/units', {'property_id': home['id'], 'number': number,
            'usage_type': 'WOHNUNG', 'mea': mea, 'area': mea, 'heated_area': mea})
        person = create(world, '/api/persons', {'first_name': name, 'last_name': 'Eigentuemer'})
        owner = create(world, '/api/owner-profiles', {'person_id': person['id']})
        create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'],
            'valid_from': '2000-01-01'})
        w.update({f'u{number}': unit['id'], f'owner_{suffix}': owner['id']})
    basis = {'property_id': home['id']}
    if scoped:
        scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
            'label': 'WEG Rücklagenkreis', 'valid_from': '2000-01-01', 'whole_property': True}, keyed=True)
        w['scope'] = scope['id']
        create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
            'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'],
            'reason': 'Synthetic reserve corrections'}, keyed=True)
        basis['accounting_scope_id'] = scope['id']
    for key, kind, label in [('bank_giro', 'LAUFEND', 'Girokonto'),
                             ('bank_ruecklage', 'RUECKLAGE', 'Ruecklagenkonto')]:
        account = create(world, '/api/bank-accounts', {**basis, 'account_type': kind, 'label': label})
        w[key] = account['id']
    categories = world.act('GET', '/api/cost-categories')
    assert categories.status_code == 200, categories.text
    categories = {row['code']: row for row in categories.json()}
    for code, amount in [('GARTENPFLEGE', '4000.00'), ('WEG_VERWALTERKOSTEN', '3000.00'),
                         ('ERHALTUNGSRUECKLAGE', '6000.00')]:
        category = categories[code]
        cost = create(world, '/api/cost-entries', {**basis, 'cost_category_id': category['id'],
            'cost_category_plan_version': category['plan_version'], 'document_number': f'RE-{code}',
            'invoice_date': '2025-06-01', 'service_period_from': '2025-01-01',
            'service_period_to': '2025-12-31', 'gross_amount': amount, 'owner_distribution_key': 'MEA',
            'tenant_distribution_key': 'MEA', 'tenant_apportionable': 'NICHT_UMLAGEFAEHIG', 'source': 'MANUAL'})
        response = world.act('POST', f"/api/cost-entries/{cost['id']}/confirm")
        assert response.status_code == 200, response.text
    return w


def reserve_correction_plan(world, w):
    """Finalize the same 7000-euro costs and 6000-euro reserve on its scope."""
    plan = create(world, '/api/economic-plans', {'property_id': w['property'],
        'accounting_scope_id': w['scope'], 'period_start': '2025-01-01',
        'period_end': '2025-12-31', 'plan_year': 2025})
    values = {'planned_costs': [
        {'category_code': 'GARTENPFLEGE', 'label': 'Gartenpflege', 'planned_amount': '4000.00', 'cost_group': 'UMLAGEFAEHIG'},
        {'category_code': 'WEG_VERWALTERKOSTEN', 'label': 'Verwalter', 'planned_amount': '3000.00', 'cost_group': 'NICHT_UMLAGEFAEHIG'},
    ], 'reserve_contribution_total': '6000.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    return plan['id']


@given('an empty asset report for 2020 precedes the first real report for 2025', target_fixture='stray_reserve_draft')
def stray_reserve_draft(world, seed_data):
    # The original deletable legacy draft has no immutable accounting-scope binding.
    w = reserve_correction_property(world, seed_data, scoped=False)
    for key, year in [('stray', 2020), ('real', 2025)]:
        report = create(world, '/api/weg-settlements/asset-reports', {
            'property_id': w['property'],
            'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        w[key] = report['id']
    return w


@when('I delete the accidentally opened 2020 draft')
def delete_stray_reserve_draft(world, stray_reserve_draft):
    response = world.act('DELETE', f"/api/weg-settlements/asset-reports/{stray_reserve_draft['stray']}")
    assert response.status_code == 204, response.text


@then('the 2020 report disappears and I can import 12500 euros for 2025 with a reason')
def real_opening_import(world, stray_reserve_draft):
    w = stray_reserve_draft
    reports = world.act('GET', '/api/weg-settlements', params={'property_id': w['property'], 'run_type': 'VERMOEGENSBERICHT'})
    assert w['stray'] not in [row['id'] for row in reports.json()]
    path = f"/api/weg-settlements/{w['real']}/reserve-ledger"
    response = world.act('POST', path + '/overrides', json={
        'component': 'OPENING', 'amount': '12500.00', 'reason': 'Bankbestätigter Anfangsbestand 2025'})
    assert response.status_code == 201, response.text
    assert world.act('GET', path).json()['opening_balance'] == '12500.00'


@when('I correct a receipt without withdrawals by plus one euro')
def positive_adjustment(world, reserve_scenario):
    from tests.financial_coverage_helpers import reserve_evidence
    w = reserve_scenario
    category = next(row for row in world.act('GET', '/api/cost-categories').json() if row['code'] == 'GARTENPFLEGE')
    cost = create(world, '/api/cost-entries', {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'cost_category_id': category['id'], 'cost_category_plan_version': category['plan_version'],
        'document_number': 'RE-without-payout', 'invoice_date': '2025-02-01',
        'service_period_from': '2025-01-01', 'service_period_to': '2025-12-31',
        'gross_amount': '1200.00', 'tenant_apportionable': 'UMLAGEFAEHIG', 'source': 'MANUAL'})
    assert world.act('POST', f"/api/cost-entries/{cost['id']}/confirm").status_code == 200
    w['ce_garten'] = cost['id']
    evidence = world.run(reserve_evidence(world.actor, w))
    w['adjustment_refusal'] = world.act('POST', f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '1.00', 'reason': 'Receipt has no withdrawals',
        **{key.removeprefix('reserve_'): value for key, value in evidence.items()}})


@then('the adjustment is refused without increasing the reserve balance')
def positive_adjustment_refused(world, reserve_scenario):
    w = reserve_scenario
    refused = w['adjustment_refusal']
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_ADJUSTMENT_EXCEEDS_WITHDRAWALS'
    ledger = world.act('GET', f"/api/weg-settlements/{w['run']}/reserve-ledger").json()
    assert ledger['closing_balance'] == '6000.00'


@then('the opening correction dialog fits the viewport with an error and visible actions')
def opening_correction_viewport(world, reserve_scenario, browser_world, tmp_path):
    import os
    from playwright.async_api import expect
    w = reserve_scenario
    directory = Path(__file__).resolve().parents[3] / 'docs/assets/reserve-ledger' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    created = world.act('POST', f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '5000.00', 'reason': 'Bank-confirmed opening'})
    assert created.status_code == 201, created.text
    report = create(world, '/api/weg-settlements/asset-reports', {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})

    async def inspect():
        page = browser_world.page
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        for kind, route in [('weg', f"/weg-abrechnungen/{w['run']}"), ('asset', f"/vermoegensberichte/{report['id']}")]:
            for width in (1280, 360):
                await page.set_viewport_size({'width': width, 'height': 800})
                await browser_world.visit(route)
                assert page.url.endswith(route) and await page.title()
                await page.get_by_role('button', name='Korrektur erfassen', exact=True).click()
                await page.get_by_label('Art', exact=True).select_option('OPENING')
                amount = page.get_by_role('dialog').locator('input')
                await expect(amount).to_have_value('5000,00')
                await amount.fill('5500,00')
                await expect(page.get_by_text('Differenz:', exact=False)).to_contain_text('+500,00')
                await page.get_by_label('Begründung *', exact=True).fill('Anfangsbestand anhand vollständiger Bankunterlagen berichtigt')
                await page.screenshot(path=str(directory / f'round16-{kind}-opening-after-{width}.png'), animations='disabled')
                if width == 360:
                    await amount.fill('ungültig')
                    await page.get_by_role('button', name='Speichern', exact=True).click()
                    await expect(page.get_by_role('dialog').get_by_role('alert')).to_be_visible()
                    await page.screenshot(path=str(directory / f'round16-{kind}-error-after-{width}.png'), animations='disabled')
                    await expect(page.get_by_role('button', name='Speichern', exact=True)).to_be_in_viewport()
                    await expect(page.get_by_role('button', name='Abbrechen', exact=True)).to_be_in_viewport()
                    dialog = page.get_by_role('dialog')
                    assert await dialog.evaluate('(el) => el.scrollHeight <= el.clientHeight')
                    assert await dialog.locator('form').evaluate('(el) => el.noValidate')
                    close = await page.get_by_role('button', name='Schließen', exact=True).bounding_box()
                    assert close['width'] >= 44 and close['height'] >= 44, close
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert await page.locator('vite-error-overlay').count() == 0
                await page.get_by_role('button', name='Abbrechen', exact=True).click()
        await page.get_by_role('button', name='Korrektur erfassen', exact=True).click()
        await page.get_by_label('Art', exact=True).select_option('WITHDRAWAL')
        await page.get_by_label('Betrag *', exact=True).fill('1200,00')
        await page.get_by_label('Begründung *').fill('Belegte Entnahmekorrektur anhand vollständiger Bankunterlagen geprüft')
        await page.get_by_role('button', name='Speichern', exact=True).click()
        refusal = page.get_by_role('dialog').get_by_role('alert')
        await expect(refusal).to_have_text('Bitte verknüpfen Sie einen bestätigten Kostenbeleg und einen Beschluss aus der Beschluss-Sammlung.')
        await refusal.scroll_into_view_if_needed()
        await expect(page.get_by_role('button', name='Speichern', exact=True)).to_be_in_viewport()
        await expect(page.get_by_role('button', name='Abbrechen', exact=True)).to_be_in_viewport()
        fields = page.get_by_role('dialog').locator('fieldset')
        assert await fields.evaluate('(el) => el.scrollHeight > el.clientHeight')
        await page.screenshot(path=str(directory / 'round16-withdrawal-error-after-360.png'), animations='disabled')
        await page.get_by_label('Art', exact=True).scroll_into_view_if_needed()
        await expect(page.get_by_role('button', name='Speichern', exact=True)).to_be_in_viewport()
        assert not errors, errors
    browser_world.run(inspect())


def imported_payout(world, w):
    statement = ('Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck;Währung\n'
        '02.02.2025;03.02.2025;-1200,00;Gartenservice;;Reparatur;EUR\n').encode()
    response = world.act('POST', f"/api/bank-accounts/{w['bank_giro']}/imports", params={'dry_run': 'false'},
        files={'file': ('reserve.csv', statement, 'text/csv')})
    assert response.status_code == 200, response.text
    transactions = world.act('GET', '/api/bank-transactions', params={'bank_account_id': w['bank_giro'], 'status': 'NEU'}).json()['items']
    transaction = next(row for row in transactions if row['amount'] == '-1200.00')
    response = world.act('POST', f"/api/bank-transactions/{transaction['id']}/assign", json={'lines': [{
        'payment_type': 'RUECKLAGENENTNAHME', 'amount': '1200.00', 'period_year': 2025, 'period_month': 2}]})
    assert response.status_code == 200, response.text
    return transaction['id'], response.json()['payments'][0]['id']


@when('I attest opening balances of 5000, 5500 and 5200 with reasons')
def reattest_opening(world, reserve_scenario):
    w = reserve_scenario
    for opening in ('5000.00', '5500.00', '5200.00'):
        values = {'paid_advances': [{'owner_profile_id': w['owner_a'], 'paid_total': '0.00'}],
            'reserve_opening_balance': opening, 'reserve_override_reason': 'Bank records reconciled'}
        world.run(attest_fixture(world.actor, 'settlement', w['run'], values))


@then('the opening is 5200 and retains entries of 5000, 500 and minus 300')
def corrected_opening(world, reserve_scenario):
    from decimal import Decimal
    ledger = world.look(world.actor, f"/api/weg-settlements/{reserve_scenario['run']}/reserve-ledger").json()
    assert ledger['opening_balance'] == '5200.00'
    assert sorted(Decimal(row['amount']) for row in ledger['entries'] if row.get('component') == 'OPENING') == list(map(Decimal, ['-300', '500', '5000']))


@when('I release an imported reserve payout draft with incomplete evidence (none or one reference)')
def release_payout(world, reserve_scenario):
    w = reserve_scenario
    w['transaction'], w['payout'] = imported_payout(world, w)
    response = world.act('DELETE', f"/api/payments/{w['payout']}")
    assert response.status_code == 204, response.text
    retained = world.look(world.actor, f"/api/payments/{w['payout']}")
    assert retained.status_code == 200, retained.text
    assert retained.json()['status'] == 'cancelled'
    assert retained.json()['bank_transaction_id'] == w['transaction']
    assert world.act('DELETE', f"/api/payments/{w['payout']}").status_code == 409


@then('its bank line is new and can be assigned again')
def retype_payout(world, reserve_scenario):
    w = reserve_scenario
    rows = world.act('GET', '/api/bank-transactions', params={'bank_account_id': w['bank_giro'], 'status': 'NEU'}).json()['items']
    assert next(row for row in rows if row['id'] == w['transaction'])['status'] == 'NEU'
    response = world.act('POST', f"/api/bank-transactions/{w['transaction']}/assign", json={'lines': [{
        'payment_type': 'RUECKLAGENANTEIL', 'owner_profile_id': w['owner_a'], 'amount': '1200.00', 'period_year': 2025, 'period_month': 2}]})
    assert response.status_code == 200, response.text
    assert response.json()['payments'][0]['status'] == 'booked'
    assert response.json()['payments'][0]['id'] != w['payout']


@then('the opening review and payout release work on desktop and mobile')
def opening_and_release_forms(world, reserve_scenario, browser_world, request):
    from playwright.async_api import expect
    from tests.bdd.report import artifact_directory, attach_artifact
    w = reserve_scenario
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = world.act('POST', path + '/overrides', json={'component': 'OPENING', 'amount': '5000.00', 'reason': 'Bank-confirmed opening'})
    assert response.status_code == 201, response.text
    report = create(world, '/api/weg-settlements/asset-reports', {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    directory = artifact_directory(request.node)

    async def inspect(width, payout):
        page = browser_world.page
        await page.set_viewport_size({'width': width, 'height': 1000})
        for prefix, route, label, balance in [
            ('weg', f"/weg-abrechnungen/{w['run']}", 'Rücklagen-Anfangsbestand', '5500.00' if width == 1280 else '5200.00'),
            ('asset', f"/vermoegensberichte/{report['id']}", 'Rücklage Anfangsbestand', '5000.00' if width == 1280 else '5500.00')]:
            await browser_world.visit(route)
            assert page.url.endswith(route)
            assert await page.title()
            current = page.get_by_text('Aktueller Anfangsbestand:', exact=False)
            await expect(current).not_to_contain_text('Wird geladen')
            if prefix == 'weg':
                await page.get_by_label('Tatsächlich gezahltes Hausgeld · Mustereigentümer mit langem Nachnamen', exact=True).fill('6000.00')
            else:
                await page.get_by_label('Kontostand · WEG Girokonto', exact=True).fill('11000.00')
            await page.get_by_label(label, exact=True).fill(balance)
            await page.get_by_label('Begründung der Rücklagenwerte', exact=True).fill('Anfangsbestand anhand vollständiger Bankunterlagen berichtigt')
            await page.get_by_label('Grundlage der Prüfung', exact=True).fill('Vollständige synthetische Jahresunterlagen geprüft')
            await page.get_by_role('checkbox').check()
            await page.get_by_role('button', name='Datengrundlage speichern', exact=True).click()
            await expect(page.get_by_role('status')).to_contain_text('Prüfung gespeichert')
            await expect(current).to_contain_text('5.200,00' if balance == '5200.00' else '5.500,00' if balance == '5500.00' else '5.000,00')
            await page.get_by_label(label, exact=True).scroll_into_view_if_needed()
            await capture(f'round10-{prefix}-opening-after-{width}.png')
            panel = page.get_by_role('region', name='Rücklagenbuch', exact=True)
            opening_entry = panel.get_by_text('Korrektur Anfangsbestand', exact=False).first
            await expect(opening_entry).to_be_visible()
            await expect(panel.get_by_text('Anfangsbestand anhand vollständiger Bankunterlagen berichtigt', exact=True).first).to_be_visible()
            await opening_entry.scroll_into_view_if_needed()
            await expect(opening_entry).to_be_in_viewport()
            await capture(f'round10-{prefix}-ledger-after-{width}.png')
        await browser_world.visit(f'/zahlungen/{payout}')
        release = page.get_by_role('button', name='Zuordnung lösen', exact=True)
        await expect(release).to_be_visible()
        await release.scroll_into_view_if_needed()
        await capture(f'round10-release-after-{width}.png')
        page.once('dialog', lambda dialog: dialog.accept())
        await release.click()
        await expect(page).to_have_url(f"{browser_world.session.base_url}/bankkonten/{w['bank_giro']}/umsaetze")
        new_status = page.get_by_role('cell', name='Neu', exact=True)
        await expect(new_status).to_be_visible()
        await new_status.scroll_into_view_if_needed()
        await expect(new_status).to_be_in_viewport()
        await capture(f'round10-released-bank-line-{width}.png')

    async def capture(filename):
        page = browser_world.page
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert await page.locator('vite-error-overlay').count() == 0
        if directory:
            destination = directory / filename
            await page.screenshot(path=str(destination), full_page=False)
            attach_artifact(request.node, destination)

    for width in (1280, 360):
        _, payout = imported_payout(world, w)
        browser_world.run(inspect(width, payout))


@given('a WEG with a finalized reserve plan and a booked reserve contribution', target_fixture='reserve_scenario')
def reserve_scenario(world, seed_data):
    world.actor = world.administrator_of('Verwaltung Alpha')
    world.actor_organization = 'Verwaltung Alpha'
    client_a_admin = world.actor
    user = str(seed_data['user_a_admin_id'])
    home = create(world, '/api/properties', {'name': 'WEG Rücklagenbuch mit langer Objektbezeichnung',
        'management_type': 'WEG', 'mea_denominator': 1000,
        'data_audience': {'label': 'Reserve acceptance', 'reason': 'Synthetic reserve journey',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'], 'reason': 'Synthetic reserve journey'}]}}, keyed=True)
    unit = create(world, '/api/units', {'property_id': home['id'], 'number': 'Wohnung 1', 'usage_type': 'WOHNUNG', 'mea': '1000', 'area': '100'})
    person = create_person(world, 'Verwaltung Alpha', 'Mustereigentümer mit langem Nachnamen')
    owner = create(world, '/api/owner-profiles', {'person_id': person.id})
    create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'], 'valid_from': '2020-01-01'})
    scope = create(world, '/api/accounting-scopes', {'property_id': home['id'], 'scope_type': 'WEG',
        'label': 'WEG Rücklagenkreis', 'valid_from': '2025-01-01', 'whole_property': True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic reserve journey'}, keyed=True)
    account = create(world, '/api/bank-accounts', {'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'account_type': 'LAUFEND', 'label': 'WEG Girokonto'})
    plan = create(world, '/api/economic-plans', {'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'plan_year': 2025, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    plan_values = {'planned_costs': [], 'reserve_contribution_total': '6000.00'}
    world.run(attest_fixture(client_a_admin, 'economic_plan', plan['id'], plan_values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=plan_values)
    assert response.status_code == 200, response.text
    run = create(world, '/api/weg-settlements', {'property_id': home['id'], 'accounting_scope_id': scope['id'],
        'economic_plan_id': plan['id'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    w = {'run': run['id'], 'owner_a': owner['id'], 'bank_giro': account['id'],
         'property': home['id'], 'scope': scope['id']}
    response = world.send(client_a_admin, 'POST', '/api/payments', json={
        'bank_account_id': w['bank_giro'], 'direction': 'IN', 'amount': '6000.00',
        'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENANTEIL',
        'owner_profile_id': w['owner_a'], 'period_year': 2025, 'period_month': 2})
    assert response.status_code == 201, response.text
    response = world.send(client_a_admin, 'POST', f"/api/payments/{response.json()['id']}/book")
    assert response.status_code == 200, response.text
    return w


@then('draft reserve captions and opening corrections remain accurate after computation')
def draft_reserve_wording(world, reserve_scenario, browser_world, tmp_path):
    from playwright.async_api import expect
    w = reserve_scenario
    observed_titles = []
    observed_captions = []
    errors = []

    async def inspect_correction():
        page = browser_world.page
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        await browser_world.visit(f"/weg-abrechnungen/{w['run']}")
        await page.get_by_role('button', name='Korrektur erfassen', exact=True).click()
        await page.get_by_label('Art', exact=True).select_option('OPENING')
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await page.screenshot(path=str(tmp_path / f'round13c-opening-{width}.png'), animations='disabled')
        observed_titles.append(await page.get_by_role('dialog').inner_text())
        await page.get_by_role('button', name='Abbrechen', exact=True).click()

    browser_world.run(inspect_correction())
    sibling = create(world, '/api/weg-settlements', {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'economic_plan_id': world.act('GET', f"/api/weg-settlements/{w['run']}").json()['economic_plan_id'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    finalize(world, w)
    values = {'paid_advances': [{'owner_profile_id': w['owner_a'], 'paid_total': '0.00'}]}
    world.run(attest_fixture(world.actor, 'settlement', sibling['id'], values))

    async def inspect_computed():
        page = browser_world.page
        await browser_world.visit(f"/weg-abrechnungen/{sibling['id']}")
        await page.get_by_label('Tatsächlich gezahltes Hausgeld · Mustereigentümer mit langem Nachnamen', exact=True).fill('0.00')
        await page.get_by_label('Rücklagen-Anfangsbestand', exact=True).fill('')
        ledger = page.get_by_role('region', name='Rücklagenbuch', exact=True)
        async with page.expect_response(lambda response: response.url.endswith(f"/{sibling['id']}/compute")) as computed:
            await page.get_by_role('button', name='Berechnen', exact=True).click()
        response = await computed.value
        assert response.status == 200, await response.text()
        await expect(page.get_by_text('Noch nicht berechnet.', exact=False)).to_have_count(0)
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await ledger.scroll_into_view_if_needed()
            await page.screenshot(path=str(tmp_path / f'round13c-draft-{width}.png'), animations='disabled')
            observed_captions.append(await ledger.inner_text())
            assert await ledger.evaluate('(element) => element.scrollWidth <= element.clientWidth')
        assert await page.locator('vite-error-overlay').count() == 0

    browser_world.run(inspect_computed())
    assert 'Anfangsbestand korrigieren' in observed_titles[0], str(tmp_path)
    assert all('Finalisierter Rücklagenstand' not in caption for caption in observed_captions), str(tmp_path)
    assert not errors, errors


@when('I finalize an asset report with an opening of 3000 euros')
def finalize_plain_asset_report(world, reserve_scenario):
    from tests.financial_coverage_helpers import reserve_evidence
    w = reserve_scenario
    report = create(world, '/api/weg-settlements/asset-reports', {
        'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    w['plain_report'] = report['id']
    values = {'reserve_opening_balance': '3000.00',
        'reserve_override_reason': 'Bankbestätigter Anfangsbestand',
        'bank_balances': [{'bank_account_id': w['bank_giro'], 'label': 'WEG Girokonto',
            'account_type': 'LAUFEND', 'balance': '9000.00'}],
        'receivables': [{'label': 'Hausgeld Müller', 'amount': '100.00'}],
        'payables': [{'label': 'Rechnung Dachreparatur', 'amount': '250.00'}]}
    world.run(attest_fixture(world.actor, 'settlement', report['id'], values))
    category = next(row for row in world.act('GET', '/api/cost-categories').json() if row['code'] == 'GARTENPFLEGE')
    cost = create(world, '/api/cost-entries', {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'cost_category_id': category['id'], 'cost_category_plan_version': category['plan_version'],
        'document_number': 'RE-asset-report-correction', 'invoice_date': '2025-02-01',
        'service_period_from': '2025-01-01', 'service_period_to': '2025-12-31',
        'gross_amount': '50.00', 'tenant_apportionable': 'UMLAGEFAEHIG', 'source': 'MANUAL'})
    assert world.act('POST', f"/api/cost-entries/{cost['id']}/confirm").status_code == 200
    evidence = world.run(reserve_evidence(world.actor, {**w, 'ce_garten': cost['id']}))
    response = world.act('POST', f"/api/weg-settlements/{report['id']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '-50.00', 'reason': 'Begründete Rücklagenkorrektur',
        **{key.removeprefix('reserve_'): value for key, value in evidence.items()}})
    assert response.status_code == 201, response.text
    world.run(attest_fixture(world.actor, 'settlement', report['id'], values))
    response = world.act('POST', f"/api/weg-settlements/asset-reports/{report['id']}/finalize", json=values)
    assert response.status_code == 200, response.text


@then('the asset report shows four plain reserve labels and every amount in German format on desktop and mobile')
def plain_asset_report(world, reserve_scenario, browser_world, tmp_path):
    import os
    import re
    from playwright.async_api import expect

    directory = tmp_path
    if os.getenv('VERWALINO_CAPTURE_DESIGN') == '1':
        directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/asset-report-reserve'
        directory.mkdir(parents=True, exist_ok=True)

    async def inspect():
        page = browser_world.page
        await browser_world.visit(f"/vermoegensberichte/{reserve_scenario['plain_report']}")
        await expect(page.get_by_role('button', name='PDF herunterladen')).to_be_visible()
        assert page.url.endswith(f"/vermoegensberichte/{reserve_scenario['plain_report']}")
        assert await page.title() == 'Hausverwaltung'
        await page.get_by_role('link').filter(has_text='Alle Vermögensberichte').click()
        await expect(page.get_by_role('heading', name='Vermögensberichte', exact=True)).to_be_visible()
        await page.go_back()
        await expect(page.get_by_role('button', name='PDF herunterladen')).to_be_visible()
        table = page.locator('.card').filter(has=page.get_by_role('heading', name='Vermögensaufstellung', exact=True))
        await expect(table).to_be_visible()
        # Capture both widths before assertions so the red run also records the baseline.
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await table.scroll_into_view_if_needed()
            await page.screenshot(path=str(directory / f'asset-report-{width}.png'), animations='disabled')
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await expect(table.get_by_role('columnheader', name='Erhaltungsrücklage', exact=True)).to_be_visible()
            for label, amount in [('Anfangsbestand', '3.000,00 €'), ('Zuführungen', '6.000,00 €'),
                                  ('Entnahmen', '0,00 €'), ('Endbestand', '8.950,00 €')]:
                row = table.get_by_role('row').filter(has_text=re.compile(f'^{label}'))
                await expect(row).to_have_count(1)
                await expect(row.get_by_role('cell').last).to_have_text(amount)
            text = await table.inner_text()
            assert 'Erhaltungsruecklage' not in text and 'Zufuehrung' not in text
            assert 'RESERVE_' not in text
            for label, amount in [('WEG Girokonto', '9.000,00 €'), ('Hausgeld Müller', '100,00 €'),
                                  ('Rechnung Dachreparatur', '250,00 €'), ('Begründete Rücklagenkorrekturen', '-50,00 €')]:
                await expect(table.get_by_role('row').filter(has_text=label).get_by_role('cell').last).to_have_text(amount)
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    browser_world.run(inspect())


@when('I review its reserve ledger and record the imported opening balance')
def review(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = world.look(client_a_admin, path)
    assert response.status_code == 200, response.text
    assert response.json()['planned_contributions_total'] == '6000.00'
    assert response.json()['contributions_total'] == '6000.00'
    response = world.send(client_a_admin, 'POST', path + '/overrides', json={
        'component': 'OPENING', 'amount': '20000.00', 'reason': 'Imported opening confirmed against bank records'})
    assert response.status_code == 201, response.text


@when('I finalize the WEG settlement with the reviewed ledger')
def finalize(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    values = {'paid_advances': [{'owner_profile_id': w[key], 'paid_total': '0.00'} for key in ('owner_a',)]}
    world.run(attest_fixture(client_a_admin, 'settlement', w['run'], values))
    response = world.send(client_a_admin, 'POST', f"/api/weg-settlements/{w['run']}/finalize", json=values)
    assert response.status_code == 200, response.text


@then('the reserve closing balance includes the cash contribution')
def closing(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    response = world.look(client_a_admin, f"/api/weg-settlements/{w['run']}/reserve-ledger")
    assert response.status_code == 200, response.text
    assert response.json()['closing_balance'] == '26000.00'
    assert response.json()['frozen'] is True
    w['frozen'] = response.json()


@then('the finalized reserve ledger remains unchanged')
def frozen(world, reserve_scenario):
    client_a_admin = world.actor
    w = reserve_scenario
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = world.send(client_a_admin, 'POST', path + '/overrides', json={
        'component': 'ADJUSTMENT', 'amount': '1.00', 'reason': 'Must not change a frozen year'})
    assert response.status_code == 409, response.text
    assert world.look(client_a_admin, path).json() == w['frozen']


@when('I prepare the next WEG year and asset report')
def next_year(world, reserve_scenario):
    w = reserve_scenario
    common = {'property_id': w['property'], 'accounting_scope_id': w['scope'],
              'period_start': '2026-01-01', 'period_end': '2026-12-31'}
    plan = create(world, '/api/economic-plans', {**common, 'plan_year': 2026})
    values = {'planned_costs': [], 'reserve_contribution_total': '6000.00'}
    world.run(attest_fixture(world.actor, 'economic_plan', plan['id'], values))
    response = world.act('POST', f"/api/economic-plans/{plan['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    w['next_run'] = create(world, '/api/weg-settlements', {**common, 'economic_plan_id': plan['id']})['id']
    w['next_report'] = create(world, '/api/weg-settlements/asset-reports', common)['id']


@then('a late receipt is shown separately with its original date on both reserve pages')
def visible_late_cash(world, reserve_scenario, browser_world, request):
    from playwright.async_api import expect
    from tests.bdd.report import artifact_directory, attach_artifact
    w = reserve_scenario
    directory = artifact_directory(request.node)

    async def inspect(stage):
        page = browser_world.page
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            for prefix, path in [('weg', f"/weg-abrechnungen/{w['next_run']}"),
                                 ('asset', f"/vermoegensberichte/{w['next_report']}")]:
                await browser_world.visit(path)
                panel = page.get_by_role('region', name='Rücklagenbuch', exact=True)
                await expect(panel.get_by_text('Anfangsbestand', exact=True)).to_be_visible()
                label = panel.get_by_text('Nachträgliche Buchungen zum abgeschlossenen Jahr', exact=True)
                if stage == 'after':
                    await expect(label).to_be_visible()
                    await expect(panel.get_by_text('31.12.2025', exact=True)).to_be_visible()
                    await expect(panel.get_by_text('26.999,00', exact=False)).to_be_visible()
                    await expect(panel.locator(f'a[href="/zahlungen/{w["late_payment"]}"]')).to_be_visible()
                    await expect(panel.get_by_text('Eigentümer: Mustereigentümer mit langem Nachnamen', exact=True).first).to_be_visible()
                else:
                    await expect(label).to_have_count(0)
                reason = page.get_by_label('Begründung der Rücklagenwerte', exact=True)
                await expect(reason).to_be_visible()
                assert await reason.evaluate("(el) => !!el.closest('.form-grid') && el.clientHeight >= 60")
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                if directory:
                    # Capture the shared panel with all dated rows at each width.
                    await panel.scroll_into_view_if_needed()
                    filename = directory / f'{prefix}-{stage}-{width}.png'
                    await page.screenshot(path=str(filename), full_page=False)
                    attach_artifact(request.node, filename)
                    if stage == 'after':
                        await reason.scroll_into_view_if_needed()
                        filename = directory / f'{prefix}-form-{width}.png'
                        await page.screenshot(path=str(filename), full_page=False)
                        attach_artifact(request.node, filename)
                table = panel.locator('table')
                assert await table.evaluate('(table) => table.scrollWidth <= table.parentElement.clientWidth')
                assert await table.locator('td').evaluate_all('(cells) => cells.every(cell => cell.scrollWidth <= cell.clientWidth)')

    browser_world.run(inspect('before'))
    cash = create(world, '/api/payments', {'bank_account_id': w['bank_giro'], 'direction': 'IN',
        'amount': '999.00', 'value_date': '2025-12-31', 'payment_type': 'RUECKLAGENANTEIL',
        'owner_profile_id': w['owner_a'], 'period_year': 2025, 'period_month': 12})
    response = world.act('POST', f"/api/payments/{cash['id']}/book")
    assert response.status_code == 200, response.text
    w['late_payment'] = cash['id']
    browser_world.run(inspect('after'))

    meeting = create(world, '/api/meetings', {'property_id': w['property'], 'meeting_date': '2026-02-01',
        'meeting_type': 'ORDENTLICH'})
    response = world.act('POST', f"/api/meetings/{meeting['id']}/hold")
    assert response.status_code == 200, response.text
    resolution = create(world, '/api/meetings/resolutions', {'meeting_id': meeting['id'],
        'wording': 'Gartenreparatur aus der Erhaltungsrücklage', 'result': 'ANGENOMMEN',
        'votes_yes': 1, 'votes_no': 0, 'votes_abstain': 0})
    entry = create(world, f"/api/meetings/resolutions/{resolution['id']}/register", {})

    async def inspect_register():
        page = browser_world.page
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await browser_world.visit(f"/beschluss-sammlung?property_id={w['property']}#beschluss-{entry['id']}")
            row = page.locator(f'#beschluss-{entry["id"]}')
            await expect(row).to_contain_text('Gartenreparatur aus der Erhaltungsrücklage')
            await expect(row).to_be_in_viewport()

    browser_world.run(inspect_register())


@when('I try to book a reserve payout without its cost receipt and resolution')
def unbacked_payout(world, reserve_scenario):
    w = reserve_scenario
    draft = create(world, '/api/payments', {'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '1200.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME'})
    w['payout'] = draft['id']
    w['refusal'] = world.act('POST', f"/api/payments/{draft['id']}/book")


@then('booking is refused and the payout remains an editable draft')
def payout_refused(world, reserve_scenario):
    w = reserve_scenario
    assert w['refusal'].status_code == 422, w['refusal'].text
    assert w['refusal'].json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    assert world.look(world.actor, f"/api/payments/{w['payout']}").json()['status'] == 'draft'


@then('payment and correction forms require cost receipt and resolution on desktop and mobile')
def evidence_forms(world, reserve_scenario, browser_world, request, tmp_path):
    from playwright.async_api import expect
    from tests.bdd.report import artifact_directory, attach_artifact
    w = reserve_scenario
    category = next(row for row in world.act('GET', '/api/cost-categories').json() if row['code'] == 'GARTENPFLEGE')
    cost = create(world, '/api/cost-entries', {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'cost_category_id': category['id'], 'cost_category_plan_version': category['plan_version'],
        'document_number': 'RE-2025-Ä-123456789', 'invoice_date': '2025-02-01',
        'service_period_from': '2025-01-01', 'service_period_to': '2025-12-31',
        'gross_amount': '1200.00', 'tenant_apportionable': 'UMLAGEFAEHIG', 'source': 'MANUAL'})
    response = world.act('POST', f"/api/cost-entries/{cost['id']}/confirm")
    assert response.status_code == 200, response.text
    meeting = create(world, '/api/meetings', {'property_id': w['property'], 'meeting_date': '2025-01-15', 'meeting_type': 'ORDENTLICH'})
    assert world.act('POST', f"/api/meetings/{meeting['id']}/hold").status_code == 200
    resolution = create(world, '/api/meetings/resolutions', {'meeting_id': meeting['id'],
        'wording': 'Gartenreparatur aus der Erhaltungsrücklage mit ausführlicher Begründung',
        'result': 'ANGENOMMEN', 'votes_yes': 1, 'votes_no': 0, 'votes_abstain': 0})
    register = create(world, f"/api/meetings/resolutions/{resolution['id']}/register", {})
    statement = ('Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck;Währung\n'
        '02.02.2025;03.02.2025;-1200,00;Gartenservice;;Reparatur;EUR\n'
        '04.02.2025;05.02.2025;-100,00;Gartenservice;;Prüfung;EUR\n').encode()
    imported = world.act('POST', f"/api/bank-accounts/{w['bank_giro']}/imports", params={'dry_run': 'false'},
        files={'file': ('reserve.csv', statement, 'text/csv')})
    assert imported.status_code == 200, imported.text
    transactions = world.act('GET', '/api/bank-transactions', params={'bank_account_id': w['bank_giro']}).json()['items']
    transaction = next(row for row in transactions if row['amount'] == '-1200.00')
    assigned = world.act('POST', f"/api/bank-transactions/{transaction['id']}/assign", json={'lines': [{
        'payment_type': 'RUECKLAGENENTNAHME', 'amount': '1200.00', 'period_year': 2025, 'period_month': 2}]})
    assert assigned.status_code == 200, assigned.text
    payout = assigned.json()['payments'][0]['id']
    directory = artifact_directory(request.node) or tmp_path

    async def inspect():
        page = browser_world.page
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await browser_world.visit(f"/weg-abrechnungen/{w['run']}")
            await page.get_by_role('button', name='Korrektur erfassen', exact=True).click()
            await page.get_by_label('Art', exact=True).select_option('WITHDRAWAL')
            await expect(page.get_by_label('Bestätigter Kostenbeleg *', exact=True)).to_be_enabled()
            await expect(page.get_by_label('Beschluss-Sammlung *', exact=True)).to_have_attribute('required', '')
            await page.get_by_label('Betrag *', exact=True).fill('-1.200,00')
            await page.get_by_label('Bestätigter Kostenbeleg *', exact=True).select_option(cost['id'])
            await page.get_by_label('Beschluss-Sammlung *', exact=True).select_option(register['id'])
            await page.get_by_label('Begründung *', exact=True).fill('Belegte Entnahmekorrektur für die Gartenreparatur')
            await capture('correction', width)
            await page.get_by_role('button', name='Abbrechen', exact=True).click()
            await browser_world.visit('/zahlungen')
            await page.get_by_role('button', name='+ Neue Zahlung', exact=True).click()
            await page.get_by_label('Bankkonto *', exact=True).select_option(w['bank_giro'])
            await page.get_by_label('Zahlungstyp *', exact=True).select_option('RUECKLAGENENTNAHME')
            await expect(page.get_by_label('Bestätigter Kostenbeleg *', exact=True)).to_be_enabled()
            await page.get_by_label('Betrag *', exact=True).fill('1200.00')
            await page.get_by_role('button', name='Anlegen', exact=True).click()
            refusal = page.get_by_role('alert')
            await expect(refusal).to_contain_text('Bitte verknüpfen Sie einen bestätigten Kostenbeleg und einen Beschluss aus der Beschluss-Sammlung.')
            await refusal.scroll_into_view_if_needed()
            await capture('round13c-payout-refusal', width)
            await expect(page.get_by_label('Beschluss-Sammlung *', exact=True)).to_have_attribute('required', '')
            await page.get_by_label('Bestätigter Kostenbeleg *', exact=True).select_option(cost['id'])
            await page.get_by_label('Beschluss-Sammlung *', exact=True).select_option(register['id'])
            await page.get_by_label('Bestätigter Kostenbeleg *', exact=True).scroll_into_view_if_needed()
            await capture('payment-capture', width)
            await browser_world.visit(f'/zahlungen/{payout}')
            await expect(page.get_by_role('alert')).to_contain_text('bleibt ein Entwurf')
            await expect(page.get_by_role('button', name='Entwurf verwerfen', exact=True)).to_have_count(0)
            await page.get_by_role('button', name='Bearbeiten', exact=True).click()
            await expect(page.get_by_label('Bestätigter Kostenbeleg *', exact=True)).to_be_enabled()
            await page.get_by_label('Bestätigter Kostenbeleg *', exact=True).select_option(cost['id'])
            await page.get_by_label('Beschluss-Sammlung *', exact=True).select_option(register['id'])
            await page.get_by_label('Bestätigter Kostenbeleg *', exact=True).scroll_into_view_if_needed()
            await capture('imported-payment-edit', width)
            await page.get_by_role('button', name='Abbrechen', exact=True).click()
            await browser_world.visit(f"/bankkonten/{w['bank_giro']}/umsaetze")
            await page.get_by_role('button', name='Aufteilen', exact=True).click()
            await page.get_by_label('Zuordnen als', exact=True).select_option('payment')
            await page.get_by_label('Zahlungsart', exact=True).select_option('RUECKLAGENENTNAHME')
            await page.get_by_label('Betrag (EUR)', exact=True).fill('100,00')
            await expect(page.get_by_role('button', name='Als Entwurf übernehmen', exact=True)).to_be_enabled()
            await capture('bank-payout-draft', width)
            if width == 360:
                await page.get_by_role('button', name='Als Entwurf übernehmen', exact=True).click()
                await expect(page.get_by_role('status')).to_contain_text('Rücklagenentnahme als Entwurf übernommen')
            else:
                await page.get_by_role('button', name='Abbrechen', exact=True).click()
        await browser_world.visit(f'/zahlungen/{payout}')
        await page.get_by_role('button', name='Bearbeiten', exact=True).click()
        await expect(page.get_by_label('Bestätigter Kostenbeleg *', exact=True)).to_be_enabled()
        await page.get_by_label('Bestätigter Kostenbeleg *', exact=True).select_option(cost['id'])
        await page.get_by_label('Beschluss-Sammlung *', exact=True).select_option(register['id'])
        await page.get_by_role('button', name='Speichern', exact=True).click()
        await expect(page.get_by_role('button', name='Buchen', exact=True)).to_be_visible()
        page.once('dialog', lambda dialog: dialog.accept())
        await page.get_by_role('button', name='Buchen', exact=True).click()
        await expect(page.get_by_role('button', name='Buchen', exact=True)).to_have_count(0)
        await expect(page.get_by_role('link', name=cost['document_number'], exact=True)).to_have_attribute('href', f"/kosten/{cost['id']}")
        await expect(page.get_by_role('link', name=f"Beschluss Nr. {register['sequence_number']} · {resolution['wording']}", exact=True)).to_have_attribute(
            'href', f"/beschluss-sammlung?property_id={w['property']}#beschluss-{register['id']}")
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await page.get_by_role('heading', name='Details', exact=True).scroll_into_view_if_needed()
            await capture('round13c-payout-detail', width)
        assert not errors, errors

    async def capture(name, width):
        page = browser_world.page
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert await page.locator('vite-error-overlay').count() == 0
        assert await page.locator('[data-reserve-evidence]').evaluate_all("""groups => groups.every(group => {
          const box = group.getBoundingClientRect();
          return box.left >= 0 && box.right <= window.innerWidth && group.scrollWidth <= group.clientWidth
            && [...group.querySelectorAll('p, label, select')].every(element => {
              const rect = element.getBoundingClientRect();
              return rect.left >= box.left && rect.right <= box.right;
            });
        })""")
        if directory:
            filename = directory / f'{name}-{width}.png'
            await page.screenshot(path=str(filename), full_page=False, animations='disabled')
            attach_artifact(request.node, filename)

    browser_world.run(inspect())


@given('a WEG with partially assigned Hausgeld and a finalized reserve plan', target_fixture='reserve_scenario')
def partially_assigned_hausgeld(world, seed_data):
    from tests.test_reserve_ledger import payment
    w = reserve_correction_property(world, seed_data)
    w['cash'] = world.run(payment(world.actor, w, '1300.00', kind='HAUSGELD_VORSCHUSS'))
    basis = {'property_id': w['property'], 'accounting_scope_id': w['scope'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'}
    report = create(world, '/api/weg-settlements/asset-reports', basis)
    w['run'] = report['id']
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'CONTRIBUTION', 'payment_id': w['cash'], 'amount': '100.00', 'reason': 'Partial cash assignment'})
    assert response.status_code == 201, response.text
    w['plan'] = reserve_correction_plan(world, w)
    response = world.act('PUT', f"/api/units/{w['u1']}", json={'number': 'Wohnung 1 · Vorderhaus Südflügel · Aufgang Änne'})
    assert response.status_code == 200, response.text
    run = create(world, '/api/weg-settlements', {**basis, 'economic_plan_id': w['plan']})
    w['weg_run'] = run['id']
    return w


@pytest.mark.parametrize('setup', [partially_assigned_hausgeld, stray_reserve_draft])
def test_reserve_scenario_setup_never_connects_to_a_local_database(world, seed_data, monkeypatch, setup):
    def forbidden(*args, **kwargs):
        pytest.fail('Reserve scenario setup must use the configured HTTP target, not a direct database connection')

    with monkeypatch.context() as guard:
        guard.setattr('psycopg2.connect', forbidden)
        w = setup(world, seed_data)
    assert world.act('GET', f"/api/properties/{w['property']}").status_code == 200


@when('I correct the unit contribution by 300 euros')
def unit_contribution(world, reserve_scenario):
    w = reserve_scenario
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': '300.00', 'reason': 'Reconciled unit cash'})
    assert response.status_code == 201, response.text
    w['correction'] = response.json()['id']


@then('only 200 euros remain assignable and a further 300 euros is refused')
def unit_contribution_cap(world, reserve_scenario):
    w = reserve_scenario
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    ledger = world.look(world.actor, path).json()
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '200.00'
    refused = world.act('POST', path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '300.00', 'reason': 'Would exceed remaining cash'})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'


@when('I reduce the correction to 100 euros and restore it with a reason')
def restore_unit_contribution(world, reserve_scenario):
    w = reserve_scenario
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides/{w['correction']}/reduce"
    for amount, reason in [('100.00', 'Wrong correction selected'), ('300.00', 'Versehentlich verringert')]:
        response = world.act('POST', path, json={'reduced_to': amount, 'reason': reason})
        assert response.status_code == 201, response.text


@then('the correction is 300 euros and both counter-entries remain visible')
def restored_unit_contribution(world, reserve_scenario):
    w = reserve_scenario
    ledger = world.look(world.actor, f"/api/weg-settlements/{w['run']}/reserve-ledger").json()
    assert next(row for row in ledger['entries'] if row.get('id') == w['correction'])['current_amount'] == '300.00'
    assert [row['amount'] for row in ledger['entries'] if row.get('reverses_entry_id') == w['correction']] == ['-200.00', '200.00']


@then('I can restore an accidental reduction and see when nothing remains to delete on both reserve pages')
def restore_contribution_forms(world, reserve_scenario, browser_world, tmp_path):
    import os
    from playwright.async_api import expect
    w = reserve_scenario
    directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/reserve-correction-restoration' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path

    async def inspect():
        page = browser_world.page
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        for kind, route in [('weg', f"/weg-abrechnungen/{w['weg_run']}"), ('asset', f"/vermoegensberichte/{w['run']}")]:
            for width in (1280, 360):
                await page.set_viewport_size({'width': width, 'height': 800})
                await browser_world.visit(route)
                panel = page.get_by_role('region', name='Rücklagenbuch')
                change = panel.get_by_role('button', name='Korrektur ändern / löschen', exact=True)
                dialog = page.get_by_role('dialog')

                async def open_correction(amount, reason):
                    await change.click()
                    await dialog.get_by_label('Verbleibender Korrekturbetrag *', exact=True).fill(amount)
                    await dialog.get_by_role('textbox', name='Begründung *', exact=True).fill(reason)

                await open_correction('100,00', 'Falsche Korrektur gewählt')
                await dialog.get_by_role('button', name='Korrektur speichern', exact=True).click()
                await expect(dialog).not_to_be_visible()
                await expect(panel.get_by_text('Verbleibende Korrektur:', exact=False)).to_contain_text('100,00')
                await open_correction('400,00', 'Versehentlich verringert')
                await dialog.get_by_role('button', name='Korrektur speichern', exact=True).click()
                await expect(dialog.get_by_role('alert')).to_have_text('Sie können für diese Korrektur höchstens 300,00 € beibehalten.')
                await expect(dialog.get_by_label('Verbleibender Korrekturbetrag *', exact=True)).to_have_value('400,00')
                await expect(dialog.get_by_role('textbox', name='Begründung *', exact=True)).to_have_value('Versehentlich verringert')
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert await dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth')
                await expect(dialog.get_by_role('button', name='Korrektur speichern', exact=True)).to_be_in_viewport()
                await expect(dialog.get_by_role('button', name='Korrektur löschen', exact=True)).to_be_in_viewport()
                await page.screenshot(path=str(directory / f'{kind}-after-{width}.png'), animations='disabled')
                await dialog.get_by_label('Verbleibender Korrekturbetrag *', exact=True).fill('300,00')
                await dialog.get_by_role('button', name='Korrektur speichern', exact=True).click()
                await expect(dialog).not_to_be_visible()
                await expect(panel.get_by_text('Verbleibende Korrektur:', exact=False)).to_contain_text('300,00')
                await expect(panel.get_by_text('Wiederherstellung einer Zuführungskorrektur', exact=True).last).to_be_visible()
                await open_correction('0,00', 'Korrektur vollständig aufgehoben')
                await dialog.get_by_role('button', name='Korrektur löschen', exact=True).click()
                await expect(dialog).not_to_be_visible()
                await open_correction('0,00', 'Erneut aufheben')
                await dialog.get_by_role('button', name='Korrektur löschen', exact=True).click()
                await expect(dialog.get_by_role('alert')).to_have_text('Diese Korrektur ist bereits vollständig aufgehoben. Es ist kein Restbetrag mehr vorhanden.')
                await page.screenshot(path=str(directory / f'{kind}-zero-{width}.png'), animations='disabled')
                # The same rule permits restoration from zero, ready for the next viewport.
                await dialog.get_by_label('Verbleibender Korrekturbetrag *', exact=True).fill('300,00')
                await dialog.get_by_role('textbox', name='Begründung *', exact=True).fill('Versehentlich verringert')
                await dialog.get_by_role('button', name='Korrektur speichern', exact=True).click()
                await expect(dialog).not_to_be_visible()
        assert not errors, errors

    browser_world.run(inspect())


@when('I attest a derived contribution or withdrawal total')
def derived_attestation(world, reserve_scenario):
    reserve_scenario['refusals'] = [world.act('POST', '/api/financial-input-coverage', json={
        'subject_kind': 'settlement', 'subject_id': reserve_scenario['run'], 'confirmed_complete': True,
        'reason': 'Reviewed synthetic totals', 'values': {field: '1000.00'}})
        for field in ('reserve_contributions_total', 'reserve_withdrawals_total')]


@then('each attestation is refused with the derived-total read-only code')
def derived_attestation_refused(reserve_scenario):
    for response in reserve_scenario['refusals']:
        assert response.status_code == 422, response.text
        assert response.json()['detail']['code'] == 'RESERVE_DERIVED_TOTAL_READ_ONLY'


@then('the unit correction shows remaining headroom and preserves inputs after refusal')
def unit_correction_form(world, reserve_scenario, browser_world, tmp_path):
    import os
    from playwright.async_api import expect
    w = reserve_scenario
    directory = Path(__file__).resolve().parents[3] / 'docs/assets/reserve-ledger' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path

    async def inspect():
        page = browser_world.page
        for kind, route, opening in [('weg', f"/weg-abrechnungen/{w['weg_run']}", 'Rücklagen-Anfangsbestand'),
                                     ('asset', f"/vermoegensberichte/{w['run']}", 'Rücklage Anfangsbestand')]:
            await browser_world.visit(route)
            await expect(page.get_by_label(opening, exact=True)).to_be_visible()
            for label in ('Rücklagen-Entnahmen (Jahr)', 'Rücklage Zuführung', 'Rücklage Entnahmen'):
                await expect(page.get_by_label(label, exact=True)).to_have_count(0)
            for width in (1280, 360):
                await page.set_viewport_size({'width': width, 'height': 1000})
                await browser_world.visit(route)
                await expect(page.get_by_text('Aktueller Anfangsbestand:', exact=False)).not_to_contain_text('Wird geladen')
                await page.get_by_label(opening, exact=True).scroll_into_view_if_needed()
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                await page.screenshot(path=str(directory / f'round13b-{kind}-after-{width}.png'))
        await page.get_by_role('button', name='Korrektur erfassen', exact=True).click()
        await page.get_by_label('Art', exact=True).select_option('CONTRIBUTION')
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            await page.screenshot(path=str(directory / f'round13b-contribution-after-{width}.png'))
        unit = page.get_by_role('combobox', name='Einheit *', exact=True)
        await expect(unit).to_be_visible()
        await unit.select_option(w['u1'])
        await expect(page.get_by_text('Noch zuordenbar:', exact=False)).to_contain_text('500,00')
        await expect(page.get_by_text('Allgemeine Zuführungskorrektur', exact=True)).to_have_count(0)
        await page.get_by_label('Betrag *', exact=True).fill('500,01')
        await page.get_by_label('Begründung *', exact=True).fill('Gebuchtes Hausgeld der Einheit anhand der Bankunterlagen geprüft')
        await page.get_by_role('button', name='Speichern', exact=True).click()
        await expect(page.get_by_role('alert').last).to_have_text('Sie können dieser Einheit noch höchstens 500,00 € als Zuführung zuordnen.')
        await expect(page.get_by_label('Betrag *', exact=True)).to_have_value('500,01')
        for width in (1280, 360):
            await page.set_viewport_size({'width': width, 'height': 1000})
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.screenshot(path=str(directory / f'round13b-contribution-after-{width}.png'))
        await page.get_by_label('Betrag *', exact=True).fill('300,00')
        await page.get_by_role('button', name='Speichern', exact=True).click()
        await expect(page.get_by_role('dialog')).to_have_count(0)
        await expect(page.get_by_text('Einheit: Wohnung 1 · Vorderhaus Südflügel · Aufgang Änne', exact=True).last).to_be_visible()
        await page.get_by_role('button', name='Korrektur erfassen', exact=True).click()
        await expect(page.get_by_role('combobox', name='Einheit *', exact=True)).to_be_visible()
        await page.get_by_role('combobox', name='Einheit *', exact=True).select_option(w['u1'])
        await expect(page.get_by_text('Noch zuordenbar:', exact=False)).to_contain_text('200,00')
    browser_world.run(inspect())


@when('the Hausgeld is reversed and I delete the contribution correction with a reason')
def delete_unbacked_contribution(world, reserve_scenario):
    w = reserve_scenario
    assert world.act('POST', f"/api/payments/{w['cash']}/reverse").status_code == 201
    response = world.act('POST', f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides/{w['correction']}/delete",
        json={'reason': 'Fehlbuchung'})
    assert response.status_code == 201, response.text
    w['reversal'] = response.json()['id']


@then('the retained reversal restores the reserve balance and the period can be finalized')
def contribution_release_finalizes(world, reserve_scenario):
    w = reserve_scenario
    ledger = world.act('GET', f"/api/weg-settlements/{w['run']}/reserve-ledger").json()
    assert ledger['contributions_total'] == '0.00'
    row = next(row for row in ledger['entries'] if row.get('id') == w['reversal'])
    assert row['amount'] == '-300.00' and row['reverses_entry_id'] == w['correction']
    assert row['reason'] == 'Fehlbuchung'
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '0.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'}],
        'receivables': [], 'payables': []}
    world.run(attest_fixture(world.actor, 'settlement', w['run'], values))
    response = world.act('POST', f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert response.status_code == 200, response.text


@then('reducing and deleting the contribution correction remains usable on both reserve pages')
def contribution_reduction_forms(world, reserve_scenario, browser_world, tmp_path):
    import os
    from playwright.async_api import expect
    w = reserve_scenario
    directory = Path(__file__).resolve().parents[3] / 'docs/assets/reserve-ledger' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    routes = [('weg', f"/weg-abrechnungen/{w['weg_run']}"), ('asset', f"/vermoegensberichte/{w['run']}")]

    async def inspect():
        page = browser_world.page
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        if os.environ.get('VERWALINO_RESERVE_CAPTURE_BEFORE') == '1':
            for kind, route in routes:
                for width in (1280, 360):
                    await page.set_viewport_size({'width': width, 'height': 800})
                    await browser_world.visit(route)
                    panel = page.get_by_role('region', name='Rücklagenbuch')
                    await expect(panel.get_by_text('Einheit: Wohnung 1', exact=False).last).to_be_visible()
                    await panel.scroll_into_view_if_needed()
                    await page.screenshot(path=str(directory / f'round18-{kind}-contribution-before-{width}.png'), animations='disabled')
        for kind, route in routes:
            for width in (1280, 360):
                await page.set_viewport_size({'width': width, 'height': 800})
                await browser_world.visit(route)
                panel = page.get_by_role('region', name='Rücklagenbuch')
                await panel.get_by_role('button', name='Korrektur ändern / löschen', exact=True).click()
                dialog = page.get_by_role('dialog')
                await expect(dialog).to_have_accessible_name('Korrektur ändern / löschen')
                await dialog.get_by_role('button', name='Korrektur speichern', exact=True).click()
                await expect(dialog.get_by_role('alert')).to_have_text('Bitte begründen Sie die Rücklagenkorrektur.')
                await page.screenshot(path=str(directory / f'round18-{kind}-contribution-after-{width}.png'), animations='disabled')
                await expect(dialog.get_by_role('button', name='Korrektur löschen', exact=True)).to_be_in_viewport()
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert await dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth')
                await dialog.get_by_label('Begründung *', exact=True).fill('Hausgeld und verbleibende Korrektur abgeglichen')
                await dialog.get_by_label('Verbleibender Korrekturbetrag *', exact=True).fill('200,00' if width == 1280 else '100,00')
                # Exercise one reduction per page; other visits check the same remaining original.
                if kind == 'weg':
                    await dialog.get_by_role('button', name='Korrektur speichern', exact=True).click()
                    await expect(dialog).not_to_be_visible()
                    await expect(panel.get_by_text('Verbleibende Korrektur:', exact=False)).to_contain_text('200,00' if width == 1280 else '100,00')
                else:
                    await dialog.get_by_role('button', name='Abbrechen', exact=True).click()
        await panel.get_by_role('button', name='Korrektur ändern / löschen', exact=True).click()
        dialog = page.get_by_role('dialog')
        await dialog.get_by_label('Begründung *', exact=True).fill('Verbleibende Korrektur vollständig aufgehoben')
        await dialog.get_by_role('button', name='Korrektur löschen', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await expect(panel.get_by_role('button', name='Korrektur ändern / löschen', exact=True)).to_have_count(1)
        await expect(panel.get_by_text('Verbleibende Korrektur:', exact=False)).to_contain_text('0,00')
        assert not errors, errors
    browser_world.run(inspect())


@then('both correction dialogs show the personal data hint on both reserve pages')
def correction_reason_privacy_hint(world, reserve_scenario, browser_world, tmp_path):
    import os
    from playwright.async_api import expect
    w = reserve_scenario
    directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/reserve-reason-hint' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    phase = 'before' if os.environ.get('VERWALINO_RESERVE_CAPTURE_BEFORE') == '1' else 'after'
    hint_text = 'Bitte keine personenbezogenen Daten eintragen.'

    async def inspect():
        page = browser_world.page
        errors, missing = [], []
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        for kind, route in [('weg', f"/weg-abrechnungen/{w['weg_run']}"), ('asset', f"/vermoegensberichte/{w['run']}")]:
            for width in (1280, 360):
                await page.set_viewport_size({'width': width, 'height': 800})
                await browser_world.visit(route)
                if width == 360:
                    await page.wait_for_function("() => { const aside = document.querySelector('aside'); return !aside || aside.getBoundingClientRect().right <= 0; }")
                assert page.url.endswith(route) and await page.title()
                panel = page.get_by_role('region', name='Rücklagenbuch')
                await expect(panel.get_by_text('Verbleibende Korrektur:', exact=False)).to_contain_text('300,00')
                for action, name in [('Korrektur erfassen', 'create'), ('Korrektur ändern / löschen', 'reduce')]:
                    await panel.get_by_role('button', name=action, exact=True).click()
                    dialog = page.get_by_role('dialog')
                    if name == 'create':
                        await dialog.get_by_label('Art', exact=True).select_option('CONTRIBUTION')
                    reason = dialog.get_by_role('textbox', name='Begründung *', exact=True)
                    await reason.scroll_into_view_if_needed()
                    await reason.fill('Hausgeld anhand der Bankunterlagen abgeglichen')
                    hint = dialog.get_by_text(hint_text, exact=True)
                    if await hint.count():
                        await hint.scroll_into_view_if_needed()
                        await expect(hint).to_be_in_viewport()
                        await expect(reason).to_have_accessible_description(hint_text)
                    else:
                        missing.append((kind, width, action))
                    await expect(dialog.get_by_role('button', name='Abbrechen', exact=True)).to_be_in_viewport()
                    assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    assert await page.locator('vite-error-overlay').count() == 0
                    await page.screenshot(path=str(directory / f'{kind}-{name}-{phase}-{width}.png'), animations='disabled')
                    await dialog.get_by_role('button', name='Abbrechen', exact=True).click()
        assert not errors, errors
        assert not missing, f'Missing personal data hint: {missing}'
    browser_world.run(inspect())


@when('the backing Hausgeld is reversed')
def reverse_correction_backing(world, reserve_scenario):
    response = world.act('POST', f"/api/payments/{reserve_scenario['cash']}/reverse")
    assert response.status_code == 201, response.text


@then('keeping 100 euros directs me to delete the correction on desktop and mobile')
def unbacked_correction_action(world, reserve_scenario, browser_world, tmp_path):
    import os
    from playwright.async_api import expect
    w = reserve_scenario
    directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/reserve-correction-deletion' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    before = world.act('GET', path).json()
    message = 'Für diese Korrektur ist kein positiver Restbetrag mehr möglich. Bitte verwenden Sie „Korrektur löschen“.'

    async def inspect():
        page = browser_world.page
        errors, messages = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        for kind, route in [('weg', f"/weg-abrechnungen/{w['weg_run']}"), ('asset', f"/vermoegensberichte/{w['run']}")]:
            for width in (1280, 360):
                await page.set_viewport_size({'width': width, 'height': 800})
                await browser_world.visit(route)
                panel = page.get_by_role('region', name='Rücklagenbuch')
                await panel.get_by_role('button', name='Korrektur ändern / löschen', exact=True).click()
                dialog = page.get_by_role('dialog')
                amount = dialog.get_by_label('Verbleibender Korrekturbetrag *', exact=True)
                reason = dialog.get_by_role('textbox', name='Begründung *', exact=True)
                await amount.fill('100,00')
                await reason.fill('Nach Hausgeld-Storno verbleibende Korrektur abstimmen')
                async with page.expect_response(lambda response: response.url.endswith('/reduce')) as pending:
                    await dialog.get_by_role('button', name='Korrektur speichern', exact=True).click()
                response = await pending.value
                assert response.status == 422, await response.text()
                assert (await response.json())['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
                alert = dialog.get_by_role('alert')
                await expect(alert).to_be_visible()
                messages.append(await alert.inner_text())
                await expect(amount).to_have_value('100,00')
                await expect(reason).to_have_value('Nach Hausgeld-Storno verbleibende Korrektur abstimmen')
                await expect(dialog.get_by_role('button', name='Korrektur löschen', exact=True)).to_be_in_viewport()
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert await dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth')
                await page.screenshot(path=str(directory / f'{kind}-after-{width}.png'), animations='disabled')
                await dialog.get_by_role('button', name='Abbrechen', exact=True).click()
        assert not errors, errors
        assert messages == [message] * 4
        await panel.get_by_role('button', name='Korrektur ändern / löschen', exact=True).click()
        dialog = page.get_by_role('dialog')
        await dialog.get_by_role('textbox', name='Begründung *', exact=True).fill('Nicht mehr gedeckte Korrektur vollständig aufheben')
        await dialog.get_by_role('button', name='Korrektur löschen', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await expect(panel.get_by_role('button', name='Korrektur ändern / löschen', exact=True)).to_have_count(1)
        await expect(panel.get_by_text('Verbleibende Korrektur:', exact=False)).to_contain_text('0,00')

    browser_world.run(inspect())
    ledger = world.act('GET', path).json()
    original = next(row for row in ledger['entries'] if row.get('id') == w['correction'])
    assert original['amount'] == '300.00' and original['current_amount'] == '0.00'
    assert len(ledger['entries']) == len(before['entries']) + 1
    reversal = next(row for row in ledger['entries'] if row.get('reverses_entry_id') == w['correction'])
    assert reversal['amount'] == '-300.00'
