"""Tenant/provider destination boundaries through the existing settings API."""
import json

import pytest


async def _ai_audit_payloads(session, org_id):
    from sqlalchemy import text
    result = await session.execute(
        text("SELECT payload_diff FROM audit_event WHERE organization_id=:org AND entity_type='organization_ai_settings' ORDER BY occurred_at"),
        {'org': org_id})
    return [row[0] for row in result.all()]


@pytest.mark.asyncio
async def test_destination_change_never_reuses_or_discovers_with_an_existing_secret(client_a_admin, seed_data):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    first = await client_a_admin.put(path,json={'api_key':'synthetic-original','model':'gpt-5.6-luna','import_enabled':True,'confirmation':True})
    assert first.status_code == 200
    discover = await client_a_admin.post(path+'/models',json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1'})
    assert discover.status_code == 422
    changed = await client_a_admin.put(path,json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1','model':'local-model','import_enabled':True})
    assert changed.status_code == 422
    assert (await client_a_admin.get(path)).json()['provider'] == 'openai'
    changed = await client_a_admin.put(path,json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1'})
    assert changed.status_code == 200
    assert changed.json()['key_set'] is False
    assert changed.json()['import_enabled'] is False
    assert 'synthetic-original' not in changed.text


@pytest.mark.asyncio
async def test_custom_model_name_does_not_claim_official_provider_price(client_a_admin, seed_data):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    response = await client_a_admin.put(path,json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1','api_key':'synthetic-local','model':'gpt-5.6-luna','import_enabled':True,'monthly_limit_usd':'0','confirmation':True})
    assert response.status_code == 200
    assert response.json()['price_known'] is False


@pytest.mark.asyncio
async def test_custom_provider_key_is_encrypted_at_rest(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    response = await client_a_admin.put(path,json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1','api_key':'synthetic-local','model':'local-model','confirmation':True})
    assert response.status_code == 200
    encrypted = await migrator_session.scalar(text('SELECT encrypted_api_key FROM organization_ai_settings WHERE organization_id=:org'),{'org':seed_data['org_a_id']})
    assert encrypted and 'synthetic-local' not in encrypted


@pytest.mark.asyncio
async def test_timeout_is_persisted_validated_and_reset_for_new_destination(client_a_admin, seed_data, monkeypatch):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    assert (await client_a_admin.get(path)).json()['request_timeout_seconds'] is None
    body = {'provider':'openai_compatible','base_url':'http://localhost:11434/v1','request_timeout_seconds':240}
    assert (await client_a_admin.put(path,json=body)).json()['request_timeout_seconds'] == 240
    assert (await client_a_admin.get(path)).json()['request_timeout_seconds'] == 240
    from app.declarations import openai_models
    async def listed(key, **options):
        assert options['timeout_seconds'] == 240
        return ['synthetic-model']
    monkeypatch.setattr(openai_models, 'list_models', listed)
    assert (await client_a_admin.post(path+'/models',json={'api_key':'synthetic-local'})).status_code == 200
    for invalid in (0, -1, 3601, 1.5, True):
        assert (await client_a_admin.put(path,json={**body,'request_timeout_seconds':invalid})).status_code == 422
    changed = await client_a_admin.put(path,json={'provider':'openai_compatible','base_url':'http://localhost:1234/v1'})
    assert changed.json()['request_timeout_seconds'] is None


@pytest.mark.asyncio
async def test_unchanged_ai_settings_save_writes_no_audit_event(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    body = {'api_key':'synthetic-unchanged-key','model':'gpt-5.6-luna','import_enabled':True,'confirmation':True}
    assert (await client_a_admin.put(path, json=body)).status_code == 200
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    assert (await client_a_admin.put(path, json=body)).status_code == 200
    assert len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id'])) == before


@pytest.mark.asyncio
async def test_ai_key_set_and_replacement_are_audited_without_the_key(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    await client_a_admin.put(path, json={'api_key': None, 'import_enabled': False, 'qa_enabled': False})
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    assert (await client_a_admin.put(path, json={'api_key':'synthetic-first-key','model':'gpt-5.6-luna','confirmation':True})).status_code == 200
    assert (await client_a_admin.put(path, json={'api_key':'synthetic-second-key','model':'gpt-5.6-luna','confirmation':True})).status_code == 200
    payloads = (await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))[before:]
    assert payloads == [{'api_key': 'set', 'confirmation': 'recorded'}, {'api_key': 'replaced', 'confirmation': 'recorded'}]
    assert 'synthetic-first-key' not in json.dumps(payloads)
    assert 'synthetic-second-key' not in json.dumps(payloads)


@pytest.mark.asyncio
async def test_ai_qa_switch_change_is_audited(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    assert (await client_a_admin.put(path, json={'api_key':'synthetic-qa-key','model':'gpt-5.6-luna','confirmation':True})).status_code == 200
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    assert (await client_a_admin.put(path, json={'model':'gpt-5.6-luna','qa_enabled':True})).status_code == 200
    payloads = (await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))[before:]
    assert payloads == [{'qa_enabled': {'from': False, 'to': True}}]


@pytest.mark.asyncio
async def test_refused_ai_settings_save_writes_no_audit_event(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    await client_a_admin.put(path, json={'api_key': None, 'import_enabled': False, 'qa_enabled': False})
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    response = await client_a_admin.put(path, json={'import_enabled': True})
    assert response.status_code == 422
    assert len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id'])) == before


@pytest.mark.asyncio
async def test_storing_a_key_without_confirmation_is_refused_with_a_structured_code(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    response = await client_a_admin.put(path, json={'api_key':'synthetic-key','model':'gpt-5.6-luna','import_enabled':True})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'ORGANIZATION_AI_CONFIRMATION_REQUIRED'
    read = (await client_a_admin.get(path)).json()
    assert read['key_set'] is False
    assert read['confirmation_required'] is False
    assert len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id'])) == before


@pytest.mark.asyncio
async def test_confirmation_records_who_time_version_and_destination(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    response = await client_a_admin.put(path, json={'api_key':'synthetic-key','model':'gpt-5.6-luna','confirmation':True})
    assert response.status_code == 200, response.text
    body = (await client_a_admin.get(path)).json()
    assert body['confirmation_required'] is False
    confirmation = body['confirmation']
    assert set(confirmation) == {'confirmed_by_name', 'confirmed_at'}
    assert confirmation['confirmed_at']
    row = (await migrator_session.execute(text(
        'SELECT c.confirmed_by_user_id, c.provider, c.base_url, c.text_version, u.display_name'
        ' FROM organization_ai_confirmation c'
        ' JOIN "user" u ON u.id=c.confirmed_by_user_id AND u.organization_id=c.organization_id'
        ' WHERE c.organization_id=:org'), {'org': seed_data['org_a_id']})).mappings().one()
    assert row['provider'] == 'openai'
    assert row['base_url'] is None
    # The text of version 3 also covers invoices (issue #500) and the documents of the inbox (issue #501); it is the version
    # every new confirmation records.
    assert row['text_version'] == '3'
    assert body['invoice_confirmation_required'] is False and body['inbox_confirmation_required'] is False
    assert row['confirmed_by_user_id'] == seed_data['user_a_admin_id']
    assert row['display_name'] == confirmation['confirmed_by_name']


@pytest.mark.asyncio
async def test_stored_key_without_confirmation_stays_off_until_confirmed(client_a_admin, seed_data, migrator_session, monkeypatch):
    import psycopg2
    from app.services.ai_settings_service import _cipher
    from tests.conftest import MIGRATOR_SYNC_URL
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    encrypted = _cipher(seed_data['org_a_id']).encrypt(b'synthetic-legacy-key').decode()
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('INSERT INTO organization_ai_settings (organization_id,encrypted_api_key) VALUES (%s,%s)',
                       (str(seed_data['org_a_id']), encrypted))
    body = (await client_a_admin.get(path)).json()
    assert body['key_set'] is True
    assert body['confirmation_required'] is True
    assert body['confirmation'] is None
    # Fail closed: the stored key is not handed to the provider.
    assert (await client_a_admin.post(path+'/models', json={})).status_code == 422
    confirmed = await client_a_admin.put(path, json={'confirmation': True})
    assert confirmed.status_code == 200, confirmed.text
    body = (await client_a_admin.get(path)).json()
    assert body['confirmation_required'] is False
    assert body['confirmation']['confirmed_by_name']
    from app.declarations import openai_models
    async def listed(key, **options):
        assert key == 'synthetic-legacy-key'
        return ['gpt-5.6-luna']
    monkeypatch.setattr(openai_models, 'list_models', listed)
    usable = await client_a_admin.post(path+'/models', json={})
    assert usable.status_code == 200, usable.text


@pytest.mark.asyncio
async def test_replacing_the_key_or_destination_requires_a_new_confirmation(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    stored = await client_a_admin.put(path, json={'api_key':'synthetic-first','model':'gpt-5.6-luna','confirmation':True})
    assert stored.status_code == 200, stored.text
    replaced = await client_a_admin.put(path, json={'api_key':'synthetic-second','model':'gpt-5.6-luna'})
    assert replaced.status_code == 422
    assert replaced.json()['detail']['code'] == 'ORGANIZATION_AI_CONFIRMATION_REQUIRED'
    body = (await client_a_admin.get(path)).json()
    assert body['key_set'] is True
    assert body['confirmation_required'] is False
    moved = await client_a_admin.put(path, json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1','api_key':'synthetic-local','model':'local-model'})
    assert moved.status_code == 422
    assert moved.json()['detail']['code'] == 'ORGANIZATION_AI_CONFIRMATION_REQUIRED'
    confirmed = await client_a_admin.put(path, json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1','api_key':'synthetic-local','model':'local-model','confirmation':True})
    assert confirmed.status_code == 200, confirmed.text
    rows = (await migrator_session.execute(text(
        'SELECT provider, base_url FROM organization_ai_confirmation WHERE organization_id=:org ORDER BY confirmed_at'),
        {'org': seed_data['org_a_id']})).all()
    assert rows == [('openai', None), ('openai_compatible', 'http://localhost:11434/v1')]


@pytest.mark.asyncio
async def test_destination_change_without_a_new_key_drops_the_key_and_is_audited(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    stored = await client_a_admin.put(path, json={'api_key':'synthetic-first','model':'gpt-5.6-luna','confirmation':True})
    assert stored.status_code == 200, stored.text
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    moved = await client_a_admin.put(path, json={'provider':'anthropic'})
    assert moved.status_code == 200, moved.text
    assert moved.json()['key_set'] is False
    payloads = (await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))[before:]
    assert payloads == [{'api_key': 'removed', 'provider': {'from': 'openai', 'to': 'anthropic'}}]


@pytest.mark.asyncio
async def test_removing_the_key_keeps_the_confirmation_record(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    stored = await client_a_admin.put(path, json={'api_key':'synthetic-first','model':'gpt-5.6-luna','confirmation':True})
    assert stored.status_code == 200, stored.text
    removed = await client_a_admin.put(path, json={'api_key': None})
    assert removed.status_code == 200, removed.text
    body = (await client_a_admin.get(path)).json()
    assert body['key_set'] is False
    assert body['confirmation'] is None
    count = await migrator_session.scalar(text(
        'SELECT count(*) FROM organization_ai_confirmation WHERE organization_id=:org'), {'org': seed_data['org_a_id']})
    assert count == 1


@pytest.mark.asyncio
async def test_confirmation_without_a_stored_key_records_nothing(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    response = await client_a_admin.put(path, json={'confirmation': True})
    assert response.status_code == 200, response.text
    count = await migrator_session.scalar(text(
        'SELECT count(*) FROM organization_ai_confirmation WHERE organization_id=:org'), {'org': seed_data['org_a_id']})
    assert count == 0
    assert len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id'])) == before


@pytest.mark.asyncio
async def test_reentering_the_stored_legacy_key_without_confirmation_is_refused(client_a_admin, seed_data, migrator_session):
    import psycopg2
    from app.services.ai_settings_service import _cipher
    from tests.conftest import MIGRATOR_SYNC_URL
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    encrypted = _cipher(seed_data['org_a_id']).encrypt(b'synthetic-legacy-key').decode()
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('INSERT INTO organization_ai_settings (organization_id,encrypted_api_key) VALUES (%s,%s)',
                       (str(seed_data['org_a_id']), encrypted))
    response = await client_a_admin.put(path, json={'api_key':'synthetic-legacy-key','model':'gpt-5.6-luna'})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'ORGANIZATION_AI_CONFIRMATION_REQUIRED'
    body = (await client_a_admin.get(path)).json()
    assert body['key_set'] is True
    assert body['confirmation_required'] is True


@pytest.mark.asyncio
async def test_confirmation_rows_are_immutable_and_bound_to_the_acting_session_user(client_a_admin, seed_data):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    stored = await client_a_admin.put(path, json={'api_key':'synthetic-first','model':'gpt-5.6-luna','confirmation':True})
    assert stored.status_code == 200, stored.text
    org = seed_data['org_a_id']
    async with get_tenant_session(org) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(seed_data['user_a_admin_id'])})
        with pytest.raises(DBAPIError, match='immutable|permission denied'):
            async with db.begin_nested():
                await db.execute(text("UPDATE organization_ai_confirmation SET confirmed_at='2020-01-01', text_version='0' WHERE organization_id=:org"), {'org': org})
        with pytest.raises(DBAPIError, match='immutable|permission denied'):
            async with db.begin_nested():
                await db.execute(text("DELETE FROM organization_ai_confirmation WHERE organization_id=:org"), {'org': org})
        with pytest.raises(DBAPIError, match='acting user'):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO organization_ai_confirmation (organization_id,confirmed_by_user_id,provider,base_url,text_version,confirmed_at)"
                                      " VALUES (:org,:clerk,'anthropic',NULL,'1','2019-01-01')"), {'org': org, 'clerk': str(seed_data['user_a_clerk_id'])})
        await db.execute(text("INSERT INTO organization_ai_confirmation (organization_id,confirmed_by_user_id,provider,base_url,text_version,confirmed_at)"
                              " VALUES (:org,:admin,'openai',NULL,'1','2019-01-01')"), {'org': org, 'admin': str(seed_data['user_a_admin_id'])})
        confirmed_at = await db.scalar(text("SELECT confirmed_at FROM organization_ai_confirmation"
                                            " WHERE organization_id=:org AND provider='openai' ORDER BY confirmed_at DESC LIMIT 1"), {'org': org})
        assert confirmed_at.year >= 2026


def _models(body):
    return body['model'], body['document_model']


@pytest.mark.asyncio
async def test_reading_and_extraction_models_persist_and_change_independently(client_a_admin, seed_data):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    saved = await client_a_admin.put(path, json={'api_key': 'synthetic-key', 'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'confirmation': True})
    assert saved.status_code == 200, saved.text
    assert _models((await client_a_admin.get(path)).json()) == ('gpt-5.6-luna', 'gpt-5.4-mini')
    # The settings form sends both choices on every save: changing one keeps the other.
    changed = await client_a_admin.put(path, json={'model': 'future-model', 'document_model': 'gpt-5.4-mini'})
    assert _models(changed.json()) == ('future-model', 'gpt-5.4-mini')
    changed = await client_a_admin.put(path, json={'model': 'future-model', 'document_model': 'gpt-5.6-luna'})
    assert _models(changed.json()) == ('future-model', 'gpt-5.6-luna')
    assert _models((await client_a_admin.get(path)).json()) == ('future-model', 'gpt-5.6-luna')
    # The same model may serve both roles.
    both = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.6-luna'})
    assert _models(both.json()) == ('gpt-5.6-luna', 'gpt-5.6-luna')
    assert both.json()['key_set'] is True


@pytest.mark.asyncio
async def test_reading_model_stays_unset_until_an_administrator_chooses_it(client_a_admin, seed_data):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    assert (await client_a_admin.get(path)).json()['document_model'] is None
    saved = await client_a_admin.put(path, json={'api_key': 'synthetic-key', 'model': 'gpt-5.6-luna', 'import_enabled': True, 'qa_enabled': True, 'confirmation': True})
    assert saved.status_code == 200, saved.text
    assert saved.json()['document_model'] is None
    assert saved.json()['document_price_known'] is False


@pytest.mark.asyncio
async def test_reading_model_price_status_is_reported_apart_from_the_extraction_model(client_a_admin, seed_data):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    saved = await client_a_admin.put(path, json={'api_key': 'synthetic-key', 'model': 'future-model', 'document_model': 'gpt-5.4-mini', 'confirmation': True})
    assert (saved.json()['price_known'], saved.json()['document_price_known']) == (False, True)
    swapped = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'future-model'})
    assert (swapped.json()['price_known'], swapped.json()['document_price_known']) == (True, False)
    # A compatible endpoint never takes an official price from a reused model name.
    local = await client_a_admin.put(path, json={'provider': 'openai_compatible', 'base_url': 'http://localhost:11434/v1', 'api_key': 'synthetic-local',
        'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'confirmation': True})
    assert (local.json()['price_known'], local.json()['document_price_known']) == (False, False)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['model', 'document_model'])
@pytest.mark.parametrize('value', ['', 'two words', 'x' * 256, 'bad;model', 'modell\u00fc', None])
async def test_both_model_choices_are_validated_alike(client_a_admin, seed_data, field, value):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    response = await client_a_admin.put(path, json={field: value})
    assert response.status_code == (200 if value is None else 422), response.text


@pytest.mark.asyncio
async def test_reading_and_extraction_choices_are_isolated_between_organizations(client_a_admin, client_b_admin, seed_data):
    path_a = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    path_b = f"/api/organizations/{seed_data['org_b_id']}/ai-settings"
    assert (await client_a_admin.put(path_a, json={'api_key': 'synthetic-a', 'model': 'model-a', 'document_model': 'reader-a', 'confirmation': True})).status_code == 200
    assert _models((await client_b_admin.get(path_b)).json()) == (None, None)
    assert (await client_b_admin.put(path_b, json={'api_key': 'synthetic-b', 'model': 'model-b', 'document_model': 'reader-b', 'confirmation': True})).status_code == 200
    assert _models((await client_a_admin.get(path_a)).json()) == ('model-a', 'reader-a')
    assert _models((await client_b_admin.get(path_b)).json()) == ('model-b', 'reader-b')
    # Another organization's settings stay a 404 for either administrator.
    assert (await client_b_admin.put(path_a, json={'document_model': 'reader-b'})).status_code == 404
    assert _models((await client_a_admin.get(path_a)).json()) == ('model-a', 'reader-a')


def _store_confirmed_before_invoices(org, admin, *, provider='openai', text_version='1'):
    """What an installation holds that was upgraded: a key confirmed with an earlier text (version 1 names neither
    invoices nor inbox documents, version 2 names invoices), switches on."""
    import psycopg2
    from app.services.ai_settings_service import _cipher
    from tests.conftest import MIGRATOR_SYNC_URL
    encrypted = _cipher(org).encrypt(b'synthetic-earlier-key').decode()
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO organization_ai_settings (organization_id,provider,model,document_model,import_enabled,encrypted_api_key)
            VALUES (%s,%s,'gpt-5.6-luna','gpt-5.4-mini',true,%s)""", (str(org), provider, encrypted))
        cursor.execute("""INSERT INTO organization_ai_confirmation (organization_id,confirmed_by_user_id,provider,base_url,text_version)
            VALUES (%s,%s,%s,NULL,%s)""", (str(org), str(admin), provider, text_version))


@pytest.mark.asyncio
async def test_a_confirmation_that_predates_invoices_keeps_the_declaration_import_and_asks_again_for_invoices(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    from app.core.db import get_worker_tenant_session
    from app.services.ai_settings_service import declaration_import_blocker, effective_ai_settings, invoice_import_blocker
    org = seed_data['org_a_id']
    path = f"/api/organizations/{org}/ai-settings"
    _store_confirmed_before_invoices(org, seed_data['user_a_admin_id'])

    async def blockers():
        async with get_worker_tenant_session(org) as db:
            effective = await effective_ai_settings(db, org)
        return declaration_import_blocker(effective), invoice_import_blocker(effective, needs_reading=False)

    body = (await client_a_admin.get(path)).json()
    assert (body['key_set'], body['import_enabled'], body['confirmation_required']) == (True, True, False)
    assert body['invoice_confirmation_required'] is True
    # The upgrade changes nothing for the declaration import and sends no invoice to the provider.
    assert await blockers() == (None, 'ai_invoice_confirmation_missing')
    # Saving the other settings without the new confirmation changes neither.
    kept = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': False})
    assert kept.status_code == 200, kept.text
    assert kept.json()['invoice_confirmation_required'] is True and await blockers() == (None, 'ai_invoice_confirmation_missing')
    # Confirming once more, without entering the key again, records the current text and releases invoices.
    before = len(await _ai_audit_payloads(migrator_session, org))
    confirmed = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': True})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['invoice_confirmation_required'] is False and confirmed.json()['confirmation_required'] is False
    assert await blockers() == (None, None)
    versions = (await migrator_session.execute(text(
        'SELECT text_version FROM organization_ai_confirmation WHERE organization_id=:org ORDER BY confirmed_at'), {'org': org})).scalars().all()
    assert versions == ['1', '3']
    assert (await _ai_audit_payloads(migrator_session, org))[before:] == [{'confirmation': 'recorded'}]
    # Confirming again records nothing more.
    await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': True})
    assert await migrator_session.scalar(text('SELECT count(*) FROM organization_ai_confirmation WHERE organization_id=:org'), {'org': org}) == 2


@pytest.mark.asyncio
async def test_a_confirmation_of_the_text_with_invoices_keeps_them_and_holds_back_the_documents_of_the_inbox_until_confirmed_again(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    from app.core.db import get_worker_tenant_session
    from app.services.ai_settings_service import declaration_import_blocker, effective_ai_settings, invoice_import_blocker
    org = seed_data['org_a_id']
    path = f"/api/organizations/{org}/ai-settings"
    _store_confirmed_before_invoices(org, seed_data['user_a_admin_id'], text_version='2')

    async def blockers():
        async with get_worker_tenant_session(org) as db:
            effective = await effective_ai_settings(db, org)
        return (declaration_import_blocker(effective), invoice_import_blocker(effective, needs_reading=False),
                invoice_import_blocker(effective, needs_reading=False, inbox=True))

    body = (await client_a_admin.get(path)).json()
    assert (body['key_set'], body['confirmation_required'], body['invoice_confirmation_required'], body['inbox_confirmation_required']) == (True, False, False, True)
    # What the earlier text covered stays; an inbox document is not sent until a text that names it is confirmed.
    assert await blockers() == (None, None, 'ai_inbox_confirmation_missing')
    kept = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': False})
    assert kept.status_code == 200, kept.text
    assert kept.json()['inbox_confirmation_required'] is True and await blockers() == (None, None, 'ai_inbox_confirmation_missing')
    # Confirming once more records the current text without entering the key again and releases the inbox.
    confirmed = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': True})
    assert confirmed.status_code == 200, confirmed.text
    assert (confirmed.json()['invoice_confirmation_required'], confirmed.json()['inbox_confirmation_required']) == (False, False)
    assert await blockers() == (None, None, None)
    versions = (await migrator_session.execute(text(
        'SELECT text_version FROM organization_ai_confirmation WHERE organization_id=:org ORDER BY confirmed_at'), {'org': org})).scalars().all()
    assert versions == ['2', '3']


@pytest.mark.asyncio
async def test_invoices_need_no_second_confirmation_without_a_stored_key_or_for_a_stored_key_never_confirmed(client_a_admin, seed_data):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    assert (await client_a_admin.get(path)).json()['invoice_confirmation_required'] is False
    stored = await client_a_admin.put(path, json={'api_key': 'synthetic-new', 'model': 'gpt-5.6-luna', 'confirmation': True})
    assert stored.status_code == 200, stored.text
    assert stored.json()['invoice_confirmation_required'] is False
    removed = await client_a_admin.put(path, json={'api_key': None})
    assert removed.json()['key_set'] is False and removed.json()['invoice_confirmation_required'] is False


async def _invoice_blocker(org):
    from app.core.db import get_worker_tenant_session
    from app.services.ai_settings_service import effective_ai_settings, invoice_import_blocker
    async with get_worker_tenant_session(org) as db:
        return invoice_import_blocker(await effective_ai_settings(db, org), needs_reading=False)


@pytest.mark.asyncio
async def test_an_installation_that_processes_invoices_shows_and_records_the_text_that_names_them(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    org = seed_data['org_a_id']
    path = f"/api/organizations/{org}/ai-settings"
    refused = await client_a_admin.put(path, json={'api_key': 'synthetic-key', 'model': 'gpt-5.6-luna', 'import_enabled': True})
    assert 'Dokumente (Teilungserklärungen, Rechnungen und Dokumente im Posteingang) und extrahierte Texte' in refused.json()['detail']['message']
    stored = await client_a_admin.put(path, json={'api_key': 'synthetic-key', 'model': 'gpt-5.6-luna', 'import_enabled': True, 'confirmation': True})
    assert stored.json()['invoice_processing_enabled'] is True and stored.json()['invoice_confirmation_required'] is False
    assert stored.json()['inbox_confirmation_required'] is False
    assert await migrator_session.scalar(text('SELECT text_version FROM organization_ai_confirmation WHERE organization_id=:org'), {'org': org}) == '3'
    assert await _invoice_blocker(org) is None


@pytest.mark.asyncio
async def test_an_installation_without_invoice_processing_names_no_invoices_and_its_confirmation_does_not_cover_them(client_a_admin, seed_data, migrator_session, monkeypatch):
    from sqlalchemy import text
    from app.core.config import settings
    monkeypatch.setattr(settings, 'invoice_extraction_enabled', False)
    org = seed_data['org_a_id']
    path = f"/api/organizations/{org}/ai-settings"
    refused = await client_a_admin.put(path, json={'api_key': 'synthetic-key', 'model': 'gpt-5.6-luna', 'import_enabled': True})
    assert refused.status_code == 422 and 'Rechnung' not in refused.json()['detail']['message']
    assert 'Dokumente (Teilungserklärungen und Dokumente im Posteingang) und extrahierte Texte' in refused.json()['detail']['message']
    stored = await client_a_admin.put(path, json={'api_key': 'synthetic-key', 'model': 'gpt-5.6-luna', 'import_enabled': True, 'confirmation': True})
    assert stored.status_code == 200, stored.text
    body = stored.json()
    # The card learns that no invoices are processed here, and asks for nothing about them; the declaration import and
    # the inbox, which every installation processes with the models, are confirmed.
    assert (body['invoice_processing_enabled'], body['inbox_processing_enabled']) == (False, True)
    assert (body['invoice_confirmation_required'], body['inbox_confirmation_required'], body['confirmation_required']) == (False, False, False)
    assert body['confirmation']['confirmed_by_name']
    # The text this installation showed names declarations and inbox documents but no invoices, and that is the version on record.
    assert await migrator_session.scalar(text('SELECT text_version FROM organization_ai_confirmation WHERE organization_id=:org'), {'org': org}) == '4'
    assert (await client_a_admin.get(path)).json()['invoice_confirmation_required'] is False

    # Invoice processing is switched on later: nothing the organization confirmed named invoices, so none is sent;
    # its inbox documents stay confirmed.
    monkeypatch.setattr(settings, 'invoice_extraction_enabled', True)
    again = (await client_a_admin.get(path)).json()
    assert (again['invoice_processing_enabled'], again['invoice_confirmation_required'], again['confirmation_required']) == (True, True, False)
    assert again['inbox_confirmation_required'] is False and again['import_enabled'] is True
    assert await _invoice_blocker(org) == 'ai_invoice_confirmation_missing'
    # Confirming the text that names invoices, without entering the key again, releases them.
    confirmed = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'import_enabled': True, 'confirmation': True})
    assert confirmed.status_code == 200 and confirmed.json()['invoice_confirmation_required'] is False
    assert await _invoice_blocker(org) is None
    versions = (await migrator_session.execute(text('SELECT text_version FROM organization_ai_confirmation WHERE organization_id=:org ORDER BY confirmed_at'), {'org': org})).scalars().all()
    assert versions == ['4', '3']


@pytest.mark.asyncio
async def test_where_invoices_are_not_processed_a_key_confirmed_before_the_inbox_was_covered_is_asked_once_for_the_inbox_only(client_a_admin, seed_data, migrator_session, monkeypatch):
    from sqlalchemy import text
    from app.core.config import settings
    org = seed_data['org_a_id']
    _store_confirmed_before_invoices(org, seed_data['user_a_admin_id'])
    monkeypatch.setattr(settings, 'invoice_extraction_enabled', False)
    path = f"/api/organizations/{org}/ai-settings"
    body = (await client_a_admin.get(path)).json()
    # Nothing is asked about invoices here, and the declaration import stays confirmed; the inbox, which is processed,
    # is not named by the text of version 1.
    assert (body['invoice_processing_enabled'], body['invoice_confirmation_required'], body['confirmation_required']) == (False, False, False)
    assert body['inbox_confirmation_required'] is True
    # Confirming once, without entering the key again, records the text this installation shows and asks no more.
    saved = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'import_enabled': True, 'confirmation': True})
    assert saved.status_code == 200 and saved.json()['inbox_confirmation_required'] is False
    versions = (await migrator_session.execute(text('SELECT text_version FROM organization_ai_confirmation WHERE organization_id=:org ORDER BY confirmed_at'), {'org': org})).scalars().all()
    assert versions == ['1', '4']


# The confirmation texts and what they name (issue #501). The expected values below are the product's wording of the
# texts, written out here and not read from the service: version 1 names declarations, 2 adds invoices, 3 names
# declarations, invoices and the documents of the inbox, 4 names declarations and the inbox without invoices.
TEXT_NAMES = {'1': {'declarations'}, '2': {'declarations', 'invoices'}, '3': {'declarations', 'invoices', 'inbox'}, '4': {'declarations', 'inbox'}}


def test_one_table_maps_every_confirmation_text_to_the_kinds_it_names():
    from app.services.ai_settings_service import CONFIRMATION_TEXTS, named_kinds
    assert {version: set(kinds) for version, kinds in CONFIRMATION_TEXTS.items()} == TEXT_NAMES

    class Recorded:
        def __init__(self, version):
            self.text_version = version
    for version, kinds in TEXT_NAMES.items():
        assert named_kinds((Recorded(version), 'Somebody')) == kinds
    # No confirmation, or one of a text this version of Verwalino does not know, names nothing: nothing is sent.
    assert named_kinds(None) == frozenset()
    for unknown in ('0', '5', 'forged', ''):
        assert named_kinds((Recorded(unknown), 'Somebody')) == frozenset()


@pytest.mark.parametrize('invoices,version,kinds', [
    (True, '3', {'declarations', 'invoices', 'inbox'}),
    (False, '4', {'declarations', 'inbox'}),
], ids=['installation-processing-invoices', 'installation-without-invoices'])
def test_an_installation_shows_and_records_the_text_for_exactly_the_kinds_it_sends(monkeypatch, invoices, version, kinds):
    from app.core.config import settings
    from app.services.ai_settings_service import CONFIRMATION_TEXTS, confirmation_text_version, installation_kinds
    monkeypatch.setattr(settings, 'invoice_extraction_enabled', invoices)
    assert installation_kinds() == kinds
    assert confirmation_text_version() == version and CONFIRMATION_TEXTS[version] == kinds


def test_every_set_of_kinds_an_installation_can_send_has_a_text_that_names_exactly_those(monkeypatch):
    """A new kind, or a switch that changes the kinds, fails here until a text that names them exists."""
    from app.core.config import settings
    from app.services.ai_settings_service import CONFIRMATION_TEXTS, confirmation_text_version, installation_kinds
    for invoices in (True, False):
        monkeypatch.setattr(settings, 'invoice_extraction_enabled', invoices)
        assert [version for version, kinds in CONFIRMATION_TEXTS.items() if kinds == installation_kinds()] == [confirmation_text_version()]


def _store_key(org, admin, version):
    """A stored key, switched on, confirmed with the text of this version; without a version, never confirmed."""
    if version is not None:
        return _store_confirmed_before_invoices(org, admin, text_version=version)
    import psycopg2
    from app.services.ai_settings_service import _cipher
    from tests.conftest import MIGRATOR_SYNC_URL
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO organization_ai_settings (organization_id,provider,model,document_model,import_enabled,encrypted_api_key)
            VALUES (%s,'openai','gpt-5.6-luna','gpt-5.4-mini',true,%s)""", (str(org), _cipher(org).encrypt(b'synthetic-earlier-key').decode()))


# What each installation (invoices processed or not; the inbox is processed by every one) does with a confirmation of
# each earlier text. ``asked``: what the card still asks for (confirmation at all, invoices, inbox documents).
# ``before``: what the three paths (declaration import, invoice, inbox document) may send before the administrator
# confirms again, as the blocker each path answers. ``records``: the text recorded when the administrator confirms
# once more without entering the key again (nothing when nothing this installation sends is missing). ``after``: the
# invoice path's answer then; the two others are free. Invoices are only named by the text of an installation that
# processes them, so where they are not processed the invoice path stays closed, which nothing there asks for.
ASKED = 'ai_invoice_confirmation_missing'
ASKED_INBOX = 'ai_inbox_confirmation_missing'
OFF = 'ai_import_disabled'
NEVER = ('DECLARATION_IMPORT_DISABLED', OFF, OFF)
MATRIX = [
    # invoices processed: the text shown is version 3
    (True, None, dict(asked=(True, True, True), before=NEVER, records='3', after=None)),
    (True, '1', dict(asked=(False, True, True), before=(None, ASKED, ASKED_INBOX), records='3', after=None)),
    (True, '2', dict(asked=(False, False, True), before=(None, None, ASKED_INBOX), records='3', after=None)),
    (True, '3', dict(asked=(False, False, False), before=(None, None, None), records=None, after=None)),
    (True, '4', dict(asked=(False, True, False), before=(None, ASKED, None), records='3', after=None)),
    (True, '0', dict(asked=(True, True, True), before=NEVER, records='3', after=None)),
    # no invoices processed: the text shown is version 4
    (False, None, dict(asked=(True, False, True), before=NEVER, records='4', after=ASKED)),
    (False, '1', dict(asked=(False, False, True), before=(None, ASKED, ASKED_INBOX), records='4', after=ASKED)),
    (False, '2', dict(asked=(False, False, True), before=(None, None, ASKED_INBOX), records='4', after=ASKED)),
    (False, '3', dict(asked=(False, False, False), before=(None, None, None), records=None, after=None)),
    (False, '4', dict(asked=(False, False, False), before=(None, ASKED, None), records=None, after=ASKED)),
    (False, '0', dict(asked=(True, False, True), before=NEVER, records='4', after=ASKED)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('invoices,version,expected', MATRIX, ids=[f"{'invoices' if invoices else 'no-invoices'}-after-{version or 'no-confirmation'}" for invoices, version, _ in MATRIX])
async def test_each_installation_asks_once_for_what_an_earlier_confirmation_did_not_name_and_sends_nothing_before(
        client_a_admin, seed_data, migrator_session, monkeypatch, invoices, version, expected):
    from sqlalchemy import text
    from app.core.config import settings
    from app.core.db import get_worker_tenant_session
    from app.services.ai_settings_service import declaration_import_blocker, effective_ai_settings, invoice_import_blocker
    monkeypatch.setattr(settings, 'invoice_extraction_enabled', invoices)
    org = seed_data['org_a_id']
    path = f"/api/organizations/{org}/ai-settings"
    _store_key(org, seed_data['user_a_admin_id'], version)

    async def paths():
        async with get_worker_tenant_session(org) as db:
            effective = await effective_ai_settings(db, org)
        return (declaration_import_blocker(effective), invoice_import_blocker(effective, needs_reading=False),
                invoice_import_blocker(effective, needs_reading=False, inbox=True))

    def asked(body):
        return (body['confirmation_required'], body['invoice_confirmation_required'], body['inbox_confirmation_required'])

    body = (await client_a_admin.get(path)).json()
    assert (body['key_set'], body['invoice_processing_enabled'], body['inbox_processing_enabled']) == (True, invoices, True)
    assert asked(body) == expected['asked']
    # Before the administrator confirms, no path sends more than the earlier text named.
    assert await paths() == expected['before']
    # Saving the other settings without the tick changes none of it.
    kept = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': False})
    assert kept.status_code == 200, kept.text
    assert asked(kept.json()) == expected['asked'] and await paths() == expected['before']
    # Confirming once more, without entering the key again, records the text this installation shows, and only if
    # something it sends is missing; afterwards nothing it sends is asked for again.
    confirmed = await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': True})
    assert confirmed.status_code == 200, confirmed.text
    assert asked(confirmed.json()) == (False, False, False)
    recorded = (await migrator_session.execute(text(
        'SELECT text_version FROM organization_ai_confirmation WHERE organization_id=:org ORDER BY confirmed_at'), {'org': org})).scalars().all()
    assert recorded == ([version] if version is not None else []) + ([expected['records']] if expected['records'] else [])
    assert await paths() == (None, expected['after'], None)
    # Confirming again records nothing more.
    await client_a_admin.put(path, json={'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True, 'confirmation': True})
    assert await migrator_session.scalar(text('SELECT count(*) FROM organization_ai_confirmation WHERE organization_id=:org'), {'org': org}) == len(recorded)
