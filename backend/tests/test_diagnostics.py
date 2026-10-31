"""Privacy and structural reproduction contracts for manually shared bundles."""

from datetime import datetime, timezone
import io
import json
import tarfile

import pytest

from app.diagnostics import build_bundle, bundle_filename, config_shapes


@pytest.fixture
def private_logs():
    private = [
        'Müller, Jürgen', 'Anna Beispiel', 'DE89 3704 0044 0532 0130 00',
        'DE89-3704-0044-0532-0130-00', 'juergen@example.org', '+49 170 1234567',
        'url-password', 'assignment-password', 'signed-session-cookie',
        'Geheimweg 7', 'unrecognized-private-body', 'Singlewordname',
    ]
    lines = [
        '2026-10-30 12:00:00,123 ERROR app.test request_id=failure-123456 RuntimeError: ' + ' '.join(private),
        'Traceback (most recent call last):',
        '  raise RuntimeError("unrecognized-private-body")',
        'RuntimeError: postgresql://user:url-password@db/test HV_SMTP_PASSWORD=assignment-password',
        '2026-10-30 12:00:01,123 WARNING app.test request_id=warning-123456 Cookie: hv_session=signed-session-cookie',
        '2026-10-30 12:00:02,123 INFO app.test request_id=ignored-123456 informational',
    ]
    return lines, private


