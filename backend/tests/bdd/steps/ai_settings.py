"""AI settings through HTTP, with a deterministic provider model list."""
from types import SimpleNamespace
from pytest_bdd import given, when, then


@given("OpenAI lists two models for the organization's test key")
def ai_model_list(world, monkeypatch):
    import openai
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['api_key'] == 'synthetic-ai-settings-key'
            self.models = self
        async def list(self, **kwargs):
            return SimpleNamespace(data=[SimpleNamespace(id='gpt-5.6-luna'), SimpleNamespace(id='future-model')])
        async def close(self):
            pass
    monkeypatch.setattr(openai, 'AsyncOpenAI', Client)
    me = world.act('GET', '/api/organizations/me')
    world.data['ai_path'] = f"/api/organizations/{me.json()['id']}/ai-settings"


@when("the administrator chooses a listed AI model and confirms the provider")
def choose_ai(world):
    models = world.act('POST', world.data['ai_path'] + '/models', json={'api_key': 'synthetic-ai-settings-key'})
    assert models.status_code == 200, world.describe_response()
    assert {m['id'] for m in models.json()['models']} == {'gpt-5.6-luna', 'future-model'}
    world.act('PUT', world.data['ai_path'], json={
        'provider': 'openai', 'api_key': 'synthetic-ai-settings-key', 'model': 'future-model',
        'import_enabled': True, 'qa_enabled': True, 'monthly_limit_usd': '12.50',
        'confirmation': True})


@then("the AI settings are saved without returning the key")
def saved_ai(world):
    assert world.response.status_code == 200, world.describe_response()
    assert 'synthetic-ai-settings-key' not in world.response.text
    assert world.response.json()['key_set'] is True


@then("the organization's next AI settings read uses the chosen model")
def read_ai(world):
    response = world.act('GET', world.data['ai_path'])
    assert response.status_code == 200, world.describe_response()
    body = response.json()
    assert body['provider'] == 'openai'
    assert body['key_set'] is True
    assert body['model'] == 'future-model'
    assert body['import_enabled'] is True
    assert body['qa_enabled'] is True
    assert body['monthly_limit_usd'] == '12.50'
    assert body['price_known'] is False
    assert body['request_timeout_seconds'] is None
    assert body['confirmation_required'] is False
    confirmation = body['confirmation']
    assert confirmation['provider'] == 'openai'
    assert confirmation['base_url'] is None
    assert confirmation['text_version'] == '1'
    assert confirmation['confirmed_by_name']
    assert confirmation['confirmed_at']


@given("Verwaltung Alpha has configured its own AI model")
def alpha_ai(world):
    alpha = world.administrator_of('Verwaltung Alpha')
    org = world.look(alpha, '/api/organizations/me').json()['id']
    path = f'/api/organizations/{org}/ai-settings'
    response = world.send(alpha, 'PUT', path, json={'model':'alpha-only-model'})
    assert response.status_code == 200, world.describe_response()
    world.data['ai_alpha_path'] = path


@when("Verwaltung Beta tries to read and change Alpha's AI settings")
def foreign_ai(world):
    beta = world.administrator_of('Verwaltung Beta')
    for method, suffix, body in [('GET','',None),('PUT','',{'model':'beta-model'}),('POST','/models',{})]:
        response = world.send(beta, method, world.data['ai_alpha_path'] + suffix, json=body)
        assert response.status_code == 404, world.describe_response()


@then("the AI settings are reported missing and Alpha keeps its model")
def alpha_unchanged(world):
    response = world.look(world.administrator_of('Verwaltung Alpha'), world.data['ai_alpha_path'])
    assert response.json()['model'] == 'alpha-only-model'
    assert 'alpha-only-model' not in world.response.text


@given("a compatible provider lists a synthetic model")
def compatible_model_list(world, monkeypatch):
    from app.declarations.provider_runtime import ProviderClient
    async def listed(client, *, timeout=15):
        assert timeout == 180
        assert client.provider == 'openai_compatible'
        assert client.base_url == 'https://compatible.example/v1'
        return ['synthetic-compatible-model']
    monkeypatch.setattr(ProviderClient, 'list_models', listed)
    me = world.act('GET', '/api/organizations/me')
    world.data['ai_path'] = f"/api/organizations/{me.json()['id']}/ai-settings"


