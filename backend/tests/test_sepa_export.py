"""T50: explicit CORE mandates and read-only monthly rent file, never transmission.

Schemas are unchanged ISO / EPC originals downloaded from their primary sites.
IBAN/creditor IDs below are PUBLIC synthetic test vectors, not credentials.
"""
from pathlib import Path
import uuid
import asyncio

from lxml import etree
import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.test_rent_changes import rent_db, rent_world, api_client, post_change  # noqa: F401

IBAN = 'DE89370400440532013000'
CREDITOR = 'DE98ZZZ09999999999'
NS = {'p':'urn:iso:std:iso:20022:tech:xsd:pain.008.001.08'}
SCHEMAS = Path(__file__).parent / 'fixtures' / 'sepa'


@pytest.mark.parametrize('value', [IBAN, 'de89 3704 0044 0532 0130 00'])
def test_sepa_iban_checksum_and_normalization(value):
    from app.services.sepa_export import validate_iban
    assert validate_iban(value) == IBAN


@pytest.mark.parametrize('value', ['DE00370400440532013000', '', 'DE123', None, '<IBAN>'])
def test_sepa_invalid_iban(value):
    from app.services.sepa_export import validate_iban
    with pytest.raises(ValueError):
        validate_iban(value)


def test_sepa_creditor_checksum_excludes_business_code():
    from app.services.sepa_export import validate_creditor_id
    assert validate_creditor_id(CREDITOR) == CREDITOR
    assert validate_creditor_id('de98abc09999999999') == 'DE98ABC09999999999'
    for value in ('DE00ZZZ09999999999', 'DE98ZZZ0999999999', '', None):
        with pytest.raises(ValueError):
            validate_creditor_id(value)


@pytest_asyncio.fixture
async def sepa_world(rent_world, rent_db):
    w = dict(rent_world)
    await rent_db.execute(text("UPDATE person SET iban=:iban,street='Testweg 1',zip_code='12345',city='Teststadt' WHERE id=:person"), {**w,'iban':IBAN})
    await rent_db.commit()
    async with api_client(w) as api:
        response = await api.post('/api/bank-accounts', json={'property_id':str(w['property']),
            'account_type':'LAUFEND','label':'SEPA synthetic only','iban':IBAN})
        assert response.status_code == 201, response.text
        w['bank'] = uuid.UUID(response.json()['id'])
    return w


def account_url(w):
    return f"/api/payments/sepa/accounts/{w['bank']}"


def creditor():
    return {'creditor_id':CREDITOR,'name':'Synthetic Vermietung','country':'DE',
            'town':'Teststadt','bank_authorized':True}


def mandate(w, **changes):
    return {'tenant_profile_id':str(w['tenant']), 'reference':'SYNTHETIC-2026-01',
        'signed_on':'2026-01-01', 'sequence_type':'FRST','last_used_on':None,
        'debtor_country':'DE','confirmed_iban':IBAN,
        'original_reference':'Synthetic paper original T50', 'mandate_confirmed':True, **changes}


def export_body(w, **changes):
    return {'bank_account_id':str(w['bank']), 'accounting_scope_id':None,
        'year':2026,'month':10,'collection_date':'2026-10-30',
        'message_id':'SYNTHETIC-OCT-' + uuid.uuid4().hex[:12],
        'pre_notification_on':'2026-10-15','notice_period_days':14,
        'shorter_period_agreed':False,'pre_notification_confirmed':True,
        'bank_profile_confirmed':True, **changes}


async def configure(api, w):
    response = await api.put(account_url(w)+'/creditor', json=creditor())
    assert response.status_code == 200, response.text
    response = await api.post(account_url(w)+'/mandates', json=mandate(w))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_sepa_collects_rent_and_both_effective_advances(sepa_world):
    w = sepa_world
    async with api_client(w) as api:
        await configure(api, w)
        changed = await api.post(f"/api/leases/{w['lease']}/rent-changes", json={
            'valid_from': '2026-10-01', 'base_rent': '750.00', 'advance_payment': '150.00',
            'heating_advance_payment': '90.00'})
        assert changed.status_code == 201, changed.text
        response = await api.post('/api/payments/sepa/preview', json=export_body(w))
        assert response.status_code == 200, response.text
        assert response.json()['total'] == response.json()['rows'][0]['amount'] == '990.00'


