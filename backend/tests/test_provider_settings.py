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
    assert row['text_version'] == '1'
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
