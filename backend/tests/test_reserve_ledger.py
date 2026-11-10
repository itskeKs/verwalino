"""Reserve API and frozen annual statements, on synthetic WEG records."""
import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_m4_weg import _seed_weg, _create_and_finalize_plan
from tests.financial_coverage_helpers import attest_fixture, reserve_evidence
from tests.test_rent_changes import rent_db as rent_db, rent_world as rent_world, api_client
from tests.test_organization_export import export_store as export_store
from tests.person_retention_support import repayment_recipient
from tests.test_financial_scope_boundary import scope_seed as scope_seed
from tests.test_invoice_extraction_worker import worker_tenant as worker_tenant
from tests.test_ownership_change import (
    ownership_world as ownership_world, ownership_client, create_owner, end_owner,
)


async def reserve_world(api, seed):
    world = _seed_weg(seed)
    plan = await _create_and_finalize_plan(api, world)
    response = await api.post('/api/weg-settlements', json={
        'property_id': world['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    world['run'] = response.json()['id']
    world['plan'] = plan
    return world


async def reserve_erasure_case(api, seed):
    w = await reserve_world(api, seed)
    person = await api.post('/api/persons', json={'first_name': 'Eva', 'last_name': 'Muster'})
    assert person.status_code == 201, person.text
    w['person'] = person.json()['id']
    entry = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '300.00', 'reason': 'Abstimmung mit Eva Muster'})
    assert entry.status_code == 201, entry.text
    w['entry'] = entry.json()['id']
    return w


async def retained_anna(api, property_id):
    person, payment = await repayment_recipient(api, property_id, book=False)
    response = await api.put('/api/persons/' + person['id'], json={'first_name': 'Anna', 'last_name': 'Beispiel'})
    assert response.status_code == 200, response.text
    person = response.json()
    booked = await api.post(f"/api/payments/{payment['id']}/book")
    assert booked.status_code == 200, booked.text
    return person, booked.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['request-deletion', 'anonymize'])
async def test_real_repayment_retains_anna_while_incidental_eva_is_anonymized(
        export_store, isolated_reserve_admin, rent_world, migrator_session, command):
    from app.engine.canonical import canonical_json, sha256_hex
    api, seed = isolated_reserve_admin
    w = await reserve_erasure_case(api, seed)
    anna, payment = await retained_anna(api, rent_world['property'])
    preserved = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '300.00', 'reason': 'Abstimmung mit Anna Beispiel'})
    assert preserved.status_code == 201, preserved.text
    frozen = await finalize(api, w)
    query = text('SELECT to_jsonb(r) FROM settlement_run r WHERE id=CAST(:id AS uuid)')
    before_run = await migrator_session.scalar(query, {'id': w['run']})
    before = await reserve_erasure_rows(migrator_session, seed['org_a_id'])
    status = (await api.get(f"/api/dsgvo/persons/{anna['id']}/status")).json()
    assert status['decision'] == 'RESTRICT' and not status['has_active_relationship']
    assert status['bindings'] == [{'kind': 'BOOKED_PAYMENT', 'entity_type': 'payment',
        'entity_id': payment['id'], 'retention_until': payment['retention_until']}]
    restricted = await api.post(f"/api/dsgvo/persons/{anna['id']}/request-deletion")
    assert restricted.status_code == 200, restricted.text
    assert restricted.json()['applied'] == 'RESTRICTED'
    assert (await api.post(f"/api/dsgvo/persons/{anna['id']}/anonymize")).status_code == 409
    assert (await api.get('/api/persons/' + anna['id'])).json() == anna
    assert (await api.get('/api/payments/' + payment['id'])).json() == payment
    assert await reserve_erasure_rows(migrator_session, seed['org_a_id']) == before
    response = await api.post(f"/api/dsgvo/persons/{w['person']}/{command}")
    assert response.status_code == 200, response.text
    assert response.json()['applied'] == 'ANONYMIZED'
    expected_ledger, expected_audit = before
    expected_ledger[w['entry']]['reason'] = 'Abstimmung mit Gelöscht'
    for event in expected_audit.values():
        if event['payload_diff'].get('entry_id') == w['entry']:
            event['payload_diff']['reason'] = 'Abstimmung mit Gelöscht'
    assert await reserve_erasure_rows(migrator_session, seed['org_a_id']) == (expected_ledger, expected_audit)
    assert (await api.get('/api/payments/' + payment['id'])).json() == payment
    assert await migrator_session.scalar(query, {'id': w['run']}) == before_run
    assert sha256_hex(canonical_json(before_run['snapshot_payload'])) == frozen['snapshot_sha256']
    repeated = await api.post(f"/api/dsgvo/persons/{w['person']}/{command}")
    assert repeated.json()['applied'] == 'NONE_ALREADY_ANONYMIZED'


async def reserve_erasure_rows(db, org):
    params = {'org': org}
    ledger = (await db.execute(text('SELECT to_jsonb(e) FROM reserve_ledger_entry e WHERE organization_id=:org'), params)).scalars().all()
    audit = (await db.execute(text("SELECT to_jsonb(e) FROM audit_event e WHERE organization_id=:org AND action LIKE 'reserve.%'"), params)).scalars().all()
    return {r['id']: r for r in ledger}, {r['id']: r for r in audit}


@pytest.fixture
async def isolated_reserve_admin(seed_data, rent_world):
    seed = {**seed_data, 'org_a_id': rent_world['org'], 'user_a_admin_id': rent_world['admin']}
    async with api_client(rent_world) as api:
        yield api, seed


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['request-deletion', 'anonymize'])
async def test_person_erasure_preserves_a_known_longer_person_name_in_both_copies(
        isolated_reserve_admin, migrator_session, command):
    api, seed = isolated_reserve_admin
    w = await reserve_erasure_case(api, seed)
    maria = await api.post('/api/persons', json={'first_name': 'Maria Eva', 'last_name': 'Muster'})
    assert maria.status_code == 201, maria.text
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    for reason in ['Abstimmung mit Maria Eva Muster',
                   'Abstimmung mit Eva Muster; Abstimmung mit MARIA\tEVA MUSTER; 300,00 €']:
        entry = await api.post(path, json={'component': 'OPENING', 'amount': '300.00', 'reason': reason})
        assert entry.status_code == 201, entry.text
    frozen = await finalize(api, w)
    before = await reserve_erasure_rows(migrator_session, seed['org_a_id'])
    response = await api.post(f"/api/dsgvo/persons/{w['person']}/{command}")
    assert response.status_code == 200, response.text
    assert response.json()['applied'] == 'ANONYMIZED'
    expected_ledger, expected_audit = before
    for row in expected_ledger.values():
        row['reason'] = row['reason'].replace('mit Eva Muster', 'mit Gelöscht')
    for row in expected_audit.values():
        row['payload_diff']['reason'] = row['payload_diff']['reason'].replace('mit Eva Muster', 'mit Gelöscht')
    assert await reserve_erasure_rows(migrator_session, seed['org_a_id']) == (expected_ledger, expected_audit)
    person = (await api.get('/api/persons/' + maria.json()['id'])).json()
    assert (person['first_name'], person['last_name']) == ('Maria Eva', 'Muster')
    assert (await api.get(f"/api/weg-settlements/{w['run']}")).json()['snapshot_sha256'] == frozen['snapshot_sha256']


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['request-deletion', 'anonymize'])
async def test_person_erasure_fits_an_accepted_maximum_length_reason_without_truncating_context(
        isolated_reserve_admin, migrator_session, command):
    api, seed = isolated_reserve_admin
    w = await reserve_world(api, seed)
    person = await api.post('/api/persons', json={'first_name': 'Li', 'last_name': 'Wu'})
    assert person.status_code == 201, person.text
    prefix = 'Abstimmung mit Li Wu; '
    reason = prefix + 'x' * (2000 - len(prefix))
    assert len(reason) == 2000
    entry = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '300.00', 'reason': reason})
    assert entry.status_code == 201, entry.text
    before = await reserve_erasure_rows(migrator_session, seed['org_a_id'])
    response = await api.post(f"/api/dsgvo/persons/{person.json()['id']}/{command}")
    assert response.status_code == 200, response.text
    assert response.json()['applied'] == 'ANONYMIZED'
    after = await reserve_erasure_rows(migrator_session, seed['org_a_id'])
    scrubbed = after[0][entry.json()['id']]['reason']
    assert len(scrubbed) <= 2000
    assert 'Li Wu' not in scrubbed
    assert scrubbed.startswith('Abstimmung mit ')
    assert scrubbed.endswith('; ' + 'x' * (2000 - len(prefix)))
    expected_ledger, expected_audit = before
    expected_ledger[entry.json()['id']]['reason'] = scrubbed
    for row in expected_audit.values():
        if row['payload_diff']['entry_id'] == entry.json()['id']:
            row['payload_diff']['reason'] = scrubbed
    assert after == (expected_ledger, expected_audit)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['insert', 'update', 'delete'])
@pytest.mark.parametrize('with_actor', [True, False])
async def test_person_identity_writes_wait_for_the_existing_erasure_financial_lock(
        isolated_reserve_admin, migrator_session, app_rw_session, operation, with_actor):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    api, seed = isolated_reserve_admin
    person = await api.post('/api/persons', json={'first_name': 'Maria Eva', 'last_name': 'Muster'})
    assert person.status_code == 201, person.text
    await migrator_session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('financial-inputs:'||CAST(:org AS text),0))"),
        {'org': str(seed['org_a_id'])})
    statements = {
        'insert': "INSERT INTO person(organization_id,first_name,last_name,street,zip_code,city) VALUES (:org,'Maria Eva','Muster','','','')",
        'update': 'UPDATE person SET first_name=first_name WHERE id=CAST(:person AS uuid)',
        'delete': 'DELETE FROM person WHERE id=CAST(:person AS uuid)',
    }
    async with async_sessionmaker(app_rw_session.bind).begin() as db:
        await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(seed['org_a_id']), 'actor': str(seed['user_a_admin_id']) if with_actor else ''})
        await db.execute(text("SET LOCAL lock_timeout='100ms'"))
        with pytest.raises(DBAPIError) as refused:
            async with db.begin_nested():
                await db.execute(text(statements[operation]), {'org': seed['org_a_id'], 'person': person.json()['id']})
        assert refused.value.orig.sqlstate == '55P03'


@pytest.mark.asyncio
async def test_person_erasure_prepares_known_names_once_for_many_contacts_and_reasons(
        isolated_reserve_admin, migrator_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    api, seed = isolated_reserve_admin
    w = await reserve_erasure_case(api, seed)
    async with async_sessionmaker(migrator_session.bind).begin() as db:
        await db.execute(text("""INSERT INTO person(organization_id,first_name,last_name,street,zip_code,city)
            SELECT :org,'Contact '||n,'Other '||n,'','','' FROM generate_series(1,1000) n"""), {'org': seed['org_a_id']})
    for _ in range(20):
        entry = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'OPENING', 'amount': '300.00', 'reason': 'Abstimmung mit Eva Muster'})
        assert entry.status_code == 201, entry.text
    async with migrator_session.begin_nested() as instrumentation:
        await migrator_session.execute(text('CREATE TEMP TABLE reserve_erasure_lookups(calls integer) ON COMMIT DROP'))
        await migrator_session.execute(text('ALTER FUNCTION prepare_person_reserve_erasure(uuid) RENAME TO reserve_erasure_test_original'))
        await migrator_session.execute(text("""CREATE FUNCTION public.prepare_person_reserve_erasure(org uuid) RETURNS jsonb
            LANGUAGE plpgsql AS $$ BEGIN INSERT INTO pg_temp.reserve_erasure_lookups VALUES(1);
            RETURN public.reserve_erasure_test_original(org); END $$"""))
        await migrator_session.execute(text('REVOKE ALL ON FUNCTION prepare_person_reserve_erasure(uuid) FROM PUBLIC,app_rw,app_worker,app_review'))
        await migrator_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(seed['org_a_id']), 'actor': str(seed['user_a_admin_id'])})
        assert await migrator_session.scalar(text("SELECT apply_person_privacy(:org,CAST(:person AS uuid),'ANONYMIZE')"),
            {'org': seed['org_a_id'], 'person': w['person']}) == 'ANONYMIZED'
        assert await migrator_session.scalar(text('SELECT count(*) FROM pg_temp.reserve_erasure_lookups')) == 1
        ledger, audit = await reserve_erasure_rows(migrator_session, seed['org_a_id'])
        assert len(ledger) == len(audit) == 21
        assert {row['reason'] for row in ledger.values()} == {'Abstimmung mit Gelöscht'}
        assert {row['payload_diff']['reason'] for row in audit.values()} == {'Abstimmung mit Gelöscht'}
        assert not await migrator_session.scalar(text("SELECT NULLIF(current_setting('app.person_reserve_erasure_names',true),'') IS NOT NULL"))
        await instrumentation.rollback()


@pytest.mark.asyncio
async def test_person_erasure_includes_reductions_and_restorations_but_not_similar_names_or_foreign_rows(
        client_a_admin, client_b_admin, seed_data, migrator_session):
    api = client_a_admin
    person = (await api.post('/api/persons', json={'first_name': 'Eva', 'last_name': 'Muster'})).json()['id']
    for first, last in [('Eva', 'Musterdorf'), ('Eva', 'Muster-Sommer'), ('Hanna', 'Muster'), ('Maria Eva', 'Muster')]:
        assert (await api.post('/api/persons', json={'first_name': first, 'last_name': last})).status_code == 201
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    reason = 'Abstimmung mit Eva Muster; Eva Musterdorf; Eva Muster-Sommer; Hanna Muster; Maria Eva Muster; 300,00 €'
    entry = await api.post(path, json={'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': '300.00', 'reason': reason})
    assert entry.status_code == 201, entry.text
    correction = path + '/' + entry.json()['id']
    for amount in ('100.00', '300.00'):
        changed = await api.post(correction + '/reduce', json={'reduced_to': amount, 'reason': reason})
        assert changed.status_code == 201, changed.text
    foreign_property = (await client_b_admin.post('/api/properties', json={'name': 'Erasure isolation', 'management_type': 'WEG'})).json()['id']
    foreign_run = (await client_b_admin.post('/api/weg-settlements/asset-reports', json={
        'property_id': foreign_property, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})).json()['id']
    foreign_entry = await client_b_admin.post(f'/api/weg-settlements/{foreign_run}/reserve-ledger/overrides', json={
        'component': 'OPENING', 'amount': '300.00', 'reason': reason})
    assert foreign_entry.status_code == 201, foreign_entry.text
    before = await reserve_erasure_rows(migrator_session, seed_data['org_a_id'])
    foreign_before = await reserve_erasure_rows(migrator_session, seed_data['org_b_id'])
    response = await api.post(f'/api/dsgvo/persons/{person}/request-deletion')
    assert response.status_code == 200, response.text
    after = await reserve_erasure_rows(migrator_session, seed_data['org_a_id'])
    expected_ledger, expected_audit = before
    for row in expected_ledger.values():
        if row['reason'] == reason:
            row['reason'] = reason.replace('mit Eva Muster;', 'mit Gelöscht;')
    for row in expected_audit.values():
        if row['payload_diff'].get('reason') == reason:
            row['payload_diff']['reason'] = reason.replace('mit Eva Muster;', 'mit Gelöscht;')
    assert after == (expected_ledger, expected_audit)
    assert {'reserve.override_added', 'reserve.contribution_reduced', 'reserve.contribution_restored'} <= {r['action'] for r in after[1].values()}
    assert await reserve_erasure_rows(migrator_session, seed_data['org_b_id']) == foreign_before


@pytest.mark.asyncio
async def test_person_erasure_context_does_not_authorize_direct_financial_mutation(
        client_a_admin, seed_data, app_rw_session, migrator_session):
    w = await reserve_erasure_case(client_a_admin, seed_data)
    params = {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id']),
              'person': w['person'], 'entry': w['entry'], 'run': w['run']}
    await app_rw_session.execute(text("""SELECT set_config('app.current_org_id',:org,true),
        set_config('app.current_user_id',:actor,true),set_config('app.person_reserve_erasure',:person,true)"""), params)
    statements = [
        "UPDATE reserve_ledger_entry SET reason='Abstimmung mit Gelöscht' WHERE id=CAST(:entry AS uuid)",
        "UPDATE reserve_ledger_entry SET amount=301 WHERE id=CAST(:entry AS uuid)",
        "DELETE FROM reserve_ledger_entry WHERE id=CAST(:entry AS uuid)",
        "UPDATE person SET anonymized_at=now() WHERE id=CAST(:person AS uuid)",
        "UPDATE audit_event SET payload_diff=jsonb_set(payload_diff,'{reason}','\"Gelöscht\"') WHERE entity_id=CAST(:run AS uuid)",
        "SELECT person_reserve_erasure_text(CAST(:org AS uuid),'Eva Muster')",
    ]
    for statement in statements:
        with pytest.raises(DBAPIError) as refused:
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(statement), params)
        assert refused.value.orig.sqlstate in {'23514', '42501'}
    assert not await app_rw_session.scalar(text("SELECT has_function_privilege(current_user,'erase_person_reserve_reasons()','EXECUTE')"))
    before = await reserve_erasure_rows(migrator_session, seed_data['org_a_id'])
    # Even an operator's forged context outside the nested person trigger is refused.
    await migrator_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.person_reserve_erasure',:person,true)"), params)
    for statement in (statements[0], statements[1], statements[4]):
        with pytest.raises(DBAPIError) as refused:
            async with migrator_session.begin_nested():
                await migrator_session.execute(text(statement), params)
        assert refused.value.orig.sqlstate == '23514'
    assert await reserve_erasure_rows(migrator_session, seed_data['org_a_id']) == before


@pytest.mark.asyncio
async def test_retained_repayment_identity_cannot_be_scrubbed_from_a_forged_context(
        isolated_reserve_admin, rent_world, migrator_session, app_rw_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    api, seed = isolated_reserve_admin
    w = await reserve_world(api, seed)
    anna, payment = await retained_anna(api, rent_world['property'])
    entry = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '300.00', 'reason': 'Abstimmung mit Anna Beispiel'})
    assert entry.status_code == 201, entry.text
    before = await reserve_erasure_rows(migrator_session, seed['org_a_id'])
    async with async_sessionmaker(app_rw_session.bind).begin() as db:
        await db.execute(text("""SELECT set_config('app.current_org_id',:org,true),
            set_config('app.current_user_id',:actor,true),set_config('app.person_reserve_erasure',:person,true)"""),
            {'org': str(seed['org_a_id']), 'actor': str(seed['user_a_admin_id']), 'person': anna['id']})
        await db.execute(text('CREATE TEMP TABLE erasure_attack(id uuid) ON COMMIT DROP'))
        await db.execute(text("""CREATE FUNCTION pg_temp.erasure_attack() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN UPDATE public.reserve_ledger_entry SET reason='Abstimmung mit Gelöscht' WHERE id=NEW.id;
            RETURN NEW; END $$"""))
        await db.execute(text("""CREATE TRIGGER erasure_attack AFTER INSERT ON erasure_attack
            FOR EACH ROW EXECUTE FUNCTION pg_temp.erasure_attack()"""))
        with pytest.raises(DBAPIError) as refused:
            async with db.begin_nested():
                await db.execute(text('INSERT INTO erasure_attack VALUES (CAST(:entry AS uuid))'), {'entry': entry.json()['id']})
        assert refused.value.orig.sqlstate == '42501'
        assert await db.scalar(text("SELECT apply_person_privacy(:org,CAST(:person AS uuid),'ANONYMIZE')"),
            {'org': seed['org_a_id'], 'person': anna['id']}) == 'RETAINED'
    assert await reserve_erasure_rows(migrator_session, seed['org_a_id']) == before
    assert (await api.get('/api/payments/' + payment['id'])).json() == payment


@pytest.mark.asyncio
async def test_person_name_lock_does_not_lock_an_unwritable_strict_tenant(seed_data, app_rw_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from tests.test_property_source_enforcement import synthetic_enforcement
    async with synthetic_enforcement(seed_data):
        async with async_sessionmaker(app_rw_session.bind).begin() as denied:
            await denied.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id','',true)"),
                {'org': str(seed_data['org_a_id'])})
            assert (await denied.execute(text("UPDATE person SET first_name='Forbidden' WHERE false"))).rowcount == 0
            async with async_sessionmaker(app_rw_session.bind).begin() as other:
                assert await other.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended('financial-inputs:'||CAST(:org AS text),0))"),
                    {'org': str(seed_data['org_a_id'])})


@pytest.mark.asyncio
@pytest.mark.parametrize('first,last,company,value,expected', [
    ('Eva', 'Muster', None, 'Abstimmung mit EVA\tMUSTER (300,00 €)', 'Abstimmung mit Gelöscht (300,00 €)'),
    ('Eva', 'Muster', None, 'Eva, Muster, Hanna Muster, Eva Musterdorf, Eva Muster-Sommer',
     'Eva, Muster, Hanna Muster, Eva Musterdorf, Eva Muster-Sommer'),
    ('A(nna)', 'Bei.spiel+', None, 'A(nna) Bei.spiel+; Eva Muster', 'Gelöscht; Eva Muster'),
    ('Eva', 'Muster', 'Beispiel (GmbH)', 'An Beispiel (GmbH); Beispiel GmbH', 'An Gelöscht; Beispiel GmbH'),
])
async def test_person_erasure_matches_complete_literal_names_without_touching_amounts(
        client_a_admin, seed_data, migrator_session, first, last, company, value, expected):
    person = await client_a_admin.post('/api/persons', json={'first_name': first, 'last_name': last, 'company_name': company})
    assert person.status_code == 201, person.text
    await migrator_session.execute(text("SELECT set_config('app.person_reserve_erasure',:person,true)"), {'person': person.json()['id']})
    assert await migrator_session.scalar(text('SELECT person_reserve_erasure_text(:org,:value)'),
        {'org': seed_data['org_a_id'], 'value': value}) == expected


@pytest.mark.asyncio
async def test_person_erasure_preserves_unrelated_audit_fields_events_and_operator_erasure_path(
        client_a_admin, seed_data, migrator_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import json
    api = client_a_admin
    w = await reserve_erasure_case(api, seed_data)
    original = 'Abstimmung mit Eva Muster'
    # Seed extra payload fields and misleading copies through ordinary INSERTs.
    async with async_sessionmaker(migrator_session.bind).begin() as db:
        for kind, action, entry_id, reason in [
            ('settlement_run', 'reserve.override_added', w['entry'], original),
            ('person', 'reserve.override_added', w['entry'], original),
            ('settlement_run', 'reserve.unrelated', w['entry'], original),
            ('settlement_run', 'reserve.override_added', w['person'], original),
            ('settlement_run', 'reserve.override_added', w['entry'], original + ' (different copy)'),
        ]:
            await db.execute(text("""INSERT INTO audit_event(organization_id,entity_type,entity_id,action,payload_diff,prev_event_hash)
                VALUES (:org,:kind,CAST(:run AS uuid),:action,CAST(:payload AS jsonb),'retained-original-hash')"""),
                {'org': seed_data['org_a_id'], 'kind': kind, 'run': w['run'], 'action': action,
                 'payload': json.dumps({'entry_id': entry_id, 'reason': reason, 'other': 'Eva Muster',
                                        'nested': {'reason': original}, 'amount': '300.00'})})
    before = await reserve_erasure_rows(migrator_session, seed_data['org_a_id'])
    # Organization/operator master-data anonymization must not invoke this new individual path.
    async with migrator_session.begin_nested() as point:
        await migrator_session.execute(text("UPDATE person SET anonymized_at=now() WHERE id=CAST(:person AS uuid)"), {'person': w['person']})
        assert await reserve_erasure_rows(migrator_session, seed_data['org_a_id']) == before
        await point.rollback()
    response = await api.post(f"/api/dsgvo/persons/{w['person']}/anonymize")
    assert response.status_code == 200, response.text
    expected_ledger, expected_audit = before
    affected = {identifier for identifier, row in expected_ledger.items() if row['reason'] == original}
    for identifier in affected:
        expected_ledger[identifier]['reason'] = 'Abstimmung mit Gelöscht'
    for row in expected_audit.values():
        if (row['entity_type'] == 'settlement_run' and row['action'] == 'reserve.override_added'
                and row['payload_diff'].get('entry_id') in affected and row['payload_diff'].get('reason') == original):
            row['payload_diff']['reason'] = 'Abstimmung mit Gelöscht'
    assert await reserve_erasure_rows(migrator_session, seed_data['org_a_id']) == (expected_ledger, expected_audit)


@pytest.mark.asyncio
async def test_reserve_append_only_guard_is_always_enabled(migrator_session):
    assert await migrator_session.scalar(text("""SELECT tgenabled::text FROM pg_trigger
        WHERE tgrelid='public.reserve_ledger_entry'::regclass
        AND tgname='guard_reserve_ledger'""")) == 'A'
    retained = (await migrator_session.execute(text("""SELECT pk,disposition,retain_when
        FROM organization_retention_catalog() WHERE entity='reserve_ledger_entry'"""))).one()
    assert tuple(retained) == (['id'], 'retain', 'true')


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['DELETE FROM', 'UPDATE'])
async def test_replica_superuser_cannot_mutate_reserve_history(client_a_admin, seed_data, operation):
    import psycopg2
    from contextlib import closing
    from app.core.config import settings
    from app.core.declaration_test_database import require_disposable_database_settings

    w = await unit_correction_world(client_a_admin, seed_data)
    _, _, url = require_disposable_database_settings(settings)
    with closing(psycopg2.connect(url)) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT current_user,session_user,rolsuper FROM pg_roles WHERE rolname=current_user')
        assert cursor.fetchone() == ('app_migrator', 'app_migrator', True)
        cursor.execute("SET LOCAL session_replication_role='replica'")
        cursor.execute('SHOW session_replication_role')
        assert cursor.fetchone() == ('replica',)
        assignment = ' SET reason=reason' if operation == 'UPDATE' else ''
        with pytest.raises(psycopg2.errors.CheckViolation) as refused:
            cursor.execute(f'{operation} reserve_ledger_entry{assignment} WHERE settlement_run_id=%s', (w['run'],))
        assert refused.value.pgcode == '23514'
        assert refused.value.diag.constraint_name == 'reserve_ledger_append_only'
        connection.rollback()
        cursor.execute('SHOW session_replication_role')
        assert cursor.fetchone() == ('origin',)
        cursor.execute('SELECT count(*) FROM reserve_ledger_entry WHERE settlement_run_id=%s', (w['run'],))
        assert cursor.fetchone() == (1,)


