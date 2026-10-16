"""Physical consumption follows retained derivative provenance across uploads."""
import importlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store, fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import confirm_grant, ready, command

pytestmark = pytest.mark.asyncio


async def rendered(api, logical, partition):
    reply = await api.post('/api/logical-documents/'+logical['id']+'/visual-extracts',
        json={'partition_revision_id':partition, 'reason':'Retain exact physical source provenance'},
        headers={'Idempotency-Key':str(uuid.uuid4())})
    assert reply.status_code == 201, reply.text
    return reply.json()


async def aliases(ids, digest, first=None, last=None):
    return await read_value('SELECT source_physical_aliases(:org,:hash,:first,:last)',
        {**ids, 'hash':digest, 'first':first, 'last':last})


async def synthetic_edge(ids, template, digest, source, first, last):
    """Migration-role geometry fixture only; product rendering is tested separately."""
    await mutate(text("""WITH proof AS (
      INSERT INTO visual_extract_attestation(organization_id,actor_id,request_key,request_digest,backend_pid,transaction_id,payload)
      SELECT organization_id,actor_id,gen_random_uuid(),request_digest,pg_backend_pid(),pg_current_xact_id(),payload
      FROM visual_extract_attestation WHERE id=(SELECT attestation_id FROM visual_extract_artifact WHERE id=:template)
      RETURNING id
    ) INSERT INTO visual_extract_artifact SELECT (jsonb_populate_record(NULL::visual_extract_artifact,
      to_jsonb(v)||jsonb_build_object('id',gen_random_uuid(),'attestation_id',proof.id,'request_key',gen_random_uuid(),
        'content_sha256',CAST(:hash AS text),'source_sha256',CAST(:source AS text),'page_start',CAST(:first AS integer),'page_end',CAST(:last AS integer),
        'verified_page_count',200,'output_page_count',CAST(:last AS integer)-CAST(:first AS integer)+1))).*
      FROM visual_extract_artifact v CROSS JOIN proof WHERE v.id=:template"""),
      {**ids, 'template':uuid.UUID(template['id']), 'hash':digest, 'source':source, 'first':first, 'last':last})


async def legacy_source(api,ids,data,scoped=True):
    category = await read_value("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1",ids)
    body = {'accounting_scope_id':str(ids['scope']),'property_id':str(ids['property']),'cost_category_id':str(category),
        'invoice_date':'2026-01-01','service_period_from':'2026-01-01','service_period_to':'2026-01-31',
        'gross_amount':'17.00','document_number':'LEGACY-CLAIM','tenant_apportionable':'NICHT_UMLAGEFAEHIG'}
    if not scoped: body.pop('accounting_scope_id')
    response = await api.post('/api/cost-entries',json=body); assert response.status_code == 201,response.text
    cost = response.json()
    response = await api.post('/api/invoice-documents/upload',files={'file':('legacy.pdf',data,'application/pdf')},data={'cost_entry_id':cost['id']})
    assert response.status_code == 201,response.text
    document = response.json()
    response = await api.post('/api/cost-entries/'+cost['id']+'/confirm'); assert response.status_code == 200,response.text
    # Rehearse the exact proven-legacy backfill contract without inventing intake
    # review provenance for this independently confirmed source.
    await mutate(text("""WITH allocation AS (
      INSERT INTO allocation_set(organization_id,kind,source_document_id,primary_cost_id,property_id,scope_id,cost_count,
        content_sha256,actor_id,reason,whole_original_disclosure_reviewed,confirmed_at)
      SELECT :org,'proven_legacy',d.id,c.id,c.property_id,b.scope_id,1,d.content_sha256,c.confirmed_by,'Proven retained legacy source',false,c.confirmed_at
      FROM invoice_document d JOIN cost_entry c ON c.organization_id=d.organization_id AND c.id=d.cost_entry_id
        LEFT JOIN accounting_scope_binding b ON b.organization_id=c.organization_id AND b.cost_entry_id=c.id
      WHERE d.organization_id=:org AND d.id=:document RETURNING *
    ) INSERT INTO document_cost_link(organization_id,allocation_set_id,source_document_id,cost_entry_id,position)
      SELECT organization_id,id,source_document_id,primary_cost_id,1 FROM allocation"""),{**ids,'document':uuid.UUID(document['id'])})
    return cost,document,body


