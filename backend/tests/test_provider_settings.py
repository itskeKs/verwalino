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
    first = await client_a_admin.put(path,json={'api_key':'synthetic-original','model':'gpt-5.6-luna','import_enabled':True})
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
    response = await client_a_admin.put(path,json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1','api_key':'synthetic-local','model':'gpt-5.6-luna','import_enabled':True,'monthly_limit_usd':'0'})
    assert response.status_code == 200
    assert response.json()['price_known'] is False


@pytest.mark.asyncio
async def test_custom_provider_key_is_encrypted_at_rest(client_a_admin, seed_data, migrator_session):
    from sqlalchemy import text
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    response = await client_a_admin.put(path,json={'provider':'openai_compatible','base_url':'http://localhost:11434/v1','api_key':'synthetic-local','model':'local-model'})
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
    body = {'api_key':'synthetic-unchanged-key','model':'gpt-5.6-luna','import_enabled':True}
    assert (await client_a_admin.put(path, json=body)).status_code == 200
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    assert (await client_a_admin.put(path, json=body)).status_code == 200
    assert len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id'])) == before


@pytest.mark.asyncio
async def test_ai_key_set_and_replacement_are_audited_without_the_key(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    await client_a_admin.put(path, json={'api_key': None, 'import_enabled': False, 'qa_enabled': False})
    before = len(await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))
    assert (await client_a_admin.put(path, json={'api_key':'synthetic-first-key','model':'gpt-5.6-luna'})).status_code == 200
    assert (await client_a_admin.put(path, json={'api_key':'synthetic-second-key','model':'gpt-5.6-luna'})).status_code == 200
    payloads = (await _ai_audit_payloads(migrator_session, seed_data['org_a_id']))[before:]
    assert payloads == [{'api_key': 'set'}, {'api_key': 'replaced'}]
    assert 'synthetic-first-key' not in json.dumps(payloads)
    assert 'synthetic-second-key' not in json.dumps(payloads)


@pytest.mark.asyncio
async def test_ai_qa_switch_change_is_audited(client_a_admin, seed_data, migrator_session):
    path = f"/api/organizations/{seed_data['org_a_id']}/ai-settings"
    assert (await client_a_admin.put(path, json={'api_key':'synthetic-qa-key','model':'gpt-5.6-luna'})).status_code == 200
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