@pytest.mark.asyncio
async def test_sepa_october_persisted_mandate_real_xsds_and_no_booking(sepa_world, rent_db):
    w = sepa_world
    async with api_client(w) as api:
        m = await configure(api, w)
    async with api_client(w) as api:
        stored = await api.get(account_url(w))
        assert stored.status_code == 200, stored.text
        assert stored.json()['creditor']['creditor_id'] == CREDITOR
        assert [row['id'] for row in stored.json()['mandates']] == [m['id']]
        before = await rent_db.scalar(text('SELECT count(*) FROM payment WHERE organization_id=:org'), w)
        body = export_body(w)
        preview = await api.post('/api/payments/sepa/preview', json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()['total'] == '850.00'
        assert preview.json()['rows'][0]['amount'] == '850.00'
        xml = await api.post('/api/payments/sepa.xml', json={**body,'expected_source_digest':preview.json()['source_digest']})
        assert xml.status_code == 200, xml.text
        assert xml.headers['cache-control'] == 'private, no-store'
        assert 'attachment' in xml.headers['content-disposition']
        assert 'xml' in xml.headers['content-type']
        root = etree.fromstring(xml.content)
        for filename in ['pain.008.001.08.xsd','EPC130-08_2025_V1.0_pain.008.001.08.xsd']:
            etree.XMLSchema(etree.parse(str(SCHEMAS/filename))).assertValid(root)
        assert root.xpath('string(//p:GrpHdr/p:NbOfTxs)', namespaces=NS) == '1'
        assert root.xpath('string(//p:GrpHdr/p:CtrlSum)', namespaces=NS) == '850.00'
        assert root.xpath('string(//p:InstdAmt)', namespaces=NS) == '850.00'
        assert root.xpath('string(//p:SeqTp)', namespaces=NS) == 'FRST'
        assert root.xpath('string(//p:MndtId)', namespaces=NS) == 'SYNTHETIC-2026-01'
        assert root.xpath('string(//p:DtOfSgntr)', namespaces=NS) == '2026-01-01'
        assert root.xpath('string(//p:ReqdColltnDt)', namespaces=NS) == '2026-10-30'
        again = await api.post('/api/payments/sepa.xml', json={**body,'expected_source_digest':preview.json()['source_digest']})
        assert again.status_code == 200
        assert etree.fromstring(again.content).xpath('//p:EndToEndId/text()', namespaces=NS) == root.xpath('//p:EndToEndId/text()', namespaces=NS)
        assert await rent_db.scalar(text('SELECT count(*) FROM payment WHERE organization_id=:org'), w) == before
        after = await api.get(account_url(w))
        assert after.json() == stored.json(), 'Download is not bank execution and never advances mandate state'


@pytest.mark.asyncio
async def test_sepa_uses_effective_rent_history_and_stale_preview(sepa_world):
    w = sepa_world
    async with api_client(w) as api:
        await configure(api,w)
        body = export_body(w)
        preview = await api.post('/api/payments/sepa/preview',json=body)
        assert preview.status_code == 200, preview.text
        await post_change(api,w,valid_from='2026-10-16',base_rent='760.00',advance_payment='150.00')
        stale = await api.post('/api/payments/sepa.xml',json={**body,'expected_source_digest':preview.json()['source_digest']})
        assert stale.status_code == 409, stale.text
        changed = await api.post('/api/payments/sepa/preview',json=body)
        assert changed.status_code == 200, changed.text
        assert changed.json()['total'] == '880.97', '700×15/31 + 760×16/31 + 150; existing engine rounds each component'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'pre_notification_confirmed':False}, {'bank_profile_confirmed':False},
    {'notice_period_days':1,'shorter_period_agreed':False},
    {'pre_notification_on':'2026-10-29'}, {'month':13}, {'message_id':'bad//identifier'},
])
async def test_sepa_rejects_unconfirmed_or_invalid_export(sepa_world, change):
    async with api_client(sepa_world) as api:
        await configure(api,sepa_world)
        response = await api.post('/api/payments/sepa/preview',json=export_body(sepa_world,**change))
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_sepa_missing_mandate_and_reader_foreign_boundaries(sepa_world):
    w=sepa_world
    async with api_client(w) as api:
        assert (await api.post('/api/payments/sepa/preview',json=export_body(w))).status_code == 422
        await configure(api,w)
    async with api_client(w,'reader') as reader:
        assert (await reader.put(account_url(w)+'/creditor',json=creditor())).status_code == 403
        assert (await reader.post(account_url(w)+'/mandates',json=mandate(w))).status_code == 403
    async with api_client(w,foreign=True) as foreign:
        assert (await foreign.get(account_url(w))).status_code == 404
        assert (await foreign.post('/api/payments/sepa/preview',json=export_body(w))).status_code == 404


