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


@when("the administrator chooses a listed AI model and enables import and questions")
def choose_ai(world):
    models = world.act('POST', world.data['ai_path'] + '/models', json={'api_key': 'synthetic-ai-settings-key'})
    assert models.status_code == 200, world.describe_response()
    assert {m['id'] for m in models.json()['models']} == {'gpt-5.6-luna', 'future-model'}
    world.act('PUT', world.data['ai_path'], json={
        'provider': 'openai', 'api_key': 'synthetic-ai-settings-key', 'model': 'future-model',
        'import_enabled': True, 'qa_enabled': True, 'monthly_limit_usd': '12.50',
    })


@then("the AI settings are saved without returning the key")
def saved_ai(world):
    assert world.response.status_code == 200, world.describe_response()
    assert 'synthetic-ai-settings-key' not in world.response.text
    assert world.response.json()['key_set'] is True


@then("the organization's next AI settings read uses the chosen model")
def read_ai(world):
    response = world.act('GET', world.data['ai_path'])
    assert response.status_code == 200, world.describe_response()
    assert response.json() == {
        'provider': 'openai', 'base_url': None, 'key_set': True, 'model': 'future-model',
        'import_enabled': True, 'qa_enabled': True, 'monthly_limit_usd': '12.50',
        'price_known': False, 'request_timeout_seconds': None,
    }


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


@when("the administrator selects that provider and enables imports")
def choose_compatible_ai(world):
    body = {'provider':'openai_compatible','base_url':'https://compatible.example/v1','api_key':'synthetic-compatible-key'}
    models = world.act('POST', world.data['ai_path'] + '/models', json=body)
    assert models.status_code == 200, world.describe_response()
    assert models.json()['models'] == [{'id':'synthetic-compatible-model','evaluated':False,'price_known':False}]
    world.act('PUT',world.data['ai_path'],json={**body,'model':'synthetic-compatible-model','import_enabled':True})


@then("the selected endpoint and model are saved without returning the key")
def read_compatible_ai(world):
    assert world.response.status_code == 200, world.describe_response()
    response = world.act('GET',world.data['ai_path'])
    assert response.json()['provider'] == 'openai_compatible'
    assert response.json()['base_url'] == 'https://compatible.example/v1'
    assert response.json()['model'] == 'synthetic-compatible-model'
    assert response.json()['import_enabled'] is True
    assert response.json()['key_set'] is True
    assert 'synthetic-compatible-key' not in response.text
