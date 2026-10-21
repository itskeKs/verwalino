"""Attachments retain property source read/prepare/export and revocation boundaries."""
import pytest
from app.core.storage import LocalFsDocumentStore
from app.services import attachment_service
from tests.test_property_master_access import (
    scope_seed, worker_tenant, committed_actor, existing, principals,
)
from tests.test_scoped_account_statements import client
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = LocalFsDocumentStore(tmp_path)
    monkeypatch.setattr(attachment_service, "get_store", lambda: value)
    return value


async def upload(api, prop):
    return await api.post('/api/attachments', data={'property_id': prop},
                          files={'file': ('private.txt', b'private original', 'text/plain')})


async def test_attachment_read_is_neither_prepare_nor_export_and_revoke_hides_all(scope_seed, store, monkeypatch):
    async with client(principals(scope_seed), 'actor') as api:
        prop, _ = await existing(api)
        response = await upload(api, prop)
        assert response.status_code == 201, response.text
        path = f"/api/attachments/{response.json()['id']}"
        domain, receipt = await grant(api, scope_seed, prop, caps=['read'])
        def denied_bytes(_):
            pytest.fail('Unauthorized export reached document storage')
        monkeypatch.setattr(attachment_service.AttachmentService, '_read_verified', staticmethod(denied_bytes))
        async with synthetic_enforcement(scope_seed):
            assert (await api.get(path)).status_code == 200
            assert len((await api.get('/api/attachments')).json()['items']) == 1
            assert (await api.get(path + '/download')).status_code == 404
            assert (await upload(api, prop)).status_code == 404
            assert (await api.post(path + '/archive', json={'reason': 'Not authorized'})).status_code == 404
            assert (await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            assert (await api.get('/api/attachments')).json()['items'] == []
            assert (await api.get('/api/attachments', params={'property_id': prop})).status_code == 404
            assert (await api.get(path)).status_code == 404
            assert (await api.get(path + '/download')).status_code == 404


async def test_attachment_prepare_export_and_paused_source(scope_seed, store):
    async with client(principals(scope_seed), 'actor') as api:
        prop, _ = await existing(api)
        await grant(api, scope_seed, prop, caps=['read', 'prepare', 'export'])
        async with synthetic_enforcement(scope_seed):
            response = await upload(api, prop)
            assert response.status_code == 201, response.text
            path = f"/api/attachments/{response.json()['id']}"
            assert (await api.get(path + '/download')).content == b'private original'
            await change_mode(scope_seed, 'PAUSED')
            assert (await api.get(path)).status_code == 200
            assert (await upload(api, prop)).status_code == 404
            assert (await api.post(path + '/archive', json={'reason': 'Paused'})).status_code == 404