@pytest.mark.asyncio
async def test_sepa_reader_can_export_without_mutation(sepa_world):
    w=sepa_world
    async with api_client(w) as admin:
        await configure(admin,w)
    async with api_client(w,'reader') as reader:
        account=await reader.get(account_url(w))
        assert account.status_code==200,account.text
        body=export_body(w)
        preview=await reader.post('/api/payments/sepa/preview',json=body)
        assert preview.status_code==200,preview.text
        result=await reader.post('/api/payments/sepa.xml',json={**body,'expected_source_digest':preview.json()['source_digest']})
        assert result.status_code==200,result.text
        assert (await reader.get(account_url(w))).json()==account.json()


@pytest.mark.asyncio
async def test_sepa_partial_october_uses_existing_component_rounding(sepa_world):
    w=sepa_world
    async with api_client(w) as api:
        response=await api.patch(f"/api/leases/{w['lease']}",json={'valid_to':'2026-10-16'})
        assert response.status_code==200,response.text
        assert response.json()['valid_to']=='2026-10-16'
        await configure(api,w)
        preview=await api.post('/api/payments/sepa/preview',json=export_body(w))
        assert preview.status_code==200,preview.text
        assert preview.json()['total']=='438.71', '700×16/31=361.29 plus 150×16/31=77.42'


@pytest.mark.asyncio
@pytest.mark.parametrize('update,expected',[
    ("first_name='Changed signed identity'",422),
    ("iban='GB82WEST12345698765432'",422),
    ("processing_restricted=true",409),
])
async def test_sepa_identity_and_processing_changes_block_export(sepa_world,rent_db,update,expected):
    w=sepa_world
    async with api_client(w) as api:
        await configure(api,w)
        body=export_body(w)
        preview=await api.post('/api/payments/sepa/preview',json=body)
        assert preview.status_code==200,preview.text
        await rent_db.execute(text(f'UPDATE person SET {update} WHERE id=:person'),w)
        await rent_db.commit()
        response=await api.post('/api/payments/sepa.xml',json={**body,'expected_source_digest':preview.json()['source_digest']})
        assert response.status_code==expected,response.text
        assert not response.content.startswith(b'<?xml')


@pytest.mark.asyncio
async def test_sepa_usage_is_explicit_and_revocation_persists(sepa_world):
    w=sepa_world
    async with api_client(w) as api:
        m=await configure(api,w)
        duplicate=await api.post(account_url(w)+'/mandates',json=mandate(w))
        assert duplicate.status_code==409,duplicate.text
        usage=await api.put(f"/api/payments/sepa/mandates/{m['id']}",json={'sequence_type':'RCUR','last_used_on':'2026-09-15'})
        assert usage.status_code==200,usage.text
        body=export_body(w)
        preview=await api.post('/api/payments/sepa/preview',json=body)
        assert preview.status_code==200,preview.text
        xml=await api.post('/api/payments/sepa.xml',json={**body,'expected_source_digest':preview.json()['source_digest']})
        assert xml.status_code==200,xml.text
        assert etree.fromstring(xml.content).xpath('string(//p:SeqTp)',namespaces=NS)=='RCUR'
        account=(await api.get(account_url(w))).json()
        assert account['mandates'][0]['last_used_on']=='2026-09-15'
        backwards=await api.put(f"/api/payments/sepa/mandates/{m['id']}",json={'sequence_type':'FRST','last_used_on':None})
        assert backwards.status_code==409,backwards.text
        assert (await api.post(f"/api/payments/sepa/mandates/{m['id']}/revoke")).status_code==204
        assert (await api.post('/api/payments/sepa/preview',json=body)).status_code==422
    async with api_client(w) as api:
        account=(await api.get(account_url(w))).json()
        assert account['mandates'][0]['revoked_at'] is not None