@pytest.mark.asyncio
async def test_reserve_ledger_foreign_keys_match_database_columns_and_delete_actions(migrator_session):
    from app.models.reserve_ledger_entry import ReserveLedgerEntry

    rows = (await migrator_session.execute(text("""
        SELECT c.confdeltype::text AS confdeltype,
            array_agg(source.attname ORDER BY key.ordinality) AS local_columns,
            array_agg(target.relname || '.' || referenced.attname ORDER BY key.ordinality) AS target_columns
        FROM pg_constraint c
        JOIN pg_class target ON target.oid = c.confrelid
        CROSS JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS key(local_num, target_num, ordinality)
        JOIN pg_attribute source ON source.attrelid = c.conrelid AND source.attnum = key.local_num
        JOIN pg_attribute referenced ON referenced.attrelid = c.confrelid AND referenced.attnum = key.target_num
        WHERE c.conrelid = 'reserve_ledger_entry'::regclass AND c.contype = 'f'
        GROUP BY c.oid, target.relname
    """))).mappings().all()
    actions = {'a': 'NO ACTION', 'r': 'RESTRICT', 'c': 'CASCADE', 'n': 'SET NULL', 'd': 'SET DEFAULT'}
    database = {(tuple(row['local_columns']), tuple(row['target_columns'])):
                actions[row['confdeltype']] for row in rows}
    constraints = ReserveLedgerEntry.__table__.foreign_key_constraints
    model = {(tuple(element.parent.name for element in constraint.elements),
              tuple(element.target_fullname for element in constraint.elements)):
             constraint.ondelete or 'NO ACTION' for constraint in constraints}
    assert len(database) == len(rows)
    assert len(model) == len(constraints)
    assert database.keys() == model.keys()
    mismatches = [f'{local} -> {target}: database={database[(local, target)]}, model={model[(local, target)]}'
                  for local, target in sorted(database) if database[(local, target)] != model[(local, target)]]
    assert not mismatches, '\n'.join(mismatches)


@pytest.mark.asyncio
async def test_reducing_300_to_100_retains_minus_200_and_refuses_direct_delete_as_app_rw(
        client_a_admin, seed_data, app_rw_session):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    added = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '300.00', 'reason': 'Reconcile booked unit contribution'})
    assert added.status_code == 201, added.text
    original = added.json()['id']
    reduced = await api.post(path + f'/overrides/{original}/reduce', json={
        'reduced_to': '100.00', 'reason': 'Correct contribution to 100 euros'})
    assert reduced.status_code == 201, reduced.text
    ledger = (await api.get(path)).json()
    row = next(row for row in ledger['entries'] if row.get('id') == original)
    assert row['amount'] == '300.00'
    assert row['current_amount'] == '100.00'
    counters = [row for row in ledger['entries'] if row.get('reverses_entry_id') == original]
    assert len(counters) == 1
    assert counters[0]['amount'] == '-200.00'

    assert await app_rw_session.scalar(text('SELECT current_user')) == 'app_rw'
    with pytest.raises(DBAPIError) as mode_refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("SET LOCAL session_replication_role='replica'"))
    assert mode_refused.value.orig.sqlstate == '42501'
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text('DELETE FROM reserve_ledger_entry WHERE id=:entry'), {'entry': original})
    assert refused.value.orig.sqlstate == '23514'
    assert refused.value.orig.__cause__.constraint_name == 'reserve_ledger_append_only'
    retained = (await app_rw_session.execute(text('SELECT id,amount FROM reserve_ledger_entry WHERE id IN (:original,:counter)'),
        {'original': original, 'counter': counters[0]['id']})).all()
    assert {str(row.id): row.amount for row in retained} == {
        original: Decimal('300.00'), counters[0]['id']: Decimal('-200.00')}


@pytest.mark.asyncio
async def test_ledger_separates_planned_from_cash_and_requires_override_reason(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = await api.get(path)
    assert response.status_code == 200, response.text
    assert response.json()['planned_contributions_total'] == '6000.00'
    assert response.json()['contributions_total'] == '0.00'
    assert {row['owner_name'] for row in response.json()['entries']} == {'Anton Eigentuemer', 'Berta Eigentuemer'}
    override = path + '/overrides'
    for reason in ('', ' '):
        refused = await api.post(override, json={'component': 'OPENING', 'amount': '20000.00', 'reason': reason})
        assert refused.status_code == 422
        assert refused.json()['detail']['code'] == 'RESERVE_CORRECTION_REASON_REQUIRED'
    response = await api.post(override, json={'component': 'OPENING', 'amount': '20000.00', 'reason': 'Imported bank-confirmed balance'})
    assert response.status_code == 201, response.text
    assert (await api.get(path)).json()['closing_balance'] == '20000.00'


@pytest.mark.asyncio
async def test_ledger_keeps_readable_payment_references_on_draft_and_frozen_reads(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post('/api/payments', json={
        'bank_account_id': w['bank_giro'], 'direction': 'IN', 'amount': '1000.00',
        'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENANTEIL',
        'owner_profile_id': w['owner_a'], 'note': 'Zuführung für Änne Groß'})
    assert response.status_code == 201, response.text
    cash = response.json()['id']
    assert (await api.post(f'/api/payments/{cash}/book')).status_code == 200
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    for frozen in (False, True):
        if frozen:
            await finalize(api, w)
        ledger = (await api.get(path)).json()
        row = next(row for row in ledger['entries'] if row.get('payment_id') == cash)
        assert row['payment_note'] == 'Zuführung für Änne Groß'
        assert row['owner_name'] == 'Anton Eigentuemer'
        assert ledger['frozen'] is frozen


@pytest.mark.asyncio
async def test_ledger_foreign_ids_and_readonly_writes_are_refused(client_a_admin, client_b_admin, client_a_readonly, seed_data):
    w = await reserve_world(client_a_admin, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    assert (await client_b_admin.get(path)).status_code == 404
    body = {'component': 'ADJUSTMENT', 'amount': '1.00', 'reason': 'Reviewed interest'}
    assert (await client_b_admin.post(path + '/overrides', json=body)).status_code == 404
    assert (await client_a_readonly.post(path + '/overrides', json=body)).status_code == 403


@pytest.mark.asyncio
async def test_hidden_reserve_history_has_the_same_answer_as_a_missing_run(client_a_admin, seed_data, monkeypatch):
    import uuid
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    w = await reserve_world(client_a_admin, seed_data)
    missing = await client_a_admin.get(f'/api/weg-settlements/{uuid.uuid4()}/reserve-ledger')
    class HiddenHistory(Exception):
        sqlstate = '42501'
    monkeypatch.setattr(ReserveLedgerRepository, 'finalized', AsyncMock(side_effect=DBAPIError(None, None, HiddenHistory())))
    hidden = await client_a_admin.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


@pytest.mark.asyncio
async def test_reserve_history_helper_refuses_missing_properties(client_a_admin, seed_data, app_rw_session):
    import uuid
    w = await reserve_world(client_a_admin, seed_data)
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
        {'org': str(seed_data['org_a_id'])})
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
        {'actor': str(seed_data['user_a_admin_id'])})
    statement = text("SELECT reserve_history_access(:org,:property,'2026-12-31')")
    assert await app_rw_session.scalar(statement, {'org': seed_data['org_a_id'], 'property': w['property']}) is None
    with pytest.raises(DBAPIError) as hidden:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(statement, {'org': seed_data['org_a_id'], 'property': uuid.uuid4()})
    assert hidden.value.orig.sqlstate == '42501'


@pytest.mark.asyncio
@pytest.mark.parametrize('storno', [False, True])
async def test_late_hausgeld_uses_frozen_plan_and_storno_amount_after_source_changes(monkeypatch, storno):
    from types import SimpleNamespace as S
    from unittest.mock import AsyncMock
    from datetime import date
    from app.repositories.economic_plan_repo import EconomicPlanLineRepository
    from app.services.reserve_ledger_service import ReserveLedgerService
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    payload = {'inputs': {'units': [{'id': 'a', 'number': '1', 'mea': '2'}, {'id': 'b', 'number': '2', 'mea': '1'}],
        'owner_periods': [{'unit_id': unit, 'owner_profile_id': owner, 'from': '2020-01-01', 'to': None, 'mea_share': None}
                         for unit, owner in [('a', 'payer'), ('b', 'other')]]},
        'result': {'owners': [{'unit_id': unit, 'owner_profile_id': owner, 'annual_advance': '100.00'}
                             for unit, owner in [('a', 'payer'), ('b', 'other')]]}}
    original = S(id='original-plan', snapshot_payload=payload, reserve_contribution_total=Decimal('75'), period_start=start, period_end=end)
    replacement = S(id='replacement-plan', snapshot_payload=payload, reserve_contribution_total=Decimal('150'), period_start=start, period_end=end)
    frozen = S(id='closed', status='finalized', period_start=start, period_end=end, management_mandate_id=None,
        snapshot_payload={'reserve_ledger': {'closing_balance': '50.00', 'economic_plan_id': original.id,
            'entries': [{'kind': 'CONTRIBUTION', 'payment_id': 'original-cash', 'amount': '50.00',
                         'owner_profile_id': 'payer', 'date': '2025-05-05'}]}})
    run = S(id='next', status='draft', period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
        management_mandate_id=None, economic_plan_id=None)
    cash = S(id='late-cash', payment_type='HAUSGELD_VORSCHUSS', amount=Decimal('100'), direction='OUT' if storno else 'IN',
        value_date=date(2025, 8, 5), owner_profile_id='payer', bank_account_id='bank',
        reverses_id='original-cash' if storno else None, reversed_by_id=None)
    original_cash = S(id='original-cash', payment_type='HAUSGELD_VORSCHUSS', amount=Decimal('100'), direction='IN',
        value_date=date(2025, 5, 5), owner_profile_id='payer', bank_account_id='bank',
        reverses_id=None, reversed_by_id=cash.id if storno else None)
    service = ReserveLedgerService(None, 'org', 'actor')
    service.repo = S(finalized=AsyncMock(return_value=[(frozen, None)]), current_scope=AsyncMock(return_value=None),
        plans=AsyncMock(return_value=[]), entries=AsyncMock(return_value=[]), payments=AsyncMock(return_value=[original_cash, cash]),
        owner_units=AsyncMock(return_value={cash.id: {'a', 'b'} if storno else {'a'}}),
        cash_plans=AsyncMock(return_value=[replacement, original]), hausgeld_share=AsyncMock(return_value=Decimal('50')),
        plan_rows=AsyncMock(return_value=[{'kind': 'PLANNED', 'date': '2025-01-01',
            'owner_profile_id': 'payer', 'amount': '50.00'}]))
    lines = AsyncMock(return_value=[S(line_type='OWNER_ADVANCE', owner_profile_id='payer', planned_amount=Decimal('100'))])
    monkeypatch.setattr(EconomicPlanLineRepository, 'list_for_plan', lines)
    ledger = await service.read(run)
    assert ledger['late_cash_total'] == ('-50.00' if storno else '50.00')
    assert ledger['closing_balance'] == ('0.00' if storno else '100.00')
    lines.assert_awaited_once_with(original.id)
    service.repo.plan_rows.assert_awaited_once_with(original)


@pytest.mark.asyncio
async def test_same_period_frozen_ledger_cannot_replace_another_accessible_scope():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from datetime import date
    from app.services.reserve_ledger_service import ReserveLedgerService
    scope_a, scope_b = SimpleNamespace(id='a', whole_property=False), SimpleNamespace(id='b', whole_property=False)
    frozen = SimpleNamespace(status='finalized', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        snapshot_payload={'reserve_ledger': {'closing_balance': '9999.00'}}, management_mandate_id=None)
    run = SimpleNamespace(id='draft-b', status='draft', period_start=frozen.period_start, period_end=frozen.period_end,
        economic_plan_id=None, management_mandate_id=None)
    service = ReserveLedgerService(None, 'org', 'actor')
    service.repo = SimpleNamespace(finalized=AsyncMock(return_value=[(frozen, scope_a)]),
        current_scope=AsyncMock(return_value=scope_b), plans=AsyncMock(return_value=[]),
        owner_units=AsyncMock(return_value={}),
        entries=AsyncMock(return_value=[]), payments=AsyncMock(return_value=[]))
    ledger = await service.read(run)
    assert ledger['closing_balance'] == '0.00' and not ledger['frozen']


async def payment(api, w, amount, *, kind='RUECKLAGENANTEIL', direction='IN', when='2025-02-01', evidence=None):
    body = {'bank_account_id': w['bank_giro'], 'direction': direction, 'amount': amount,
            'value_date': when, 'payment_type': kind, 'period_year': int(when[:4]), 'period_month': int(when[5:7])}
    if direction == 'IN' or kind == 'HAUSGELD_VORSCHUSS':
        body['owner_profile_id'] = w['owner_a']
    if kind == 'RUECKLAGENENTNAHME':
        body.update(evidence or await reserve_evidence(api, w))
    response = await api.post('/api/payments', json=body)
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    response = await api.post(f'/api/payments/{identifier}/book')
    assert response.status_code == 200, response.text
    return identifier


def paid(w):
    return {'paid_advances': [{'owner_profile_id': w[key], 'paid_total': '0.00'} for key in ('owner_a', 'owner_b')]}


async def finalize(api, w):
    await attest_fixture(api, 'settlement', w['run'], paid(w))
    response = await api.post(f"/api/weg-settlements/{w['run']}/finalize", json=paid(w))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('hausgeld,reserve,refund,reverse,expected', [
    ('4200.00', '3600.00', '0.00', False, '3600.00'),
    ('7800.00', '1000.00', '0.00', False, '4600.00'),
    ('0.00', '3600.00', '0.00', False, '3600.00'),
    ('4200.00', '1000.00', '0.00', False, '2938.46'),
    ('4200.00', '3600.00', '0.00', True, '1938.46'),
    ('9000.00', '3600.00', '0.00', True, '4153.85'),
    ('4200.00', '100.00', '200.00', False, '1838.46'),
    ('-100.00', '8000.00', '0.00', False, '3700.00'),
], ids=['split-payer', 'excess', 'reserve-only', 'partial-split', 'reserve-storno', 'hausgeld-only-overpayment', 'negative-net-reserve', 'negative-net-hausgeld'])
async def test_split_reserve_cash_counts_each_euro_once(
        client_a_admin, seed_data, hausgeld, reserve, refund, reverse, expected):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if Decimal(hausgeld):
        await payment(api, w, str(abs(Decimal(hausgeld))), kind='HAUSGELD_VORSCHUSS',
                      direction='OUT' if Decimal(hausgeld) < 0 else 'IN')
    receipt = await payment(api, w, reserve)
    if reverse:
        response = await api.post(f'/api/payments/{receipt}/reverse', json={'note': 'Synthetic duplicate'})
        assert response.status_code == 201, response.text
    if Decimal(refund):
        response = await api.post('/api/payments', json={
            'bank_account_id': w['bank_giro'], 'owner_profile_id': w['owner_a'],
            'direction': 'OUT', 'amount': refund, 'value_date': '2025-02-01',
            'payment_type': 'RUECKLAGENANTEIL', 'period_year': 2025, 'period_month': 2})
        assert response.status_code == 201, response.text
        assert (await api.post(f"/api/payments/{response.json()['id']}/book")).status_code == 200
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['contributions_total'] == expected
    assert ledger['closing_balance'] == expected
    correction = [row for row in ledger['entries'] if row['kind'] == 'CONTRIBUTION'
                  and not row.get('payment_id')]
    assert len(correction) == (1 if expected in ('3600.00', '3700.00') and Decimal(hausgeld) else 0)
    if correction:
        assert correction[0]['owner_profile_id'] == w['owner_a']
        assert correction[0]['date'] == '2025-12-31'
        assert correction[0]['reason']


@pytest.mark.asyncio
@pytest.mark.parametrize('topology,hausgeld,reserve,expected', [
    ('two-units', '7000.00', '6500.00', '6500.00'),
    ('coowners', '2100.00', '1800.00', '1800.00'),
])
async def test_split_reserve_uses_the_owners_plan_totals_across_units_and_coowners(
        client_a_admin, seed_data, topology, hausgeld, reserve, expected):
    api = client_a_admin
    w = _seed_weg(seed_data)
    unit = w['u2'] if topology == 'two-units' else w['u1']
    old = (await api.get('/api/ownership', params={'unit_id': unit})).json()[0]
    response = await api.patch(f"/api/ownership/{old['id']}", json={'valid_to': '2024-12-31'})
    assert response.status_code == 200, response.text
    owners = [(w['owner_a'], '1')] if topology == 'two-units' else [
        (w['owner_a'], '0.5'), (w['owner_b'], '0.5')]
    for owner, share in owners:
        response = await api.post('/api/ownership', json={'unit_id': unit,
            'owner_profile_id': owner, 'valid_from': '2025-01-01', 'mea_share': share})
        assert response.status_code == 201, response.text
    plan = await _create_and_finalize_plan(api, w)
    response = await api.post('/api/weg-settlements', json={'property_id': w['property'],
        'economic_plan_id': plan, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    await payment(api, w, hausgeld, kind='HAUSGELD_VORSCHUSS')
    await payment(api, w, reserve)
    ledger = (await api.get(f"/api/weg-settlements/{response.json()['id']}/reserve-ledger")).json()
    assert ledger['contributions_total'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('hausgeld,reserve,expected,late,closing', [
    ('4200.00', '3600.00', '3600.00', '2300.00', '5900.00'),
    ('7800.00', '1000.00', '4600.00', '1600.00', '6200.00'),
], ids=['split-payer', 'excess'])
async def test_split_reserve_frozen_entries_hash_and_pdf_ignore_late_cash(
        client_a_admin, seed_data, migrator_session, hausgeld, reserve, expected, late, closing):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await payment(api, w, hausgeld, kind='HAUSGELD_VORSCHUSS')
    await payment(api, w, reserve)
    statement = await finalize(api, w)
    path = f"/api/weg-settlements/{w['run']}"
    frozen = (await api.get(path + '/reserve-ledger')).json()
    assert frozen['contributions_total'] == expected
    payload = await migrator_session.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)'), {'id': w['run']})
    assert payload['reserve_ledger']['entries'] == [
        {key: value for key, value in row.items() if key not in (
            'owner_name', 'payment_note', 'cost_document_number', 'register_sequence_number')}
        for row in frozen['entries']]
    before_pdf = (await api.get(path + '/pdf')).content
    await payment(api, w, '1000.00', when='2025-12-31')
    await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', when='2025-12-31')
    assert (await api.get(path + '/reserve-ledger')).json() == frozen
    assert (await api.get(path)).json() == statement
    assert (await api.get(path + '/pdf')).content == before_pdf
    assert await migrator_session.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)'), {'id': w['run']}) == payload
    response = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    ledger_response = await api.get(f"/api/weg-settlements/{response.json()['id']}/reserve-ledger")
    assert ledger_response.status_code == 200, ledger_response.text
    ledger = ledger_response.json()
    assert ledger['late_cash_total'] == late
    assert ledger['contributions_total'] == '0.00'
    assert ledger['closing_balance'] == closing


@pytest.mark.asyncio
@pytest.mark.parametrize('successor', [False, True], ids=['ownership-change', 'owner-without-soll'])
async def test_split_reserve_uses_cash_date_ownership_and_never_invents_successor_soll(
        client_a_admin, seed_data, successor):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    old = (await api.get('/api/ownership', params={'unit_id': w['u1']})).json()[0]
    response = await api.patch(f"/api/ownership/{old['id']}", json={'valid_to': '2025-06-30'})
    assert response.status_code == 200, response.text
    if successor:
        person = (await api.post('/api/persons', json={'first_name': 'Clara', 'last_name': 'Successor'})).json()
        new_owner = (await api.post('/api/owner-profiles', json={'person_id': person['id']})).json()['id']
    else:
        new_owner = w['owner_b']
    response = await api.post('/api/ownership', json={'unit_id': w['u1'],
        'owner_profile_id': new_owner, 'valid_from': '2025-07-01'})
    assert response.status_code == 201, response.text
    cash = []
    for owner, when, h, r in ([(new_owner, '2025-08-01', '4200.00', '3600.00')] if successor else [
        (w['owner_a'], '2025-02-01', '4200.00', '3600.00'),
        (new_owner, '2025-08-01', '2800.00', '2400.00')]):
        local = {**w, 'owner_a': owner}
        cash.append(await payment(api, local, h, kind='HAUSGELD_VORSCHUSS', when=when))
        await payment(api, local, r, when=when)
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['contributions_total'] == ('5538.46' if successor else '6000.00')
    if not successor:
        # Raw payment rows retain the different unit sets at each value date.
        raw = {row['payment_id']: row['amount'] for row in ledger['entries'] if row.get('payment_id') in cash}
        assert raw == {cash[0]: '1938.46', cash[1]: '1292.31'}


@pytest.mark.asyncio
@pytest.mark.parametrize('selected,manual,expected', [
    (True, False, '3600.00'), (False, False, '5538.46'), (True, True, '3600.00'),
], ids=['run-own-plan', 'no-run-plan', 'manual-hausgeld-assignment'])
async def test_split_reserve_correction_uses_only_run_plan_and_actual_credited_hausgeld(
        monkeypatch, selected, manual, expected):
    from types import SimpleNamespace as S
    from unittest.mock import AsyncMock
    from uuid import UUID
    from app.services.reserve_ledger_service import ReserveLedgerService
    from app.repositories.economic_plan_repo import EconomicPlanLineRepository
    owner = UUID(int=1)
    run = S(id='run', status='draft', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
            economic_plan_id='plan' if selected else None, management_mandate_id=None)
    plan = S(id='plan', reserve_contribution_total=Decimal('3600'), snapshot_sha256='plan-hash')
    payments = [S(id=kind, payment_type=kind, amount=Decimal(amount), direction='IN',
        value_date=date(2025, 2, 1), owner_profile_id=owner, bank_account_id='bank',
        reverses_id=None, reversed_by_id=None) for kind, amount in [
            ('HAUSGELD_VORSCHUSS', '4200'), ('RUECKLAGENANTEIL', '3600')]]
    service = ReserveLedgerService(None, 'org', 'actor')
    assignment = S(id='assignment', payment_id=payments[0].id, component='CONTRIBUTION',
        amount=Decimal('1000'), reason='Reviewed assignment', reverses_entry_id=None) if manual else None
    service.repo = S(finalized=AsyncMock(return_value=[]), current_scope=AsyncMock(return_value=None),
        plans=AsyncMock(return_value=[plan] if selected else []),
        plan_rows=AsyncMock(return_value=[{'kind': 'PLANNED', 'owner_profile_id': str(owner),
            'amount': '3600.00', 'date': '2025-01-01'}]),
        entries=AsyncMock(return_value=[assignment] if manual else []), payments=AsyncMock(return_value=payments),
        owner_units=AsyncMock(return_value={}), cash_plan=AsyncMock(return_value=plan),
        hausgeld_share=AsyncMock(return_value=Decimal('1938.46')))
    lines = AsyncMock(return_value=[S(line_type='OWNER_ADVANCE', owner_profile_id=owner, planned_amount=Decimal('7800'))])
    monkeypatch.setattr(EconomicPlanLineRepository, 'list_for_plan', lines)
    ledger = await service.read(run)
    assert ledger['contributions_total'] == expected
    if selected:
        lines.assert_awaited_once_with('plan')
    else:
        lines.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_reserve_already_frozen_overcount_is_returned_without_recomputation():
    from copy import deepcopy
    from types import SimpleNamespace as S
    from app.services.reserve_ledger_service import ReserveLedgerService
    ledger = {'contributions_total': '5538.46', 'closing_balance': '5538.46',
        'entries': [{'kind': 'CONTRIBUTION', 'payment_id': 'original-receipt', 'amount': '5538.46'}]}
    run = S(status='finalized', snapshot_payload={'reserve_ledger': deepcopy(ledger)}, snapshot_sha256='retained-hash')
    service = ReserveLedgerService(None, 'org', 'actor')
    service.repo = None  # Any attempt to read current inputs must fail.
    assert await service.read(run) == {**ledger, 'frozen': True}
    assert run.snapshot_payload == {'reserve_ledger': ledger}
    assert run.snapshot_sha256 == 'retained-hash'


@pytest.mark.asyncio
@pytest.mark.parametrize('late_kind,expected,correction', [
    ('reserve-storno', '-1661.54', '1938.46'),
    ('hausgeld-storno', '0.00', '1938.46'),
    ('hausgeld-receipt', '0.00', '-1938.46'),
])
async def test_split_reserve_late_cash_recomputes_the_frozen_period_correction(
        client_a_admin, seed_data, migrator_session, late_kind, expected, correction):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if late_kind != 'hausgeld-receipt':
        hausgeld = await payment(api, w, '4200.00', kind='HAUSGELD_VORSCHUSS')
    reserve = await payment(api, w, '3600.00')
    statement = await finalize(api, w)
    path = f"/api/weg-settlements/{w['run']}"
    frozen = (await api.get(path + '/reserve-ledger')).json()
    assert frozen['closing_balance'] == '3600.00'
    query = text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)')
    payload = await migrator_session.scalar(query, {'id': w['run']})
    if late_kind == 'hausgeld-receipt':
        late_payment = await payment(api, w, '4200.00', kind='HAUSGELD_VORSCHUSS')
    else:
        original = reserve if late_kind == 'reserve-storno' else hausgeld
        response = await api.post(f'/api/payments/{original}/reverse', json={'note': 'Late correction'})
        assert response.status_code == 201, response.text
        late_payment = response.json()['id']
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    ledger_response = await api.get(f"/api/weg-settlements/{response.json()['id']}/reserve-ledger")
    assert ledger_response.status_code == 200, ledger_response.text
    ledger = ledger_response.json()
    assert (await api.get(path + '/reserve-ledger')).json() == frozen
    assert (await api.get(path)).json() == statement
    assert await migrator_session.scalar(query, {'id': w['run']}) == payload
    assert ledger['late_cash_total'] == expected
    assert Decimal(ledger['closing_balance']) == Decimal('3600.00') + Decimal(expected)
    assert ledger['contributions_total'] == '0.00'
    assert any(row.get('payment_id') == late_payment for row in ledger['entries'])
    adjustments = [row for row in ledger['entries'] if row['kind'] == 'LATE_CASH'
                   and not row.get('payment_id')]
    assert len(adjustments) == 1
    assert adjustments[0] == {
        'kind': 'LATE_CASH', 'component': 'CONTRIBUTION', 'date': '2025-12-31',
        'amount': correction, 'owner_profile_id': w['owner_a'],
        'reason': 'Rücklagenanteil bereits im Hausgeld-Soll enthalten', 'correction': 'SPLIT_RESERVE',
        'owner_name': 'Anton Eigentuemer', 'payment_note': None,
        'cost_document_number': None, 'register_sequence_number': None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('late_kind,hausgeld,reserve,opening,expected,closing,share,correction', [
    ('reserve-storno', '4200', '3600', '5538.46', '-3600.00', '1938.46', '-3600.00', '0.00'),
    ('hausgeld-storno', '4200', '3600', '5538.46', '-1938.46', '3600.00', '-1938.46', '0.00'),
    ('reserve-storno', '0', '6000', '6000.00', '-6000.00', '0.00', '-6000.00', '0.00'),
    ('hausgeld-receipt', '4200', '3600', '5538.46', '-1838.46', '3700.00', '46.15', '-1884.61'),
], ids=['reserve-storno', 'hausgeld-storno', 'reserve-only-storno', 'hausgeld-receipt'])
async def test_split_reserve_late_cash_corrects_a_legacy_frozen_overcount(
        monkeypatch, late_kind, hausgeld, reserve, opening, expected, closing, share, correction):
    from copy import deepcopy
    from types import SimpleNamespace as S
    from unittest.mock import AsyncMock
    from uuid import UUID
    from app.services.reserve_ledger_service import ReserveLedgerService
    from app.repositories.economic_plan_repo import EconomicPlanLineRepository
    owner = UUID(int=1)
    plan = S(id='historical-plan', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
    payments = [S(id=identifier, payment_type=kind, amount=Decimal(amount), direction='IN',
        value_date=date(2025, 2, 1), owner_profile_id=owner, bank_account_id='bank',
        reverses_id=None, reversed_by_id=None) for identifier, kind, amount in [
            ('hausgeld', 'HAUSGELD_VORSCHUSS', hausgeld), ('reserve', 'RUECKLAGENANTEIL', reserve)]
        if Decimal(amount)]
    reversed_id = 'reserve' if late_kind == 'reserve-storno' else 'hausgeld'
    late = S(id='late-payment', payment_type='RUECKLAGENANTEIL' if reversed_id == 'reserve' else 'HAUSGELD_VORSCHUSS',
        amount=Decimal('100' if late_kind == 'hausgeld-receipt' else reserve if reversed_id == 'reserve' else hausgeld),
        direction='IN' if late_kind == 'hausgeld-receipt' else 'OUT', value_date=date(2025, 2, 1),
        owner_profile_id=owner, bank_account_id='bank',
        reverses_id=None if late_kind == 'hausgeld-receipt' else reversed_id, reversed_by_id=None)
    payments.append(late)
    frozen = {'economic_plan_id': plan.id, 'contributions_total': opening, 'closing_balance': opening,
        'entries': [{'kind': 'CONTRIBUTION', 'date': '2025-02-01', 'payment_id': identifier,
                     'owner_profile_id': str(owner), 'amount': amount}
                    for identifier, amount in [('hausgeld', '1938.46'), ('reserve', format(Decimal(reserve), '.2f'))]
                    if identifier != 'hausgeld' or Decimal(hausgeld)]}
    historical = S(id='history', status='finalized', period_start=plan.period_start, period_end=plan.period_end,
        snapshot_payload={'reserve_ledger': deepcopy(frozen)}, snapshot_sha256='retained-hash')
    run = S(id='current', status='draft', period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
        economic_plan_id=None, management_mandate_id=None)
    service = ReserveLedgerService(None, 'org', 'actor')
    service.repo = S(finalized=AsyncMock(return_value=[(historical, None)]),
        current_scope=AsyncMock(return_value=None), plans=AsyncMock(return_value=[]),
        entries=AsyncMock(return_value=[]), payments=AsyncMock(return_value=payments),
        owner_units=AsyncMock(return_value={}), cash_plans=AsyncMock(return_value=[plan]),
        hausgeld_share=AsyncMock(return_value=Decimal('46.15')),
        plan_rows=AsyncMock(return_value=[{'kind': 'PLANNED', 'owner_profile_id': str(owner),
            'amount': '3600.00', 'date': '2025-01-01'}]))
    monkeypatch.setattr(EconomicPlanLineRepository, 'advance_totals_per_owner',
        AsyncMock(return_value={owner: Decimal('7800')}))
    ledger = await service.read(run)
    assert historical.snapshot_payload == {'reserve_ledger': frozen}
    assert historical.snapshot_sha256 == 'retained-hash'
    assert (await service.read(historical))['closing_balance'] == opening
    assert (ledger['late_cash_total'], ledger['closing_balance']) == (expected, closing)
    assert ledger['opening_balance'] == opening
    assert next(row['amount'] for row in ledger['entries'] if row.get('payment_id') == late.id) == share
    assert [row['amount'] for row in ledger['entries'] if row['kind'] == 'LATE_CASH'
            and not row.get('payment_id')] == ([correction] if Decimal(correction) else [])


@pytest.mark.asyncio
async def test_split_reserve_late_cash_counts_the_same_periods_annual_and_asset_correction_once(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace as S
    from unittest.mock import AsyncMock
    from uuid import UUID
    from app.services.reserve_ledger_service import ReserveLedgerService
    from app.repositories.economic_plan_repo import EconomicPlanLineRepository
    owner = UUID(int=1)
    plan = S(id='historical-plan', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
    payments = [S(id=identifier, payment_type=kind, amount=Decimal(amount), direction=direction,
        value_date=date(2025, 2, 1), owner_profile_id=owner, bank_account_id='bank',
        reverses_id='reserve' if identifier == 'storno' else None, reversed_by_id=None)
        for identifier, kind, amount, direction in [
            ('hausgeld', 'HAUSGELD_VORSCHUSS', '4200', 'IN'),
            ('reserve', 'RUECKLAGENANTEIL', '3600', 'IN'),
            ('storno', 'RUECKLAGENANTEIL', '3600', 'OUT')]]
    frozen = {'economic_plan_id': plan.id, 'contributions_total': '3600.00', 'closing_balance': '3600.00',
        'entries': [{'kind': 'CONTRIBUTION', 'date': '2025-02-01', 'payment_id': identifier,
                     'owner_profile_id': str(owner), 'amount': amount}
                    for identifier, amount in [('hausgeld', '1938.46'), ('reserve', '3600.00')]] + [
                        {'kind': 'CONTRIBUTION', 'date': '2025-12-31', 'amount': '-1938.46',
                         'owner_profile_id': str(owner), 'correction': 'SPLIT_RESERVE',
                         # Recognized by the structured marker, never by its display text.
                         'reason': 'Früherer Wortlaut der Korrektur'}]}
    history = [S(id=kind, status='finalized', run_type=kind,
        period_start=plan.period_start, period_end=plan.period_end,
        snapshot_payload={'reserve_ledger': deepcopy(frozen)}, snapshot_sha256=kind + '-hash')
        for kind in ('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT')]
    run = S(id='current', status='draft', period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
        economic_plan_id=None, management_mandate_id=None)
    service = ReserveLedgerService(None, 'org', 'actor')
    service.repo = S(finalized=AsyncMock(return_value=[(historical, None) for historical in history]),
        current_scope=AsyncMock(return_value=None), plans=AsyncMock(return_value=[]),
        entries=AsyncMock(return_value=[]), payments=AsyncMock(return_value=payments),
        owner_units=AsyncMock(return_value={}), cash_plans=AsyncMock(return_value=[plan]),
        plan_rows=AsyncMock(return_value=[{'kind': 'PLANNED', 'owner_profile_id': str(owner),
            'amount': '3600.00', 'date': '2025-01-01'}]))
    monkeypatch.setattr(EconomicPlanLineRepository, 'advance_totals_per_owner',
        AsyncMock(return_value={owner: Decimal('7800')}))
    ledger = await service.read(run)
    for historical in history:
        assert historical.snapshot_payload == {'reserve_ledger': frozen}
        assert historical.snapshot_sha256 == historical.run_type + '-hash'
        assert await service.read(historical) == {**frozen, 'frozen': True}
    assert (ledger['late_cash_total'], ledger['closing_balance']) == ('-1661.54', '1938.46')
    assert [row['amount'] for row in ledger['entries'] if row['kind'] == 'LATE_CASH'
            and not row.get('payment_id')] == ['1938.46']


@pytest.mark.asyncio
async def test_split_reserve_late_cash_in_successive_years_includes_earlier_frozen_late_rows(
        client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    reserve = await payment(api, w, '3600.00')
    await finalize(api, w)
    hausgeld = await payment(api, w, '4200.00', kind='HAUSGELD_VORSCHUSS')
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
              for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]],
              'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', report, values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{report}/finalize', json=values)
    assert response.status_code == 200, response.text
    path = f'/api/weg-settlements/{report}/reserve-ledger'
    frozen = (await api.get(path)).json()
    assert any(row.get('payment_id') == hausgeld for row in frozen['entries'])
    query = text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)')
    payload = await migrator_session.scalar(query, {'id': report})
    response = await api.post(f'/api/payments/{reserve}/reverse', json={'note': 'Late reserve correction'})
    assert response.status_code == 201, response.text
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2027-01-01', 'period_end': '2027-12-31'})
    assert response.status_code == 201, response.text
    next_report = response.json()['id']
    ledger_response = await api.get(f'/api/weg-settlements/{next_report}/reserve-ledger')
    assert ledger_response.status_code == 200, ledger_response.text
    ledger = ledger_response.json()
    assert (await api.get(path)).json() == frozen
    assert await migrator_session.scalar(query, {'id': report}) == payload
    assert (frozen['late_cash_total'], ledger['late_cash_total']) == ('0.00', '-1661.54')
    assert ledger['opening_balance'] == frozen['closing_balance'] == '3600.00'
    assert ledger['closing_balance'] == '1938.46'
    assert not any(row.get('payment_id') == hausgeld for row in ledger['entries'])
    assert next(row['amount'] for row in ledger['entries'] if row['kind'] == 'LATE_CASH'
                and not row.get('payment_id')) == '1938.46'
    await attest_fixture(api, 'settlement', next_report, values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{next_report}/finalize', json=values)
    assert response.status_code == 200, response.text
    issued = (await api.get(f'/api/weg-settlements/{next_report}/reserve-ledger')).json()
    assert issued == {**ledger, 'frozen': True, 'contribution_headroom': []}
    assert (await api.get(path)).json() == frozen
    assert await migrator_session.scalar(query, {'id': report}) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize('financing, funded', [
    ('draft', '0.00'), ('split', '1000.00'), ('reversed', '0.00'),
    ('corrected', '1000.00'), ('adjusted', '800.00'),
])
async def test_owner_costs_use_net_reserve_financing_of_the_active_receipt_chain(
        client_a_admin, seed_data, migrator_session, financing, funded):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    if financing == 'draft':
        draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'],
            'direction': 'OUT', 'amount': '1000.00', 'value_date': '2025-02-01',
            'payment_type': 'RUECKLAGENENTNAHME', **evidence})
        assert draft.status_code == 201, draft.text
    else:
        cash = await payment(api, w, '400.00' if financing == 'split' else '1000.00',
                             kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
        if financing == 'split':
            await payment(api, w, '600.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
        elif financing == 'reversed':
            response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Repair payout cancelled'})
            assert response.status_code == 201, response.text
        elif financing == 'adjusted':
            response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
                'component': 'ADJUSTMENT', 'amount': '200.00', 'reason': 'Correct receipt financing',
                **{k.removeprefix('reserve_'): v for k, v in evidence.items()}})
            assert response.status_code == 201, response.text
    receipt = w['ce_garten']
    if financing == 'corrected':
        response = await api.post(f'/api/cost-entries/{receipt}/correction', json={
            'document_number': 'RE-corrected-with-frozen-financing'})
        assert response.status_code == 201, response.text
        receipt = response.json()['id']
        assert (await api.post(f'/api/cost-entries/{receipt}/confirm')).status_code == 200
    statement = await finalize(api, w)
    expected = Decimal('7000.00') - Decimal(funded)
    assert Decimal(statement['total_cost']) == expected
    for key, share in [('owner_a', Decimal('0.6')), ('owner_b', Decimal('0.4'))]:
        line = next(row for row in statement['lines'] if row['line_type'] == 'OWNER_COST_TOTAL'
                    and row['addressee_owner_profile_id'] == w[key])
        assert Decimal(line['amount']) == expected * share
    payload = await migrator_session.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)'),
        {'id': w['run']})
    position = next(row for row in payload['inputs']['cost_positions'] if row['cost_entry_id'] == receipt)
    assert position['gross_amount'] == '4000.00'
    assert position.get('reserve_funded_amount', '0.00') == funded
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['withdrawals_total'] == ('1000.00' if financing == 'adjusted' else funded)
    assert ledger['overrides_total'] == ('200.00' if financing == 'adjusted' else '0.00')