@when("the administrator selects that provider and confirms it")
def choose_compatible_ai(world):
    body = {'provider':'openai_compatible','base_url':'https://compatible.example/v1','api_key':'synthetic-compatible-key'}
    models = world.act('POST', world.data['ai_path'] + '/models', json=body)
    assert models.status_code == 200, world.describe_response()
    assert models.json()['models'] == [{'id':'synthetic-compatible-model','evaluated':False,'price_known':False}]
    world.act('PUT',world.data['ai_path'],json={**body,'model':'synthetic-compatible-model','import_enabled':True,'confirmation':True})


@then("the selected endpoint and model are saved without returning the key")
def read_compatible_ai(world):
    assert world.response.status_code == 200, world.describe_response()
    response = world.act('GET',world.data['ai_path'])
    assert response.json()['provider'] == 'openai_compatible'
    assert response.json()['base_url'] == 'https://compatible.example/v1'
    assert response.json()['model'] == 'synthetic-compatible-model'
    assert response.json()['import_enabled'] is True
    assert response.json()['key_set'] is True
    assert response.json()['confirmation_required'] is False
    assert response.json()['confirmation']['base_url'] == 'https://compatible.example/v1'
    assert 'synthetic-compatible-key' not in response.text




def _ai_path(world):
    if 'ai_path' not in world.data:
        me = world.act('GET', '/api/organizations/me')
        world.data['ai_path'] = f"/api/organizations/{me.json()['id']}/ai-settings"
    return world.data['ai_path']

@when("the administrator saves an AI key without the provider confirmation")
def save_ai_key_without_confirmation(world):
    world.act('PUT', _ai_path(world), json={
        'provider': 'openai', 'api_key': 'synthetic-ai-settings-key', 'model': 'gpt-5.6-luna',
        'import_enabled': True})


@then("the save is refused because the provider confirmation is missing")
def refused_without_confirmation(world):
    assert world.response.status_code == 422, world.describe_response()
    assert world.response.json()['detail']['code'] == 'ORGANIZATION_AI_CONFIRMATION_REQUIRED'


@then("no key is stored")
def no_key_stored(world):
    response = world.act('GET', world.data['ai_path'])
    assert response.json()['key_set'] is False
    assert response.json()['confirmation_required'] is False


@then("the previously stored key is unchanged")
def stored_key_unchanged(world):
    response = world.act('GET', world.data['ai_path'])
    assert response.json()['key_set'] is True


@when("the administrator saves an AI key with the provider confirmation")
def save_ai_key_with_confirmation(world):
    world.act('PUT', _ai_path(world), json={
        'provider': 'openai', 'api_key': 'synthetic-ai-settings-key', 'model': 'gpt-5.6-luna',
        'import_enabled': True, 'confirmation': True})
    assert world.response.status_code == 200, world.describe_response()


@then("the key is stored and the settings name the confirmer and time")
def read_confirmation(world):
    response = world.act('GET', world.data['ai_path'])
    assert response.status_code == 200, world.describe_response()
    body = response.json()
    assert body['key_set'] is True
    assert body['confirmation_required'] is False
    confirmation = body['confirmation']
    assert confirmation['provider'] == 'openai'
    assert confirmation['text_version'] == '1'
    assert confirmation['confirmed_by_name']
    assert confirmation['confirmed_at']


@given("the organization has a stored AI key without a recorded confirmation")
def stored_key_without_confirmation(world):
    import psycopg2
    from app.services.ai_settings_service import _cipher
    from tests.conftest import MIGRATOR_SYNC_URL
    organization_id = _ai_path(world).rsplit('/', 2)[-2]
    encrypted = _cipher(organization_id).encrypt(b'synthetic-ai-settings-key').decode()
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('''INSERT INTO organization_ai_settings
          (organization_id,provider,model,import_enabled,qa_enabled,encrypted_api_key)
          VALUES (%s,'openai','gpt-5.6-luna',true,true,%s)
          ON CONFLICT(organization_id) DO UPDATE SET encrypted_api_key=excluded.encrypted_api_key''',
          (organization_id, encrypted))


