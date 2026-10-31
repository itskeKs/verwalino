"""Authenticated manual downloads, organization isolation and no uploads."""
import json
import logging

from fastapi import Depends
import httpx
import pytest

from app.core.tenant import get_current_user
from app.main import app
from tests.test_diagnostics import unpack_bundle


@pytest.mark.parametrize('client_name', ['client_a_clerk', 'client_a_readonly', 'client_unauth'])
async def test_only_administrators_can_download_diagnostics(client_a_clerk, client_a_readonly, client_unauth, client_name):
    client = {'client_a_clerk': client_a_clerk, 'client_a_readonly': client_a_readonly, 'client_unauth': client_unauth}[client_name]
    response = await client.post('/api/admin/diagnostics')
    assert response.status_code == (401 if client_name == 'client_unauth' else 403)


async def test_administrator_downloads_matching_manifest_and_audit_line(client_a_admin, seed_data, migrator_session, caplog):
    with caplog.at_level(logging.INFO):
        response = await client_a_admin.post('/api/admin/diagnostics', headers={'X-Request-ID': 'download-123456'})
    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/gzip'
    assert response.headers['Content-Disposition'].startswith('attachment; filename="verwalino-diagnostics-')
    assert response.headers['Cache-Control'] == 'no-store'
    members = unpack_bundle(response.content)
    assert json.loads(members['manifest.json'])['app_version'] == app.version
    assert json.loads(members['manifest.json'])['release_target'] == '0145'
    assert json.loads(members['containers.json']) is None
    database = json.loads(members['database.json'])
    assert database['scope'] == 'organization'
    from sqlalchemy import text
    expected = await migrator_session.scalar(text('SELECT count(*) FROM membership WHERE organization_id=:org'), {'org': seed_data['org_a_id']})
    assert database['row_counts']['membership'] == expected
    audit = [r for r in caplog.records if r.getMessage() == 'diagnostics_download']
    assert len(audit) == 1 and audit[0].request_id == 'download-123456'


async def test_api_bundle_correlates_seeded_failure_without_other_organization_logs(client_a_admin, client_b_admin, monkeypatch):
    monkeypatch.setattr(app.router, 'routes', list(app.router.routes))

    @app.get('/api/test-admin-diagnostics-failure')
    async def failure(auth=Depends(get_current_user)):
        raise RuntimeError('Müller, Jürgen private@example.org body-secret')

    for client, identity in [(client_a_admin, 'own-error-123456'), (client_b_admin, 'foreign-error-123456')]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=client.base_url, cookies=client.cookies,
        ) as failure_client:
            response = await failure_client.get('/api/test-admin-diagnostics-failure', headers={'X-Request-ID': identity})
        assert response.status_code == 500
        assert response.json()['request_id'] == identity
    response = await client_a_admin.post('/api/admin/diagnostics')
    errors = unpack_bundle(response.content)['errors.log']
    assert 'own-error-123456' in errors and 'RuntimeError' in errors
    assert 'foreign-error-123456' not in errors
    assert all(value not in errors for value in ('Müller', 'private@example.org', 'body-secret'))


async def test_admin_support_link_comes_from_setting(client_a_admin, client_a_clerk, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'support_url', 'https://support.example.org/issues')
    response = await client_a_admin.get('/api/admin/diagnostics')
    assert response.status_code == 200
    assert response.json() == {'support_url': 'https://support.example.org/issues'}
    assert (await client_a_clerk.get('/api/admin/diagnostics')).status_code == 403


async def test_hosted_admin_receives_only_tenant_metadata(client_a_admin, client_b_admin, seed_data, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'error_tracking_environment', 'hosted-production')
    monkeypatch.setenv('HV_OPERATOR_PRIVATE_SWITCH', 'operator-only')
    response = await client_b_admin.post('/api/admin/diagnostics')
    assert response.status_code == 200
    members = unpack_bundle(response.content)
    database = json.loads(members['database.json'])
    assert database['status'] == 'available'
    assert database['organization_count'] is None
    assert database['migration_head'] is None
    # Alpha has three memberships; Beta has one. No Alpha rows may contribute.
    assert database['row_counts']['membership'] == 1
    environment = json.loads(members['environment.json'])
    assert environment == {
        'deployment_mode': 'hosted', 'app_version': app.version,
        'feature_flags': {
            'HV_ENABLE_API_DOCS': settings.enable_api_docs,
            'HV_FINANCIAL_COMPUTATION_ENABLED': settings.financial_computation_enabled,
            'HV_INVOICE_EXTRACTION_ENABLED': settings.invoice_extraction_enabled,
            'HV_DECLARATION_IMPORT_ENABLED': settings.declaration_import_enabled,
            'HV_DECLARATION_QA_ENABLED': settings.declaration_qa_enabled,
        },
    }
    content = ''.join(members.values())
    assert all(value not in content for value in (
        str(seed_data['org_a_id']), 'Verwaltung Alpha', 'HV_OPERATOR_PRIVATE_SWITCH',
        'HV_SESSION_SECRET', 'HV_DATABASE_URL', 'HV_SMTP_PASSWORD', 'hosted-production',
    ))