async def test_real_derivative_reupload_cannot_create_second_financial_effect(migrator_session,scope_seed,store,monkeypatch):
    ids = await fixture(migrator_session,scope_seed); await confirm_grant(ids)
    async with client(ids) as api:
        logical, allocation, treatment, preview, url = await ready(api,ids)
        adopted = await api.post(url,**command(treatment,preview)); assert adopted.status_code == 201,adopted.text
        partition = (await api.get('/api/document-refs/'+allocation['document_ref_id']+'/partition')).json()['current']['id']
        artifact = await rendered(api,logical,partition)
        data = (await api.get('/api/visual-extracts/'+artifact['id']+'/content')).content
        assert artifact['content_sha256'] != artifact['source_sha256']
        known = await aliases(ids,artifact['content_sha256'])
        assert {'hash':artifact['source_sha256'],'first':1,'last':1} in known
        assert await read_value('SELECT allocation_source_overlap(:org,:hash,NULL,NULL,NULL)',{**ids,'hash':artifact['content_sha256']})
        # Re-upload exact rendered bytes with a different reviewed invoice number
        # so the physical guard, rather than business-field matching, must block.
        monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
        _, _, second, blocked, retry_url = await ready(api,ids,document_number='REUPLOADED-DERIVATIVE')
        assert not blocked['can_adopt']
        denied = await api.post(retry_url,**command(second,blocked)); assert denied.status_code == 409,denied.text
        assert await read_value('SELECT count(*) FROM allocation_set WHERE organization_id=:org',ids) == 1
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids) == 2
        # The raw released-invoice path must reject the same known derivative too.
        with pytest.raises(DBAPIError,match='Source conflict'):
            await mutate(text("""INSERT INTO invoice_document(id,organization_id,uploaded_by,storage_uri,storage_backend,mime_type,
              content_byte_size,content_sha256,version,chain_root_id,status,released_at,released_by,retention_until)
              VALUES(:document,:org,:clerk,'synthetic/reuploaded','LOCAL_FS','application/pdf',1,:hash,1,:document,'active',now(),:clerk,'2034-12-31')"""),
              {**ids,'document':uuid.uuid4(),'hash':artifact['content_sha256']})
        raw = {**ids,'cost':uuid.uuid4(),'document':uuid.uuid4(),'hash':artifact['content_sha256']}
        await mutate(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,cost_category_plan_version,
          invoice_date,service_period_from,service_period_to,gross_amount,document_number,tenant_apportionable,status,source)
          SELECT :cost,:org,:property,id,1,'2026-01-01','2026-01-01','2026-01-31',17,'DIFFERENT-RAW-NUMBER','NICHT_UMLAGEFAEHIG','needs_review','MANUAL'
          FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""),raw)
        await mutate(text("""INSERT INTO invoice_document(id,organization_id,cost_entry_id,uploaded_by,storage_uri,storage_backend,mime_type,
          content_byte_size,content_sha256,version,chain_root_id,status)
          VALUES(:document,:org,:cost,:clerk,'synthetic/reuploaded','LOCAL_FS','application/pdf',1,:hash,1,:document,'active')"""),raw)
        with pytest.raises(DBAPIError,match='Source conflict'):
            await mutate(text("UPDATE cost_entry SET status='confirmed',confirmed_at=now(),confirmed_by=:clerk WHERE id=:cost"),raw)


async def test_alias_geometry_shared_ancestors_and_disconnected_provenance(migrator_session,scope_seed,store):
    from tests.test_visual_extract_artifacts import basis
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,_,logical,_,body = await basis(api,ids)
        template = await rendered(api,logical,body['partition_revision_id'])
    root, first, second, sibling, disjoint, other = [character*64 for character in 'abcdef']
    await synthetic_edge(ids,template,first,root,10,14)
    await synthetic_edge(ids,template,second,first,2,3)
    await synthetic_edge(ids,template,sibling,root,12,15)
    await synthetic_edge(ids,template,disjoint,root,14,16)
    nested = await aliases(ids,second)
    assert {'hash':first,'first':2,'last':3} in nested
    assert {'hash':root,'first':11,'last':12} in nested
    narrow = await aliases(ids,second,2,2)
    assert {'hash':root,'first':12,'last':12} in narrow
    async def overlap(left,right):
        return await read_value('SELECT source_aliases_overlap(CAST(:left AS jsonb),CAST(:right AS jsonb))',
            {'left':__import__('json').dumps(await aliases(ids,left)), 'right':__import__('json').dumps(await aliases(ids,right))})
    assert await overlap(second,sibling)
    assert not await overlap(second,disjoint)
    # Identical output bytes can have more than one retained provenance. Preserve
    # both alternatives even when neither is the first-created artifact.
    await synthetic_edge(ids,template,second,other,30,31)
    assert {'hash':other,'first':30,'last':31} in await aliases(ids,second)
    with pytest.raises(DBAPIError,match='further review'):
        await aliases(ids,second,1,3)
    for role in ('app_rw','app_worker','app_review'):
        assert not await read_value("SELECT has_function_privilege(:role,'source_physical_aliases(uuid,text,integer,integer)','EXECUTE')",{'role':role})
    with pytest.raises(DBAPIError,match='permission denied'):
        await read_value('SELECT source_physical_aliases(:org,:hash,NULL,NULL)',{**ids,'hash':second},actor_key='clerk')
    # Provenance is organization-local; knowing another tenant's hash grants no
    # identity relation in this organization.
    assert await aliases({'org':uuid.uuid4()},second) == [{'hash':second,'first':None,'last':None}]