def unpack_bundle(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        assert all(member.isfile() and member.mode == 0o600 for member in archive)
        return {member.name: archive.extractfile(member).read().decode() for member in archive}


def test_every_bundle_member_excludes_personal_data_and_config_values(private_logs):
    lines, private = private_logs
    members = unpack_bundle(build_bundle(
        config={'HV_SMTP_PASSWORD': 'x' * 24, 'HV_ALLOWED_HOSTS': 'secret.example.org',
                'HV_SESSION_SECRET': 'a-secret', 'HV_ENABLE_API_DOCS': False},
        log_lines=lines,
        containers=[{'Service': 'app', 'State': 'running', 'Health': 'healthy',
                     'Name': 'Müller, Jürgen', 'Command': 'url-password', 'Publishers': private}],
        database={'migration_head': '0146', 'organization_count': 2, 'row_counts': {'person': 37}},
        now=datetime(2026, 10, 30, tzinfo=timezone.utc),
    ))
    assert set(members) == {'manifest.json', 'environment.json', 'containers.json', 'database.json', 'errors.log', 'README.txt'}
    for member in members.values():
        for value in [*private, 'x' * 24, 'a-secret', 'secret.example.org']:
            assert value not in member
    environment = json.loads(members['environment.json'])
    assert environment['config']['HV_SMTP_PASSWORD'] == '<set>'
    assert environment['config']['HV_ENABLE_API_DOCS'] == '<bool>'
    assert environment['feature_flags']['HV_ENABLE_API_DOCS'] is False
    assert json.loads(members['containers.json']) == [{'service': 'app', 'state': 'running', 'health': 'healthy'}]
    manifest = json.loads(members['manifest.json'])
    assert manifest['app_version'] == '0.1.0'
    assert manifest['release_target'] == '0146'
    assert manifest['created_at'] == '2026-10-30T00:00:00+00:00'
    assert manifest['image_digest'] is None
    assert 'request_id=failure-123456' in members['errors.log']
    assert 'RuntimeError' in members['errors.log']
    assert 'request_id=warning-123456' in members['errors.log']
    assert 'ignored-123456' not in members['errors.log']
    assert bundle_filename(datetime(2026, 10, 30, tzinfo=timezone.utc)) == 'verwalino-diagnostics-20261030T000000000000Z.tar.gz'


def test_only_latest_warning_and_error_records_are_retained():
    lines = [f'2026-10-30 12:00:00,123 ERROR app.test request_id=error-{i:08} ValueError: private' for i in range(600)]
    errors = unpack_bundle(build_bundle(log_lines=lines))['errors.log']
    assert len(errors.splitlines()) == 500
    assert 'error-00000099' not in errors
    assert 'error-00000100' in errors
    assert 'error-00000599' in errors


def test_metadata_cannot_smuggle_free_text_into_a_bundle():
    members = unpack_bundle(build_bundle(
        release_id='Müller, Jürgen', image='private@example.org',
        containers=[{'Service': 'private@example.org', 'State': 'private-name'}],
        database={'migration_head': 'password=secret', 'row_counts': {'person': 'private-name'}, 'name': 'private-name'},
    ))
    assert all(value not in ''.join(members.values()) for value in ('Müller', 'private@example.org', 'private-name', 'password=secret'))


async def test_database_collection_is_read_only_and_scoped_to_visible_rows(seed_data, migrator_session):
    from app.diagnostics import collect_database

    from sqlalchemy import event
    from app.core.db import get_engine

    statements = []
    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    engine = get_engine().sync_engine
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        own = await collect_database(seed_data['org_a_id'], seed_data['user_a_admin_id'])
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert 'SET TRANSACTION READ ONLY' in statements
    assert not any('alembic_version' in sql or 'count(*) FROM organization' in sql for sql in statements)
    total = await collect_database()
    assert own['status'] == total['status'] == 'available'
    assert own['migration_head'] is None
    from sqlalchemy import text
    organizations = await migrator_session.scalar(text('SELECT count(*) FROM organization'))
    own_members = await migrator_session.scalar(text('SELECT count(*) FROM membership WHERE organization_id=:org'), {'org': seed_data['org_a_id']})
    all_members = await migrator_session.scalar(text('SELECT count(*) FROM membership'))
    assert own.get('organization_count') is None
    assert total['organization_count'] == organizations
    assert own['row_counts']['membership'] == own_members
    assert total['row_counts']['membership'] == all_members


async def test_seeded_request_failure_keeps_request_id_and_error_class(monkeypatch, caplog):
    import httpx
    from app.main import app
    from app.core.logging import RequestFormatter

    monkeypatch.setattr(app.router, 'routes', list(app.router.routes))

    @app.get('/api/test-diagnostics-failure')
    async def fail():
        raise RuntimeError('Müller, Jürgen unrecognized-private-body')

    caplog.handler.setFormatter(RequestFormatter())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
        response = await client.get('/api/test-diagnostics-failure')
    assert response.status_code == 500
    errors = unpack_bundle(build_bundle(log_lines=caplog.text.splitlines()))['errors.log']
    assert response.json()['request_id'] in errors
    assert 'RuntimeError' in errors
    assert 'Müller' not in errors
    assert 'unrecognized-private-body' not in errors


async def test_migration_head_is_read_when_app_role_has_permission():
    from sqlalchemy import text
    from app.diagnostics import collect_database

    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL

    engine = create_async_engine(MIGRATOR_URL)
    previous = False
    try:
        async with engine.begin() as connection:
            previous = await connection.scalar(text("SELECT has_table_privilege('app_rw', 'alembic_version', 'SELECT')"))
            await connection.execute(text('REVOKE SELECT ON alembic_version FROM app_rw'))
        try:
            unavailable = await collect_database()
            assert unavailable['migration_head'] is None
            assert unavailable['status'] == 'available'
            async with engine.begin() as connection:
                await connection.execute(text('GRANT SELECT ON alembic_version TO app_rw'))
            assert (await collect_database())['migration_head'] == '0146'
        finally:
            async with engine.begin() as connection:
                await connection.execute(text('GRANT SELECT ON alembic_version TO app_rw' if previous else
                                              'REVOKE SELECT ON alembic_version FROM app_rw'))
    finally:
        await engine.dispose()


def test_personal_data_in_a_supplied_request_id_is_omitted_from_every_member():
    iban = 'DE89370400440532013000'
    members = unpack_bundle(build_bundle(log_lines=[
        f'2026-10-30 12:00:00,123 ERROR app.test request_id={iban} RuntimeError: private',
    ]))
    assert all(iban not in value for value in members.values())


def test_opaque_uuid_and_conventional_request_ids_survive_scrubbing():
    identities = ['12345678-1234-4234-8234-123456789012', 'failure-123456']
    logs = [f'2026-10-30 12:00:00,123 ERROR app.test request_id={value} RuntimeError: private' for value in identities]
    errors = unpack_bundle(build_bundle(log_lines=logs))['errors.log']
    assert all(identity in errors for identity in identities)


@pytest.mark.parametrize('kind', ['Exception', 'RuntimeError', 'IntegrityError', 'ExceptionGroup'])
def test_exception_classes_survive_without_their_messages(kind):
    logs = ['2026-10-30 12:00:00,123 ERROR app.test request_id=error-123456 failed', f'{kind}: private-message']
    errors = unpack_bundle(build_bundle(log_lines=logs))['errors.log']
    assert kind in errors
    assert 'private-message' not in errors


@pytest.mark.parametrize('key', [
    'HV_SESSION_SECRET', 'HV_SMTP_PASSWORD', 'HV_SETUP_TOKEN', 'HV_ERROR_TRACKING_DSN',
    'HV_OPENAI_API_KEY', 'HV_SIGNING_KEY_ID', 'HV_DATABASE_URL',
    'HV_DATABASE_URL_SYNC', 'HV_DATABASE_URL_MIGRATOR',
])
def test_secret_shapes_do_not_disclose_value_lengths(key):
    from pydantic import SecretStr

    for value in ['postgresql://user:pw@db/test', 'https://user:long-password@host/', SecretStr('tiny')]:
        assert config_shapes({key: value}) == {key: '<set>'}
    assert config_shapes({key: ''}) == {key: '<unset>'}


def test_non_secret_strings_retain_length_shapes():
    assert config_shapes({'HV_ALLOWED_HOSTS': 'localhost', 'HV_SUPPORT_URL': 'https://example.org'}) == {
        'HV_ALLOWED_HOSTS': '<set, 9 chars>', 'HV_SUPPORT_URL': '<set, 19 chars>',
    }


def test_appending_records_keeps_files_bounded_and_scrubbed_without_repeated_reads(monkeypatch, tmp_path):
    import logging
    from pathlib import Path
    from uuid import UUID
    from app.core.config import settings
    from app.core.logging import DiagnosticsHandler
    from app.core.error_tracking import request_state

    directory = tmp_path / 'diagnostics'
    monkeypatch.setattr(settings, 'diagnostics_dir', str(directory))
    original_open = Path.open
    reads = 0

    def track_reads(path, mode='r', *args, **kwargs):
        nonlocal reads
        if path.parent == directory and 'r' in mode:
            reads += 1
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', track_reads)
    handler = DiagnosticsHandler()
    organization = UUID(int=4)
    token = request_state.set({'organization_id': organization})
    try:
        for index in range(525):
            # A replacement handler must retain this PID's already written records.
            if index == 20:
                handler = DiagnosticsHandler()
            record = logging.LogRecord('app.test', logging.ERROR, '', 0, 'private@example.org body-secret', (),
                                       (RuntimeError, RuntimeError('password=private-secret'), None))
            record.request_id = f'append-{index:08}'
            handler.handle(record)
    finally:
        request_state.reset(token)

    assert reads <= 4  # At most one initial read per file and handler.
    files = list(directory.glob('*.jsonl'))
    assert len(files) == 2
    for path in files:
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        limit = 50 if str(organization) in path.name else 500
        assert len(entries) == limit
        assert f'append-{525 - limit:08}' in entries[0]['line']
        assert 'append-00000524' in entries[-1]['line']
        assert all('RuntimeError' in entry['line'] for entry in entries)
        assert all(private not in path.read_text() for private in ('private@example.org', 'body-secret', 'private-secret'))


def test_worker_files_share_errors_and_preserve_each_organizations_recent_records(monkeypatch, tmp_path):
    import logging
    import os
    from uuid import UUID
    from app.core.config import settings
    from app.core import logging as app_logging
    from app.core.error_tracking import request_state

    monkeypatch.setattr(settings, 'diagnostics_dir', str(tmp_path / 'diagnostics'))
    first, second, reader = (app_logging.DiagnosticsHandler() for _ in range(3))
    org_a, org_b = UUID(int=1), UUID(int=2)
    active = {10101, 20202}
    def probe(pid, signal):
        if pid not in active:
            raise ProcessLookupError
    monkeypatch.setattr(os, 'kill', probe)
    for pid, handler, organization, count in [(10101, first, org_a, 60), (20202, second, org_b, 600)]:
        monkeypatch.setattr(os, 'getpid', lambda: pid)
        token = request_state.set({'organization_id': organization})
        try:
            for index in range(count):
                record = logging.LogRecord('app.test', logging.ERROR, '', 0, 'private-body', (),
                                           (RuntimeError, RuntimeError('private-message'), None))
                record.created = 1700000000 + (1000 if organization == org_b else 0) + index
                record.request_id = f'worker-{pid}-{index:08}'
                handler.handle(record)
        finally:
            request_state.reset(token)
    # An independent reader sees both workers, with no process-local buffers.
    own = reader.for_organization(org_a)
    busy = reader.for_organization(org_b)
    assert len(own) == 50
    assert 'worker-10101-00000009' not in '\n'.join(own)
    assert 'worker-10101-00000010' in own[0]
    assert len(busy) == 500
    assert 'worker-20202-00000100' in busy[0]
    assert 'worker-20202-00000599' in busy[-1]
    directory = tmp_path / 'diagnostics'
    assert len((directory / '20202.jsonl').read_text().splitlines()) == 500
    for path in directory.glob('*.jsonl'):
        assert len(path.read_text().splitlines()) <= 500
        assert path.stat().st_mode & 0o777 == 0o600
        assert 'private-body' not in path.read_text() and 'private-message' not in path.read_text()
    assert directory.stat().st_mode & 0o777 == 0o700
    # Dead workers and their organization reserves are removed on read.
    active.remove(10101)
    assert reader.for_organization(org_a) == []
    assert list(directory.glob('10101*.jsonl')) == []


def test_shared_error_buffer_excludes_unvalidated_organizations(monkeypatch, tmp_path):
    import logging
    from uuid import UUID
    from app.core.config import settings
    from app.core.logging import DiagnosticsHandler
    from app.core.error_tracking import request_state

    monkeypatch.setattr(settings, 'diagnostics_dir', str(tmp_path / 'diagnostics'))
    token = request_state.set(None)
    try:
        DiagnosticsHandler().handle(logging.LogRecord('app.test', logging.ERROR, '', 0, 'private', (), None))
    finally:
        request_state.reset(token)
    assert DiagnosticsHandler().for_organization(UUID(int=1)) == []
    assert not list((tmp_path / 'diagnostics').glob('*.jsonl'))


def test_shared_error_buffer_preserves_distinct_events_with_identical_public_fields(monkeypatch, tmp_path):
    import logging
    from uuid import UUID
    from app.core.config import settings
    from app.core.logging import DiagnosticsHandler
    from app.core.error_tracking import request_state

    monkeypatch.setattr(settings, 'diagnostics_dir', str(tmp_path / 'diagnostics'))
    writer = DiagnosticsHandler()
    organization = UUID(int=3)
    token = request_state.set({'organization_id': organization})
    try:
        for _ in range(50):
            record = logging.LogRecord('app.test', logging.WARNING, '', 0, 'private', (), None)
            record.created = 1700000000
            record.request_id = 'same-error-123456'
            writer.handle(record)
    finally:
        request_state.reset(token)
    lines = DiagnosticsHandler().for_organization(organization)
    assert len(lines) == 50
    assert len(set(lines)) == 1  # Only the internal event identity differs.