@pytest.mark.asyncio
async def test_finalized_unfinanced_statement_keeps_its_amounts_hash_and_pdf_after_late_financing(
        client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    statement = await finalize(api, w)
    assert statement['total_cost'] == '7000.00'
    payload = await migrator_session.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)'),
        {'id': w['run']})
    assert all('reserve_funded_amount' not in row for row in payload['inputs']['cost_positions'])
    before_pdf = pdf_text((await api.get(f"/api/weg-settlements/{w['run']}/pdf")).content)
    await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    assert (await api.get(f"/api/weg-settlements/{w['run']}")).json() == statement
    assert pdf_text((await api.get(f"/api/weg-settlements/{w['run']}/pdf")).content) == before_pdf
    assert (await api.post(f"/api/weg-settlements/{w['run']}/compute", json=paid(w))).status_code == 409
    assert await migrator_session.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)'),
        {'id': w['run']}) == payload


@pytest.mark.asyncio
async def test_receipt_financing_batch_does_not_expose_foreign_or_missing_costs(
        client_a_admin, client_b_admin, seed_data, app_rw_session):
    import uuid
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    w = await reserve_world(client_a_admin, seed_data)
    await payment(client_a_admin, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
        {'org': str(seed_data['org_b_id'])})
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
        {'actor': str(seed_data['user_b_admin_id'])})
    repo = ReserveLedgerRepository(app_rw_session, seed_data['org_b_id'])
    assert await repo.funded_amounts([uuid.UUID(w['ce_garten']), uuid.uuid4()]) == {}
    for action in ('compute', 'finalize'):
        response = await client_b_admin.post(f"/api/weg-settlements/{w['run']}/{action}", json=paid(w))
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_hausgeld_uses_only_its_frozen_reserve_share_and_reversal_net_cash(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    identifier = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS')
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    first = (await api.get(path)).json()
    assert first['contributions_total'] == '600.00'
    # An unrelated cash movement and the synthetic reserve cost do not fund reserves.
    await payment(api, w, '500.00', kind='SONDERZAHLUNG')
    assert (await api.get(path)).json()['contributions_total'] == '600.00'
    reversed_payment = await api.post(f'/api/payments/{identifier}/reverse', json={'note': 'Incorrect receipt'})
    assert reversed_payment.status_code == 201, reversed_payment.text
    assert (await api.get(path)).json()['contributions_total'] == '0.00'


@pytest.mark.asyncio
async def test_finalized_year_freezes_cash_and_carries_closing_into_next_period(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '20000.00', 'reason': 'Imported balance'})
    await payment(api, w, '6000.00')
    result = await finalize(api, w)
    frozen = (await api.get(path)).json()
    # H=0, R=6000, Soll=7800, planned=3600: Rc=6000, correction=2400.
    assert frozen['closing_balance'] == '23600.00'
    assert frozen['frozen'] is True
    # Preserve the issued opening; expose late cash separately in the next period.
    # Late R 2000: Rc 7800 (capped at Soll), correction 4200, delta -1800, late cash 200.
    late = await payment(api, w, '2000.00', when='2025-12-31')
    assert (await api.get(path)).json() == frozen
    assert (await api.get(f"/api/weg-settlements/{w['run']}")).json()['snapshot_sha256'] == result['snapshot_sha256']
    response = await api.post('/api/economic-plans', json={'property_id': w['property'], 'plan_year': 2026,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    plan = response.json()['id']
    values = {'planned_costs': [], 'reserve_contribution_total': '1200.00'}
    await attest_fixture(api, 'economic_plan', plan, values)
    assert (await api.post(f'/api/economic-plans/{plan}/finalize', json=values)).status_code == 200
    response = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    next_run = response.json()['id']
    ledger = (await api.get(f'/api/weg-settlements/{next_run}/reserve-ledger')).json()
    assert ledger['opening_balance'] == '23600.00'
    assert ledger['opening_source_run_id'] == w['run']
    assert ledger['late_cash_total'] == '200.00'
    assert ledger['closing_balance'] == '23800.00'
    assert next(row for row in ledger['entries'] if row.get('payment_id') == late) == {
        'kind': 'LATE_CASH', 'component': 'CONTRIBUTION', 'date': '2025-12-31',
        'amount': '2000.00', 'payment_id': late, 'bank_account_id': w['bank_giro'],
        'owner_profile_id': w['owner_a'],
        'owner_name': 'Anton Eigentuemer', 'payment_note': None,
        'cost_document_number': None, 'register_sequence_number': None,
    }
    assert [row for row in ledger['entries'] if row['kind'] == 'LATE_CASH' and not row.get('payment_id')] == [{
        'kind': 'LATE_CASH', 'component': 'CONTRIBUTION', 'date': '2025-12-31',
        'amount': '-1800.00', 'owner_profile_id': w['owner_a'],
        'reason': 'Rücklagenanteil bereits im Hausgeld-Soll enthalten', 'correction': 'SPLIT_RESERVE',
        'owner_name': 'Anton Eigentuemer', 'payment_note': None,
        'cost_document_number': None, 'register_sequence_number': None,
    }]
    next_world = {**w, 'run': next_run}
    next_result = await finalize(api, next_world)
    lines = next_result['lines']
    assert next(row['sort_order'] for row in lines if row['line_type'] == 'RESERVE_OPENING') < next(
        row['sort_order'] for row in lines if row['label'] == 'Nachträgliche Buchungen zum abgeschlossenen Jahr')
    closing_note = next(row['note'] for row in lines if row['line_type'] == 'RESERVE_CLOSING')
    assert 'nachträgliche Buchungen' in closing_note
    assert (await api.get(f'/api/weg-settlements/{next_run}/reserve-ledger')).json()['closing_balance'] == '23800.00'
    # A report for the issued year consumes the exact same frozen ledger.
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    report_values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
                      for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]],
                     'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', report, report_values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{report}/finalize', json=report_values)
    assert response.status_code == 200, response.text
    assert next(row['amount'] for row in response.json()['lines'] if row['line_type'] == 'RESERVE_CLOSING') == '23600.00'
    stored = await migrator_session.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)'), {'id': report})
    assert stored['reserve_ledger']['closing_balance'] == '23600.00'
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    await attest_fixture(api, 'settlement', report, report_values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{report}/finalize', json=report_values)
    assert response.status_code == 200, response.text
    document = await api.get(f'/api/weg-settlements/asset-reports/{report}/pdf')
    assert document.status_code == 200, document.text
    content = pdf_text(document.content)
    assert 'Nachträgliche Buchungen zum abgeschlossenen Jahr' in content
    assert '200,00 EUR' in content
    assert '23.600,00 EUR' in content and '23.800,00 EUR' in content
    assert 'nachträgliche Buchungen' in content
    amounts = {line['line_type']: Decimal(line['amount']) for line in response.json()['lines']}
    assert amounts['RESERVE_OPENING'] + amounts['RESERVE_CONTRIBUTION'] - amounts['RESERVE_WITHDRAWAL'] + amounts['RESERVE_LATE_CASH'] == amounts['RESERVE_CLOSING'] == Decimal('23800.00')


async def ownership_plan(api, w, year, reserve='365.00'):
    response = await api.post('/api/economic-plans', json={
        'property_id': w['property'], 'plan_year': year,
        'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
    assert response.status_code == 201, response.text
    plan = response.json()['id']
    values = {'planned_costs': [{'category_code': 'WEG_VERWALTERKOSTEN', 'label': 'Annual cost',
        'planned_amount': '365.00', 'cost_group': 'NICHT_UMLAGEFAEHIG'}], 'reserve_contribution_total': reserve}
    await attest_fixture(api, 'economic_plan', plan, values)
    assert (await api.post(f'/api/economic-plans/{plan}/finalize', json=values)).status_code == 200
    return plan


@pytest.mark.asyncio
async def test_buyer_hausgeld_uses_unit_ratio_and_allows_attestation_compute_and_finalize(ownership_world):
    w = ownership_world
    async with ownership_client(w) as api:
        seller = await create_owner(api, w)
        plan = await ownership_plan(api, w, 2026)
        await end_owner(api, seller)
        await create_owner(api, w, 'buyer', valid_from='2026-07-01')
        response = await api.post('/api/bank-accounts', json={
            'property_id': w['property'], 'account_type': 'LAUFEND', 'label': 'Synthetic change-month cash'})
        assert response.status_code == 201, response.text
        cash_world = {'bank_giro': response.json()['id'], 'owner_a': w['buyer']}
        buyer_payment = await payment(api, cash_world, '100.00', kind='HAUSGELD_VORSCHUSS', when='2026-08-05')
        response = await api.post('/api/weg-settlements', json={
            'property_id': w['property'], 'economic_plan_id': plan,
            'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        response = await api.get(f'/api/weg-settlements/{run}/reserve-ledger')
        assert response.status_code == 200, response.text
        assert response.json()['contributions_total'] == '50.00'
        assert next(row['amount'] for row in response.json()['entries'] if row.get('payment_id') == buyer_payment) == '50.00'
        # Attribute each party's cash using ownership on its value date.
        for payer, when in [('seller', '2026-06-30'), ('buyer', '2026-07-15')]:
            await payment(api, {**cash_world, 'owner_a': w[payer]}, '10.00', kind='HAUSGELD_VORSCHUSS', when=when)
        values = {'paid_advances': [{'owner_profile_id': w[payer], 'paid_total': '0.00'} for payer in ('seller', 'buyer')]}
        await attest_fixture(api, 'settlement', run, values)
        for action in ('compute', 'finalize'):
            response = await api.post(f'/api/weg-settlements/{run}/{action}', json=values)
            assert response.status_code == 200, response.text
        assert (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()['contributions_total'] == '60.00'
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        assert response.status_code == 201, response.text
        report = response.json()['id']
        report_values = {'bank_balances': [{'bank_account_id': cash_world['bank_giro'], 'label': 'Cash',
            'account_type': 'LAUFEND', 'balance': '120.00'}], 'receivables': [], 'payables': []}
        await attest_fixture(api, 'settlement', report, report_values)
        for action in ('compute', 'finalize'):
            response = await api.post(f'/api/weg-settlements/asset-reports/{report}/{action}', json=report_values)
            assert response.status_code == 200, response.text
        # The buyer's next-year cash uses the unit's current cash-year ratio.
        next_plan = await ownership_plan(api, w, 2027, reserve='1095.00')
        await payment(api, cash_world, '100.00', kind='HAUSGELD_VORSCHUSS', when='2027-02-01')
        response = await api.post('/api/weg-settlements', json={
            'property_id': w['property'], 'economic_plan_id': next_plan,
            'period_start': '2027-01-01', 'period_end': '2027-12-31'})
        assert response.status_code == 201, response.text
        response = await api.get(f"/api/weg-settlements/{response.json()['id']}/reserve-ledger")
        assert response.status_code == 200, response.text
        assert response.json()['contributions_total'] == '75.00'
        assert response.json()['late_cash_total'] == '0.00'


@pytest.mark.asyncio
async def test_unknown_hausgeld_payer_is_accepted_and_listed_without_plan_assignment(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    person = await api.post('/api/persons', json={'first_name': 'Unknown', 'last_name': 'Synthetic payer'})
    assert person.status_code == 201, person.text
    owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
    assert owner.status_code == 201, owner.text
    response = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'IN',
        'amount': '100.00', 'value_date': '2025-08-05', 'payment_type': 'HAUSGELD_VORSCHUSS',
        'owner_profile_id': owner.json()['id'], 'period_year': 2025, 'period_month': 8})
    assert response.status_code == 201, response.text
    cash = response.json()['id']
    response = await api.post(f'/api/payments/{cash}/book')
    assert response.status_code == 200, response.text
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['unlinked_payment_ids'] == [cash]
    assert next(row for row in ledger['entries'] if row.get('payment_id') == cash)['kind'] == 'UNASSIGNED'


@pytest.mark.asyncio
@pytest.mark.parametrize('known_owner', [False, True])
async def test_hausgeld_booked_before_plan_stays_unassigned_until_resolved(client_a_admin, seed_data, migrator_session, known_owner):
    api = client_a_admin
    w = _seed_weg(seed_data)
    if not known_owner:
        person = await api.post('/api/persons', json={'first_name': 'Unknown', 'last_name': 'Synthetic payer'})
        owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
        w['owner_a'] = owner.json()['id']
    cash = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', when='2025-08-05')
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    path = f'/api/weg-settlements/{report}/reserve-ledger'
    ledger = (await api.get(path)).json()
    assert ledger['unlinked_payment_ids'] == [cash]
    assert next(row for row in ledger['entries'] if row.get('payment_id') == cash)['amount'] == '1300.00'
    assert next(row for row in ledger['entries'] if row.get('payment_id') == cash)['kind'] == 'UNASSIGNED'
    audit = await migrator_session.scalar(text("SELECT payload_diff FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='payment.booked'"), {'id': cash})
    assert audit['reserve_plan_unresolved'] is True
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Girokonto', 'account_type': 'LAUFEND', 'balance': '1300.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Rücklagenkonto', 'account_type': 'RUECKLAGE', 'balance': '0.00'},
    ], 'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', report, values)
    blocked = await api.post(f'/api/weg-settlements/asset-reports/{report}/compute', json=values)
    assert blocked.status_code == 422
    assert blocked.json()['detail']['code'] == 'RESERVE_ASSIGNMENT_REQUIRED'
    await _create_and_finalize_plan(api, w)
    ledger = (await api.get(path)).json()
    if not known_owner:
        assert ledger['unlinked_payment_ids'] == [cash]
        assigned = await api.post(path + '/overrides', json={
            'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': '600.00',
            'reason': 'Bank statement and unit assignment reviewed'})
        assert assigned.status_code == 201, assigned.text
        ledger = (await api.get(path)).json()
        row = next(row for row in ledger['entries'] if row.get('payment_id') == cash)
        assert row['reason'] == 'Bank statement and unit assignment reviewed'
        duplicate = await api.post(path + '/overrides', json={
            'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': '600.00', 'reason': 'Duplicate'})
        assert duplicate.status_code == 422
        overlapping = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2025-07-01', 'period_end': '2025-12-31'})
        assert overlapping.status_code == 201, overlapping.text
        other = (await api.get(f"/api/weg-settlements/{overlapping.json()['id']}/reserve-ledger")).json()
        assert other['unlinked_payment_ids'] == []
        assert other['contributions_total'] == '600.00'
    assert ledger['unlinked_payment_ids'] == []
    assert ledger['contributions_total'] == '600.00'
    await attest_fixture(api, 'settlement', report, values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{report}/compute', json=values)
    assert response.status_code == 200, response.text
    if not known_owner:
        response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Cancel reviewed assignment'})
        assert response.status_code == 201, response.text
        ledger = (await api.get(path)).json()
        assert ledger['contributions_total'] == '0.00' and ledger['unlinked_payment_ids'] == []


@pytest.mark.asyncio
async def test_cash_ratio_excludes_units_owned_only_before_or_after_value_date(client_a_admin, seed_data, migrator_session):
    from uuid import UUID
    from types import SimpleNamespace
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository

    api = client_a_admin
    w = _seed_weg(seed_data)
    cash = UUID(await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', when='2025-08-05'))
    await migrator_session.execute(text("""INSERT INTO ownership_history
        (organization_id,unit_id,owner_profile_id,valid_from,valid_to)
        VALUES (:org,:unit,:owner,'2000-01-01','2025-07-01'),
               (:org,:unit,:owner,'2025-09-01',NULL)"""),
        {'org': seed_data['org_a_id'], 'unit': w['u2'], 'owner': w['owner_a']})
    units = await ReserveLedgerRepository(migrator_session, seed_data['org_a_id']).owner_units(
        [SimpleNamespace(id=cash)])
    assert units == {cash: {w['u1']}}
    # Unequal unit ratios expose an accidental historical/future unit union.
    planned = [{'unit_id': w['u1'], 'amount': '3600.00'}, {'unit_id': w['u2'], 'amount': '2400.00'}]
    owners = [{'unit_id': w['u1'], 'annual_advance': '7100.00'}, {'unit_id': w['u2'], 'annual_advance': '5900.00'}]
    import json
    amount = await migrator_session.scalar(text('SELECT reserve_hausgeld_share(1300,:units,CAST(:planned AS jsonb),CAST(:owners AS jsonb))'),
        {'units': list(units[cash]), 'planned': json.dumps(planned), 'owners': json.dumps(owners)})
    assert amount == Decimal('659.15')


@pytest.mark.asyncio
@pytest.mark.parametrize('half_year_first', [False, True])
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_imported_opening_blocks_overlapping_half_and_full_year_creation(
        client_a_admin, seed_data, monkeypatch, half_year_first, database_backstop):
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    api = client_a_admin
    w = _seed_weg(seed_data)
    imported_end, refused_end = ('2025-06-30', '2025-12-31') if half_year_first else ('2025-12-31', '2025-06-30')
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': imported_end})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    response = await api.post(f'/api/weg-settlements/{report}/reserve-ledger/overrides', json={
        'component': 'OPENING', 'amount': '1000.00', 'reason': 'Synthetic first-period opening'})
    assert response.status_code == 201, response.text
    assert (await api.get(f'/api/weg-settlements/{report}/reserve-ledger')).json()['opening_balance'] == '1000.00'
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_later_opening_import', AsyncMock(return_value=False))
    refused = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': refused_end})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_EXISTS'
    # An identical period is still valid for sibling and correction runs.
    sibling = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': imported_end})
    assert sibling.status_code == 201, sibling.text
    assert (await api.get(f'/api/weg-settlements/{report}/reserve-ledger')).json()['opening_balance'] == '1000.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence', ['ledger', 'payout', 'statement'])
async def test_populated_reserve_downgrade_refuses_without_losing_state(client_a_admin, seed_data, evidence):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from tests.conftest import MIGRATOR_SYNC_URL, clear_disposable_source_context
    from tests.test_session_review_access import _chain_snapshot

    w = await reserve_world(client_a_admin, seed_data)
    if evidence == 'ledger':
        response = await client_a_admin.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'OPENING', 'amount': '1.00', 'reason': 'Retained synthetic opening'})
        assert response.status_code == 201, response.text
    elif evidence == 'payout':
        await payment(client_a_admin, w, '1.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    else:
        sources = await reserve_evidence(client_a_admin, w)
        response = await client_a_admin.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'ADJUSTMENT', 'amount': '-1.00', 'reason': 'Retained statement evidence',
            **{key.removeprefix('reserve_'): value for key, value in sources.items()}})
        assert response.status_code == 201, response.text
        result = await finalize(client_a_admin, w)
        assert any(line['line_type'] == 'RESERVE_ADJUSTMENT' for line in result['lines'])
    path = Path(__file__).parents[1] / 'migrations/versions/0149_maintenance_reserve_ledger.py'
    spec = importlib.util.spec_from_file_location('reserve_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as outer:
            # Isolate each refusal from evidence retained by earlier tests.
            if evidence in ('payout', 'statement'):
                connection.execute(text('TRUNCATE public.reserve_ledger_entry'))
            if evidence == 'statement':
                connection.execute(text('TRUNCATE public.payment CASCADE'))
                assert connection.scalar(text("""SELECT EXISTS(SELECT 1 FROM settlement_line l
                    JOIN settlement_run r ON r.id=l.settlement_run_id
                    WHERE r.id=CAST(:run AS uuid) AND r.status='finalized'
                      AND l.line_type='RESERVE_ADJUSTMENT')"""), {'run': w['run']})
            before = _chain_snapshot(connection)
            message = {'ledger': 'Retain reserve ledger evidence', 'payout': 'Retain reserve payout bookings',
                       'statement': 'Retain reserve statement evidence'}[evidence]
            with pytest.raises(DBAPIError, match=message):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            assert _chain_snapshot(connection) == before
            with connection.connection.cursor() as cursor:
                clear_disposable_source_context(cursor)
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert connection.scalar(text("SELECT to_regclass('public.reserve_ledger_entry')")) is None
            outer.rollback()
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("correction_finalized", [False, True])
async def test_late_storno_and_hausgeld_keep_original_ratio_and_are_carried_once(client_a_admin, seed_data, correction_finalized):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    original = await payment(api, w, '100.00')
    await finalize(api, w)
    response = await api.post(f'/api/payments/{original}/reverse', json={'note': 'Late synthetic cancellation'})
    assert response.status_code == 201, response.text
    storno = response.json()['id']
    response = await api.post(f"/api/economic-plans/{w['plan']}/correction")
    assert response.status_code == 201, response.text
    if correction_finalized:
        correction = response.json()['id']
        values = {'planned_costs': [], 'reserve_contribution_total': '1200.00'}
        await attest_fixture(api, 'economic_plan', correction, values)
        assert (await api.post(f'/api/economic-plans/{correction}/finalize', json=values)).status_code == 200
    late = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', when='2025-12-31')
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    for year, opening, late_total in [(2026, '100.00', '500.00'), (2027, '600.00', '0.00')]:
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        ledger = (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()
        assert ledger['opening_balance'] == opening
        assert ledger['late_cash_total'] == late_total
        assert ledger['closing_balance'] == '600.00'
        if year == 2026:
            cash = {row['payment_id']: row for row in ledger['entries'] if row.get('payment_id')}
            assert cash[storno]['amount'] == '-100.00' and cash[storno]['kind'] == 'LATE_CASH'
            assert cash[late]['amount'] == '600.00' and cash[late]['date'] == '2025-12-31'
        else:
            assert not [row for row in ledger['entries'] if row['kind'] == 'LATE_CASH']
        await attest_fixture(api, 'settlement', run, values)
        for action in ('compute', 'finalize'):
            response = await api.post(f'/api/weg-settlements/asset-reports/{run}/{action}', json=values)
            assert response.status_code == 200, response.text
            assert next(row['amount'] for row in response.json()['lines'] if row['line_type'] == 'RESERVE_CLOSING') == '600.00'
            if year == 2026:
                lines = response.json()['lines']
                assert next(row['sort_order'] for row in lines if row['line_type'] == 'RESERVE_OPENING') < next(
                    row['sort_order'] for row in lines if row['label'] == 'Nachträgliche Buchungen zum abgeschlossenen Jahr')
    assert (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()['closing_balance'] == '100.00'


@pytest.mark.asyncio
async def test_late_payout_can_be_funded_and_its_later_storno_keeps_the_evidence(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await finalize(api, w)
    response = await api.post('/api/meetings', json={'property_id': w['property'], 'meeting_date': '2025-01-15', 'meeting_type': 'ORDENTLICH'})
    assert response.status_code == 201, response.text
    meeting = response.json()['id']
    assert (await api.post(f'/api/meetings/{meeting}/hold')).status_code == 200
    response = await api.post('/api/meetings/resolutions', json={'meeting_id': meeting, 'wording': 'Fund synthetic late repair',
        'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    assert response.status_code == 201, response.text
    response = await api.post(f"/api/meetings/resolutions/{response.json()['id']}/register")
    assert response.status_code == 201, response.text
    register = response.json()['id']
    cash = await payment(api, w, '10.00', kind='RUECKLAGENENTNAHME', direction='OUT', when='2025-12-31',
        evidence={'reserve_cost_entry_id': w['ce_garten'], 'reserve_register_entry_id': register})
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    for year, late, closing in [(2026, '-10.00', '-10.00'), (2027, '10.00', '0.00')]:
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        path = f'/api/weg-settlements/{run}/reserve-ledger'
        if year == 2026:
            assert (await api.get(path)).json()['unlinked_payment_ids'] == []
            response = await api.post(path + '/overrides', json={'component': 'FUNDING', 'amount': '0.00',
                'reason': 'Synthetic late payout evidence', 'payment_id': cash,
                'cost_entry_id': w['ce_garten'], 'register_entry_id': register})
            assert response.status_code == 201, response.text
        ledger = (await api.get(path)).json()
        assert ledger['late_cash_total'] == late
        assert ledger['closing_balance'] == closing
        assert ledger['unlinked_payment_ids'] == []
        assert next(row['register_entry_id'] for row in ledger['entries'] if row['kind'] == 'LATE_CASH') == register
        await attest_fixture(api, 'settlement', run, values)
        response = await api.post(f'/api/weg-settlements/asset-reports/{run}/finalize', json=values)
        assert response.status_code == 200, response.text
        if year == 2026:
            response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Late payout cancelled'})
            assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_typed_reserve_totals_require_reason_and_persist_an_audited_delta(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    values = {**paid(w), 'reserve_opening_balance': '1000.00'}
    response = await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement', 'subject_id': w['run'],
        'values': values, 'confirmed_complete': True, 'reason': 'Reviewed records'})
    assert response.status_code == 422, response.text
    values['reserve_override_reason'] = 'Imported bank-confirmed opening'
    await attest_fixture(api, 'settlement', w['run'], values)
    unchanged = await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement',
        'subject_id': w['run'], 'values': values, 'confirmed_complete': True, 'reason': 'Reviewed records'})
    assert unchanged.status_code == 201, unchanged.text
    response = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert response.status_code == 200, response.text
    count = await migrator_session.scalar(text('SELECT count(*) FROM reserve_ledger_entry WHERE settlement_run_id=CAST(:id AS uuid)'), {'id': w['run']})
    assert count == 1
    audits = await migrator_session.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='reserve.override_added'"), {'id': w['run']})
    assert audits == 1


@pytest.mark.asyncio
async def test_new_override_invalidates_coverage_and_cannot_be_edited(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await attest_fixture(api, 'settlement', w['run'], paid(w))
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        **evidence, 'component': 'ADJUSTMENT', 'amount': '-1.00', 'reason': 'Bank fee correction'})
    assert response.status_code == 201, response.text
    assert (await api.post(f"/api/weg-settlements/{w['run']}/finalize", json=paid(w))).status_code == 422
    with pytest.raises(DBAPIError, match='append-only'):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE reserve_ledger_entry SET reason='changed' WHERE id=CAST(:id AS uuid)"), response.json())
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert Decimal(ledger['closing_balance']) == Decimal('-1.00') and ledger['overdrawn']


@pytest.mark.asyncio
async def test_withdrawal_requires_cash_cost_and_registered_resolution(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post('/api/meetings', json={'property_id': w['property'], 'meeting_date': '2025-01-15', 'meeting_type': 'ORDENTLICH'})
    assert response.status_code == 201, response.text
    meeting = response.json()['id']
    assert (await api.post(f'/api/meetings/{meeting}/hold')).status_code == 200
    response = await api.post('/api/meetings/resolutions', json={'meeting_id': meeting, 'wording': 'Fund garden repairs from reserves',
        'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    assert response.status_code == 201, response.text
    response = await api.post(f"/api/meetings/resolutions/{response.json()['id']}/register")
    assert response.status_code == 201, response.text
    register = response.json()['id']
    cost = (await api.get(f"/api/cost-entries/{w['ce_garten']}")).json()
    response = await api.post('/api/cost-entries', json={
        **{key: cost[key] for key in ('property_id', 'cost_category_id', 'invoice_date',
            'service_period_from', 'service_period_to', 'gross_amount', 'owner_distribution_key',
            'tenant_distribution_key', 'tenant_apportionable')}, 'document_number': 'RE-2025-Ä-42'})
    assert response.status_code == 201, response.text
    w['ce_garten'] = response.json()['id']
    response = await api.post(f"/api/cost-entries/{w['ce_garten']}/confirm")
    assert response.status_code == 200, response.text
    evidence = {'reserve_cost_entry_id': w['ce_garten'], 'reserve_register_entry_id': register}
    cash = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    assert (await api.get(path)).json()['withdrawals_total'] == '1000.00'
    assert (await api.get(path)).json()['unlinked_payment_ids'] == []
    body = {'component': 'FUNDING', 'amount': '0.00', 'reason': 'Executed garden repair resolution',
            'payment_id': cash, 'cost_entry_id': w['ce_garten'], 'register_entry_id': register}
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    assert (await api.post(path + '/overrides', json=body)).status_code == 409
    ledger = (await api.get(path)).json()
    assert ledger['withdrawals_total'] == '1000.00'
    assert any(row.get('register_entry_id') == register for row in ledger['entries'])
    row = next(row for row in ledger['entries'] if row.get('register_entry_id') == register)
    assert row['cost_document_number'] == 'RE-2025-Ä-42'
    assert row['register_sequence_number'] == 1

    # Storno frees the funding limit for the corrected booking.
    response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Corrected payout'})
    assert response.status_code == 201, response.text
    replacement = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    body['payment_id'] = replacement
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    assert (await api.get(path)).json()['withdrawals_total'] == '1000.00'


@pytest.mark.asyncio
async def test_reversed_payout_retains_cash_and_evidence_without_blocking_finalization(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    cash = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Payout cancelled before allocation'})
    assert response.status_code == 201, response.text
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['withdrawals_total'] == '0.00'
    assert ledger['unlinked_payment_ids'] == []
    assert len([row for row in ledger['entries'] if row['kind'] == 'WITHDRAWAL']) == 2
    await finalize(api, w)


@pytest.mark.asyncio
async def test_reserve_payout_refuses_rental_accounts_and_personal_counterparties(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    body = {'bank_account_id': w['bank_giro'], 'payment_type': 'RUECKLAGENENTNAHME',
            'amount': '10.00', 'value_date': '2025-02-01', 'direction': 'OUT'}
    assert (await api.post('/api/payments', json={**body, 'owner_profile_id': w['owner_a']})).status_code == 422
    assert (await api.post('/api/payments', json={**body, 'direction': 'IN'})).status_code == 422
    prop = await api.post('/api/properties', json={'name': 'Rental payout rejection', 'management_type': 'MIET'})
    assert prop.status_code == 201, prop.text
    bank = await api.post('/api/bank-accounts', json={'property_id': prop.json()['id'], 'account_type': 'LAUFEND', 'label': 'Rental account'})
    assert bank.status_code == 201, bank.text
    assert (await api.post('/api/payments', json={**body, 'bank_account_id': bank.json()['id']})).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('signed,amount', [('100.00', '5000.00'), ('100.00', '-250.00'), ('-100.00', '-5000.00'), ('-100.00', '250.00')])
async def test_service_rejects_unassigned_amount_before_writing(signed, amount):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.services.reserve_ledger_service import ReserveLedgerService

    service = ReserveLedgerService(None, 'org', 'actor')
    service.read = AsyncMock(return_value={'frozen': False, 'entries': [
        {'kind': 'UNASSIGNED', 'payment_id': 'cash', 'amount': signed},
    ]})
    service.repo.create = AsyncMock(side_effect=AssertionError('Invalid assignment reached persistence'))
    with pytest.raises(HTTPException) as refused:
        await service.add(SimpleNamespace(id='run', property_id='property', period_start=None, period_end=None), 'CONTRIBUTION',
                          Decimal(amount), 'Reviewed receipt', payment_id='cash')
    assert refused.value.status_code == 422
    assert refused.value.detail['code'] == 'RESERVE_ASSIGNMENT_EXCEEDS_PAYMENT'
    service.repo.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('direction,amount', [('IN', '5000.00'), ('IN', '-250.00'), ('OUT', '-5000.00'), ('OUT', '250.00')])
async def test_unassigned_reserve_amount_cannot_exceed_or_reverse_payment(client_a_admin, seed_data, migrator_session, direction, amount):
    api = client_a_admin
    w = _seed_weg(seed_data)
    cash = await payment(api, w, '100.00', kind='HAUSGELD_VORSCHUSS', direction=direction, when='2025-08-05')
    # Remove the plan ratio: this is a cash assignment, not a general adjustment.
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-08-01', 'period_end': '2025-08-31'})
    assert response.status_code == 201, response.text
    path = f"/api/weg-settlements/{response.json()['id']}/reserve-ledger"
    before = (await api.get(path)).json()
    assert cash in before['unlinked_payment_ids']
    refused = await api.post(path + '/overrides', json={
        'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': amount, 'reason': 'Reviewed bank receipt'})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_ASSIGNMENT_EXCEEDS_PAYMENT'
    assert (await api.get(path)).json() == before
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused_write:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,payment_id,created_by)
                VALUES (:org,:property,:run,'2025-08-31','CONTRIBUTION',:amount,'Reviewed receipt',:payment,:actor)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': path.split('/')[3],
                 'amount': Decimal(amount), 'payment': cash, 'actor': seed_data['user_a_admin_id']})
    assert refused_write.value.orig.sqlstate == '23514'
    assert refused_write.value.orig.__cause__.constraint_name == 'reserve_assignment_amount'


@pytest.mark.asyncio
@pytest.mark.parametrize('direction,amount', [('IN', '0.00'), ('IN', '25.00'), ('IN', '100.00'), ('OUT', '-25.00'), ('OUT', '-100.00')])
async def test_unassigned_reserve_accepts_zero_partial_and_full_signed_payment(client_a_admin, seed_data, direction, amount):
    api = client_a_admin
    w = _seed_weg(seed_data)
    cash = await payment(api, w, '100.00', kind='HAUSGELD_VORSCHUSS', direction=direction, when='2025-08-05')
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    path = f"/api/weg-settlements/{report.json()['id']}/reserve-ledger"
    body = {'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': amount, 'reason': 'Reviewed bank receipt'}
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == amount
    assert ledger['unlinked_payment_ids'] == []
    # One retained assignment per payment also bounds the aggregate across drafts.
    duplicate = await api.post(path + '/overrides', json=body)
    assert duplicate.status_code == 422
    assert duplicate.json()['detail']['code'] == 'RESERVE_ASSIGNMENT_REQUIRED'


@pytest.mark.asyncio
async def test_reserve_payer_without_cost_or_plan_lines_is_listed_with_notice(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await payment(api, w, '600.00')
    person = await api.post('/api/persons', json={'first_name': 'Synthetic', 'last_name': 'Reserve payer without unit'})
    owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
    outsider = owner.json()['id']
    await payment(api, {**w, 'owner_a': outsider}, '500.00')
    result = await finalize(api, w)
    reserves = [line for line in result['lines'] if line['line_type'] == 'RESERVE_CONTRIBUTION' and line['addressee_owner_profile_id']]
    assert sum(Decimal(line['amount']) for line in reserves) == Decimal('1100.00')
    line = next(line for line in reserves if line['addressee_owner_profile_id'] == outsider)
    assert line['amount'] == '500.00'
    assert 'ohne Kosten- oder Planposition' in line['note']
    document = await api.get(f"/api/weg-settlements/{w['run']}/pdf")
    assert 'ohne Kosten- oder Planposition' in pdf_text(document.content)


def pdf_text(data):
    import io
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as document:
        return '\n'.join(page.extract_text() or '' for page in document.pages)


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['weg', 'asset'])
async def test_negative_reserve_is_explicitly_flagged_in_pdf(client_a_admin, seed_data, family):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if family == 'asset':
        report = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        assert report.status_code == 201, report.text
        w['run'] = report.json()['id']
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '-1000.00', 'reason': 'Imported opening overdraft'})
    assert response.status_code == 201, response.text
    if family == 'weg':
        await finalize(api, w)
        url = f"/api/weg-settlements/{w['run']}/pdf"
    else:
        values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
                  for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
        await attest_fixture(api, 'settlement', w['run'], values)
        response = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
        assert response.status_code == 200, response.text
        url = f"/api/weg-settlements/asset-reports/{w['run']}/pdf"
    document = await api.get(url)
    assert document.status_code == 200, document.text
    content = pdf_text(document.content)
    assert 'Die Rücklage ist überzogen.' in content
    assert '-1.000,00 EUR' in content
    if family == 'asset':
        assert 'Zuführungen' in content
        assert 'Erhaltungsrücklage' in content


@pytest.mark.asyncio
async def test_reserve_migration_roundtrip_below_oldest_affected_revision(migrator_session):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from app.deployment import TARGET
    from tests.conftest import MIGRATOR_SYNC_URL, clear_disposable_source_context
    graph = ScriptDirectory('migrations')
    revisions = list(graph.iterate_revisions(TARGET, '0091'))
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            with connection.connection.cursor() as cursor:
                clear_disposable_source_context(cursor)
            signatures = ['financial_computation_source_digest(uuid,uuid,uuid,boolean)',
                          'validate_accounting_binding_baseline(public.accounting_scope_binding)',
                          'organization_retention_catalog()']
            def definitions():
                return {signature: connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),
                    {'signature': signature}) for signature in signatures}
            before = definitions()
            with Operations.context(MigrationContext.configure(connection)):
                for revision in revisions:
                    from tests.migration_rehearsal import downgrade_rehearsal_revision
                    downgrade_rehearsal_revision(revision)
                    if revision.revision == '0149':
                        assert connection.scalar(text("SELECT to_regclass('reserve_ledger_entry')")) is None
                        assert connection.scalar(text("SELECT to_regprocedure('recheck_reserve_computations()')")) is None
                        assert 'RUECKLAGENENTNAHME' not in connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='payment'::regclass AND conname='ck_payment_ck_payment_type'"))
                        assert 'RESERVE_LATE_CASH' not in connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='settlement_line'::regclass AND conname='ck_settlement_line_ck_settlement_line_line_type'"))
                for revision in reversed(revisions):
                    revision.module.upgrade()
            assert definitions() == before
            transaction.rollback()
    finally:
        engine.dispose()




