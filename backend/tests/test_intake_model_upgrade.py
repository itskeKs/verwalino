"""0198 lets the inbox read and extract through the organization's two models (issue #501, ADR 0017).

The ledger of 0197 serves the inbox attempts, the claim's lease is renewed before every paid request and the proposal
function of the inbox accepts the model version and the tenth field. Real inbox attempts of the test database make the
rows these tests look at.
"""
import importlib
import json
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import deployment
from app.core.intake_extraction import extract_logical
from tests.conftest import MIGRATOR_SYNC_URL
from tests import intake_model_support
from tests.intake_model_support import Harness, confirm_partition, document_pdf, enable_models, extract_all
from tests.invoice_model_support import discard_invoice_model_data
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_intake_batches import work_sql
from tests.test_intake_extraction import model_result
from tests.test_intake_extraction_worker import source
from tests.test_intake_model_extraction import documents, receive_original, split
from tests.test_invoice_source_custody import fixture, store as store
from tests.test_scope_payment_reconciliation import read_value
from tests.test_scoped_account_statements import client, mutate

pytestmark = pytest.mark.asyncio

LEDGER_ROW = """INSERT INTO invoice_model_call(organization_id,attempt_id,intake_attempt_id,stage,pdf_page,model_name,reserved_input_tokens,
    reserved_output_tokens,reserved_cost_usd) VALUES (:org,:attempt,:intake,'read_page',1,'m',100,50,0.5)"""
CLAIM = "SELECT claim_intake_extraction(:org)"
RENEW = "SELECT renew_intake_extraction(:org,:job,:fence,:seconds)"


def _migration():
    return importlib.import_module('migrations.versions.0198_intake_model_extraction')


async def test_the_inbox_model_path_is_the_release_target_after_0197():
    revision = ScriptDirectory('migrations').get_revision('0198')
    assert revision.down_revision == '0197'
    assert deployment.TARGET == '0208'
    assert ScriptDirectory('migrations').get_heads() == ['0208']


