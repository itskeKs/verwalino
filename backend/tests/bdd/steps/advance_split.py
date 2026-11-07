from decimal import Decimal
import subprocess

from pytest_bdd import given, then, when

from tests.financial_coverage_helpers import attest_fixture
from tests.settlement_period_support import create, period_property, statement
from tests.bdd.steps.settlement_period import confirmed_period_cost


@given('a lease with 750 euros rent, 150 euros operating and 90 euros heating advance')
def split_lease(world):
    data = world.run(period_property(world.actor, month=1))
    response = world.act('PATCH', f"/api/leases/{data['lease']['id']}", json={
        'base_rent': '750.00', 'advance_payment': '150.00', 'heating_advance_payment': '90.00',
        'advance_mode': 'SEPARATE'})
    assert response.status_code == 200, world.describe_response()
    assert response.json()['advance_mode'] == 'SEPARATE'
    assert response.json()['heating_advance_payment'] == '90.00'
    data['lease'] = response.json()
    world.data['advance_split'] = data


@then('January has three expected charges totalling 990 euros')
def split_charges(world):
    data = world.data['advance_split']
    response = world.act('GET', f"/api/payments/mietkonto/lease/{data['lease']['id']}",
        params={'period_start': '2026-01-01', 'period_end': '2026-01-31'})
    assert response.status_code == 200, world.describe_response()
    assert {r['payment_type']: Decimal(r['soll']) for r in response.json()['open_items']} == {
        'KALTMIETE': Decimal('750'), 'NK_VORAUSZAHLUNG': Decimal('150'),
        'HK_VORAUSZAHLUNG': Decimal('90')}
    assert Decimal(response.json()['soll_total']) == Decimal('990')