@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['both', 'cost', 'resolution'])
async def test_reserve_payout_requires_both_references_at_booking(client_a_admin, seed_data, migrator_session, missing):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '100.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME',
        **({} if missing == 'both' else {k: v for k, v in evidence.items() if ('cost' in k) != (missing == 'cost')})})
    assert draft.status_code == 201, draft.text
    path = f"/api/payments/{draft.json()['id']}"
    refused = await api.post(path + '/book')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    assert (await api.get(path)).json()['status'] == 'draft'
    with pytest.raises(DBAPIError) as database_refusal:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE payment SET status='booked',booked_at=now(),booked_by=:actor,retention_until='2034-12-31' WHERE id=CAST(:id AS uuid)"),
                {'actor': seed_data['user_a_admin_id'], 'id': draft.json()['id']})
    assert database_refusal.value.orig.sqlstate == '23514'
    linked = await api.patch(path, json=evidence)
    assert linked.status_code == 200, linked.text
    assert (await api.post(path + '/book')).status_code == 200
    with pytest.raises(DBAPIError, match='GoBD'):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE payment SET reserve_cost_entry_id=NULL WHERE id=CAST(:id AS uuid)"), {'id': draft.json()['id']})
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['unlinked_payment_ids'] == []
    assert ledger['withdrawals_total'] == '100.00'
    assert next(row for row in ledger['entries'] if row.get('payment_id') == draft.json()['id'])['register_entry_id'] == evidence['reserve_register_entry_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['both', 'cost', 'resolution'])
@pytest.mark.parametrize('component', ['WITHDRAWAL', 'ADJUSTMENT'])
async def test_typed_withdrawal_correction_requires_both_references(client_a_admin, seed_data, missing, component):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    body = {'component': component, 'amount': '-1200.00' if component == 'ADJUSTMENT' else '1200.00',
            'reason': 'Correct documented reserve movement'}
    partial = {} if missing == 'both' else {k: v for k, v in evidence.items() if ('cost' in k) != (missing == 'cost')}
    refused = await api.post(path, json={**body, **partial})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    accepted = await api.post(path, json={**body, **evidence})
    assert accepted.status_code == 201, accepted.text


@pytest.mark.asyncio
async def test_opening_import_is_only_allowed_in_first_ledger_year(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    body = {'component': 'OPENING', 'amount': '1000.00', 'reason': 'Imported opening balance'}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    assert (await api.post(path, json=body)).status_code == 201
    await finalize(api, w)
    run = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert run.status_code == 201, run.text
    refused = await api.post(f"/api/weg-settlements/{run.json()['id']}/reserve-ledger/overrides", json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'


@pytest.mark.asyncio
async def test_earlier_draft_blocks_opening_import_and_carry_equals_frozen_closing(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    run = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert run.status_code == 201, run.text
    path = f"/api/weg-settlements/{run.json()['id']}/reserve-ledger"
    for amount in ('5000.00', '7.00'):
        refused = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': amount,
            'reason': 'Must not import above an earlier draft'})
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'
    await payment(api, w, '123.45')
    await finalize(api, w)
    frozen = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    carried = (await api.get(path)).json()
    assert carried['opening_balance'] == frozen['closing_balance'] == '123.45'
    assert carried['opening_source_run_id'] == w['run']


@pytest.mark.asyncio
async def test_first_draft_opening_accepts_audited_positive_and_negative_deltas(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    for amount in ('5000.00', '500.00', '-300.00'):
        response = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': amount,
            'reason': 'Audited opening import'})
        assert response.status_code == 201, response.text
    ledger = (await api.get(path)).json()
    assert ledger['opening_balance'] == '5200.00'
    assert sorted(Decimal(row['amount']) for row in ledger['entries'] if row.get('component') == 'OPENING') == [Decimal('-300'), Decimal('500'), Decimal('5000')]
    response = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '0.00', 'reason': 'No change'})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_AMOUNT_INVALID'