@pytest.mark.asyncio
async def test_sepa_dormant_mandate_requires_new_consent(sepa_world):
    w=sepa_world
    async with api_client(w) as api:
        assert (await api.put(account_url(w)+'/creditor',json=creditor())).status_code==200
        response=await api.post(account_url(w)+'/mandates',json=mandate(w,signed_on='2020-01-01'))
        assert response.status_code==201,response.text
        preview=await api.post('/api/payments/sepa/preview',json=export_body(w))
        assert preview.status_code==422,preview.text
        assert '36 months' in preview.text


@pytest.mark.asyncio
async def test_sepa_database_guards_identity_without_api(sepa_world,rent_db):
    from sqlalchemy.exc import DBAPIError
    w=sepa_world
    async with api_client(w) as api:
        m=await configure(api,w)
    with pytest.raises(DBAPIError,match='cannot be rewritten'):
        async with rent_db.begin_nested():
            await rent_db.execute(text("UPDATE sepa_mandate SET reference='REWRITTEN' WHERE id=:id"),{'id':m['id']})
    with pytest.raises(DBAPIError,match='Revoke active mandates'):
        async with rent_db.begin_nested():
            await rent_db.execute(text("UPDATE sepa_creditor SET name='Changed creditor' WHERE bank_account_id=:bank"),w)
    await rent_db.rollback()
    async with api_client(w) as api:
        account=(await api.get(account_url(w))).json()
        assert account['mandates'][0]['reference']=='SYNTHETIC-2026-01'
        assert account['creditor']['name']=='Synthetic Vermietung'