async def test_alias_cycles_terminate_and_both_budgets_fail_closed(migrator_session,scope_seed,store):
    from tests.test_visual_extract_artifacts import basis
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,_,logical,_,body = await basis(api,ids)
        template = await rendered(api,logical,body['partition_revision_id'])
    a,b = '1'*64,'2'*64
    await synthetic_edge(ids,template,a,b,1,1); await synthetic_edge(ids,template,b,a,1,1)
    assert len(await aliases(ids,a)) == 3  # whole output plus both page identities
    # Distinct-state limit (root plus 64 proven parents).
    for number in range(64):
        await synthetic_edge(ids,template,'3'*64,format(number+100,'064x'),1,1)
    with pytest.raises(DBAPIError,match='further review'): await aliases(ids,'3'*64)
    # Examined-edge limit, even though repeated records yield only two aliases.
    for _ in range(257): await synthetic_edge(ids,template,'4'*64,'5'*64,1,1)
    with pytest.raises(DBAPIError,match='further review'): await aliases(ids,'4'*64)


async def test_new_provenance_invalidates_preview_and_claims_find_common_ancestor(migrator_session,scope_seed,store):
    ids = await fixture(migrator_session,scope_seed); await confirm_grant(ids)
    async with client(ids) as api:
        logical,allocation,review,preview,url = await ready(api,ids)
        partition = (await api.get('/api/document-refs/'+allocation['document_ref_id']+'/partition')).json()['current']['id']
        template = await rendered(api,logical,partition)
        original = template['source_sha256']; ancestor = '6'*64
        await synthetic_edge(ids,template,original,ancestor,10,14)
        updated = (await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()
        assert updated['can_adopt'] and updated['preview_digest'] != preview['preview_digest']
        assert (await api.post(url,**command(review,preview))).status_code == 409
        adopted = await api.post(url,**command(review,updated)); assert adopted.status_code == 201,adopted.text
    await synthetic_edge(ids,template,'7'*64,ancestor,14,16)
    await synthetic_edge(ids,template,'8'*64,ancestor,15,16)
    for digest, expected in [('7'*64,True),('8'*64,False)]:
        assert await read_value('SELECT allocation_source_overlap(:org,:hash,NULL,NULL,NULL)',{**ids,'hash':digest}) is expected
    assert not await read_value('SELECT allocation_source_overlap(:org,:hash,NULL,NULL,:root)',
        {**ids,'hash':'7'*64,'root':uuid.UUID(adopted.json()['id'])})
    # A damaged/missing claim must not silently erase a retained root. Rehearse
    # only in a savepoint, restoring every immutable record afterwards.
    from app.core.config import settings
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(settings.database_url_migrator)
    try:
        async with engine.connect() as db:
            async with db.begin() as transaction:
                await db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                assert await db.scalar(text('SELECT allocation_source_overlap(:org,:hash,NULL,NULL,NULL)'),{**ids,'hash':'7'*64})
                await transaction.rollback()
    finally: await engine.dispose()


@pytest.mark.parametrize('same_bytes',[False,True])
async def test_legacy_corrections_preserve_chain_but_unrelated_derivative_confirmation_fails(migrator_session,scope_seed,store,same_bytes):
    from tests.test_invoice_extraction_worker import pdf_bytes
    ids = await fixture(migrator_session,scope_seed); await confirm_grant(ids)
    async with client(ids) as api:
        logical,allocation,_,_,_ = await ready(api,ids)
        partition = (await api.get('/api/document-refs/'+allocation['document_ref_id']+'/partition')).json()['current']['id']
        artifact = await rendered(api,logical,partition)
        original_id = await read_value('SELECT intake_original_id FROM document_ref WHERE id=:ref',{**ids,'ref':uuid.UUID(allocation['document_ref_id'])})
        data = (await api.get('/api/intake-batches/originals/'+str(original_id)+'/content')).content
        old,source,body = await legacy_source(api,ids,data)
        root_cost = old['id']
        # An unrelated cost with different business fields and a derivative of
        # this legacy-only claim must fail at the confirmation trigger itself.
        reply = await api.post('/api/cost-entries',json={**body,'document_number':'UNRELATED','gross_amount':'23.00'})
        assert reply.status_code == 201,reply.text
        unrelated = reply.json()
        derivative = (await api.get('/api/visual-extracts/'+artifact['id']+'/content')).content
        reply = await api.post('/api/invoice-documents/upload',files={'file':('again.pdf',derivative,'application/pdf')},data={'cost_entry_id':unrelated['id']})
        assert reply.status_code == 201,reply.text
        with pytest.raises(DBAPIError,match='Source conflict'):
            await read_value("UPDATE cost_entry SET status='confirmed',confirmed_at=now(),confirmed_by=:clerk WHERE id=:cost RETURNING id",
                {**ids,'cost':uuid.UUID(unrelated['id'])},actor_key='clerk')
        # Legitimate legacy correction keeps both version chains, and can retain
        # the same bytes. Repeat once to prove ancestry beyond one predecessor.
        for amount in ('18.00','19.00'):
            reply = await api.post('/api/cost-entries/'+old['id']+'/correction',json={'gross_amount':amount})
            assert reply.status_code == 201,reply.text
            correction = reply.json()
            reply = await api.post('/api/invoice-documents/'+source['chain_root_id']+'/version',
                files={'file':('corrected.pdf',data if same_bytes else pdf_bytes(),'application/pdf')},data={'cost_entry_id':correction['id']})
            assert reply.status_code == 201,reply.text
            source = reply.json()
            reply = await api.post('/api/cost-entries/'+correction['id']+'/confirm'); assert reply.status_code == 200,reply.text
            old = reply.json()
        assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids) == 1
        for replacement in (None,uuid.UUID(unrelated['id'])):
            with pytest.raises(DBAPIError,match='Established cost successor is immutable'):
                await read_value('UPDATE cost_entry SET superseded_by_id=:replacement WHERE id=:cost RETURNING id',
                    {**ids,'cost':uuid.UUID(root_cost),'replacement':replacement},actor_key='clerk')
        latest_bytes = (await api.get('/api/invoice-documents/'+source['id']+'/content')).content
        reply = await api.post('/api/cost-entries',json={**body,'document_number':'COPY-OF-CORRECTION','gross_amount':'27.00'})
        assert reply.status_code == 201,reply.text
        copied = reply.json()
        reply = await api.post('/api/invoice-documents/upload',files={'file':('copy.pdf',latest_bytes,'application/pdf')},data={'cost_entry_id':copied['id']})
        assert reply.status_code == 201,reply.text
        with pytest.raises(DBAPIError,match='Source conflict'):
            await read_value("UPDATE cost_entry SET status='confirmed',confirmed_at=now(),confirmed_by=:clerk WHERE id=:cost RETURNING id",
                {**ids,'cost':uuid.UUID(copied['id'])},actor_key='clerk')