async def imported_reserve_draft(api, w, *, split=False):
    statement = ('Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck;Währung\n'
        '02.02.2025;03.02.2025;-1200,00;Gartenservice;;Reparatur;EUR\n').encode()
    response = await api.post(f"/api/bank-accounts/{w['bank_giro']}/imports", params={'dry_run': 'false'},
        files={'file': ('reserve.csv', statement, 'text/csv')})
    assert response.status_code == 200, response.text
    page = await api.get('/api/bank-transactions', params={'bank_account_id': w['bank_giro']})
    transaction = next(row for row in page.json()['items'] if row['amount'] == '-1200.00')
    line = {'payment_type': 'RUECKLAGENENTNAHME', 'amount': '600.00' if split else '1200.00', 'period_year': 2025, 'period_month': 2}
    assigned = await api.post(f"/api/bank-transactions/{transaction['id']}/assign", json={'lines': [line, line] if split else [line]})
    assert assigned.status_code == 200, assigned.text
    return transaction['id'], assigned.json()['payments'][0]['id']


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence_part', ['none', 'cost', 'register'])
async def test_legacy_import_release_deletes_draft_and_allows_retyping(client_a_admin, client_b_admin, seed_data, migrator_session, evidence_part):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    transaction, draft = await imported_reserve_draft(api, w)
    path = f'/api/payments/{draft}'
    if evidence_part != 'none':
        evidence = await reserve_evidence(api, w)
        response = await api.patch(path, json={key: value for key, value in evidence.items() if evidence_part in key})
        assert response.status_code == 200, response.text
    assert (await client_b_admin.delete(path)).status_code == 404
    response = await api.delete(path)
    assert response.status_code == 204, response.text
    audit = (await migrator_session.execute(text("SELECT payload_diff FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='bank_transaction.released'"), {'id': transaction})).scalar_one()
    assert audit == {'payment_id': draft, 'previous_status': 'ZUGEORDNET'}
    await migrator_session.rollback()
    retained = await api.get(path)
    assert retained.status_code == 404, retained.text
    assert (await api.delete(path)).status_code == 404
    page = await api.get('/api/bank-transactions', params={'bank_account_id': w['bank_giro'], 'status': 'NEU'})
    assert next(row for row in page.json()['items'] if row['id'] == transaction)['status'] == 'NEU'
    response = await api.post(f'/api/bank-transactions/{transaction}/assign', json={'lines': [{
        'payment_type': 'RUECKLAGENANTEIL', 'owner_profile_id': w['owner_a'], 'amount': '1200.00', 'period_year': 2025, 'period_month': 2}]})
    assert response.status_code == 200, response.text
    assert response.json()['payments'][0]['status'] == 'booked'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['booked', 'evidenced', 'ledger', 'booking_stamp', 'split'])
async def test_imported_payout_release_refuses_bound_states(client_a_admin, seed_data, monkeypatch, state):
    from unittest.mock import AsyncMock
    from app.repositories.payment_repo import PaymentRepository
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    transaction, draft = await imported_reserve_draft(api, w, split=state == 'split')
    if state in ('booked', 'evidenced'):
        evidence = await reserve_evidence(api, w)
        response = await api.patch(f'/api/payments/{draft}', json=evidence)
        assert response.status_code == 200, response.text
        if state == 'booked':
            response = await api.post(f'/api/payments/{draft}/book')
            assert response.status_code == 200, response.text
    elif state == 'ledger':
        monkeypatch.setattr(PaymentRepository, 'has_reserve_entry', AsyncMock(return_value=True), raising=False)
    elif state == 'booking_stamp':
        original = PaymentRepository.get_by_id
        async def legacy_stamp(repo, payment_id):
            payment = await original(repo, payment_id)
            if payment is not None:
                from datetime import datetime, timezone
                payment.booked_at = datetime.now(timezone.utc)
            return payment
        monkeypatch.setattr(PaymentRepository, 'get_by_id', legacy_stamp)
    response = await api.delete(f'/api/payments/{draft}')
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'RESERVE_IMPORTED_DRAFT_BOUND'
    page = await api.get('/api/bank-transactions', params={'bank_account_id': w['bank_giro'], 'status': 'ZUGEORDNET'})
    assert next(row for row in page.json()['items'] if row['id'] == transaction)['status'] == 'ZUGEORDNET'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['release', 'without_reset', 'movement', 'ledger', 'booking_stamp', 'evidenced', 'booked', 'split'])
async def test_database_only_releases_unbooked_unbacked_import_without_ledger(client_a_admin, seed_data, migrator_session, case):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    transaction, draft = await imported_reserve_draft(api, w, split=case == 'split')
    if case in ('evidenced', 'booked'):
        evidence = await reserve_evidence(api, w)
        assert (await api.patch(f'/api/payments/{draft}', json=evidence)).status_code == 200
        if case == 'booked':
            assert (await api.post(f'/api/payments/{draft}/book')).status_code == 200
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_org_id',:org,true)"),
        {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
    if case == 'ledger':
        # Reproduce a historical draft-linked row; normal inserts already forbid it.
        await migrator_session.execute(text('ALTER TABLE reserve_ledger_entry DISABLE TRIGGER guard_reserve_ledger'))
        await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
            (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,payment_id,created_by)
            VALUES (:org,:property,:run,'2025-02-03','CONTRIBUTION',0,'Historical retained reference',:payment,:actor)"""),
            {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'], 'payment': draft, 'actor': seed_data['user_a_admin_id']})
        await migrator_session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        await migrator_session.execute(text('ALTER TABLE reserve_ledger_entry ENABLE ALWAYS TRIGGER guard_reserve_ledger'))
    if case == 'booking_stamp':
        await migrator_session.execute(text("UPDATE payment SET booked_at=now() WHERE id=CAST(:id AS uuid)"), {'id': draft})
    if case != 'without_reset':
        await migrator_session.execute(text("UPDATE bank_transaction SET status='NEU' WHERE id=CAST(:id AS uuid)"), {'id': transaction})
    statement = text("UPDATE payment SET cancelled_at=now()" + (",amount=1" if case == 'movement' else '') + " WHERE id=CAST(:id AS uuid)")
    if case == 'release':
        await migrator_session.execute(statement, {'id': draft})
        assert await migrator_session.scalar(text("SELECT cancelled_at IS NOT NULL FROM payment WHERE id=CAST(:id AS uuid)"), {'id': draft})
    else:
        with pytest.raises(DBAPIError):
            async with migrator_session.begin_nested():
                await migrator_session.execute(statement, {'id': draft})


@pytest.mark.asyncio
async def test_opening_reattestation_records_only_the_difference_and_stays_first_year_only(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    async def attest(run, balance, reason='Opening reconciled against bank records'):
        return await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement',
            'subject_id': run, 'values': {**paid(w), 'reserve_opening_balance': balance, 'reserve_override_reason': reason},
            'confirmed_complete': True, 'reason': 'Complete synthetic records'})
    for balance, expected in [('5000.00', ['5000.00']), ('5500.00', ['5000.00', '500.00']),
                              ('5200.00', ['5000.00', '500.00', '-300.00'])]:
        response = await attest(w['run'], balance)
        assert response.status_code == 201, response.text
        ledger = (await api.get(path)).json()
        assert ledger['opening_balance'] == balance
        rows = [row for row in ledger['entries'] if row.get('component') == 'OPENING']
        assert sorted(Decimal(row['amount']) for row in rows) == sorted(map(Decimal, expected))
        assert all(row['reason'] == 'Opening reconciled against bank records' for row in rows)
    rows = (await migrator_session.execute(text("SELECT amount,created_by FROM reserve_ledger_entry WHERE settlement_run_id=CAST(:id AS uuid) ORDER BY created_at,id"), {'id': w['run']})).all()
    assert all(row.created_by == seed_data['user_a_admin_id'] for row in rows)
    assert await migrator_session.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='reserve.override_added'"), {'id': w['run']}) == 3
    refused = await attest(w['run'], '5300.00', '')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CORRECTION_REASON_REQUIRED'
    unchanged = await attest(w['run'], '5200.00')
    assert unchanged.status_code == 201, unchanged.text
    later_plan = await ownership_plan(api, w, 2026)
    later = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': later_plan,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert later.status_code == 201, later.text
    refused = await attest(later.json()['id'], '6000.00')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'
    await finalize(api, w)
    refused = await attest(w['run'], '6000.00')
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'
    refused = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '1.00', 'reason': 'Frozen'})
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['weg', 'asset'])
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_imported_opening_blocks_creation_of_an_earlier_period(
        client_a_admin, seed_data, monkeypatch, kind, database_backstop):
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository

    api = client_a_admin
    w = _seed_weg(seed_data)
    plan = await _create_and_finalize_plan(api, w)
    later = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert later.status_code == 201, later.text
    response = await api.post(f"/api/weg-settlements/{later.json()['id']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '5000.00', 'reason': 'Audited first opening'})
    assert response.status_code == 201, response.text
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_later_opening_import', AsyncMock(return_value=False), raising=False)
    body = {'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
    if kind == 'weg':
        body['economic_plan_id'] = plan
    response = await api.post('/api/weg-settlements' + ('/asset-reports' if kind == 'asset' else ''), json=body)
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_EXISTS'
    assert (await api.get(f"/api/weg-settlements/{later.json()['id']}/reserve-ledger")).json()['opening_balance'] == '5000.00'


@pytest.mark.asyncio
async def test_earliest_import_carries_exact_frozen_closing_through_later_years(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '700.00', 'reason': 'Audited earliest opening'})
    assert response.status_code == 201, response.text
    await payment(api, w, '123.45')
    await finalize(api, w)
    previous = w['run']
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    for year in (2026, 2027):
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        ledger = (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()
        frozen = (await api.get(f'/api/weg-settlements/{previous}/reserve-ledger')).json()
        assert ledger['opening_balance'] == frozen['closing_balance'] == '823.45'
        assert ledger['opening_source_run_id'] == previous
        await attest_fixture(api, 'settlement', run, values)
        response = await api.post(f'/api/weg-settlements/asset-reports/{run}/finalize', json=values)
        assert response.status_code == 200, response.text
        previous = run


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint,sqlstate,status,code', [
    ('reserve_opening_import_only', '23514', 422, 'RESERVE_OPENING_IMPORT_ONLY'),
    ('reserve_period_finalized', '23514', 409, 'RESERVE_PERIOD_FINALIZED'),
    ('reserve_ledger_entry_organization_id_payment_id_key', '23505', 409, 'RESERVE_PAYMENT_ALREADY_ASSIGNED'),
])
async def test_override_database_errors_keep_the_specific_reserve_code(monkeypatch, constraint, sqlstate, status, code):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError
    from app.api import weg_settlements as routes

    class DriverError(Exception):
        pass
    driver = DriverError()
    driver.sqlstate = sqlstate
    driver.__cause__ = DriverError()
    driver.__cause__.constraint_name = constraint
    service = SimpleNamespace(subject=AsyncMock(), add=AsyncMock(side_effect=IntegrityError(None, None, driver)))
    monkeypatch.setattr(routes, 'ReserveLedgerService', lambda *args: service)
    with pytest.raises(HTTPException) as refusal:
        await routes.reserve_override(uuid4(), routes.ReserveOverrideRequest(
            component='OPENING', amount='1.00', reason='Audited import'),
            SimpleNamespace(organization_id=uuid4(), user_id=uuid4()), None)
    assert refusal.value.status_code == status
    assert refusal.value.detail['code'] == code


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint,status,code', [
    ('reserve_opening_import_only', 422, 'RESERVE_OPENING_IMPORT_ONLY'),
    ('reserve_period_finalized', 409, 'RESERVE_PERIOD_FINALIZED'),
])
async def test_attestation_maps_opening_database_refusals(monkeypatch, constraint, status, code):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from fastapi import HTTPException
    from app.api import financial_input_coverage as routes
    from app.services.financial_scope_service import FinancialScopeService
    class DriverError(Exception):
        sqlstate = '23514'
    driver = DriverError()
    driver.__cause__ = DriverError()
    driver.__cause__.constraint_name = constraint
    failure = DBAPIError(None, None, driver)
    subject = SimpleNamespace(id=uuid4(), run_type='WEG_JAHRESABRECHNUNG')
    monkeypatch.setattr(FinancialScopeService, 'lock_inputs', AsyncMock())
    monkeypatch.setattr(routes, 'SettlementRunRepository', lambda *args: SimpleNamespace(get_by_id_for_update=AsyncMock(return_value=subject)))
    monkeypatch.setattr(routes, 'FinancialInputPolicy', lambda *args: SimpleNamespace(attest=AsyncMock(side_effect=failure)))
    with pytest.raises(HTTPException) as refused:
        await routes.attest(routes.Attestation(subject_kind='settlement', subject_id=subject.id,
            values={'paid_advances': []}, confirmed_complete=True, reason='Reviewed inputs'),
            SimpleNamespace(organization_id=uuid4(), user_id=uuid4()), None)
    assert refused.value.status_code == status
    assert refused.value.detail['code'] == code


@pytest.mark.asyncio
async def test_unchanged_opening_reattestation_refreshes_stale_cash_coverage(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    values = {**paid(w), 'reserve_opening_balance': '5000.00', 'reserve_override_reason': 'Reviewed opening'}
    first = await attest_fixture(api, 'settlement', w['run'], values)
    repeated = await attest_fixture(api, 'settlement', w['run'], values)
    assert repeated['id'] == first['id']
    await payment(api, w, '100.00')
    stale = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert stale.status_code == 422, stale.text
    assert stale.json()['detail']['blockers'][0]['code'] == 'coverage_stale'
    refreshed = await attest_fixture(api, 'settlement', w['run'], values)
    assert refreshed['revision'] == first['revision'] + 1
    computed = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert computed.status_code == 200, computed.text
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert [row['amount'] for row in ledger['entries'] if row.get('component') == 'OPENING'] == ['5000.00']


@pytest.mark.asyncio
@pytest.mark.parametrize('sibling_type', ['WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT'])
async def test_draft_with_finalized_sibling_accepts_unchanged_opening_and_non_reserve_attestation(
        client_a_admin, seed_data, sibling_type):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    body = {'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
    route = '/api/weg-settlements'
    if sibling_type == 'WEG_JAHRESABRECHNUNG':
        body['economic_plan_id'] = w['plan']
    else:
        route += '/asset-reports'
    sibling = await api.post(route, json=body)
    assert sibling.status_code == 201, sibling.text
    sibling_id = sibling.json()['id']
    original_path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    imported = await api.post(original_path + '/overrides', json={
        'component': 'OPENING', 'amount': '5000.00', 'reason': 'Bank-confirmed initial balance'})
    assert imported.status_code == 201, imported.text
    await finalize(api, w)
    frozen = (await api.get(original_path)).json()
    if sibling_type == 'WEG_JAHRESABRECHNUNG':
        values = paid(w)
    else:
        accounts = (await api.get('/api/bank-accounts')).json()
        values = {'bank_balances': [{'bank_account_id': account['id'], 'account_type': account['account_type'],
            'label': account['label'], 'balance': '0.00'} for account in accounts if account['property_id'] == w['property']],
            'receivables': [], 'payables': []}
    for include_opening in (True, False):
        reviewed = {**values, **({'reserve_opening_balance': '5000.00',
            'reserve_override_reason': 'Unchanged bank-confirmed balance'} if include_opening else {})}
        first = await attest_fixture(api, 'settlement', sibling_id, reviewed)
        repeated = await attest_fixture(api, 'settlement', sibling_id, reviewed)
        assert first['id'] == repeated['id']
        computed = await api.post(f'{route}/{sibling_id}/compute', json=reviewed)
        assert computed.status_code == 200, computed.text
    refused = await api.post('/api/financial-input-coverage', json={
        'subject_kind': 'settlement', 'subject_id': sibling_id,
        'values': {**values, 'reserve_opening_balance': '5001.00', 'reserve_override_reason': 'Actual change'},
        'confirmed_complete': True, 'reason': 'Reviewed period', 'source_document_ids': []})
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'
    assert (await api.get(original_path)).json() == frozen


@pytest.mark.asyncio
async def test_same_day_ledger_entries_keep_creation_order_across_reads(client_a_admin, seed_data, monkeypatch):
    from datetime import datetime
    from uuid import UUID
    from app.services import reserve_ledger_service as module
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    entry_class = module.ReserveLedgerEntry
    identifiers = iter([UUID(int=3), UUID(int=2), UUID(int=1)])
    timestamps = iter([datetime(2025, 1, 1, hour) for hour in (1, 2, 2)])
    monkeypatch.setattr(module, 'ReserveLedgerEntry', lambda **values: entry_class(
        **values, id=next(identifiers), created_at=next(timestamps)))
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    for reason in ('First correction', 'Second correction', 'Same timestamp, lower ID'):
        response = await api.post(path + '/overrides', json={
            'component': 'OPENING', 'amount': '100.00', 'reason': reason})
        assert response.status_code == 201, response.text
    for _ in range(2):
        response = await api.get(path)
        assert response.status_code == 200, response.text
        assert [row['reason'] for row in response.json()['entries'] if row['kind'] == 'OVERRIDE'] == [
            'First correction', 'Same timestamp, lower ID', 'Second correction']


@pytest.mark.asyncio
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_correction_period_keeps_finalized_opening_but_accepts_evidenced_corrections(
        client_a_admin, seed_data, migrator_session, database_backstop):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    report = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    for run, amount in [(w['run'], '5000.00'), (report.json()['id'], '7000.00')]:
        response = await api.post(f'/api/weg-settlements/{run}/reserve-ledger/overrides',
            json={'component': 'OPENING', 'amount': amount, 'reason': 'Reviewed import'})
        assert response.status_code == 201, response.text
    await finalize(api, w)
    correction = await api.post(f"/api/settlements/{w['run']}/correction")
    assert correction.status_code == 201, correction.text
    run = correction.json()['id']
    path = f'/api/weg-settlements/{run}/reserve-ledger'
    assert (await api.get(path)).json()['opening_balance'] == '12000.00'
    if database_backstop:
        await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_org_id',:org,true)"),
            {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
        with pytest.raises(DBAPIError) as refused:
            async with migrator_session.begin_nested():
                await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by)
                    VALUES (:org,:property,:run,'2025-01-01','OPENING',100,'Frozen import',:actor)"""),
                    {'org': seed_data['org_a_id'], 'property': w['property'], 'run': run, 'actor': seed_data['user_a_admin_id']})
        assert refused.value.orig.__cause__.constraint_name == 'reserve_period_finalized'
        await migrator_session.rollback()
    else:
        refused = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '100.00', 'reason': 'Frozen import'})
        assert refused.status_code == 409, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'
    evidence = await reserve_evidence(api, w)
    for component in ('WITHDRAWAL', 'ADJUSTMENT'):
        response = await api.post(path + '/overrides', json={'component': component, 'amount': '1.00',
            'reason': 'Evidence-backed correction', **{key.removeprefix('reserve_'): value for key, value in evidence.items()}})
        assert response.status_code == 201, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('overlap', [False, True])
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_opening_refuses_earlier_or_overlapping_period_on_both_input_paths(
        client_a_admin, seed_data, monkeypatch, overlap, database_backstop):
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    from tests.test_financial_input_policy import attest
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    report = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2025-01-01' if overlap else '2026-01-01',
        'period_end': '2025-06-30' if overlap else '2026-12-31'})
    assert report.status_code == 201, report.text
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_earlier_ledger', AsyncMock(return_value=False))
        monkeypatch.setattr(ReserveLedgerRepository, 'has_overlapping_ledger', AsyncMock(return_value=False), raising=False)
    run = report.json()['id']
    body = {'component': 'OPENING', 'amount': '5000.00', 'reason': 'Conflicting import'}
    refused = await api.post(f'/api/weg-settlements/{run}/reserve-ledger/overrides', json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'
    values = {'bank_balances': [{'bank_account_id': w[key], 'account_type': kind, 'label': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]],
        'receivables': [], 'payables': [], 'reserve_opening_balance': '5000.00', 'reserve_override_reason': 'Conflicting import'}
    refused = await attest(api, 'settlement', run, values)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'


@pytest.mark.asyncio
@pytest.mark.parametrize('reserve_fields', [False, True])
async def test_finalized_attestation_uses_reserve_code_only_for_reserve_fields(client_a_admin, seed_data, reserve_fields):
    from tests.test_financial_input_policy import attest
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await finalize(api, w)
    values = {**paid(w), **({'reserve_opening_balance': '1.00'} if reserve_fields else {})}
    refused = await attest(api, 'settlement', w['run'], values)
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail'] == ({'code': 'RESERVE_PERIOD_FINALIZED',
        'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'} if reserve_fields else 'Only draft inputs can be attested')


@pytest.mark.asyncio
async def test_direct_ledger_write_locks_its_cost_evidence(client_a_admin, seed_data, migrator_session, app_rw_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    # NO KEY UPDATE permits the FK's KEY SHARE lock, but blocks the guard's UPDATE lock.
    await migrator_session.execute(text('SELECT id FROM cost_entry WHERE id=:cost FOR NO KEY UPDATE'),
        {'cost': evidence['reserve_cost_entry_id']})
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    await app_rw_session.execute(text("SET LOCAL lock_timeout='200ms'"))
    with pytest.raises(DBAPIError) as blocked:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                VALUES (:org,:property,:run,'2025-01-01','WITHDRAWAL',1,'Concurrent evidence',:cost,:register,:actor)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'],
                 'cost': evidence['reserve_cost_entry_id'], 'register': evidence['reserve_register_entry_id'],
                 'actor': seed_data['user_a_admin_id']})
    assert blocked.value.orig.sqlstate == '55P03'


@pytest.mark.asyncio
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_compatible_weg_scope_cannot_reimport_a_finalized_opening(
        migrator_session, app_rw_session, scope_seed, worker_tenant, monkeypatch, database_backstop):
    from uuid import uuid4
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    from app.services.reserve_ledger_service import ReserveLedgerService
    from tests.test_scope_grants import scope_fixture, bind
    from tests.financial_coverage_helpers import add_report_recipient

    session = migrator_session
    api, _ = worker_tenant
    ids = await scope_fixture(session, scope_seed)
    ids['next_scope'] = uuid4()
    await session.execute(text("""INSERT INTO accounting_scope
        (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
        VALUES (:next_scope,:org,:property,'WEG','Second whole-property scope','2025-01-01',true,'REVIEWED',:actor)"""), ids)
    for scope in (ids['scope'], ids['next_scope']):
        await session.execute(text("""INSERT INTO scope_grant
            (organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (:org,:scope,:actor,ARRAY['read','prepare','confirm','approve','export'],:actor,'Reviewed report access')"""),
            {**ids, 'scope': scope})
    await bind(session, ids)
    await session.commit()
    await add_report_recipient(ids)
    body = {'property_id': str(ids['property']), 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
    original = await api.post('/api/weg-settlements/asset-reports', json={**body, 'accounting_scope_id': str(ids['scope'])})
    assert original.status_code == 201, original.text
    run = original.json()['id']
    opening = await api.post(f'/api/weg-settlements/{run}/reserve-ledger/overrides', json={
        'component': 'OPENING', 'amount': '5000.00', 'reason': 'Reviewed first import'})
    assert opening.status_code == 201, opening.text
    values = {'bank_balances': [{'bank_account_id': str(ids['bank']), 'account_type': 'LAUFEND', 'label': 'Test', 'balance': '0.00'}],
        'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', run, values)
    finalized = await api.post(f'/api/weg-settlements/asset-reports/{run}/finalize', json=values)
    assert finalized.status_code == 200, finalized.text
    sibling = await api.post('/api/weg-settlements/asset-reports', json={**body, 'accounting_scope_id': str(ids['next_scope'])})
    assert sibling.status_code == 201, sibling.text
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_finalized_opening_period', AsyncMock(return_value=False))
    else:
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(ids['org']), 'actor': str(ids['actor'])})
        ledger = ReserveLedgerService(app_rw_session, ids['org'], ids['actor'])
        subject = await ledger.subject(sibling.json()['id'], write=True)
        assert await ledger.repo.has_finalized_opening_period(subject) is True
        await app_rw_session.rollback()
    refused = await api.post(f"/api/weg-settlements/{sibling.json()['id']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '100.00', 'reason': 'Incompatible reimport'})
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'
    assert (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()['opening_balance'] == '5000.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('import_first', [False, True])
@pytest.mark.parametrize('overlap', [False, True])
async def test_whole_property_weg_scopes_share_the_first_period_rule(migrator_session, scope_seed, monkeypatch, import_first, overlap):
    from uuid import uuid4
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.services.reserve_ledger_service import ReserveLedgerService
    from app.services.weg_settlement_service import WegSettlementService
    from tests.test_financial_scope_boundary import actor
    from tests.test_scope_grants import scope_fixture

    session = migrator_session
    ids = await scope_fixture(session, scope_seed)
    ids['next_scope'] = uuid4()
    await session.execute(text("""INSERT INTO accounting_scope
        (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
        VALUES (:next_scope,:org,:property,'WEG','Next reserve year','2025-01-01',true,'REVIEWED',:actor)"""), ids)
    await session.execute(text("""INSERT INTO scope_grant
        (organization_id,scope_id,user_id,capabilities,granted_by,reason)
        VALUES (:org,:next_scope,:clerk,ARRAY['read','prepare'],:actor,'Prepare next reserve year')"""), ids)
    await actor(session, ids)
    service = WegSettlementService(session, ids['org'], ids['clerk'])
    if not import_first:
        await service.create_asset_report_draft(ids['property'], date(2025, 1, 1), date(2025, 12, 31), accounting_scope_id=ids['scope'])
    await session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
    later = await service.create_asset_report_draft(ids['property'], date(2025, 1, 1) if overlap else date(2026, 1, 1),
        date(2025, 6, 30) if overlap else date(2026, 12, 31), accounting_scope_id=ids['next_scope'])
    ledger = ReserveLedgerService(session, ids['org'], ids['clerk'])
    monkeypatch.setattr(ledger, 'read', AsyncMock(return_value={'frozen': False, 'entries': []}))
    if import_first:
        await ledger.add(later, 'OPENING', Decimal('5000'), 'Audited first import')
        await session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
    with pytest.raises(HTTPException) as refused:
        if import_first:
            await service.create_asset_report_draft(ids['property'], date(2025, 1, 1), date(2025, 12, 31), accounting_scope_id=ids['scope'])
        else:
            await ledger.add(later, 'OPENING', Decimal('5000'), 'Not the earliest WEG period')
    assert refused.value.status_code == 422
    assert refused.value.detail['code'] == ('RESERVE_OPENING_IMPORT_EXISTS' if import_first else 'RESERVE_OPENING_IMPORT_ONLY')
    await session.execute(text('RESET ROLE'))


@pytest.mark.asyncio
@pytest.mark.parametrize('component,amount', [('WITHDRAWAL', '1000000.00'),
                                            ('ADJUSTMENT', '-50000.00')])
async def test_typed_correction_cannot_exceed_confirmed_cost(client_a_admin, seed_data, component, amount):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': component, 'amount': amount, 'reason': 'Over cost amount', **evidence})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
@pytest.mark.parametrize('component', ['WITHDRAWAL'])
@pytest.mark.parametrize('amount', ['-999999.00', '0.00'])
async def test_typed_cash_correction_requires_positive_amount(client_a_admin, seed_data, component, amount):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': component, 'amount': amount, 'reason': 'Invalid sign', **evidence})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_AMOUNT_INVALID'


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['WITHDRAWAL', 'ADJUSTMENT', 'PAYOUT'])
async def test_corrections_share_the_remaining_cost_with_booked_payouts(client_a_admin, seed_data, first):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    payment_evidence = await reserve_evidence(api, w)
    evidence = {k.removeprefix('reserve_'): v for k, v in payment_evidence.items()}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    if first == 'PAYOUT':
        await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=payment_evidence)
    else:
        response = await api.post(path, json={'component': first, 'amount': '-3000.00' if first == 'ADJUSTMENT' else '3000.00',
            'reason': 'Documented correction', **evidence})
        assert response.status_code == 201, response.text
    for amount, status in [('1200.00', 422), ('1000.00', 201), ('0.01', 422)]:
        response = await api.post(path, json={'component': 'WITHDRAWAL', 'amount': amount,
            'reason': 'Use remaining confirmed cost', **evidence})
        assert response.status_code == status, response.text
        if status == 422:
            assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