@pytest.mark.asyncio
async def test_sepa_parallel_previews_and_post_render_export_recheck(sepa_world,monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.api import sepa
    w=sepa_world
    async with api_client(w) as api:
        await configure(api,w)
        body=export_body(w)
        previews=await asyncio.gather(*(api.post('/api/payments/sepa/preview',json=body) for _ in range(2)))
        assert all(r.status_code==200 for r in previews),[r.text for r in previews]
        assert previews[0].json()==previews[1].json()
        rendered=False
        denied=False
        original_render,original_scalar=sepa.render_sepa,AsyncSession.scalar
        def render(*args,**kwargs):
            nonlocal rendered
            result=original_render(*args,**kwargs)
            rendered=True
            return result
        async def scalar(self,statement,*args,**kwargs):
            nonlocal denied
            if rendered and 'property_financial_disclosure_access' in str(statement) and "'export'" in str(statement):
                denied=True
                return False
            return await original_scalar(self,statement,*args,**kwargs)
        monkeypatch.setattr(sepa,'render_sepa',render)
        monkeypatch.setattr(AsyncSession,'scalar',scalar)
        response=await api.post('/api/payments/sepa.xml',json={**body,'expected_source_digest':previews[0].json()['source_digest']})
        assert rendered and denied
        assert response.status_code==404,response.text
        assert not response.content.startswith(b'<?xml')


@pytest.mark.asyncio
async def test_sepa_migration_roundtrip_preserves_old_data_and_security(sepa_world):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template,issue_body
    w=sepa_world
    async with api_client(w) as api:
        await configure(api,w)
        original=(await api.get(account_url(w))).json()
        template=await create_template(api)
        issued=await api.post('/api/letters/issue',json=issue_body(w,template))
        assert issued.status_code==201,issued.text
        original_pdf=(await api.get(issued.json()[0]['pdf_url'])).content
    spec=importlib.util.spec_from_file_location('sepa_migration',Path(__file__).parents[1]/'migrations/versions/0133_sepa_mandates.py')
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction=connection.begin()
            def snapshot():
                tables=connection.execute(text("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname NOT IN ('sepa_creditor','sepa_mandate')
                    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
                    ORDER BY c.relname""")).scalars().all()
                rows={name:connection.execute(text(f'SELECT to_jsonb(t)::text FROM "{name}" t ORDER BY to_jsonb(t)::text')).scalars().all() for name in tables}
                security=[connection.execute(text(query)).all() for query in (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname NOT LIKE 'sepa_%' ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename NOT LIKE 'sepa_%' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname NOT LIKE 'sepa_%' ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND p.proname NOT LIKE 'sepa_%' ORDER BY p.proname,p.oid",
                )]
                return rows,security
            try:
                before=snapshot()
                assert before[0]['lease_agreement'] and before[0]['letter_document'], 'Preserve actual existing leases and issued originals'
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(DBAPIError,match='Cannot remove populated SEPA'):
                        with connection.begin_nested():
                            migration.downgrade()
                    assert snapshot()==before
                    connection.execute(text('TRUNCATE sepa_mandate,sepa_creditor'))
                    migration.downgrade()
                    assert connection.scalar(text("SELECT to_regclass('public.sepa_mandate')")) is None
                    assert snapshot()==before
                    migration.upgrade()
                    assert snapshot()==before
                    assert connection.scalar(text("SELECT count(*) FROM pg_policies WHERE tablename IN ('sepa_creditor','sepa_mandate')"))==6
                    assert connection.scalar(text("SELECT count(*) FROM pg_class WHERE relname IN ('sepa_creditor','sepa_mandate') AND relrowsecurity AND relforcerowsecurity"))==2
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(w) as api:
        assert (await api.get(account_url(w))).json()==original
        assert (await api.get(issued.json()[0]['pdf_url'])).content==original_pdf


@pytest.mark.asyncio
async def test_sepa_preview_waits_for_financial_input_lock(sepa_world, rent_db):
    w = sepa_world
    async with api_client(w) as api:
        await configure(api, w)
        await rent_db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                              {'key': f"financial-inputs:{w['org']}"})
        blocker = await rent_db.scalar(text('SELECT pg_backend_pid()'))
        request = asyncio.create_task(api.post('/api/payments/sepa/preview', json=export_body(w)))
        waiting = False
        try:
            for _ in range(200):
                waiting = await rent_db.scalar(text("""SELECT EXISTS (
                    SELECT 1 FROM pg_locks l WHERE l.locktype='advisory'
                    AND NOT l.granted AND :blocker = ANY(pg_blocking_pids(l.pid)))"""),
                    {'blocker': blocker})
                if waiting or request.done():
                    break
                await asyncio.sleep(0.01)
        finally:
            await rent_db.rollback()
        response = await asyncio.wait_for(request, timeout=15)
        assert waiting, 'SEPA must use the shared financial input boundary'
        assert response.status_code == 200, response.text
        assert response.json()['total'] == '850.00'


@pytest.mark.asyncio
async def test_sepa_parallel_registration_keeps_one_active_mandate(sepa_world):
    w = sepa_world
    async with api_client(w) as first, api_client(w) as second:
        response = await first.put(account_url(w) + '/creditor', json=creditor())
        assert response.status_code == 200, response.text
        responses = await asyncio.gather(
            first.post(account_url(w) + '/mandates', json=mandate(w)),
            second.post(account_url(w) + '/mandates', json=mandate(w)),
        )
        assert sorted(response.status_code for response in responses) == [201, 409]
        saved = await first.get(account_url(w))
        assert saved.status_code == 200, saved.text
        assert len(saved.json()['mandates']) == 1
        assert saved.json()['mandates'][0]['revoked_at'] is None