@when('the manager derives missing heating advances and finalizes a statement with 1900 euros operating and 1000 euros heating costs')
def split_statement(world):
    data = world.data['advance_split']
    for code, amount in [('GRUNDSTEUER', '1900.00'), ('MESSDIENST', '1000.00')]:
        confirmed_period_cost(world, data, code, amount, '2026-01-01', '2026-12-31', 'WOHNFLAECHE')
    for kind, amount in [('NK_VORAUSZAHLUNG', '1800.00'), ('HK_VORAUSZAHLUNG', '1080.00')]:
        payment = world.run(create(world.actor, '/api/payments', {
            'bank_account_id': data['account']['id'], 'direction': 'IN', 'amount': amount,
            'value_date': '2026-12-31', 'payment_type': kind, 'period_year': 2026,
            'tenant_profile_id': data['tenant']['id'], 'lease_agreement_id': data['lease']['id']}))
        response = world.act('POST', f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, world.describe_response()
    run = world.run(create(world.actor, '/api/settlements', {
        **statement(data, '2026-01-01', '2026-12-31'), 'advance_payments_total': '1800.00'}))
    url = f"/api/settlements/{run['id']}"
    world.act('POST', '/api/financial-input-coverage', json={
        'subject_kind': 'settlement', 'subject_id': run['id'], 'confirmed_complete': True,
        'reason': 'Synthetic complete annual records reviewed', 'source_document_ids': [],
        'values': {'advance_payments_total': '1800.00', 'advance_payments_source': 'MANUAL',
            'heating_advance_payments_total': run['heating_advance_payments_total'],
            'heating_advance_payments_source': 'MANUAL'}})
    refused = world.act('POST', url + '/finalize', json={})
    assert refused.status_code == 422, world.describe_response()
    assert refused.json()['detail']['code'] == 'financial_inputs_incomplete'
    response = world.act('PATCH', url + '/advance-payments-derived', params={'period_year': 2026})
    assert response.status_code == 200, world.describe_response()
    assert response.json()['heating_advance_payments_total'] == '1080.00'
    world.run(attest_fixture(world.actor, 'settlement', run['id'], {
        'advance_payments_total': '1800.00', 'advance_payments_source': 'DERIVED',
        'heating_advance_payments_total': '1080.00', 'heating_advance_payments_source': 'DERIVED'}))
    response = world.act('POST', url + '/finalize', json={})
    assert response.status_code == 200, world.describe_response()
    world.data['split_run'] = response.json()


@then('the operating balance is 100 euros, the heating balance is minus 80 euros and the total is 20 euros')
def split_results(world):
    run = world.data['split_run']
    lines = {r['label']: Decimal(r['amount']) for r in run['lines']}
    assert lines['Betriebskosten abzüglich Vorauszahlungen'] == Decimal('100')
    assert lines['Heizkosten abzüglich Vorauszahlungen'] == Decimal('-80')
    assert Decimal(run['balance']) == Decimal('20')


@then('the PDF contains both component results')
def split_pdf(world):
    response = world.act('GET', f"/api/settlements/{world.data['split_run']['id']}/pdf")
    assert response.status_code == 200, world.describe_response()
    content = subprocess.run(['pdftotext', '-', '-'], input=response.content, capture_output=True, check=True).stdout.decode()
    assert 'Betriebskosten abzüglich Vorauszahlungen' in content
    assert 'Heizkosten abzüglich Vorauszahlungen' in content
    assert '1.080,00' in content and '-80,00' in content and '20,00' in content


@when('the manager accepts both proposed advances from January 2027')
def accept_split_proposal(world):
    url = f"/api/settlements/{world.data['split_run']['id']}/advance-proposal"
    response = world.act('GET', url)
    assert response.status_code == 200, world.describe_response()
    assert response.json()['monthly_amount'] == '158.33'
    assert response.json()['heating_monthly_amount'] == '83.33'
    response = world.act('POST', url, json={'valid_from': '2027-01-01'})
    assert response.status_code == 201, world.describe_response()


@then('one rent change carries 158.33 euros operating and 83.33 euros heating advance')
def split_history(world):
    response = world.act('GET', f"/api/leases/{world.data['advance_split']['lease']['id']}/rent-changes")
    assert response.status_code == 200, world.describe_response()
    row, = response.json()
    assert (row['base_rent'], row['advance_payment'], row['heating_advance_payment']) == ('750.00', '158.33', '83.33')


@when("another organization requests the lease's advance history and statement actions")
def foreign_advances(world):
    data = world.data['advance_split']
    run = world.run(create(world.actor, '/api/settlements', statement(data, '2026-01-01', '2026-12-31')))
    world.actor = world.administrator_of('Verwaltung Beta')
    lease_url, run_url = f"/api/leases/{data['lease']['id']}", f"/api/settlements/{run['id']}"
    requests = [
        ('GET', f"/api/payments/mietkonto/lease/{data['lease']['id']}", {'params': {
            'period_start': '2026-01-01', 'period_end': '2026-12-31', 'as_of': '2026-11-01'}}),
        ('GET', lease_url + '/rent-changes', {}),
        ('PATCH', lease_url, {'json': {'advance_mode': 'SEPARATE', 'heating_advance_payment': '999.00'}}),
        ('POST', lease_url + '/rent-changes', {'json': {'valid_from': '2026-04-01', 'base_rent': '750.00',
            'advance_mode': 'SEPARATE', 'advance_payment': '150.00', 'heating_advance_payment': '999.00'}}),
        ('GET', run_url, {}),
        ('PATCH', run_url + '/advance-payments', {'json': {'heating_advance_payments_total': '999.00'}}),
        ('PATCH', run_url + '/advance-payments-derived', {'params': {'period_year': 2026}}),
        ('GET', run_url + '/advance-proposal', {}),
        ('POST', run_url + '/advance-proposal', {'json': {'valid_from': '2027-01-01'}}),
    ]
    world.data['foreign_advance_responses'] = [world.act(method, path, **kwargs) for method, path, kwargs in requests]


@then('all separate advance requests are reported as not found')
def no_foreign_advances(world):
    assert [response.status_code for response in world.data['foreign_advance_responses']] == [404] * 9


@when("the manager finalizes Bernd's statement without heating costs or heating advances")
def single_statement(world):
    data = world.run(period_property(world.actor, month=1))
    response = world.act('PATCH', f"/api/leases/{data['lease']['id']}", json={
        'base_rent': '600.00', 'advance_payment': '120.00', 'heating_advance_payment': '0.00'})
    assert response.status_code == 200, world.describe_response()
    confirmed_period_cost(world, data, 'GRUNDSTEUER', '970.00', '2026-01-01', '2026-12-31', 'WOHNFLAECHE')
    run = world.run(create(world.actor, '/api/settlements', {
        **statement(data, '2026-01-01', '2026-12-31'),
        'advance_payments_total': '1440.00', 'heating_advance_payments_total': '0.00'}))
    world.run(attest_fixture(world.actor, 'settlement', run['id'], {
        'advance_payments_total': '1440.00', 'advance_payments_source': 'MANUAL',
        'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL'}))
    response = world.act('POST', f"/api/settlements/{run['id']}/finalize", json={})
    assert response.status_code == 200, world.describe_response()
    world.data['single_run'] = response.json()


@then('the statement and PDF show one block of paid advances and a credit of 470 euros')
def single_results(world):
    run = world.data['single_run']
    assert run['balance'] == '-470.00'
    advances = [line for line in run['lines'] if line['line_type'] == 'ADVANCE_PAYMENT']
    assert [(line['label'], line['amount']) for line in advances] == [('Geleistete Vorauszahlungen', '1440.00')]
    assert not any(line['line_type'] == 'SUBTOTAL' and line['note'] for line in run['lines'])
    response = world.act('GET', f"/api/settlements/{run['id']}/pdf")
    assert response.status_code == 200, world.describe_response()
    content = subprocess.run(['pdftotext', '-', '-'], input=response.content, capture_output=True, check=True).stdout.decode()
    assert 'Geleistete Vorauszahlungen' in content
    assert 'Heizkosten-Vorauszahlungen' not in content
    assert '-470,00' in content


@given('a lease with 750 euros rent and 240 euros combined advance')
def combined_lease(world):
    data = world.run(period_property(world.actor, month=1))
    response = world.act('PATCH', f"/api/leases/{data['lease']['id']}", json={
        'base_rent': '750.00', 'advance_payment': '240.00', 'advance_mode': 'COMBINED'})
    assert response.status_code == 200, world.describe_response()
    assert response.json().get('advance_mode') == 'COMBINED'
    data['lease'] = response.json()
    world.data['advance_split'] = data


def april_account(world, as_of):
    data = world.data['advance_split']
    response = world.act('GET', f"/api/payments/mietkonto/lease/{data['lease']['id']}", params={
        'period_start': '2026-04-01', 'period_end': '2026-04-30', 'as_of': as_of})
    assert response.status_code == 200, world.describe_response()
    return response.json()


@then('April has two expected charges totalling 990 euros')
def combined_charges(world):
    result = april_account(world, '2026-04-07')
    assert {r['payment_type']: Decimal(r['soll']) for r in result['open_items']} == {
        'KALTMIETE': Decimal('750'), 'NK_VORAUSZAHLUNG': Decimal('240')}
    assert Decimal(result['soll_total']) == Decimal('990')


@then('all April rent and advances are due on 7 April 2026')
def april_due(world):
    before = april_account(world, '2026-04-06')
    due = april_account(world, '2026-04-07')
    assert Decimal(before['due_balance']) == 0
    assert Decimal(due['due_balance']) == 990


def book_monthly_advances(world, months, amounts):
    data = world.data['advance_split']
    booked = []
    for month in months:
        for kind, amount in amounts:
            payment = world.run(create(world.actor, '/api/payments', {
                'bank_account_id': data['account']['id'], 'direction': 'IN', 'amount': amount,
                'value_date': f'2026-{month:02d}-07', 'payment_type': kind,
                'period_year': 2026, 'period_month': month,
                'tenant_profile_id': data['tenant']['id'], 'lease_agreement_id': data['lease']['id']}))
            response = world.act('POST', f"/api/payments/{payment['id']}/book")
            assert response.status_code == 200, world.describe_response()
            booked.append(response.json())
    return booked


@when('the manager books the first six combined advances')
def first_combined_bookings(world):
    world.data['original_advance_bookings'] = book_monthly_advances(
        world, range(1, 7), [('NK_VORAUSZAHLUNG', '240.00')])


@when('changes to 150 euros operating and 90 euros heating from July')
@when('the manager changes to 150 euros operating and 90 euros heating from July')
def change_to_separate(world):
    data = world.data['advance_split']
    url = f"/api/leases/{data['lease']['id']}/rent-changes"
    response = world.act('POST', url, json={
        'valid_from': '2026-07-01', 'advance_mode': 'SEPARATE',
        'base_rent': '750.00', 'advance_payment': '150.00', 'heating_advance_payment': '90.00'})
    assert response.status_code == 201, world.describe_response()
    assert response.json()['advance_mode'] == 'SEPARATE'


@when('the manager finalizes the annual statement with 1900 euros operating and 1000 euros heating costs')
def combined_or_mixed_statement(world):
    mixed = 'original_advance_bookings' in world.data
    if mixed:
        book_monthly_advances(world, range(7, 13), [
            ('NK_VORAUSZAHLUNG', '150.00'), ('HK_VORAUSZAHLUNG', '90.00')])
    else:
        book_monthly_advances(world, range(1, 13), [('NK_VORAUSZAHLUNG', '240.00')])
    finalize_annual_advance_statement(world, '2340.00' if mixed else '2880.00', '540.00' if mixed else '0.00')


@when('books one 2340 euro advance for 2026 without a month')
def book_annual_advance(world):
    data = world.data['advance_split']
    payment = world.run(create(world.actor, '/api/payments', {
        'bank_account_id': data['account']['id'], 'direction': 'IN', 'amount': '2340.00',
        'value_date': '2026-12-07', 'payment_type': 'NK_VORAUSZAHLUNG', 'period_year': 2026,
        'tenant_profile_id': data['tenant']['id'], 'lease_agreement_id': data['lease']['id']}))
    response = world.act('POST', f"/api/payments/{payment['id']}/book")
    assert response.status_code == 200, world.describe_response()
    assert response.json()['period_month'] is None


@when('calculates the annual draft with 1900 euros operating and 1000 euros heating costs and zero advances')
def calculate_annual_draft_without_advances(world):
    run = annual_advance_draft(world, advance_payments_total='0.00', heating_advance_payments_total='0.00')
    world.data['annual_draft'] = run['id']
    attest_annual_advances(world, run['id'], '0.00', '0.00', 'MANUAL')
    response = world.act('POST', f"/api/settlements/{run['id']}/compute", json={})
    assert response.status_code == 200, world.describe_response()
    computed = response.json()
    assert Decimal(computed['balance']) == Decimal('2900')
    assert computed['lines']


@when('derives the advances from bookings and reopens the draft')
def derive_and_reopen_annual_draft(world):
    url = f"/api/settlements/{world.data['annual_draft']}"
    response = world.act('PATCH', url + '/advance-payments-derived', params={'period_year': 2026})
    assert response.status_code == 200, world.describe_response()
    response = world.act('GET', url)
    assert response.status_code == 200, world.describe_response()


@then('the reloaded draft has 2340 euros in advances and no calculated balance or lines')
def annual_draft_has_no_stale_result(world):
    draft = world.response.json()
    assert Decimal(draft['advance_payments_total']) == Decimal('2340')
    assert draft['advance_payments_source'] == 'DERIVED'
    assert draft['balance'] is None
    assert draft['total_cost'] is None
    assert draft['lines'] == []


@when('the manager confirms the new advance basis and calculates and finalizes the draft')
def recalculate_and_finalize_annual_draft(world):
    run_id = world.data['annual_draft']
    url = f'/api/settlements/{run_id}'
    attest_annual_advances(world, run_id, '2340.00', '0.00', 'DERIVED')
    response = world.act('POST', url + '/compute', json={})
    assert response.status_code == 200, world.describe_response()
    assert Decimal(response.json()['balance']) == Decimal('560')
    response = world.act('POST', url + '/finalize', json={})
    assert response.status_code == 200, world.describe_response()
    world.data['combined_run'] = response.json()


def annual_advance_draft(world, **advances):
    data = world.data['advance_split']
    for code, amount in [('GRUNDSTEUER', '1900.00'), ('MESSDIENST', '1000.00')]:
        confirmed_period_cost(world, data, code, amount, '2026-01-01', '2026-12-31', 'WOHNFLAECHE')
    return world.run(create(world.actor, '/api/settlements', {
        **statement(data, '2026-01-01', '2026-12-31'), **advances}))


def attest_annual_advances(world, run_id, advance_total, heating_total, source):
    world.run(attest_fixture(world.actor, 'settlement', run_id, {
        'advance_payments_total': advance_total, 'advance_payments_source': source,
        'heating_advance_payments_total': heating_total, 'heating_advance_payments_source': source}))


def finalize_annual_advance_statement(world, advance_total, heating_total):
    run = annual_advance_draft(world)
    url = f"/api/settlements/{run['id']}"
    response = world.act('PATCH', url + '/advance-payments-derived', params={'period_year': 2026})
    assert response.status_code == 200, world.describe_response()
    attest_annual_advances(world, run['id'], advance_total, heating_total, 'DERIVED')
    response = world.act('POST', url + '/finalize', json={})
    assert response.status_code == 200, world.describe_response()
    world.data['combined_run'] = response.json()


@then('the statement and PDF show one unallocated advance of 2340 euros and 560 euros payable')
def unallocated_advance_results(world):
    run = world.data['combined_run']
    label = 'Vorauszahlungen ohne Aufteilung'
    lines = [line for line in run['lines'] if line['line_type'] == 'ADVANCE_PAYMENT']
    assert [(line['label'], Decimal(line['amount'])) for line in lines] == [(label, Decimal('2340'))]
    assert Decimal(run['balance']) == Decimal('560')
    response = world.act('GET', f"/api/settlements/{run['id']}/pdf")
    assert response.status_code == 200, world.describe_response()
    content = subprocess.run(['pdftotext', '-layout', '-', '-'], input=response.content,
                             capture_output=True, check=True).stdout.decode()
    assert sum(label in line and '2.340,00' in line for line in content.splitlines()) == 1, content
    assert '560,00' in content
    world.data['annual_pdf'] = response.content


@then('neither advance setter can change the finalized result or PDF')
def finalized_annual_advances_are_immutable(world):
    run = world.data['combined_run']
    url = f"/api/settlements/{run['id']}"
    response = world.act('PATCH', url + '/advance-payments', json={'advance_payments_total': '0.00'})
    assert response.status_code == 409, world.describe_response()
    response = world.act('PATCH', url + '/advance-payments-derived', params={'period_year': 2026})
    assert response.status_code == 409, world.describe_response()
    reloaded = world.act('GET', url)
    assert reloaded.status_code == 200, world.describe_response()
    for field in ('advance_payments_total', 'balance', 'lines', 'snapshot_sha256'):
        assert reloaded.json()[field] == run[field]
    pdf = world.act('GET', url + '/pdf')
    assert pdf.status_code == 200, world.describe_response()
    assert pdf.content == world.data['annual_pdf']


@then('the statement and PDF show 2880 euros combined credit and 20 euros payable')
def combined_results(world):
    run = world.data['combined_run']
    lines = [line for line in run['lines'] if line['line_type'] == 'ADVANCE_PAYMENT']
    assert [Decimal(line['amount']) for line in lines] == [Decimal('2880')]
    assert 'inkl. Heizkosten' in lines[0]['label']
    assert Decimal(run['balance']) == Decimal('20')
    response = world.act('GET', f"/api/settlements/{run['id']}/pdf")
    assert response.status_code == 200, world.describe_response()
    content = subprocess.run(['pdftotext', '-', '-'], input=response.content, capture_output=True, check=True).stdout.decode()
    assert 'inkl. Heizkosten' in content and '2.880,00' in content and '20,00' in content


@then('the original bookings remain unchanged')
def original_bookings_unchanged(world):
    for original in world.data['original_advance_bookings']:
        response = world.act('GET', f"/api/payments/{original['id']}")
        assert response.status_code == 200, world.describe_response()
        assert response.json() == original


@then('the statement credits 1440 euros combined, 900 euros operating and 540 euros heating, with 20 euros payable')
def mixed_results(world):
    run = world.data['combined_run']
    lines = [line for line in run['lines'] if line['line_type'] == 'ADVANCE_PAYMENT']
    assert sorted(Decimal(line['amount']) for line in lines) == [Decimal('540'), Decimal('900'), Decimal('1440')]
    assert Decimal(run['balance']) == Decimal('20')


@then('the combined proposal is 241.67 euros and adoption keeps the finalized statement unchanged')
def combined_proposal_preserves_original(world):
    run = world.data['combined_run']
    path = f"/api/settlements/{run['id']}"
    pdf = world.act('GET', path + '/pdf')
    assert pdf.status_code == 200, world.describe_response()
    before = world.act('GET', path).json()
    proposal = world.act('GET', path + '/advance-proposal')
    assert proposal.status_code == 200, world.describe_response()
    row = proposal.json()
    assert (row['advance_mode'], row['tenant_cost'], row['monthly_amount'], row['heating_monthly_amount']) == (
        'COMBINED', '2900.00', '241.67', '0.00')
    adopted = world.act('POST', path + '/advance-proposal', json={'valid_from': '2027-01-01'})
    assert adopted.status_code == 201, world.describe_response()
    assert (adopted.json()['advance_mode'], adopted.json()['advance_payment']) == ('COMBINED', '241.67')
    after = world.act('GET', path).json()
    assert before['snapshot_sha256'] == after['snapshot_sha256'] == run['snapshot_sha256']
    assert before['lines'] == after['lines']
    assert world.act('GET', path + '/pdf').content == pdf.content


def book_tenant_advances(world, direction, amount):
    data = world.data['advance_split']
    for kind in ('NK_VORAUSZAHLUNG', 'HK_VORAUSZAHLUNG'):
        payment = world.run(create(world.actor, '/api/payments', {
            'bank_account_id': data['account']['id'], 'direction': direction, 'amount': amount,
            'value_date': '2026-07-05' if direction == 'IN' else '2026-07-20',
            'payment_type': kind, 'period_year': 2026, 'period_month': 7,
            'tenant_profile_id': data['tenant']['id'], 'lease_agreement_id': data['lease']['id']}))
        response = world.act('POST', f"/api/payments/{payment['id']}/book")
        assert response.status_code == 200, world.describe_response()


@given('Anna paid 50 euros each in operating and heating advances for July 2026')
def paid_advances_for_july(world):
    world.data['advance_split'] = world.run(period_property(world.actor, month=1))
    book_tenant_advances(world, 'IN', '50.00')


@when('the manager books a 70 euro refund for each advance and derives the 2026 statement')
def derive_refunded_advances(world):
    book_tenant_advances(world, 'OUT', '70.00')
    data = world.data['advance_split']
    run = world.run(create(world.actor, '/api/settlements', statement(data, '2026-01-01', '2026-12-31')))
    world.data['refund_run_url'] = f"/api/settlements/{run['id']}"
    response = world.act('PATCH', world.data['refund_run_url'] + '/advance-payments-derived',
                         params={'period_year': 2026})
    assert response.status_code == 200, world.describe_response()


@then('each advance is minus 20 euros')
def advances_are_negative(world):
    run = world.response.json()
    for prefix in ('', 'heating_'):
        assert run[f'{prefix}advance_payments_total'] == '-20.00'
        assert run[f'{prefix}advance_payments_source'] == 'DERIVED'


@when('the manager finalizes the statement with 100 euros operating and 80 euros heating costs')
def finalize_refunded_statement(world):
    data = world.data['advance_split']
    for code, amount in [('GRUNDSTEUER', '100.00'), ('MESSDIENST', '80.00')]:
        confirmed_period_cost(world, data, code, amount, '2026-01-01', '2026-12-31', 'WOHNFLAECHE')
    url = world.data['refund_run_url']
    world.run(attest_fixture(world.actor, 'settlement', url.rsplit('/', 1)[-1], {
        'advance_payments_total': '-20.00', 'advance_payments_source': 'DERIVED',
        'heating_advance_payments_total': '-20.00', 'heating_advance_payments_source': 'DERIVED'}))
    response = world.act('POST', url + '/finalize', json={})
    assert response.status_code == 200, world.describe_response()
    world.data['finalized_refund_run'] = response.json()


@then('the statement and PDF show both negative advances and 220 euros due')
def refunded_statement_and_pdf(world):
    run = world.data['finalized_refund_run']
    assert run['status'] == 'finalized'
    assert run['advance_payments_total'] == run['heating_advance_payments_total'] == '-20.00'
    lines = {line['label']: line['amount'] for line in run['lines']}
    assert lines['Betriebskosten abzüglich Vorauszahlungen'] == '120.00'
    assert lines['Heizkosten abzüglich Vorauszahlungen'] == '100.00'
    assert run['balance'] == '220.00'
    response = world.act('GET', world.data['refund_run_url'] + '/pdf')
    assert response.status_code == 200, world.describe_response()
    content = subprocess.run(['pdftotext', '-layout', '-', '-'], input=response.content,
                             capture_output=True, check=True).stdout.decode()
    for label in ('Betriebskosten-Vorauszahlungen', 'Heizkosten-Vorauszahlungen'):
        assert any(label in line and '-20,00' in line for line in content.splitlines()), content
    assert '220,00' in content


@when('the manager books another refund for each advance')
def book_additional_refunds(world):
    book_tenant_advances(world, 'OUT', '10.00')


@then('the finalized statement and PDF still show both negative advances and 220 euros due')
def finalized_refunds_stay_frozen(world):
    response = world.act('GET', world.data['refund_run_url'])
    assert response.status_code == 200, world.describe_response()
    assert response.json() == world.data['finalized_refund_run']
    refunded_statement_and_pdf(world)