async def test_booking_includes_typed_corrections_in_cost_cap(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '-3000.00', 'reason': 'Documented correction',
        **{k.removeprefix('reserve_'): v for k, v in evidence.items()}})
    assert response.status_code == 201, response.text
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '1200.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', **evidence})
    assert draft.status_code == 201, draft.text
    response = await api.post(f"/api/payments/{draft.json()['id']}/book")
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    with pytest.raises(DBAPIError) as refusal:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE payment SET status='booked',booked_at=now(),booked_by=:actor,retention_until='2034-12-31' WHERE id=CAST(:id AS uuid)"),
                {'actor': seed_data['user_a_admin_id'], 'id': draft.json()['id']})
    assert refusal.value.orig.__cause__.constraint_name == 'reserve_payout_evidence_required'


@pytest.mark.asyncio
@pytest.mark.parametrize('case,component,amount,constraint', [
    ('earlier_draft', 'OPENING', '5000.00', 'reserve_opening_import_only'),
    ('duplicate', 'OPENING', '-7.00', None),
    ('sign', 'OPENING', '0.00', 'reserve_amount_invalid'),
    ('sign', 'WITHDRAWAL', '-1.00', 'reserve_amount_invalid'),
    ('cap', 'ADJUSTMENT', '-50000.00', 'reserve_payout_evidence_required'),
    ('payout', 'WITHDRAWAL', '1200.00', 'reserve_payout_evidence_required'),
    ('correction', 'ADJUSTMENT', '-1200.00', 'reserve_payout_evidence_required'),
    ('valid', 'WITHDRAWAL', '1000.00', None),
])
async def test_database_mirrors_opening_and_correction_limits(client_a_admin, seed_data, migrator_session,
                                                           case, component, amount, constraint):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {} if component == 'OPENING' else await reserve_evidence(api, w)
    run, entry_date = w['run'], '2025-01-01'
    if case == 'earlier_draft':
        response = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
            'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        assert response.status_code == 201, response.text
        run, entry_date = response.json()['id'], '2026-01-01'
    elif case in ('duplicate', 'correction'):
        response = await api.post(f"/api/weg-settlements/{run}/reserve-ledger/overrides", json={
            'component': 'OPENING' if case == 'duplicate' else 'WITHDRAWAL', 'amount': '3000.00',
            'reason': 'Retained first entry', **{k.removeprefix('reserve_'): v for k, v in evidence.items()}})
        assert response.status_code == 201, response.text
    elif case == 'payout':
        await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
        {'actor': str(seed_data['user_a_admin_id'])})
    await migrator_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
        {'org': str(seed_data['org_a_id'])})
    async def insert():
        await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                VALUES (:org,:property,:run,CAST(:date AS date),:component,:amount,'Database boundary',:cost,:register,:actor)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': run, 'date': date.fromisoformat(entry_date),
                 'component': component, 'amount': Decimal(amount), 'actor': seed_data['user_a_admin_id'],
                 'cost': evidence.get('reserve_cost_entry_id'), 'register': evidence.get('reserve_register_entry_id')})
    if constraint is None:
        await insert()
        return
    message = 'Active reserve withdrawals exceed confirmed cost amount' if case in ('cap', 'payout', 'correction') else None
    with pytest.raises(DBAPIError, match=message) as refusal:
        async with migrator_session.begin_nested():
            await insert()
    assert refusal.value.orig.__cause__.constraint_name == constraint


@pytest.mark.asyncio
async def test_evidence_picker_register_exposes_resolution_results(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    response = await api.get(f"/api/meetings/register/{w['property']}")
    assert response.status_code == 200, response.text
    row = next(row for row in response.json() if row['id'] == evidence['reserve_register_entry_id'])
    assert row['result'] == 'ANGENOMMEN'


@pytest.mark.asyncio
async def test_typed_withdrawal_target_without_evidence_is_refused(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement', 'subject_id': w['run'],
        'values': {**paid(w), 'reserve_withdrawals_total': '1200.00', 'reserve_override_reason': 'Unsupported manual target'},
        'confirmed_complete': True, 'reason': 'Reviewed records'})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_DERIVED_TOTAL_READ_ONLY'


@pytest.mark.asyncio
async def test_compute_keeps_its_guard_for_historical_unbacked_payouts(client_a_admin, seed_data, monkeypatch):
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await payment(api, w, '100.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    original = ReserveLedgerRepository.payments
    async def historical_payments(self, run, start=None):
        rows = await original(self, run, start)
        for row in rows:
            if row.payment_type == 'RUECKLAGENENTNAHME':
                # Model a retained pre-gate booking without mutating its stored original.
                row.__dict__['reserve_cost_entry_id'] = None
                row.__dict__['reserve_register_entry_id'] = None
        return rows
    monkeypatch.setattr(ReserveLedgerRepository, 'payments', historical_payments)
    await attest_fixture(api, 'settlement', w['run'], paid(w))
    refused = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=paid(w))
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['reserve_cost_entry_id', 'reserve_register_entry_id'])
async def test_payout_draft_foreign_and_unknown_evidence_are_indistinguishable(client_a_admin, client_b_admin, seed_data, field):
    import uuid
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    foreign_property = await client_b_admin.post('/api/properties', json={'name': 'Foreign evidence', 'management_type': 'WEG'})
    assert foreign_property.status_code == 201, foreign_property.text
    category = (await client_b_admin.get('/api/cost-categories')).json()[0]
    foreign_cost = await client_b_admin.post('/api/cost-entries', json={
        'property_id': foreign_property.json()['id'], 'cost_category_id': category['id'],
        'invoice_date': '2025-01-15', 'service_period_from': '2025-01-01', 'service_period_to': '2025-12-31',
        'gross_amount': '100.00', 'tenant_apportionable': 'UMLAGEFAEHIG'})
    assert foreign_cost.status_code == 201, foreign_cost.text
    evidence = await reserve_evidence(client_b_admin, {'property': foreign_property.json()['id'], 'ce_garten': foreign_cost.json()['id']})
    responses = []
    for identifier in (evidence[field], uuid.uuid4()):
        response = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
            'amount': '100.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', field: str(identifier)})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]


@pytest.mark.asyncio
async def test_booking_limits_total_active_payouts_to_confirmed_cost(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    original = await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '1200.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', **evidence})
    assert draft.status_code == 201, draft.text
    path = f"/api/payments/{draft.json()['id']}"
    refused = await api.post(path + '/book')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    assert (await api.post(f'/api/payments/{original}/reverse')).status_code == 201
    accepted = await api.post(path + '/book')
    assert accepted.status_code == 200, accepted.text


@pytest.mark.asyncio
@pytest.mark.parametrize('corrected_gross,amount,status', [
    (None, '4000.00', 422), ('9000.00', '4000.00', 200), ('3000.00', '0.01', 422),
])
async def test_cost_correction_chain_keeps_payout_cap_and_frozen_references(
        client_a_admin, seed_data, corrected_gross, amount, status):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    original = await payment(api, w, '4000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    before = (await api.get(f'/api/payments/{original}')).json()
    changes = {'document_number': 'RE-2025-corrected'}
    if corrected_gross:
        changes['gross_amount'] = corrected_gross
    response = await api.post(f"/api/cost-entries/{w['ce_garten']}/correction", json=changes)
    assert response.status_code == 201, response.text
    current = response.json()['id']
    assert (await api.post(f'/api/cost-entries/{current}/confirm')).status_code == 200
    assert (await api.get(f"/api/cost-entries/{w['ce_garten']}")).json()['superseded_by_id'] == current
    assert (await api.get(f'/api/payments/{original}')).json() == before
    assert before['status'] == 'booked'
    assert before['reserve_cost_entry_id'] == w['ce_garten']
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': amount, 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME',
        **evidence, 'reserve_cost_entry_id': current})
    assert draft.status_code == 201, draft.text
    response = await api.post(f"/api/payments/{draft.json()['id']}/book")
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
async def test_funded_amount_follows_predecessors_and_successors_in_cost_chain(client_a_admin, seed_data, app_rw_session):
    from uuid import UUID
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository

    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    original = await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    ids = [w['ce_garten']]
    for gross in ('5000.00', '9000.00'):
        response = await api.post(f'/api/cost-entries/{ids[-1]}/correction', json={'gross_amount': gross})
        assert response.status_code == 201, response.text
        ids.append(response.json()['id'])
        assert (await api.post(f'/api/cost-entries/{ids[-1]}/confirm')).status_code == 200
        if gross == '5000.00':
            response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
                'component': 'ADJUSTMENT', 'amount': '-1000.00', 'reason': 'Documented additional funding',
                'cost_entry_id': ids[-1], 'register_entry_id': evidence['reserve_register_entry_id']})
            assert response.status_code == 201, response.text
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id',:actor,true), set_config('app.current_org_id',:org,true)"),
        {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
    repo = ReserveLedgerRepository(app_rw_session, seed_data['org_a_id'])
    for cost in ids:
        assert await repo.funded_amount(UUID(cost)) == Decimal('4000.00')
        assert await repo.funded_amount(UUID(cost), UUID(original)) == Decimal('1000.00')


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['payout', 'ledger'])
async def test_database_guards_keep_cost_chain_cap(client_a_admin, seed_data, migrator_session, boundary):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    await payment(api, w, '4000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    response = await api.post(f"/api/cost-entries/{w['ce_garten']}/correction", json={'document_number': 'RE-corrected'})
    assert response.status_code == 201, response.text
    current = response.json()['id']
    assert (await api.post(f'/api/cost-entries/{current}/confirm')).status_code == 200
    values = {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'],
              'property': w['property'], 'run': w['run'], 'cost': current,
              'register': evidence['reserve_register_entry_id']}
    if boundary == 'payout':
        draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
            'amount': '4000.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME',
            **evidence, 'reserve_cost_entry_id': current})
        assert draft.status_code == 201, draft.text
        values['payment'] = draft.json()['id']
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true), set_config('app.current_org_id',:org,true)"),
        {'actor': str(values['actor']), 'org': str(values['org'])})
    with pytest.raises(DBAPIError) as refusal:
        async with migrator_session.begin_nested():
            if boundary == 'payout':
                await migrator_session.execute(text("""UPDATE payment SET status='booked',booked_at=now(),
                    booked_by=:actor,retention_until='2034-12-31' WHERE id=CAST(:payment AS uuid)"""), values)
            else:
                await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                    VALUES (:org,:property,:run,'2025-02-01','WITHDRAWAL',4000,'Cost correction chain',:cost,:register,:actor)"""), values)
    assert refusal.value.orig.__cause__.constraint_name == 'reserve_payout_evidence_required'


async def unit_correction_world(api, seed):
    w = _seed_weg(seed)
    w['cash'] = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS')
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    w['run'] = report.json()['id']
    assigned = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'CONTRIBUTION', 'payment_id': w['cash'], 'amount': '100.00', 'reason': 'Partial cash assignment'})
    assert assigned.status_code == 201, assigned.text
    w['plan'] = await _create_and_finalize_plan(api, w)
    return w


@pytest.mark.asyncio
async def test_unit_contribution_corrections_consume_only_remaining_booked_headroom(client_a_admin, seed_data):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    body = {'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': '300.00', 'reason': 'Reconcile booked unit reserve share'}
    first = await api.post(path + '/overrides', json=body)
    assert first.status_code == 201, first.text
    ledger = (await api.get(path)).json()
    row = next(row for row in ledger['entries'] if row.get('id') == first.json()['id'])
    assert row['unit_id'] == w['u1'] and row['unit_number'] == '1'
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '200.00'
    refused = await api.post(path + '/overrides', json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
    assert refused.json()['detail']['message'] == 'Sie können dieser Einheit noch höchstens 200,00 € als Zuführung zuordnen.'
    final = await api.post(path + '/overrides', json={**body, 'amount': '200.00'})
    assert final.status_code == 201, final.text
    assert (await api.get(path)).json()['contributions_total'] == '600.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('paid,adjusted,requested,allowed', [
    ('0.00', None, '1.00', '0,00'), ('1200.00', '200.00', '1000.01', '1.000,00'),
])
async def test_positive_adjustment_cannot_exceed_active_withdrawals(
        client_a_admin, seed_data, app_rw_session, paid, adjusted, requested, allowed):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    evidence = {k.removeprefix('reserve_'): v for k, v in sources.items()}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    if Decimal(paid):
        await payment(api, w, paid, kind='RUECKLAGENENTNAHME', direction='OUT', evidence=sources)
    if adjusted:
        accepted = await api.post(path + '/overrides', json={
            'component': 'ADJUSTMENT', 'amount': adjusted, 'reason': 'Reduce evidenced withdrawal', **evidence})
        assert accepted.status_code == 201, accepted.text
    before = (await api.get(path)).json()
    refused = await api.post(path + '/overrides', json={
        'component': 'ADJUSTMENT', 'amount': requested, 'reason': 'Cannot create free contributions', **evidence})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail'] == {'code': 'RESERVE_ADJUSTMENT_EXCEEDS_WITHDRAWALS',
        'message': f'Sie können die Entnahmen dieses Kostenbelegs noch um höchstens {allowed} € vermindern.'}
    assert (await api.get(path)).json() == before
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id',:actor,true), set_config('app.current_org_id',:org,true)"),
        {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
    with pytest.raises(DBAPIError) as refusal:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                VALUES (:org,:property,:run,'2025-02-01','ADJUSTMENT',:amount,'Direct correction',:cost,:register,:actor)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'],
                 'amount': Decimal(requested), 'cost': evidence['cost_entry_id'],
                 'register': evidence['register_entry_id'], 'actor': seed_data['user_a_admin_id']})
    assert refusal.value.orig.sqlstate == '23514'
    assert refusal.value.orig.__cause__.constraint_name == 'reserve_adjustment_exceeds_withdrawals'


@pytest.mark.asyncio
async def test_adjustment_net_withdrawals_follow_the_receipt_correction_chain(client_a_admin, seed_data, app_rw_session):
    from uuid import UUID
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    await payment(api, w, '1200.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=sources)
    corrected = await api.post(f"/api/cost-entries/{w['ce_garten']}/correction", json={'document_number': 'RE-corrected'})
    assert corrected.status_code == 201, corrected.text
    current = corrected.json()['id']
    assert (await api.post(f'/api/cost-entries/{current}/confirm')).status_code == 200
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    for component, amount in [('WITHDRAWAL', '100.00'), ('ADJUSTMENT', '-50.00'), ('ADJUSTMENT', '200.00')]:
        accepted = await api.post(path, json={'component': component, 'amount': amount, 'reason': 'Receipt chain correction',
            'cost_entry_id': current, 'register_entry_id': sources['reserve_register_entry_id']})
        assert accepted.status_code == 201, accepted.text
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id',:actor,true), set_config('app.current_org_id',:org,true)"),
        {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
    repo = ReserveLedgerRepository(app_rw_session, seed_data['org_a_id'])
    for receipt in (w['ce_garten'], current):
        assert await repo.funded_amount(UUID(receipt)) == Decimal('1150.00')
    refused = await api.post(path, json={'component': 'ADJUSTMENT', 'amount': '1150.01', 'reason': 'One cent too much',
        'cost_entry_id': current, 'register_entry_id': sources['reserve_register_entry_id']})
    assert refused.status_code == 422, refused.text
    accepted = await api.post(path, json={'component': 'ADJUSTMENT', 'amount': '1150.00', 'reason': 'Reduce net withdrawals to zero',
        'cost_entry_id': current, 'register_entry_id': sources['reserve_register_entry_id']})
    assert accepted.status_code == 201, accepted.text


@pytest.mark.asyncio
async def test_positive_adjustment_can_reduce_a_receipt_whose_corrected_gross_is_lower(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    await payment(api, w, '1200.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=sources)
    corrected = await api.post(f"/api/cost-entries/{w['ce_garten']}/correction", json={'gross_amount': '500.00'})
    assert corrected.status_code == 201, corrected.text
    current = corrected.json()['id']
    assert (await api.post(f'/api/cost-entries/{current}/confirm')).status_code == 200
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '200.00', 'reason': 'Reduce retained withdrawal',
        'cost_entry_id': current, 'register_entry_id': sources['reserve_register_entry_id']})
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['service', 'trigger'])
async def test_storno_cannot_make_receipt_net_withdrawals_negative_after_adjustment(client_a_admin, seed_data, migrator_session, monkeypatch, boundary):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    cash = await payment(api, w, '1200.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=sources)
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '200.00', 'reason': 'Reduce withdrawal',
        **{k.removeprefix('reserve_'): v for k, v in sources.items()}})
    assert response.status_code == 201, response.text
    if boundary == 'trigger':
        from unittest.mock import AsyncMock
        from app.services.reserve_ledger_service import ReserveLedgerService
        monkeypatch.setattr(ReserveLedgerService, 'require_payout_reversal', AsyncMock())
    refused = await api.post(f'/api/payments/{cash}/reverse')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_ADJUSTMENT_EXCEEDS_WITHDRAWALS'
    assert refused.json()['detail']['message'] == (
        'Der Storno ist nicht möglich: Die positiven Korrekturen des Belegs (200,00 €) '
        'übersteigen die verbleibenden Entnahmen. Bitte gleichen Sie die Korrekturen zuerst aus.')
    original = await api.get(f'/api/payments/{cash}')
    assert original.status_code == 200, original.text
    assert original.json()['reversed_by_id'] is None
    with pytest.raises(DBAPIError) as refusal:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text('UPDATE payment SET reversed_by_id=id WHERE id=CAST(:id AS uuid)'), {'id': cash})
    assert refusal.value.orig.sqlstate == '23514'
    assert refusal.value.orig.__cause__.constraint_name == 'reserve_adjustment_exceeds_withdrawals'


@pytest.mark.asyncio
@pytest.mark.parametrize('component', ['WITHDRAWAL', 'ADJUSTMENT', 'PAYOUT'])
async def test_withdrawal_cap_message_names_the_remaining_cost(client_a_admin, seed_data, component):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    await payment(api, w, '3700.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=sources)
    if component == 'PAYOUT':
        draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
            'amount': '300.01', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', **sources})
        assert draft.status_code == 201, draft.text
        refused = await api.post(f"/api/payments/{draft.json()['id']}/book")
    else:
        refused = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': component, 'amount': '-300.01' if component == 'ADJUSTMENT' else '300.01',
            'reason': 'Exceeds remaining receipt cap', **{k.removeprefix('reserve_'): v for k, v in sources.items()}})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['message'] == 'Sie können diesem Kostenbeleg noch höchstens 300,00 € als Rücklagenentnahme zuordnen.'


@pytest.mark.asyncio
async def test_funding_cap_refusal_message_uses_the_shared_remaining_receipt_amount(client_a_admin, seed_data, monkeypatch):
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    cash = await payment(api, w, '1200.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=sources)
    original = ReserveLedgerRepository.funding_sources
    async def legacy_sources(self, *args):
        payment, cost, register = await original(self, *args)
        # Model the retained legacy payout, without changing its stored evidence.
        payment.__dict__['reserve_cost_entry_id'] = None
        payment.__dict__['reserve_register_entry_id'] = None
        cost.__dict__['gross_amount'] = Decimal('1000.00')
        return payment, cost, register
    monkeypatch.setattr(ReserveLedgerRepository, 'funding_sources', legacy_sources)
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'FUNDING', 'amount': '0.00', 'reason': 'Link retained payout', 'payment_id': cash,
        **{k.removeprefix('reserve_'): v for k, v in sources.items()}})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['message'] == 'Sie können diesem Kostenbeleg noch höchstens 1.000,00 € als Rücklagenentnahme zuordnen.'


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['', '/asset-reports'])
async def test_correction_draft_retains_superseded_engine_version_until_computed(client_a_admin, seed_data, family, monkeypatch):
    from app.services import weg_settlement_service
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if family:
        created = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
            'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        assert created.status_code == 201, created.text
        w['run'] = created.json()['id']
        values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
            for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    else:
        values = paid(w)
    monkeypatch.setattr(weg_settlement_service, 'ENGINE_VERSION', 'm4.1.0')
    await attest_fixture(api, 'settlement', w['run'], values)
    original = await api.post(f"/api/weg-settlements{family}/{w['run']}/finalize", json=values)
    assert original.status_code == 200, original.text
    monkeypatch.setattr(weg_settlement_service, 'ENGINE_VERSION', 'm4.2.0')
    corrected = await api.post(f"/api/settlements/{w['run']}/correction")
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()['engine_version'] == original.json()['engine_version'] == 'm4.1.0'
    current = corrected.json()['id']
    await attest_fixture(api, 'settlement', current, values)
    computed = await api.post(f'/api/weg-settlements{family}/{current}/compute', json=values)
    assert computed.status_code == 200, computed.text
    assert computed.json()['engine_version'] == 'm4.2.0'


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['', '/asset-reports'])
async def test_correction_invalidates_stored_draft_computation_before_refetch(client_a_admin, seed_data, family):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if family:
        created = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
            'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        w['run'] = created.json()['id']
        values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
            for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    else:
        values = paid(w)
    await attest_fixture(api, 'settlement', w['run'], values)
    computed = await api.post(f"/api/weg-settlements{family}/{w['run']}/compute", json=values)
    assert computed.status_code == 200 and computed.json()['lines'], computed.text
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '500.00', 'reason': 'Correct after computing'})
    assert response.status_code == 201, response.text
    refreshed = (await api.get(f"/api/weg-settlements/{w['run']}")).json()
    assert refreshed['lines'] == []
    assert refreshed['total_cost'] is None and refreshed['balance'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt', [False, True])
async def test_hausgeld_refunds_do_not_create_unit_contribution_headroom(client_a_admin, seed_data, receipt):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if receipt:
        await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS')
    await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', direction='OUT')
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == ('0.00' if receipt else '-600.00')
    refused = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '600.00', 'reason': 'Refunds cannot fund a unit correction'})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '0.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt', [False, True])
async def test_database_refuses_unit_contribution_headroom_from_hausgeld_refunds(client_a_admin, seed_data, app_rw_session, receipt):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if receipt:
        await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS')
    await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', direction='OUT')
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id)
                VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',600,'Refund cap',:actor,:unit)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'],
                 'actor': seed_data['user_a_admin_id'], 'unit': w['u1']})
    assert refused.value.orig.sqlstate == '23514'
    assert refused.value.orig.__cause__.constraint_name == 'reserve_contribution_exceeds_booked'
    assert await app_rw_session.scalar(text('SELECT reserve_contribution_headroom(:org,:run,:unit)'),
        {'org': seed_data['org_a_id'], 'run': w['run'], 'unit': w['u1']}) == Decimal('0.00')


@pytest.mark.asyncio
async def test_refunded_partial_hausgeld_assignment_cannot_fund_a_unit_correction(client_a_admin, seed_data):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    before = (await api.get(path)).json()
    assert next(u for u in before['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '500.00'
    await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', direction='OUT')
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == '-500.00'
    refused = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '1.00', 'reason': 'A refunded partial assignment leaves no backing'})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '0.00'


@pytest.mark.asyncio
async def test_matching_hausgeld_refund_keeps_an_accepted_unit_contribution_correction_backed(client_a_admin, seed_data):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    accepted = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '500.00', 'reason': 'Remaining booked reserve share'})
    assert accepted.status_code == 201, accepted.text
    await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', direction='OUT')
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == '0.00'
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '0.00'
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '0.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'},
    ], 'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', w['run'], values)
    finalized = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()['status'] == 'finalized'


@pytest.mark.asyncio
async def test_refund_storno_restores_backing_for_an_accepted_unit_contribution_correction(client_a_admin, seed_data):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    accepted = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '500.00', 'reason': 'Remaining booked reserve share'})
    assert accepted.status_code == 201, accepted.text
    refund = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', direction='OUT')
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == '0.00'
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '0.00'
    reversed_refund = await api.post(f'/api/payments/{refund}/reverse', json={'note': 'Refund was not made'})
    assert reversed_refund.status_code == 201, reversed_refund.text
    restored = (await api.get(path)).json()
    assert restored['contributions_total'] == '600.00'
    assert next(row for row in restored['entries'] if row.get('id') == accepted.json()['id'])['amount'] == '500.00'
    assert next(u for u in restored['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '0.00'
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '1300.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'},
    ], 'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', w['run'], values)
    finalized = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()['status'] == 'finalized'


@pytest.mark.asyncio
@pytest.mark.parametrize('correction,remaining', [('300.00', '200.00'), ('500.00', '0.00')])
async def test_later_direct_reserve_contribution_preserves_hausgeld_headroom_and_finalization(client_a_admin, seed_data, correction, remaining):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    accepted = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': correction, 'reason': 'Remaining booked reserve share'})
    assert accepted.status_code == 201, accepted.text
    before = (await api.get(path)).json()
    assert next(u for u in before['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == remaining
    direct = await payment(api, w, '200.00')
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == format(Decimal('300.00') + Decimal(correction), '.2f')
    assert next(row for row in ledger['entries'] if row.get('payment_id') == direct)['amount'] == '200.00'
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '1500.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'},
    ], 'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', w['run'], values)
    finalized = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()['status'] == 'finalized'
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == remaining


@pytest.mark.asyncio
@pytest.mark.parametrize('amount,reason,code', [
    ('500.01', 'Above booked amount', 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'),
    ('0.00', 'Zero correction', 'RESERVE_AMOUNT_INVALID'),
    ('-1.00', 'Negative correction', 'RESERVE_AMOUNT_INVALID'),
    ('1.00', ' ', 'RESERVE_CORRECTION_REASON_REQUIRED'),
])
async def test_unit_contribution_correction_rejects_invalid_inputs(client_a_admin, seed_data, amount, reason, code):
    w = await unit_correction_world(client_a_admin, seed_data)
    response = await client_a_admin.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': amount, 'reason': reason})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == code


@pytest.mark.asyncio
async def test_unit_contribution_correction_hides_other_properties_and_organizations(client_a_admin, client_b_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    other = _seed_weg(seed_data)
    body = {'component': 'CONTRIBUTION', 'unit_id': other['u1'], 'amount': '1.00', 'reason': 'Foreign unit'}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    assert (await api.post(path, json=body)).status_code == 404
    body['unit_id'] = w['u1']
    assert (await client_b_admin.post(path, json=body)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('evidenced', [False, True])
async def test_free_and_evidenced_contribution_corrections_are_refused(client_a_admin, seed_data, evidenced):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    body = {'component': 'CONTRIBUTION', 'amount': '1.00', 'reason': 'No unit reference'}
    if evidenced:
        body.update({k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()})
    refused = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_UNIT_REQUIRED'


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['weg', 'asset_report'])
@pytest.mark.parametrize('field', ['reserve_contributions_total', 'reserve_withdrawals_total'])
async def test_attesting_derived_reserve_totals_is_precisely_refused(client_a_admin, seed_data, family, field):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if family == 'asset_report':
        report = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        w['run'] = report.json()['id']
    refused = await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement', 'subject_id': w['run'],
        'confirmed_complete': True, 'reason': 'Reviewed records', 'values': {field: '1000.00'}})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail'] == {'code': 'RESERVE_DERIVED_TOTAL_READ_ONLY',
        'message': 'Zuführungen und Entnahmen werden aus Buchungen abgeleitet; Korrekturen erfolgen mit Bezug im Rücklagenbuch.'}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['no_unit', 'above_cap', 'remaining', 'wrong_property'])
async def test_database_enforces_unit_contribution_cap_as_app_rw(client_a_admin, seed_data, app_rw_session, case):
    w = await unit_correction_world(client_a_admin, seed_data)
    if case == 'remaining':
        accepted = await client_a_admin.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': '400.00', 'reason': 'First correction'})
        assert accepted.status_code == 201, accepted.text
    unit = None if case == 'no_unit' else _seed_weg(seed_data)['u1'] if case == 'wrong_property' else w['u1']
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by)
                VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',:amount,'No unit',:actor)""") if case == 'no_unit' else text("""
                INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id)
                VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',:amount,'Unit cap',:actor,:unit)"""),
                {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'], 'property': w['property'],
                 'run': w['run'], 'unit': unit, 'amount': Decimal('500.01') if case == 'above_cap' else Decimal('101.00')})
    assert refused.value.orig.sqlstate == '23514'