@then("the AI settings require the confirmation and the stored key stays unusable")
def key_unusable_without_confirmation(world):
    response = world.act('GET', world.data['ai_path'])
    body = response.json()
    assert body['key_set'] is True
    assert body['confirmation_required'] is True
    assert body['confirmation'] is None
    unusable = world.act('POST', world.data['ai_path'] + '/models', json={})
    assert unusable.status_code == 422, world.describe_response()


@when("the administrator confirms the provider for the stored key")
def confirm_stored_key(world):
    world.act('PUT', _ai_path(world), json={'confirmation': True})
    assert world.response.status_code == 200, world.describe_response()


@then("the stored key becomes usable and names the confirmer")
def key_usable_after_confirmation(world):
    usable = world.act('POST', world.data['ai_path'] + '/models', json={})
    assert usable.status_code == 200, world.describe_response()
    response = world.act('GET', world.data['ai_path'])
    body = response.json()
    assert body['confirmation_required'] is False
    assert body['confirmation']['confirmed_by_name']


@when("the administrator replaces the stored key without the confirmation")
def replace_key_without_confirmation(world):
    world.act('PUT', _ai_path(world), json={
        'api_key': 'synthetic-replacement-key', 'model': 'gpt-5.6-luna'})


@when("the administrator removes the saved key")
def remove_saved_key(world):
    world.act('PUT', _ai_path(world), json={'api_key': None, 'model': 'gpt-5.6-luna', 'import_enabled': False, 'qa_enabled': False})
    assert world.response.status_code == 200, world.describe_response()


@then("the key is gone, the functions are off and the confirmation record stays")
def key_removed_record_stays(world):
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL
    response = world.act('GET', world.data['ai_path'])
    body = response.json()
    assert body['key_set'] is False
    assert body['import_enabled'] is False
    assert body['confirmation'] is None
    organization_id = world.data['ai_path'].rsplit('/', 2)[-2]
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM organization_ai_confirmation WHERE organization_id=%s', (organization_id,))
        assert cursor.fetchone()[0] == 1


@when("the administrator saves an AI key and enables declaration import")
def save_ai_key(world):
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL
    me = world.act('GET', '/api/organizations/me')
    world.data['ai_org'] = me.json()['id']
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM audit_event WHERE organization_id=%s AND entity_type='organization_ai_settings'", (world.data['ai_org'],))
        world.data['ai_audit_before'] = {row[0] for row in cursor.fetchall()}
    response = world.act('PUT', f"/api/organizations/{world.data['ai_org']}/ai-settings", json={
        'api_key': 'synthetic-audit-key', 'model': 'gpt-5.6-luna', 'import_enabled': True,
        'confirmation': True})
    assert response.status_code == 200, world.describe_response()


@when("the administrator removes the saved key and switches the AI features off")
def remove_ai_key(world):
    response = world.act('PUT', f"/api/organizations/{world.data['ai_org']}/ai-settings", json={
        'api_key': None, 'model': 'gpt-5.6-luna', 'import_enabled': False})
    assert response.status_code == 200, world.describe_response()


@then("the new audit events record both changes without the key")
def audit_records(world):
    import json
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, action, payload_diff FROM audit_event WHERE organization_id=%s AND entity_type='organization_ai_settings' ORDER BY occurred_at", (world.data['ai_org'],))
        rows = [(action, payload) for row_id, action, payload in cursor.fetchall() if row_id not in world.data['ai_audit_before']]
    assert rows == [
        ('ai_settings.updated', {'api_key': 'set', 'confirmation': 'recorded', 'import_enabled': {'from': False, 'to': True}}),
        ('ai_settings.updated', {'api_key': 'removed', 'import_enabled': {'from': True, 'to': False}}),
    ], rows
    assert 'synthetic-audit-key' not in json.dumps(rows)