async def test_legacy_continuation_cannot_change_property_while_confirming(migrator_session,scope_seed,store):
    from tests.test_invoice_extraction_worker import pdf_bytes
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        data = pdf_bytes()
        old,source,_ = await legacy_source(api,ids,data,scoped=False)
        response = await api.post('/api/cost-entries/'+old['id']+'/correction',json={'gross_amount':'18.00'})
        assert response.status_code == 201,response.text
        correction = response.json()
        response = await api.post('/api/invoice-documents/'+source['chain_root_id']+'/version',
            files={'file':('corrected.pdf',data,'application/pdf')},data={'cost_entry_id':correction['id']})
        assert response.status_code == 201,response.text
        assert await read_value('SELECT count(*) FROM accounting_scope_binding WHERE cost_entry_id=:cost',{'cost':uuid.UUID(correction['id'])}) == 0
        with pytest.raises(DBAPIError,match='Source conflict'):
            await read_value("""UPDATE cost_entry SET property_id=:other_property,status='confirmed',confirmed_at=now(),confirmed_by=:clerk
              WHERE id=:cost RETURNING id""",{**ids,'cost':uuid.UUID(correction['id'])},actor_key='clerk')
        response = await api.post('/api/cost-entries/'+correction['id']+'/confirm')
        assert response.status_code == 200,response.text


async def test_alias_migration_roundtrip_and_populated_rollback_refusal(migrator_session,scope_seed,store):
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await fixture(migrator_session,scope_seed); await confirm_grant(ids)
    async with client(ids) as api:
        logical,allocation,review,preview,url = await ready(api,ids)
        response = await api.post(url,**command(review,preview)); assert response.status_code == 201,response.text
        partition = (await api.get('/api/document-refs/'+allocation['document_ref_id']+'/partition')).json()['current']['id']
        await rendered(api,logical,partition)
    migration = importlib.import_module('migrations.versions.0120_allocation_source_aliases')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').downgrade()
            importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
            importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
            before = {signature:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature}) for signature,_,_ in migration.PATCHES}
            with pytest.raises(DBAPIError,match='Retain physical source'),db.begin_nested(): migration.downgrade()
            point = db.begin_nested()
            try:
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                migration.downgrade(); migration.upgrade()
                assert before == {signature:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature}) for signature in before}
            finally:
                point.rollback()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').upgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').upgrade()
                importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').upgrade()
    finally: engine.dispose()