async def test_a_ledger_row_belongs_to_exactly_one_attempt_of_its_own_organization(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        await source(api, ids)
    claim = await work_sql(ids, CLAIM)
    intake = uuid.UUID(claim['attempt_id'])
    for params, violated in (({'attempt': None, 'intake': None}, 'ck_invoice_model_call_one_attempt'),
                             ({'attempt': uuid.uuid4(), 'intake': intake}, 'ck_invoice_model_call_one_attempt'),
                             ({'attempt': None, 'intake': uuid.uuid4()}, 'fk_invoice_model_call_intake_attempt')):
        with pytest.raises(DBAPIError, match=violated):
            await mutate(text(LEDGER_ROW), {**ids, **params})
    await mutate(text(LEDGER_ROW), {**ids, 'attempt': None, 'intake': intake})
    assert await read_value("SELECT count(*) FROM invoice_model_call WHERE organization_id=:org AND intake_attempt_id IS NOT NULL", ids) == 1
    # Inbox attempts are private tables, so the ledger row is an exact reference: no other organization names it.
    other = uuid.uuid4()
    await mutate(text("INSERT INTO organization(id,name) VALUES (:other,'Foreign ledger fixture')"), {**ids, 'other': other})
    with pytest.raises(DBAPIError, match='fk_invoice_model_call_intake_attempt'):
        await mutate(text(LEDGER_ROW), {**ids, 'org': other, 'attempt': None, 'intake': intake})
    assert await read_value("SELECT count(*) FROM invoice_model_call WHERE organization_id=:other", {**ids, 'other': other}) == 0


async def test_the_lease_is_renewed_only_for_the_live_fence_and_an_authorized_initiator(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        await source(api, ids, scoped=True)
    claim = await work_sql(ids, CLAIM)
    params = {'job': uuid.UUID(claim['job_id']), 'fence': uuid.UUID(claim['fence'])}
    lease = lambda: read_value("SELECT lease_expires_at FROM intake_extraction_job WHERE organization_id=:org", ids)
    first = await lease()
    assert await work_sql(ids, RENEW, seconds=900, **params) is True
    extended = await lease()
    assert (extended - first).total_seconds() > 600, "the three-minute claim now covers a request of fifteen minutes"
    assert await work_sql(ids, RENEW, seconds=1, **params) is True
    assert await lease() == extended, "a renewal never shortens the lease"
    for seconds in (0, 3601, None):
        with pytest.raises(DBAPIError, match='Invalid lease extension'):
            await work_sql(ids, RENEW, seconds=seconds, **params)
    # The interactive role cannot renew.
    with pytest.raises(DBAPIError, match='permission denied|runtime required'):
        await read_value("SELECT renew_intake_extraction(:org,:job,:fence,60)", {**ids, **params}, actor_key='clerk')
    # A fence that is not the live one renews nothing, a revoked initiator cancels the job and renews nothing.
    assert await work_sql(ids, RENEW, seconds=60, job=params['job'], fence=uuid.uuid4()) is False
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'), ids)
    assert await work_sql(ids, RENEW, seconds=60, **params) is False
    assert await read_value('SELECT status FROM intake_extraction_job WHERE organization_id=:org', ids) == 'canceled'
    assert await work_sql(ids, RENEW, seconds=60, **params) is False


async def test_the_usage_of_a_document_is_readable_only_for_those_who_may_read_the_document(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids['org'])
    async with client(ids) as api:
        ref, child = await source(api, ids, scoped=True)
    assert await extract_all(ids['org'], Harness(), processor=extract_logical) == 1
    document = {**ids, 'document': uuid.UUID(child['id'])}
    rows = await read_value("SELECT intake_model_usage(:org,:document)", document, actor_key='clerk')
    assert [(row['attempt_number'], row['stage'], row['status']) for row in rows] == [(1, 'extract_invoice', 'SETTLED')]
    assert set(rows[0]) == {'attempt_number', 'stage', 'pdf_page', 'model_name', 'status', 'reserved_input_tokens', 'reserved_output_tokens',
                            'reserved_cost_usd', 'input_tokens', 'output_tokens', 'cost_usd', 'usage_complete', 'error_code', 'created_at'}
    assert await read_value("SELECT intake_model_usage(:org,:document)", document, actor_key='actor') is None, "no read access to the original"
    assert await read_value("SELECT intake_model_usage(:org,:other)", {**ids, 'other': uuid.uuid4()}, actor_key='clerk') is None
    # The worker role acts for no person: it reads no usage either.
    assert await work_sql(ids, "SELECT intake_model_usage(:org,:document)", document=document['document']) is None


async def test_the_downgrade_refuses_to_discard_inbox_usage_or_a_model_read_proposal_and_restores_the_older_function(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    enable_models(ids['org'])
    async with client(ids) as api:
        await source(api, ids)
    assert await extract_all(ids['org'], Harness(), processor=extract_logical) == 1
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                definition = lambda: db.scalar(text("SELECT pg_get_functiondef('public.complete_intake_extraction(uuid,uuid,uuid,jsonb)'::regprocedure)"))
                constraint = lambda: db.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='extraction_revision_typed_attempt'"))
                locator = lambda: db.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='field_evidence_locator'"))
                assert 'intake-model-v1' in definition() and 'vendor_iban' in definition() and 'reading_model' in definition()
                assert "'intake-model-v1'" in constraint() and "'invoice-model-v1'" in constraint()
                assert "'reading_model'" in locator() and "'xml'" in locator() and "'unlocated'" in locator(), 'a model-read page can carry a located field'
                located_by_a_model = """INSERT INTO field_evidence(organization_id,extraction_revision_id,field_name,value_type,value,confidence,locator_status,page_number,quotation,xml_path,method)
                    SELECT organization_id,id,'gross_amount','decimal','1',0.5,'page_quote',1,'1',NULL,'%s' FROM extraction_revision WHERE processor_version='intake-model-v1' LIMIT 1"""
                with pytest.raises(DBAPIError, match='field_evidence_locator'):
                    with db.begin_nested():
                        db.execute(text(located_by_a_model % 'vision'))
                with pytest.raises(DBAPIError, match='Inbox model call records require this release'):
                    with db.begin_nested():
                        migration.downgrade()
                db.execute(text('DELETE FROM invoice_model_call'))
                with pytest.raises(DBAPIError, match='Model-read inbox proposals require this release'):
                    with db.begin_nested():
                        migration.downgrade()
                discard_invoice_model_data(lambda statement: db.execute(text(statement)))
                gate = lambda name: db.scalar(text(f"SELECT pg_get_functiondef('public.{name}'::regprocedure)"))
                assert "split_state" in gate("intake_extraction_context(uuid,uuid,uuid)") and "awaiting_split" in gate("review_source_partition(uuid,uuid,uuid,uuid,uuid[],text,boolean)")
                assert "awaiting_split" in gate("intake_batch_status(uuid,uuid,integer,integer)")
                assert "'awaiting_split'" in db.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='intake_extraction_job_status_check'"))
                limit = lambda table: db.scalar(text(f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='{table}_attempt_number_check'"))
                readers = ("claim_intake_extraction(uuid)", "intake_extraction_status(uuid,uuid)", "retry_intake_extraction(uuid,uuid,uuid,uuid,text)")
                assert all("intake_attempts_used" in gate(reader) for reader in readers), "the claim, the status and the retry read the one count"
                assert "<= 3" not in limit("intake_extraction_job") and "<= 3" not in limit("intake_extraction_attempt"), "claims are numbered without end"
                db.execute(text("UPDATE intake_extraction_job SET attempt_number=attempt_number+10"))
                db.execute(text("UPDATE intake_extraction_attempt SET attempt_number=attempt_number+10"))
                with pytest.raises(DBAPIError, match='Inbox extraction attempts beyond the third require this release'):
                    with db.begin_nested():
                        migration.downgrade()
                db.execute(text("UPDATE intake_extraction_job SET attempt_number=attempt_number-10"))
                db.execute(text("UPDATE intake_extraction_attempt SET attempt_number=attempt_number-10"))
                db.execute(text("UPDATE intake_extraction_job SET status='awaiting_split',error_code='awaiting_split'"))
                migration.downgrade()
                # The older runtime sends a whole original: a parked job is queued again, nothing else of it is lost.
                assert db.scalar(text("SELECT string_agg(DISTINCT status||':'||COALESCE(error_code,''),',') FROM intake_extraction_job")) == 'queued:'
                assert "split_state" not in gate("intake_extraction_context(uuid,uuid,uuid)")
                assert "awaiting_split" not in gate("review_source_partition(uuid,uuid,uuid,uuid,uuid[],text,boolean)")
                assert "awaiting_split" not in gate("intake_batch_status(uuid,uuid,integer,integer)") and "held" not in gate("intake_batch_status(uuid,uuid,integer,integer)")
                assert "awaiting_split" not in db.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='intake_extraction_job_status_check'"))
                assert db.scalar(text("SELECT to_regprocedure('public.hold_intake_extraction(uuid,uuid,uuid)')")) is None
                assert db.scalar(text("SELECT to_regprocedure('public.intake_split_state(uuid,uuid)')")) is None
                assert db.scalar(text("SELECT to_regprocedure('public.intake_attempts_used(uuid,uuid)')")) is None
                assert not any("intake_attempts_used" in gate(reader) for reader in readers) and all("attempt_number>=3" in gate(reader) or "attempt_number<3" in gate(reader) for reader in readers)
                assert "<= 3" in limit("intake_extraction_job") and "<= 3" in limit("intake_extraction_attempt"), "the older numbering from one to three is back"
                assert 'intake-model-v1' not in definition() and 'vendor_iban' not in definition() and 'reading_model' not in definition()
                assert "'reading_model'" not in locator() and "'text_layer'" in locator() and "'ocr'" in locator()
                assert "'intake-local-v1'" in definition() and 'intake-model-v1' not in constraint() and "'invoice-model-v1'" in constraint()
                assert db.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_name='invoice_model_call' AND column_name='intake_attempt_id'")) == 0
                assert db.scalar(text("SELECT is_nullable FROM information_schema.columns WHERE table_name='invoice_model_call' AND column_name='attempt_id'")) == 'NO'
                assert db.scalar(text("SELECT to_regprocedure('public.renew_intake_extraction(uuid,uuid,uuid,integer)')")) is None
                assert db.scalar(text("SELECT to_regprocedure('public.intake_model_usage(uuid,uuid)')")) is None
                migration.upgrade()
                assert "'reading_model'" in locator()
                assert 'intake-model-v1' in definition() and 'vendor_iban' in definition() and "'intake-model-v1'" in constraint()
                assert db.scalar(text("SELECT to_regprocedure('public.renew_intake_extraction(uuid,uuid,uuid,integer)')")) is not None
                assert "split_state" in gate("intake_extraction_context(uuid,uuid,uuid)") and "awaiting_split" in gate("intake_batch_status(uuid,uuid,integer,integer)")
                assert db.scalar(text("SELECT to_regprocedure('public.hold_intake_extraction(uuid,uuid,uuid)')")) is not None
                assert db.scalar(text("SELECT to_regprocedure('public.intake_attempts_used(uuid,uuid)')")) is not None
                assert all("intake_attempts_used" in gate(reader) for reader in readers) and "<= 3" not in limit("intake_extraction_attempt")
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


async def test_the_ledger_stays_erasable_and_the_inbox_proposal_keeps_the_tenth_field_optional(migrator_session, scope_seed, store):
    """No DELETE trigger was added (the retention catalog keeps classifying the ledger ``delete``) and an older worker's
    proposal without the tenth key is still accepted."""
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        await source(api, ids)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as db:
            assert db.scalar(text("SELECT disposition FROM public.organization_retention_catalog() WHERE entity='invoice_model_call'")) == 'delete'
            assert db.scalar(text("""SELECT count(*) FROM pg_trigger WHERE tgrelid='public.invoice_model_call'::regclass AND NOT tgisinternal
                AND (tgtype & 8) > 0 AND tgfoid::regproc::text <> 'organization_lifecycle_guard'""")) == 0
    finally:
        engine.dispose()
    claim = await work_sql(ids, CLAIM)
    from app.core.storage import get_store
    answer = model_result(get_store().get(claim['storage_uri']), claim['mime_type'], claim['page_start'], claim['page_end'], claim['verified_page_count'])
    del answer['payload']['vendor_iban']
    assert await work_sql(ids, 'SELECT complete_intake_extraction(:org,:job,:fence,CAST(:result AS jsonb))',
                          job=uuid.UUID(claim['job_id']), fence=uuid.UUID(claim['fence']), result=json.dumps(answer))


HOLD = "SELECT hold_intake_extraction(:org,:job,:fence)"


async def test_only_the_worker_role_parks_a_job_and_the_split_state_is_internal(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ref, child = await source(api, ids, scoped=True)
    claim = await work_sql(ids, CLAIM)
    assert claim["split_state"] == "decided", "a single page needs no decision"
    params = {"job": uuid.UUID(claim["job_id"]), "fence": uuid.UUID(claim["fence"])}
    with pytest.raises(DBAPIError, match="permission denied|runtime required"):
        await read_value("SELECT hold_intake_extraction(:org,:job,:fence)", {**ids, **params}, actor_key="clerk")
    for role in ("clerk", "actor"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await read_value("SELECT intake_split_state(:org,:document)", {**ids, "document": uuid.UUID(child["id"])}, actor_key=role)
    with pytest.raises(DBAPIError, match="permission denied"):
        await work_sql(ids, "SELECT intake_split_state(:org,:document)", document=uuid.UUID(child["id"]))
    # A fence that is not the live one parks nothing. A decision made after the claim queues the job again, it is not parked.
    assert await work_sql(ids, HOLD, job=params["job"], fence=uuid.uuid4()) is False
    assert await read_value("SELECT status FROM intake_extraction_job WHERE organization_id=:org", ids) == "running"
    assert await work_sql(ids, HOLD, **params) is True
    assert await read_value("SELECT status||':'||COALESCE(error_code,'') FROM intake_extraction_job WHERE organization_id=:org", ids) == "queued:"
    assert await work_sql(ids, HOLD, **params) is False, "the claim is over"


async def test_the_renewal_refuses_a_claim_whose_document_the_division_leaves_out(migrator_session, scope_seed, store):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ref = await receive_original(api, ids, document_pdf([None, None]), pages=2)
        (whole,) = (await documents(api, ref)).values()
        await confirm_partition(api, ref, whole)
        claim = await work_sql(ids, CLAIM)
        assert claim["split_state"] == "decided" and claim["logical_document_id"] == whole
        params = {"job": uuid.UUID(claim["job_id"]), "fence": uuid.UUID(claim["fence"])}
        assert await work_sql(ids, RENEW, seconds=60, **params) is True
        # The reviewer divides the original while the document is being read: the whole is not in the latest division.
        await confirm_partition(api, ref, await split(api, ref, 1, 1), await split(api, ref, 2, 2))
        assert await work_sql(ids, RENEW, seconds=60, **params) is False, "no decision, no further paid request"
        # The claim itself is still the live one: the job is parked with the reason that names the division.
        assert await work_sql(ids, HOLD, **params) is True
        assert await read_value("SELECT status||':'||error_code FROM intake_extraction_job WHERE organization_id=:org AND logical_document_id=:document",
                                {**ids, "document": uuid.UUID(whole)}) == "awaiting_split:not_in_split"


USED = "SELECT intake_attempts_used(:org,:job)"
ATTEMPT = """INSERT INTO intake_extraction_attempt(organization_id,job_id,attempt_number,fence,finished_at,result)
    VALUES (:org,:job,:number,gen_random_uuid(),clock_timestamp(),CAST(:result AS jsonb)) RETURNING id"""
REQUEST = """INSERT INTO invoice_model_call(organization_id,intake_attempt_id,stage,pdf_page,model_name,status,reserved_input_tokens,
    reserved_output_tokens,reserved_cost_usd,input_tokens,output_tokens,cost_usd,usage_complete,error_code,settled_at)
    VALUES (:org,:intake,'read_page',1,'m','SETTLED',100,50,0.5,0,0,0,true,:code,clock_timestamp())"""


async def test_an_attempt_counts_against_the_limit_only_when_a_request_left_the_host_or_the_run_was_abandoned(migrator_session, scope_seed, store):
    # The rows made here (ledger rows of an inbox attempt, attempts beyond the third) are discarded after the test: the 0198
    # downgrade refuses them, and the chain-wide downgrade tests meet them on a database that earlier tests have used.
    intake_model_support.dirty = True
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        await source(api, ids)
    job = {"job": uuid.UUID((await work_sql(ids, CLAIM))["job_id"])}
    # No role calls the count: only the functions of the claim, the status and the retry do.
    for role in ("clerk", "actor"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await read_value(USED, {**ids, **job}, actor_key=role)
    with pytest.raises(DBAPIError, match="permission denied"):
        await work_sql(ids, USED, **job)
    used = lambda: read_value(USED, {**ids, **job})
    assert await used() == 0, "the live claim has sent nothing yet"
    number = [1]

    async def attempt(result, request=False):
        number[0] += 1
        row = await read_value(ATTEMPT, {**ids, **job, "number": number[0], "result": json.dumps(result)})
        if request is not False:
            await mutate(text(REQUEST), {**ids, "intake": row, "code": request})

    await attempt({"status": "awaiting_split", "error_code": "awaiting_split"})
    await attempt({"status": "unavailable", "error_code": "ai_import_disabled"})
    await attempt({"status": "failed", "error_code": "provider_unreachable"}, request="PROVIDER_REQUEST_NOT_SENT")
    assert await used() == 0, "a park, a refusal and a request that never left the host use none of the three"
    await attempt({"status": "failed", "error_code": "model_reading_failed"}, request="PROVIDER_CONNECTION_ERROR")
    assert await used() == 1, "a request that left the host and failed uses one"
    await attempt({"status": "blocked", "error_code": "lease_expired"})
    assert await used() == 2, "an abandoned run is claimed again by itself, so it counts"
    await attempt({"status": "succeeded", "error_code": None}, request=None)
    assert await used() == 3, "a request without an error uses one"
