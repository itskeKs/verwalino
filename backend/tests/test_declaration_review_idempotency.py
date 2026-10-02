"""Durable review retries preserve one decision and enforce current access."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_tenant_session
from tests.test_derived_source_access import committed_scope, actor_session  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401
from tests.test_declaration_review_commands import review_seed
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_scoped_account_statements import client

pytestmark = pytest.mark.asyncio


async def test_original_review_request_replays_after_revision_changes(committed_scope):
    seed = committed_scope
    params = await review_seed(seed)
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    url = f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}"
    body = {'draft_revision': 1, 'decision': 'ACCEPTED'}
    async with synthetic_enforcement(seed), client({'org': params['org'], 'actor': params['actor']}, 'actor') as api:
        first = await api.patch(url, json=body, headers=headers)
        assert first.status_code == 200, first.text
        second = await api.patch(url, json=body, headers=headers)
        assert second.status_code == 200, second.text
        assert second.json() == first.json()
        async with get_tenant_session(params['org']) as db:
            await actor_session(db, params['actor'])
            assert await db.scalar(text('SELECT draft_revision FROM declaration_import WHERE id=:job'), params) == 2
            assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"), params) == 1


async def test_review_key_cannot_be_reused_for_changed_payload(committed_scope):
    seed = committed_scope
    params = await review_seed(seed)
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    url = f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}"
    async with synthetic_enforcement(seed), client({'org': params['org'], 'actor': params['actor']}, 'actor') as api:
        first = await api.patch(url, json={'draft_revision': 1, 'decision': 'ACCEPTED'}, headers=headers)
        assert first.status_code == 200, first.text
        changed = await api.patch(url, json={'draft_revision': 2, 'decision': 'REJECTED'}, headers=headers)
        assert changed.status_code == 409, changed.text
        assert changed.json()['detail']['code'] == 'IDEMPOTENCY_KEY_CONFLICT'


@pytest.mark.parametrize('bulk', [False, True])
async def test_parallel_duplicates_and_replay_after_a_later_decision(committed_scope, bulk):
    import asyncio
    from tests.test_scoped_account_statements import mutate
    seed = committed_scope
    params = await review_seed(seed)
    await mutate(text("UPDATE declaration_candidate SET candidate_type='UNIT_FIELD',subject_key='unit:1',field_key='area',proposed_value_json='{\"kind\":\"DECIMAL\",\"value\":\"50\",\"unit\":\"m2\"}',risk_class='LOW',requires_individual_review=false WHERE id=:candidate"), params)
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    url = f"/api/declaration-imports/{params['job']}/candidates/" + ('bulk-review' if bulk else str(params['candidate']))
    body = {'draft_revision': 1, 'decision': 'ACCEPTED'}
    if bulk:
        params['second'] = uuid.uuid4()
        await mutate(text("""INSERT INTO declaration_candidate(id,organization_id,import_id,stable_key,candidate_type,subject_key,field_key,
          operation,proposed_value_json,confidence,risk_class) SELECT :second,organization_id,import_id,'bulk-second',candidate_type,
          'unit:2',field_key,operation,proposed_value_json,confidence,risk_class FROM declaration_candidate WHERE id=:candidate"""), params)
        await mutate(text("INSERT INTO declaration_candidate_evidence(organization_id,candidate_id,document_id,pdf_page,quote) VALUES(:org,:second,:document,1,'Synthetic')"), params)
        body['candidate_ids'] = [str(params['candidate']), str(params['second'])]
    async with synthetic_enforcement(seed), client({'org': params['org'], 'actor': params['actor']}, 'actor') as api:
        send = api.post if bulk else api.patch
        first, second = await asyncio.gather(send(url, json=body, headers=headers), send(url, json=body, headers=headers))
        assert first.status_code == second.status_code == 200, (first.text, second.text)
        assert first.json() == second.json()
        changed = await send(url, json={**body, 'draft_revision': 2, 'decision': 'REJECTED'}, headers={'Idempotency-Key': str(uuid.uuid4())})
        assert changed.status_code == 200, changed.text
        if bulk:
            body['candidate_ids'].reverse()
        replay = await send(url, json=body, headers=headers)
        assert replay.json() == first.json()
        async with get_tenant_session(params['org']) as db:
            await actor_session(db, params['actor'])
            assert await db.scalar(text('SELECT draft_revision FROM declaration_import WHERE id=:job'), params) == 3
            assert await db.scalar(text('SELECT review_status FROM declaration_candidate WHERE id=:candidate'), params) == 'REJECTED'
            assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:job AND action='declaration_import.candidate_reviewed'"), params) == (4 if bulk else 2)


@pytest.mark.parametrize('boundary', ['paused', 'inactive', 'different_actor', 'different_import'])
async def test_replay_rechecks_current_actor_and_source(committed_scope, boundary):
    from tests.test_property_source_enforcement import change_mode
    from tests.test_scoped_account_statements import mutate
    seed = committed_scope
    params = await review_seed(seed)
    other = await review_seed(seed) if boundary == 'different_import' else None
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    url = f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}"
    body = {'draft_revision': 1, 'decision': 'ACCEPTED'}
    async with synthetic_enforcement(seed):
        async with client({'org': params['org'], 'actor': params['actor']}, 'actor') as api:
            assert (await api.patch(url, json=body, headers=headers)).status_code == 200
            try:
                if boundary == 'paused':
                    await change_mode(seed, 'PAUSED')
                elif boundary == 'inactive':
                    await mutate(text('UPDATE "user" SET is_active=false WHERE id=:actor'), params)
                elif boundary == 'different_import':
                    url = f"/api/declaration-imports/{other['job']}/candidates/{other['candidate']}"
                if boundary != 'different_actor':
                    response = await api.patch(url, json=body, headers=headers)
                    assert response.status_code in {401, 403, 404, 409}, response.text
                    assert 'Synthetic' not in response.text
            finally:
                if boundary == 'inactive':
                    await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'), params)
        if boundary == 'different_actor':
            async with client({'org': params['org'], 'actor': seed['user_a_clerk_id']}, 'actor') as api:
                response = await api.patch(url, json=body, headers=headers)
                assert response.status_code == 404, response.text


async def test_failed_receipt_transaction_can_be_retried_without_duplicate_effects(committed_scope, monkeypatch):
    from app.repositories.declaration_review_repo import DeclarationReviewRepository
    seed = committed_scope
    params = await review_seed(seed)
    original = DeclarationReviewRepository.review_request
    async def fail_after_record(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if len(args) > 4:
            raise RuntimeError('Synthetic lost transaction')
        return result
    headers = {'Idempotency-Key': str(uuid.uuid4())}
    url = f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}"
    body = {'draft_revision': 1, 'decision': 'ACCEPTED'}
    async with synthetic_enforcement(seed), client({'org': params['org'], 'actor': params['actor']}, 'actor') as api:
        monkeypatch.setattr(DeclarationReviewRepository, 'review_request', fail_after_record)
        failed = await api.patch(url, json=body, headers=headers)
        assert failed.status_code == 503, failed.text
        monkeypatch.setattr(DeclarationReviewRepository, 'review_request', original)
        retried = await api.patch(url, json=body, headers=headers)
        assert retried.status_code == 200, retried.text
        assert retried.json()['draft_revision'] == 2


async def test_receipt_storage_is_private_and_recording_requires_current_transaction(committed_scope):
    from app.core.db import get_review_tenant_session, get_worker_tenant_session
    from sqlalchemy.exc import DBAPIError
    seed = committed_scope
    params = await review_seed(seed)
    sql = "SELECT declaration_review_request(:org,:job,'single',:key,:digest,CAST(:result AS jsonb))"
    values = {**params, 'key': uuid.uuid4(), 'digest': 'a' * 64, 'result': '{"draft_revision":1}'}
    async with synthetic_enforcement(seed):
        for factory in [get_tenant_session, get_worker_tenant_session, get_review_tenant_session]:
            async with factory(params['org']) as db:
                await actor_session(db, params['actor'])
                for privilege in ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']:
                    assert not await db.scalar(text("SELECT has_table_privilege(current_user,'declaration_review_receipt',:privilege)"), {'privilege': privilege})
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(text(sql), values)


async def test_receipt_migration_retains_completed_results(committed_scope):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    seed = committed_scope
    params = await review_seed(seed)
    migration = importlib.import_module('migrations.versions.0074_declaration_review_receipts')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        # Earlier tests may retain receipts; a rollback-only truncate supplies an
        # empty downgrade rehearsal without discarding that evidence.
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                db.execute(text('TRUNCATE declaration_review_receipt'))
                migration.downgrade()
                migration.upgrade()
                assert not db.scalar(text("SELECT has_table_privilege('app_review','declaration_review_receipt','SELECT')"))
            finally:
                point.rollback()
        async with synthetic_enforcement(seed), client({'org': params['org'], 'actor': params['actor']}, 'actor') as api:
            response = await api.patch(f"/api/declaration-imports/{params['job']}/candidates/{params['candidate']}",
                json={'draft_revision': 1, 'decision': 'ACCEPTED'}, headers={'Idempotency-Key': str(uuid.uuid4())})
            assert response.status_code == 200, response.text
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            before = db.scalar(text('SELECT count(*) FROM declaration_review_receipt'))
            with pytest.raises(DBAPIError, match='Retain durable review receipts'):
                with db.begin_nested():
                    migration.downgrade()
            assert db.scalar(text('SELECT count(*) FROM declaration_review_receipt')) == before
    finally:
        engine.dispose()