@pytest.mark.asyncio
async def test_unit_contribution_foreign_key_requires_the_entry_property(client_a_admin, seed_data, migrator_session):
    w = await unit_correction_world(client_a_admin, seed_data)
    other = _seed_weg(seed_data)
    await migrator_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    point = await migrator_session.begin_nested()
    try:
        # Isolate the FK from the earlier ledger guard; rollback restores the trigger.
        await migrator_session.execute(text('ALTER TABLE reserve_ledger_entry DISABLE TRIGGER guard_reserve_ledger'))
        with pytest.raises(DBAPIError) as refused:
            async with migrator_session.begin_nested():
                await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id)
                    VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',1,'Unit of another property',:actor,:unit)"""),
                    {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'],
                     'actor': seed_data['user_a_admin_id'], 'unit': other['u1']})
        assert refused.value.orig.sqlstate == '23503'
        assert refused.value.orig.__cause__.constraint_name == 'fk_reserve_entry_unit'
    finally:
        await point.rollback()


@pytest.mark.asyncio
async def test_random_unit_cash_ratios_use_the_canonical_sql_and_exact_reversals(migrator_session):
    import json
    from fractions import Fraction
    from random import Random
    rng = Random(36)
    cases = []
    for _ in range(400):
        reserve = rng.randint(0, 100000)
        advance = reserve + rng.randint(1, 100000)
        amount = rng.randint(1, 1000000) * rng.choice([-1, 1])
        exact = Fraction(amount * reserve, advance)
        rounded = (abs(exact.numerator) * 2 + exact.denominator) // (2 * exact.denominator)
        expected = Decimal(rounded if amount > 0 else -rounded) / 100
        cases.append({'cash': str(Decimal(amount) / 100), 'expected': str(expected),
            'planned': [{'unit_id': 'u1', 'amount': str(Decimal(reserve) / 100)}],
            'owners': [{'unit_id': 'u1', 'annual_advance': str(Decimal(advance // 2) / 100)},
                       {'unit_id': 'u1', 'annual_advance': str(Decimal(advance - advance // 2) / 100)}]})
    failures = await migrator_session.scalar(text("""SELECT count(*) FROM jsonb_array_elements(CAST(:cases AS jsonb)) c
        WHERE reserve_hausgeld_share((c->>'cash')::numeric,ARRAY['u1'],c->'planned',c->'owners')<>(c->>'expected')::numeric
        OR reserve_hausgeld_share(-(c->>'cash')::numeric,ARRAY['u1'],c->'planned',c->'owners')<> -(c->>'expected')::numeric"""),
        {'cases': json.dumps(cases)})
    assert failures == 0
    for units, planned, owners in [([], [], []), (['u1'], [], []),
            (['u1'], [{'unit_id': 'u1', 'amount': '100'}], [{'unit_id': 'u1', 'annual_advance': '0'}])]:
        assert await migrator_session.scalar(text('SELECT reserve_hausgeld_share(1300,:units,CAST(:planned AS jsonb),CAST(:owners AS jsonb))'),
            {'units': units, 'planned': json.dumps(planned), 'owners': json.dumps(owners)}) is None


@pytest.mark.asyncio
async def test_zero_reserve_plan_keeps_zero_planned_rows_and_allows_finalization(client_a_admin, seed_data):
    api = client_a_admin
    w = _seed_weg(seed_data)
    plan = await _create_and_finalize_plan(api, w, reserve='0.00')
    created = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert created.status_code == 201, created.text
    w['run'] = created.json()['id']
    ledger = await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")
    assert ledger.status_code == 200, ledger.text
    assert len(ledger.json()['entries']) == 24
    assert {r['amount'] for r in ledger.json()['entries']} == {'0.00'}
    assert (await finalize(api, w))['status'] == 'finalized'


@pytest.mark.asyncio
async def test_storno_cannot_finalize_a_unit_correction_without_booked_backing(client_a_admin, seed_data, app_rw_session):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '500.00', 'reason': 'Remaining booked reserve share'})
    assert response.status_code == 201, response.text
    assert (await api.post(f"/api/payments/{w['cash']}/reverse", json={'note': 'Returned receipt'})).status_code == 201
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    assert await app_rw_session.scalar(text('SELECT reserve_contribution_headroom(:org,:run,:unit)'),
        {'org': seed_data['org_a_id'], 'run': w['run'], 'unit': w['u1']}) == Decimal('-500.00')
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '0.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'},
    ], 'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', w['run'], values)
    refused = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
    assert 'Einheit 1' in refused.json()['detail']['message']
    # Retained corrections stay unchanged; replacement cash cannot be credited twice.
    await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS')
    await attest_fixture(api, 'settlement', w['run'], values)
    # Automatic credit consumes all replacement cash, so the old correction is still unsupported.
    refused = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert refused.status_code == 422, refused.text


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['finalize', 'database'])
async def test_post_storno_refund_cannot_restore_backing_for_a_unit_correction(client_a_admin, seed_data, app_rw_session, boundary):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    accepted = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '500.00', 'reason': 'Remaining booked reserve share'})
    assert accepted.status_code == 201, accepted.text
    reversed_receipt = await api.post(f"/api/payments/{w['cash']}/reverse", json={'note': 'Returned receipt'})
    assert reversed_receipt.status_code == 201, reversed_receipt.text
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '0.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'},
    ], 'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', w['run'], values)
    refused = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
    assert 'Einheit 1' in refused.json()['detail']['message']
    refund = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', direction='OUT')
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == '-100.00'
    assert {row['payment_id']: row['amount'] for row in ledger['entries'] if row.get('payment_id')} == {
        w['cash']: '100.00', reversed_receipt.json()['id']: '-100.00', refund: '-600.00'}
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '0.00'
    if boundary == 'finalize':
        values['bank_balances'][0]['balance'] = '-1300.00'
        await attest_fixture(api, 'settlement', w['run'], values)
        refused = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
        assert 'Einheit 1' in refused.json()['detail']['message']
    else:
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
        with pytest.raises(DBAPIError) as refused:
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id)
                    VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',0.01,'Post-Storno refund cap',:actor,:unit)"""),
                    {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'],
                     'actor': seed_data['user_a_admin_id'], 'unit': w['u1']})
        assert refused.value.orig.sqlstate == '23514'
        assert refused.value.orig.__cause__.constraint_name == 'reserve_contribution_exceeds_booked'
        assert await app_rw_session.scalar(text('SELECT reserve_contribution_headroom(:org,:run,:unit)'),
            {'org': seed_data['org_a_id'], 'run': w['run'], 'unit': w['u1']}) == Decimal('-500.00')


@pytest.mark.asyncio
async def test_report_refuses_unsupported_unit_corrections_before_building_the_statement(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from fastapi import HTTPException
    from app.services.reserve_ledger_service import ReserveLedgerService
    from app.services.weg_settlement_service import WegSettlementService, AssetReportInputs
    ledger = {'entries': [], 'frozen': False, 'opening_balance': '0.00', 'contributions_total': '500.00',
        'withdrawals_total': '0.00', 'overrides_total': '0.00', 'late_cash_total': '0.00'}
    monkeypatch.setattr(ReserveLedgerService, 'read', AsyncMock(return_value=ledger))
    session = SimpleNamespace(scalar=AsyncMock(return_value='1'))
    run = SimpleNamespace(id=uuid4(), property_id=uuid4(), status='draft',
        period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
    service = WegSettlementService(session, uuid4(), uuid4())
    with pytest.raises(HTTPException) as refused:
        await service._compute_report_engine(run, AssetReportInputs(bank_balances=[], receivables=[], payables=[]))
    assert refused.value.status_code == 422
    assert refused.value.detail['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
    assert 'Einheit 1' in refused.value.detail['message']


@pytest.mark.asyncio
async def test_partial_period_unit_correction_uses_the_covering_frozen_plan(client_a_admin, seed_data):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-06-30'})
    assert report.status_code == 201, report.text
    path = f"/api/weg-settlements/{report.json()['id']}/reserve-ledger"
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == '100.00'
    assert next(u for u in ledger['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '500.00'
    accepted = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '500.00', 'reason': 'Reconcile booked first-half Hausgeld against annual plan'})
    assert accepted.status_code == 201, accepted.text
    assert (await api.get(path)).json()['contributions_total'] == '600.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['api', 'database'])
async def test_zero_adjustment_is_refused_without_invalidating_computation(client_a_admin, seed_data, app_rw_session, boundary):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    values = paid(w)
    await attest_fixture(api, 'settlement', w['run'], values)
    computed = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert computed.status_code == 200, computed.text
    before = (await api.get(f"/api/weg-settlements/{w['run']}")).json()
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    ledger = (await api.get(path)).json()
    if boundary == 'api':
        refused = await api.post(path + '/overrides', json={'component': 'ADJUSTMENT', 'amount': '0.00',
            'reason': 'No change', **{k.removeprefix('reserve_'): v for k, v in sources.items()}})
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_AMOUNT_INVALID'
    else:
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
        with pytest.raises(DBAPIError) as refused:
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                    VALUES (:org,:property,:run,'2025-02-01','ADJUSTMENT',0,'No change',:cost,:register,:actor)"""),
                    {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'],
                     'cost': sources['reserve_cost_entry_id'], 'register': sources['reserve_register_entry_id'],
                     'actor': seed_data['user_a_admin_id']})
        assert refused.value.orig.sqlstate == '23514'
        assert refused.value.orig.__cause__.constraint_name == 'reserve_amount_invalid'
    assert (await api.get(path)).json() == ledger
    assert (await api.get(f"/api/weg-settlements/{w['run']}")).json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['', '/asset-reports'])
async def test_opening_total_correction_refuses_a_second_client_with_the_same_seen_opening(client_a_admin, seed_data, family):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if family:
        report = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        assert report.status_code == 201, report.text
        w['run'] = report.json()['id']
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    body = {'component': 'OPENING', 'corrected_opening_balance': '400.00', 'expected_opening_balance': '0.00',
        'reason': 'Correct total from bank evidence'}
    accepted = await api.post(path + '/overrides', json=body)
    assert accepted.status_code == 201, accepted.text
    before = (await api.get(path)).json()
    assert before['opening_balance'] == '400.00'
    refused = await api.post(path + '/overrides', json=body)
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail'] == {'code': 'RESERVE_OPENING_CONFLICT',
        'message': 'Der Anfangsbestand wurde inzwischen geändert. Bitte laden Sie das Rücklagenbuch neu.'}
    assert (await api.get(path)).json() == before
    accepted = await api.post(path + '/overrides', json={**body, 'corrected_opening_balance': '300.00', 'expected_opening_balance': '400.00'})
    assert accepted.status_code == 201, accepted.text
    after = (await api.get(path)).json()
    assert after['opening_balance'] == '300.00'
    assert [row['amount'] for row in after['entries'] if row.get('component') == 'OPENING'] == ['400.00', '-100.00']


@pytest.mark.asyncio
async def test_payout_storno_locks_the_cost_row_before_checking_adjustments(migrator_session):
    definition = await migrator_session.scalar(text("SELECT pg_get_functiondef('guard_reserve_payment_evidence()'::regprocedure)"))
    reversal = definition.split("IF OLD.payment_type='RUECKLAGENENTNAHME'")[1].split('RETURN NEW;')[0]
    assert 'SELECT * INTO cost FROM public.cost_entry' in reversal
    assert 'FOR UPDATE;' in reversal
    assert 'reserve_funded_amount(OLD.organization_id,cost.id,OLD.id)' in reversal


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint,status,detail', [
    ('reserve_period_finalized', 409, {'code': 'RESERVE_PERIOD_FINALIZED',
        'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'}),
    ('reserve_correction_already_zero', 422, {'code': 'RESERVE_AMOUNT_INVALID',
        'message': 'Diese Korrektur ist bereits vollständig aufgehoben. Es ist kein Restbetrag mehr vorhanden.'}),
    ('reserve_amount_invalid', 422, {'code': 'RESERVE_AMOUNT_INVALID',
        'message': 'Bitte geben Sie einen anderen, nicht negativen Korrekturbetrag ein.'}),
])
async def test_contribution_reduction_maps_named_database_guards(monkeypatch, constraint, status, detail):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from fastapi import HTTPException
    from app.api import weg_settlements as routes

    class DriverError(Exception):
        sqlstate = '23514'

    driver = DriverError('Unrelated driver message')
    driver.__cause__ = DriverError()
    driver.__cause__.constraint_name = constraint
    run_id, entry_id = uuid4(), uuid4()
    run = SimpleNamespace(id=run_id)
    failure = DBAPIError(None, None, driver)
    service = SimpleNamespace(subject=AsyncMock(return_value=run),
        reduce_contribution=AsyncMock(side_effect=failure))
    monkeypatch.setattr(routes, 'ReserveLedgerService', lambda *args: service)
    with pytest.raises(HTTPException) as refused:
        await routes._reduce_reserve_contribution(run_id, entry_id, Decimal('100.00'), 'Reconcile correction',
            SimpleNamespace(organization_id=uuid4(), user_id=uuid4()),
            SimpleNamespace(begin_nested=nullcontext))
    service.reduce_contribution.assert_awaited_once_with(run, entry_id, Decimal('100.00'), 'Reconcile correction')
    assert refused.value.status_code == status
    assert refused.value.detail == detail


async def draft_unit_correction(api, seed, *, two_receipts=False):
    w = _seed_weg(seed)
    receipts = [await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS')
                for _ in range(2 if two_receipts else 1)]
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    w['run'], w['cash'] = report.json()['id'], receipts[0]
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    for cash in receipts:
        assigned = await api.post(path, json={'component': 'CONTRIBUTION', 'payment_id': cash,
            'amount': '100.00', 'reason': 'Partial assignment before plan'})
        assert assigned.status_code == 201, assigned.text
    w['plan'] = await _create_and_finalize_plan(api, w)
    added = await api.post(path, json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '800.00' if two_receipts else '500.00', 'reason': 'Reconciled unit share'})
    assert added.status_code == 201, added.text
    w['entry'] = added.json()['id']
    return w, path + '/' + w['entry']


@pytest.mark.asyncio
@pytest.mark.parametrize('path_kind', ['service', 'guard'])
async def test_reduced_unit_correction_can_be_restored_only_to_its_original_amount(
        client_a_admin, seed_data, app_rw_session, migrator_session, path_kind):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    added = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '300.00', 'reason': 'Reviewed booked Hausgeld'})
    assert added.status_code == 201, added.text
    original = added.json()['id']
    correction = path + f'/overrides/{original}'
    reduction = await api.post(correction + '/reduce', json={'reduced_to': '100.00', 'reason': 'Wrong correction selected'})
    assert reduction.status_code == 201, reduction.text
    before = (await api.get(path)).json()
    assert next(u for u in before['contribution_headroom'] if u['unit_id'] == w['u1'])['remaining'] == '400.00'
    query = text('SELECT id,action,actor_user_id,payload_diff FROM audit_event WHERE entity_id=:run ORDER BY id')
    async def audit_events():
        async with app_rw_session.bind.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
                {'org': str(seed_data['org_a_id'])})
            return (await connection.execute(query, {'run': w['run']})).all()
    prior_events = await audit_events()
    assert any(event.action == 'reserve.contribution_reduced' for event in prior_events)
    if path_kind == 'service':
        restored = await api.post(correction + '/reduce', json={'reduced_to': '300.00', 'reason': 'Versehentlich verringert'})
        assert restored.status_code == 201, restored.text
        refused = await api.post(correction + '/reduce', json={'reduced_to': '400.00', 'reason': 'Exceeds original'})
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
        assert '300,00 €' in refused.json()['detail']['message']
    else:
        insert = text("""INSERT INTO reserve_ledger_entry
            (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id,reverses_entry_id)
            VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',:delta,'Versehentlich verringert',:actor,:unit,:original)""")
        values = {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'],
            'property': w['property'], 'run': w['run'], 'unit': w['u1'], 'original': original}
        async with app_rw_session.bind.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
            await connection.execute(insert, {**values, 'delta': Decimal('200.00')})
            with pytest.raises(DBAPIError) as refused:
                async with connection.begin_nested():
                    await connection.execute(insert, {**values, 'delta': Decimal('100.00')})
            assert refused.value.orig.__cause__.constraint_name == 'reserve_contribution_exceeds_booked'
    after = (await api.get(path)).json()
    assert next(row for row in after['entries'] if row.get('id') == original)['current_amount'] == '300.00'
    for row in before['entries']:
        if row.get('id') != original:
            assert row in after['entries']
    assert next(row for row in after['entries'] if row.get('id') == original) == {
        **next(row for row in before['entries'] if row.get('id') == original), 'current_amount': '300.00'}
    assert [row['amount'] for row in after['entries'] if row.get('reverses_entry_id') == original] == ['-200.00', '200.00']
    events = await audit_events()
    assert all(event in events for event in prior_events)
    if path_kind == 'service':
        event = next(event for event in events if event.action == 'reserve.contribution_restored')
        assert event.actor_user_id == seed_data['user_a_admin_id']
        assert event.payload_diff == {'entry_id': restored.json()['id'], 'reverses_entry_id': original,
            'old_amount': '100.00', 'new_amount': '300.00', 'reason': 'Versehentlich verringert'}
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    def refuse_downgrade(session):
        with Operations.context(MigrationContext.configure(session.connection())):
            with pytest.raises(RuntimeError, match='restored reserve contribution'):
                ScriptDirectory('migrations').get_revision('0154').module.downgrade()
    await migrator_session.run_sync(refuse_downgrade)


@pytest.mark.asyncio
async def test_zero_remaining_correction_explains_that_nothing_remains_to_delete(client_a_admin, seed_data, app_rw_session):
    api = client_a_admin
    w, path = await draft_unit_correction(api, seed_data)
    assert (await api.post(path + '/delete', json={'reason': 'Remove correction'})).status_code == 201
    refused = await api.post(path + '/delete', json={'reason': 'Already removed'})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail'] == {'code': 'RESERVE_AMOUNT_INVALID',
        'message': 'Diese Korrektur ist bereits vollständig aufgehoben. Es ist kein Restbetrag mehr vorhanden.'}
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id,reverses_entry_id)
                VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',0,'Already removed',:actor,:unit,:entry)"""),
                {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'], 'property': w['property'],
                 'run': w['run'], 'unit': w['u1'], 'entry': w['entry']})
    assert refused.value.orig.__cause__.constraint_name == 'reserve_correction_already_zero'
    await app_rw_session.rollback()
    restored = await api.post(path + '/reduce', json={'reduced_to': '500.00', 'reason': 'Versehentlich verringert'})
    assert restored.status_code == 201, restored.text


@pytest.mark.asyncio
@pytest.mark.parametrize('path_kind', ['service', 'guard'])
async def test_concurrent_hausgeld_reversal_limits_restoration_to_the_booked_200_euros(
        client_a_admin, seed_data, app_rw_session, monkeypatch, path_kind):
    import asyncio
    from app.services.payment_service import PaymentService
    from app.services.reserve_ledger_service import ReserveLedgerService

    api = client_a_admin
    w = _seed_weg(seed_data)
    receipts = [await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS') for _ in range(2)]
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    w['run'] = report.json()['id']
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    for cash, amount in zip(receipts, ('100.00', '400.00'), strict=True):
        assert (await api.post(path + '/overrides', json={'component': 'CONTRIBUTION',
            'payment_id': cash, 'amount': amount, 'reason': 'Reviewed payment share'})).status_code == 201
    w['plan'] = await _create_and_finalize_plan(api, w)
    added = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION', 'unit_id': w['u1'],
        'amount': '300.00', 'reason': 'Reconcile booked reserve'})
    assert added.status_code == 201, added.text
    original = added.json()['id']
    correction = path + f'/overrides/{original}/reduce'
    assert (await api.post(correction, json={'reduced_to': '100.00', 'reason': 'Wrong correction selected'})).status_code == 201
    reversed_cash, restoration_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    reverse, subject = PaymentService.reverse_payment, ReserveLedgerService.subject

    async def hold_reversal(service, *args, **kwargs):
        result = await reverse(service, *args, **kwargs)
        reversed_cash.set()
        await asyncio.wait_for(release.wait(), 10)
        return result

    async def observe_restoration(service, *args, **kwargs):
        restoration_started.set()
        return await subject(service, *args, **kwargs)

    async def direct_restoration():
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
        restoration_started.set()
        with pytest.raises(DBAPIError) as refused:
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id,reverses_entry_id)
                    VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',200,'Versehentlich verringert',:actor,:unit,:entry)"""),
                    {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'], 'property': w['property'],
                     'run': w['run'], 'unit': w['u1'], 'entry': original})
        assert refused.value.orig.__cause__.constraint_name == 'reserve_contribution_exceeds_booked'
        await app_rw_session.rollback()

    monkeypatch.setattr(PaymentService, 'reverse_payment', hold_reversal)
    monkeypatch.setattr(ReserveLedgerService, 'subject', observe_restoration)
    async with asyncio.TaskGroup() as tasks:
        reversal = tasks.create_task(api.post(f'/api/payments/{receipts[0]}/reverse'))
        try:
            await asyncio.wait_for(reversed_cash.wait(), 10)
            restoration = tasks.create_task(direct_restoration() if path_kind == 'guard' else
                api.post(correction, json={'reduced_to': '300.00', 'reason': 'Versehentlich verringert'}))
            await asyncio.wait_for(restoration_started.wait(), 10)
            assert not restoration.done()
        finally:
            release.set()
    assert reversal.result().status_code == 201, reversal.result().text
    if path_kind == 'service':
        assert restoration.result().status_code == 422, restoration.result().text
        assert restoration.result().json()['detail'] == {'code': 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED',
            'message': 'Sie können für diese Korrektur höchstens 200,00 € beibehalten.'}
    accepted = await api.post(correction, json={'reduced_to': '200.00', 'reason': 'Restore only booked backing'})
    assert accepted.status_code == 201, accepted.text
    ledger = (await api.get(path)).json()
    assert next(row for row in ledger['entries'] if row.get('id') == original)['current_amount'] == '200.00'
    assert ledger['contributions_total'] == '600.00'


def test_direct_zero_counter_entry_audit_boundary_is_documented():
    architecture = (Path(__file__).resolve().parents[2] / 'docs/architecture.md').read_text()
    _, heading, section = architecture.partition('### Contribution reduction audit path')
    assert heading, 'Document the service audit and direct INSERT boundary'
    section = ' '.join(section.split('\n## ', 1)[0].split())
    for contract in (
        'ReserveLedgerService.reduce_contribution',
        'reserve.contribution_reduced',
        'POST /api/weg-settlements/{run_id}/reserve-ledger/overrides/{entry_id}/reduce',
        'POST /api/weg-settlements/{run_id}/reserve-ledger/overrides/{entry_id}/delete',
        'Direct SQL INSERT is not a supported application path',
        '300.00', '-300.00', '0.00',
        'does not emit `reserve.contribution_reduced`',
    ):
        assert contract in section


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['delete', 'reduce', 'direct_insert'])
async def test_zero_contribution_counter_entry_is_audited_only_through_the_service(
        client_a_admin, client_b_admin, seed_data, app_rw_session, operation):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    added = await api.post(path + '/overrides', json={
        'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': '300.00',
        'reason': 'Reconcile booked unit reserve share'})
    assert added.status_code == 201, added.text
    original_id = added.json()['id']
    assert (await api.post(f"/api/payments/{w['cash']}/reverse")).status_code == 201
    before = (await api.get(path)).json()
    assert before['contributions_total'] == '300.00'
    assert next(row for row in before['entries'] if row.get('id') == original_id)['current_amount'] == '300.00'

    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    assert await app_rw_session.scalar(text('SELECT current_user')) == 'app_rw'
    audit_query = text('SELECT id,action,actor_user_id,payload_diff FROM audit_event WHERE entity_id=:run')
    prior_events = (await app_rw_session.execute(audit_query, {'run': w['run']})).all()
    assert not any(row.action == 'reserve.contribution_reduced' for row in prior_events)
    reason = 'Fehlbuchung'
    if operation == 'direct_insert':
        async with app_rw_session.bind.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
            counter_id = str(await connection.scalar(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id,reverses_entry_id)
                VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',-300.00,:reason,:actor,:unit,:entry)
                RETURNING id"""), {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'],
                'property': w['property'], 'run': w['run'], 'unit': w['u1'], 'entry': original_id, 'reason': reason}))
    else:
        url = path + '/overrides/' + original_id
        response = await api.post(url + '/delete', json={'reason': reason}) if operation == 'delete' else await api.post(
            url + '/reduce', json={'reduced_to': '0.00', 'reason': reason})
        assert response.status_code == 201, response.text
        counter_id = response.json()['id']

    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    events = (await app_rw_session.execute(audit_query, {'run': w['run']})).all()
    new_events = [row for row in events if row.id not in {event.id for event in prior_events}]
    if operation == 'direct_insert':
        assert new_events == []
        assert not any(row.action == 'reserve.contribution_reduced' for row in events)
    else:
        assert len(new_events) == 1
        audit = new_events[0]
        assert audit.action == 'reserve.contribution_reduced'
        assert audit.actor_user_id == seed_data['user_a_admin_id']
        assert audit.payload_diff == {'entry_id': counter_id, 'reverses_entry_id': original_id,
            'old_amount': '300.00', 'new_amount': '0.00', 'reason': reason}

    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == ledger['closing_balance'] == '0.00'
    original = next(row for row in ledger['entries'] if row.get('id') == original_id)
    assert original['amount'] == '300.00' and original['current_amount'] == '0.00'
    counters = [row for row in ledger['entries'] if row.get('reverses_entry_id') == original_id]
    assert len(counters) == 1
    assert counters[0]['id'] == counter_id and counters[0]['amount'] == '-300.00'
    assert counters[0]['reason'] == reason and counters[0]['unit_id'] == w['u1']
    assert (await client_b_admin.get(path)).status_code == 404
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_b_id']), 'actor': str(seed_data['user_b_admin_id'])})
    assert (await app_rw_session.execute(audit_query, {'run': w['run']})).all() == []
    assert await app_rw_session.scalar(text('SELECT count(*) FROM reserve_ledger_entry WHERE id IN (:original,:counter)'),
        {'original': original_id, 'counter': counter_id}) == 0


@pytest.mark.asyncio
async def test_delete_unbacked_contribution_correction_restores_headroom_and_finalization(client_a_admin, seed_data, app_rw_session):
    api = client_a_admin
    w, path = await draft_unit_correction(api, seed_data)
    assert (await api.post(f"/api/payments/{w['cash']}/reverse")).status_code == 201
    deleted = await api.post(path + '/delete', json={'reason': 'Remove correction after returned Hausgeld'})
    assert deleted.status_code == 201, deleted.text
    ledger = (await api.get(path.rsplit('/overrides/', 1)[0])).json()
    assert ledger['contributions_total'] == '0.00'
    original = next(row for row in ledger['entries'] if row.get('id') == w['entry'])
    assert original['amount'] == '500.00' and original['current_amount'] == '0.00'
    reversal = next(row for row in ledger['entries'] if row.get('id') == deleted.json()['id'])
    assert reversal['amount'] == '-500.00' and reversal['reverses_entry_id'] == w['entry']
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    assert await app_rw_session.scalar(text('SELECT reserve_contribution_headroom(:org,:run,:unit)'),
        {'org': seed_data['org_a_id'], 'run': w['run'], 'unit': w['u1']}) == 0
    audit = (await app_rw_session.execute(text("SELECT actor_user_id,payload_diff FROM audit_event WHERE action='reserve.contribution_reduced' AND entity_id=:run"), {'run': w['run']})).one()
    assert audit.actor_user_id == seed_data['user_a_admin_id']
    assert audit.payload_diff['old_amount'] == '500.00' and audit.payload_diff['new_amount'] == '0.00'
    assert audit.payload_diff['reason'] == 'Remove correction after returned Hausgeld'
    assert audit.payload_diff['reverses_entry_id'] == w['entry']
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '0.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'}],
        'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', w['run'], values)
    finalized = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
    assert finalized.status_code == 200, finalized.text


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal_path', ['service', 'guard'])
@pytest.mark.parametrize('remaining_assignment,headroom,limit,target,message', [
    ('400.00', '-100.00', '200.00', '250.00', 'Sie können für diese Korrektur höchstens 200,00 € beibehalten.'),
    ('600.00', '-300.00', '0.00', '100.00', 'Für diese Korrektur ist kein positiver Restbetrag mehr möglich. Bitte verwenden Sie „Korrektur löschen“.'),
    ('700.00', '-400.00', '-100.00', '100.00', 'Für diese Korrektur ist kein positiver Restbetrag mehr möglich. Bitte verwenden Sie „Korrektur löschen“.'),
], ids=['positive', 'zero', 'negative'])
async def test_contribution_reduction_uses_the_same_limit_and_detail_for_both_refusal_paths(
        client_a_admin, seed_data, app_rw_session, monkeypatch, refusal_path, remaining_assignment, headroom, limit, target, message):
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    from app.services.reserve_ledger_service import ReserveLedgerService

    api = client_a_admin
    w = _seed_weg(seed_data)
    receipts = [await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS') for _ in range(2)]
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    w['run'] = report.json()['id']
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    for cash, amount in zip(receipts, ('100.00', remaining_assignment), strict=True):
        assigned = await api.post(path + '/overrides', json={
            'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': amount,
            'reason': 'Partial assignment before plan'})
        assert assigned.status_code == 201, assigned.text
    w['plan'] = await _create_and_finalize_plan(api, w)
    added = await api.post(path + '/overrides', json={
        'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': '300.00',
        'reason': 'Reconcile booked unit reserve share'})
    assert added.status_code == 201, added.text
    assert (await api.post(f'/api/payments/{receipts[0]}/reverse')).status_code == 201
    before = (await api.get(path)).json()
    assert next(row for row in before['entries'] if row.get('id') == added.json()['id'])['current_amount'] == '300.00'
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
        {'org': str(seed_data['org_a_id'])})
    assert await app_rw_session.scalar(text('SELECT reserve_contribution_headroom(:org,:run,:unit)'),
        {'org': seed_data['org_a_id'], 'run': w['run'], 'unit': w['u1']}) == Decimal(headroom)
    await app_rw_session.rollback()

    read_headroom = ReserveLedgerRepository.contribution_headroom
    headroom_reads, calculations = [], []
    reduction_limit = getattr(ReserveLedgerService, 'contribution_reduction_limit', None)

    async def observed_limit(service, *args):
        result = await reduction_limit(service, *args)
        calculations.append(result)
        return result

    async def observed_headroom(repo, *args):
        remaining = await read_headroom(repo, *args)
        headroom_reads.append(remaining)
        # Simulate a stale service read; the real INSERT guard sees the current backing.
        if refusal_path == 'guard' and len(headroom_reads) == 1:
            return Decimal('0.00')
        return remaining

    monkeypatch.setattr(ReserveLedgerService, 'contribution_reduction_limit', observed_limit, raising=False)
    monkeypatch.setattr(ReserveLedgerRepository, 'contribution_headroom', observed_headroom)
    refused = await api.post(path + f"/overrides/{added.json()['id']}/reduce", json={
        'reduced_to': target, 'reason': 'Reconcile after Hausgeld reversal'})
    expected_detail = {'code': 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED',
        'message': message}
    assert refused.status_code == 422, refused.text
    assert refused.json() == {'detail': expected_detail}
    expected_reads = 2 if refusal_path == 'guard' else 1
    assert headroom_reads == [Decimal(headroom)] * expected_reads
    assert len(calculations) == expected_reads
    assert calculations[-1] == (Decimal(limit), expected_detail)
    assert (await api.get(path)).json() == before


@pytest.mark.asyncio
async def test_reduce_contribution_correction_to_new_headroom_then_delete_remaining_amount(client_a_admin, seed_data):
    api = client_a_admin
    w, path = await draft_unit_correction(api, seed_data, two_receipts=True)
    assert (await api.post(f"/api/payments/{w['cash']}/reverse")).status_code == 201
    for amount in ('800.01', '500.01'):
        refused = await api.post(path + '/reduce', json={'reduced_to': amount, 'reason': 'Reconcile after reversal'})
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED'
        assert '500,00 €' in refused.json()['detail']['message']
    reduced = await api.post(path + '/reduce', json={'reduced_to': '500.00', 'reason': 'Reconcile after reversal'})
    assert reduced.status_code == 201, reduced.text
    ledger = (await api.get(path.rsplit('/overrides/', 1)[0])).json()
    assert ledger['contributions_total'] == '600.00'
    assert next(row for row in ledger['entries'] if row.get('id') == w['entry'])['current_amount'] == '500.00'
    reduced = await api.post(path + '/reduce', json={'reduced_to': '200.00', 'reason': 'Further reconciliation'})
    assert reduced.status_code == 201, reduced.text
    deleted = await api.post(path + '/delete', json={'reason': 'Remove remainder'})
    assert deleted.status_code == 201, deleted.text
    ledger = (await api.get(path.rsplit('/overrides/', 1)[0])).json()
    assert [row['amount'] for row in ledger['entries'] if row.get('reverses_entry_id') == w['entry']] == ['-300.00', '-300.00', '-200.00']
    assert ledger['contributions_total'] == '100.00'


@pytest.mark.asyncio
async def test_concurrent_finalization_refuses_reduction_with_the_finalized_message(client_a_admin, seed_data, monkeypatch):
    import asyncio
    from app.services.reserve_ledger_service import ReserveLedgerService
    from app.services.weg_settlement_service import WegSettlementService

    api = client_a_admin
    w, path = await draft_unit_correction(api, seed_data)
    sibling = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': w['plan'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert sibling.status_code == 201, sibling.text
    sibling_id = sibling.json()['id']
    await attest_fixture(api, 'settlement', sibling_id, paid(w))
    finalized, reduction_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finalize_weg, subject = WegSettlementService.finalize_weg, ReserveLedgerService.subject

    async def hold_finalization(service, *args, **kwargs):
        run = await finalize_weg(service, *args, **kwargs)
        finalized.set()
        await asyncio.wait_for(release.wait(), 10)
        return run

    async def observe_reduction(service, *args, **kwargs):
        if kwargs.get('require_draft') is False:
            reduction_started.set()
        return await subject(service, *args, **kwargs)

    monkeypatch.setattr(WegSettlementService, 'finalize_weg', hold_finalization)
    monkeypatch.setattr(ReserveLedgerService, 'subject', observe_reduction)
    async with asyncio.TaskGroup() as tasks:
        finalization = tasks.create_task(api.post(f'/api/weg-settlements/{sibling_id}/finalize', json=paid(w)))
        try:
            await asyncio.wait_for(finalized.wait(), 10)
            reduction = tasks.create_task(api.post(path + '/reduce',
                json={'reduced_to': '100.00', 'reason': 'Reconcile while colleague finalizes'}))
            await asyncio.wait_for(reduction_started.wait(), 10)
            assert not reduction.done()
        finally:
            release.set()
    assert finalization.result().status_code == 200, finalization.result().text
    refused = reduction.result()
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail'] == {'code': 'RESERVE_PERIOD_FINALIZED',
        'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'}
    ledger = (await api.get(path.rsplit('/overrides/', 1)[0])).json()
    assert ledger['frozen'] is True
    assert ledger['contributions_total'] == '600.00'
    original = next(row for row in ledger['entries'] if row.get('id') == w['entry'])
    assert original['current_amount'] == '500.00'
    assert not any(row.get('reverses_entry_id') == w['entry'] for row in ledger['entries'])


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['reduce', 'delete'])
async def test_contribution_reduction_requires_reason_hides_foreign_ids_and_freezes_period(client_a_admin, client_b_admin, seed_data, operation):
    api = client_a_admin
    w, path = await draft_unit_correction(api, seed_data)
    async def change(client, reason=None):
        body = {} if reason is None else {'reason': reason}
        if operation == 'reduce':
            return await client.post(path + '/reduce', json={**body, 'reduced_to': '100.00'})
        return await client.post(path + '/delete', json=body)
    if operation == 'delete':
        refused = await api.post(path + '/delete')
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_CORRECTION_REASON_REQUIRED'
    for reason in (None, '', ' '):
        refused = await change(api, reason)
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_CORRECTION_REASON_REQUIRED'
    assert (await change(client_b_admin, 'Foreign correction')).status_code == 404
    # A finalized WEG sibling freezes the same draft asset-report period.
    sibling = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': w['plan'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert sibling.status_code == 201, sibling.text
    await finalize(api, {**w, 'run': sibling.json()['id']})
    refused = await change(api, 'Frozen correction')
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['reduce', 'delete'])
async def test_contribution_reduction_refuses_other_ledger_components_and_payment_assignments(client_a_admin, seed_data, operation):
    api = client_a_admin
    w = await unit_correction_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    added = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '100.00', 'reason': 'Opening'})
    ledger = (await api.get(path)).json()
    assignment = next(row['id'] for row in ledger['entries'] if row.get('payment_id') == w['cash'])
    for entry in (added.json()['id'], assignment):
        url = path + '/overrides/' + entry
        refused = await api.post(url + '/reduce', json={'reduced_to': '1.00', 'reason': 'Wrong component'}) if operation == 'reduce' else await api.post(url + '/delete', json={'reason': 'Wrong component'})
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_INPUT_INVALID'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['update', 'delete'])
async def test_database_refuses_direct_mutation_of_contribution_original_as_app_rw(client_a_admin, seed_data, app_rw_session, operation):
    w, _ = await draft_unit_correction(client_a_admin, seed_data)
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text('UPDATE reserve_ledger_entry SET amount=0 WHERE id=:entry' if operation == 'update' else 'DELETE FROM reserve_ledger_entry WHERE id=:entry'), {'entry': w['entry']})
    assert refused.value.orig.sqlstate == ('42501' if operation == 'update' else '23514')
    if operation == 'delete':
        assert refused.value.orig.__cause__.constraint_name == 'reserve_ledger_append_only'


@pytest.mark.asyncio
@pytest.mark.parametrize('case,constraint', [('above_headroom', 'reserve_contribution_exceeds_booked'),
    ('increase', 'reserve_contribution_exceeds_booked'), ('blank_reason', 'reserve_correction_reason_required'),
    ('not_contribution', 'reserve_contribution_reduction_invalid'), ('finalized', 'reserve_period_finalized')])
async def test_database_mirrors_contribution_reduction_rules(client_a_admin, seed_data, app_rw_session, case, constraint):
    api = client_a_admin
    w, path = await draft_unit_correction(api, seed_data, two_receipts=True)
    if case == 'finalized':
        sibling = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': w['plan'],
            'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        await finalize(api, {**w, 'run': sibling.json()['id']})
    else:
        assert (await api.post(f"/api/payments/{w['cash']}/reverse")).status_code == 201
    if case == 'not_contribution':
        opening = await api.post(path.rsplit('/', 1)[0], json={'component': 'OPENING', 'amount': '100.00', 'reason': 'Opening'})
        w['entry'] = opening.json()['id']
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id,reverses_entry_id)
                VALUES (:org,:property,:run,'2025-12-31','CONTRIBUTION',:delta,:reason,:actor,:unit,:entry)"""),
                {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'], 'property': w['property'],
                 'run': w['run'], 'unit': w['u1'], 'entry': w['entry'],
                 'delta': Decimal('0.01') if case == 'increase' else Decimal('-299.99'),
                 'reason': ' ' if case == 'blank_reason' else 'Direct reduction'})
    assert refused.value.orig.sqlstate == '23514'
    assert refused.value.orig.__cause__.constraint_name == constraint


@pytest.mark.asyncio
async def test_opening_period_refusal_precedes_stale_opening_conflict(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    later = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    refused = await api.post(f"/api/weg-settlements/{later.json()['id']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'corrected_opening_balance': '300.00', 'expected_opening_balance': '1.00', 'reason': 'Stale forbidden import'})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'


@pytest.mark.asyncio
async def test_payout_storno_maps_trigger_refusal_even_when_reread_finds_no_refusal(client_a_admin, seed_data, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.reserve_ledger_service import ReserveLedgerService
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    sources = await reserve_evidence(api, w)
    cash = await payment(api, w, '1200.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=sources)
    added = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '200.00', 'reason': 'Reduce withdrawal',
        **{k.removeprefix('reserve_'): v for k, v in sources.items()}})
    assert added.status_code == 201, added.text
    monkeypatch.setattr(ReserveLedgerService, 'require_payout_reversal', AsyncMock())
    monkeypatch.setattr(ReserveLedgerService, 'payout_reversal_refusal', AsyncMock(return_value=None))
    refused = await api.post(f'/api/payments/{cash}/reverse')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_ADJUSTMENT_EXCEEDS_WITHDRAWALS'


@pytest.mark.asyncio
@pytest.mark.parametrize('fields', [
    {'amount': '300.00', 'expected_opening_balance': '0.00'},
    {'corrected_opening_balance': '300.00'},
    {'amount': '300.00', 'corrected_opening_balance': '300.00', 'expected_opening_balance': '0.00'},
    {'component': 'CONTRIBUTION', 'corrected_opening_balance': '300.00', 'expected_opening_balance': '0.00'},
])
async def test_opening_override_accepts_only_an_unambiguous_delta_or_paired_total(client_a_admin, seed_data, fields):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'reason': 'Reviewed bank opening', **fields})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_INPUT_INVALID'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['reduce', 'delete'])
@pytest.mark.parametrize('finalized_parent', [False, True])
async def test_contribution_reduction_hides_foreign_and_unknown_entries_before_frozen_conflict(
        client_a_admin, client_b_admin, seed_data, operation, finalized_parent):
    import uuid
    api = client_a_admin
    w, path = await draft_unit_correction(api, seed_data)
    beta = _seed_weg({**seed_data, 'org_a_id': seed_data['org_b_id']})
    foreign_run = await client_b_admin.post('/api/weg-settlements/asset-reports', json={
        'property_id': beta['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert foreign_run.status_code == 201, foreign_run.text
    foreign = await client_b_admin.post(f"/api/weg-settlements/{foreign_run.json()['id']}/reserve-ledger/overrides",
        json={'component': 'OPENING', 'amount': '100.00', 'reason': 'Foreign opening'})
    assert foreign.status_code == 201, foreign.text
    sibling = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': w['plan'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert sibling.status_code == 201, sibling.text
    await finalize(api, {**w, 'run': sibling.json()['id']})
    run_id = sibling.json()['id'] if finalized_parent else w['run']
    responses = []
    for entry in (foreign.json()['id'], str(uuid.uuid4())):
        url = f'/api/weg-settlements/{run_id}/reserve-ledger/overrides/{entry}'
        response = await api.post(url + '/reduce', json={'reduced_to': '1.00', 'reason': 'Foreign entry'}) if operation == 'reduce' else await api.post(url + '/delete', json={'reason': 'Foreign entry'})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]


@pytest.mark.asyncio
async def test_database_refuses_reducing_a_frozen_original_through_another_scope(
        client_a_admin, seed_data, migrator_session, app_rw_session):
    from uuid import uuid4
    api = client_a_admin
    w, _ = await draft_unit_correction(api, seed_data)
    sibling = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': w['plan'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    await finalize(api, {**w, 'run': sibling.json()['id']})
    ids = {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'], 'property': w['property'],
        'original_scope': uuid4(), 'destination_scope': uuid4(), 'destination': uuid4(), 'unit': w['u1']}
    for key, whole in [('original_scope', False), ('destination_scope', True)]:
        await migrator_session.execute(text("""INSERT INTO accounting_scope
            (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
            VALUES (:scope,:org,:property,'WEG','Reviewed reserve scope','2025-01-01',:whole,'REVIEWED',:actor)"""),
            {**ids, 'scope': ids[key], 'whole': whole})
        if not whole:
            await migrator_session.execute(text('INSERT INTO accounting_scope_unit(organization_id,scope_id,property_id,unit_id) VALUES (:org,:original_scope,:property,:unit)'), ids)
        await migrator_session.execute(text("""INSERT INTO scope_grant
            (organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (:org,:scope,:actor,ARRAY['read','prepare','approve'],:actor,'Reviewed reserve scope')"""),
            {**ids, 'scope': ids[key]})
    await migrator_session.execute(text("""INSERT INTO accounting_scope_binding
        (organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by)
        VALUES (:org,:property,:original_scope,:plan,'REVIEWED','Reviewed reserve plan',:actor)"""), {**ids, 'plan': w['plan']})
    for run in (w['run'], sibling.json()['id']):
        await migrator_session.execute(text("""INSERT INTO accounting_scope_binding
            (organization_id,property_id,scope_id,settlement_run_id,origin,reason,reviewed_by)
            VALUES (:org,:property,:original_scope,:run,'REVIEWED','Reviewed reserve period',:actor)"""), {**ids, 'run': run})
    await migrator_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),
        {'org': str(ids['org']), 'actor': str(ids['actor']), 'scope': str(ids['destination_scope'])})
    await migrator_session.execute(text("""INSERT INTO settlement_run
        (id,organization_id,property_id,run_type,period_start,period_end,status,engine_version,advance_payments_source)
        VALUES (:destination,:org,:property,'VERMOEGENSBERICHT','2025-01-01','2025-12-31','draft','0.1','MANUAL')"""), ids)
    await migrator_session.commit()
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id','',true)"),
        {'org': str(ids['org']), 'actor': str(ids['actor'])})
    assert await app_rw_session.scalar(text("SELECT financial_scope_access(:org,'settlement_run',:run,'read')"), {**ids, 'run': w['run']}) is True
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by,unit_id,reverses_entry_id)
                VALUES (:org,:property,:destination,'2025-12-31','CONTRIBUTION',-500,'Foreign scope reversal',:actor,:unit,:entry)"""),
                {**ids, 'entry': w['entry']})
    assert refused.value.orig.sqlstate == '23514'
    assert refused.value.orig.__cause__.constraint_name == 'reserve_contribution_reduction_invalid'


def test_contribution_deletion_uses_only_the_post_action_route():
    from tests.test_route_isolation_sweep import app_routes
    routes = app_routes()
    base = '/api/weg-settlements/{run_id}/reserve-ledger/overrides/{entry_id}'
    assert 'POST ' + base + '/delete' in routes
    assert 'DELETE ' + base not in routes


@pytest.fixture
async def retention_loop():
    import asyncio
    return asyncio.get_running_loop()


@pytest.mark.parametrize('erase', ['anna', 'eva'])
def test_external_retention_setup_preserves_the_original_privacy_assertions(
        retention_loop, client_a_admin, client_b_admin, client_unauth, seed_data, monkeypatch, erase, rent_world, export_store):
    from tests.bdd.world import World
    from tests.bdd.test_maintenance_reserve_ledger import (
        reserve_privacy_case, request_retained_anna, retained_repayment_export,
        request_incidental_eva, incidental_reason_export, preserved_privacy_snapshot,
    )
    world = World(retention_loop, [client_a_admin, client_b_admin], client_unauth)
    isolated_client = api_client(rent_world)
    from types import SimpleNamespace
    from datetime import timedelta
    from tests.bdd.steps.person_retention import retention_setup
    from tests.bdd.steps.person_retention import repayment_only, review_person, repayment_binding, erase_recipient, restricted_recipient

    def forbidden(*args, **kwargs):
        pytest.fail('External retention setup requested a local database fixture or connection')

    # Exercise the external setup path against the real local HTTP app. Only
    # the test's in-process server clock is accelerated; remote cooldown behavior
    # is covered separately in test_scenario_target.py.
    with monkeypatch.context() as guard:
        guard.setattr('tests.bdd.world.target_url', lambda: 'http://synthetic.invalid')
        guard.setattr('tests.bdd.steps.person_retention.target_url', lambda: 'http://synthetic.invalid')
        guard.setattr('psycopg2.connect', forbidden)
        guard.setattr(world, 'administrator_of', lambda name: isolated_client)
        guard.setattr('app.services.organization_export_service.EXPORT_INTERVAL', timedelta(0))
        source = retention_setup(world, SimpleNamespace(getfixturevalue=forbidden), {**seed_data, 'org_a_id': rent_world['org'], 'user_a_admin_id': rent_world['admin']})
        try:
            context = next(source)
            repayment_only(world, context)
            review_person(world)
            repayment_binding(world)
            erase_recipient(world)
            restricted_recipient(world)
            w = reserve_privacy_case(world, seed_data, context, monkeypatch)
            if erase == 'anna':
                request_retained_anna(world, w)
                retained_repayment_export(world, w)
            else:
                request_incidental_eva(world, w)
                incidental_reason_export(world, w)
            preserved_privacy_snapshot(world, w)
        finally:
            source.close()
            world.run(isolated_client.aclose())
