"""Upgrade/backup checks using only the guarded synthetic database and files."""
import io
import subprocess
import tarfile
import uuid
from datetime import date
from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import deployment
from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings
from tests.test_rent_changes import rent_db, rent_world  # noqa: F401


@pytest.mark.asyncio
async def test_advance_mode_downgrade_refuses_to_erase_dated_meaning(rent_world):
    from tests.test_rent_changes import api_client

    async with api_client(rent_world) as api:
        created = await api.post(f"/api/leases/{rent_world['lease']}/rent-changes", json={
            "valid_from": "2026-07-01", "base_rent": "750.00", "advance_payment": "240.00",
            "advance_mode": "COMBINED",
        })
        assert created.status_code == 201, created.text
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            with pytest.raises(DBAPIError, match='Retain explicit tenant advance modes'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    ScriptDirectory('migrations').get_revision('0175').module.downgrade()
            assert connection.scalar(text("SELECT advance_mode FROM lease_rent_change WHERE id=:id"),
                {"id": created.json()["id"]}) == "COMBINED"
    finally:
        engine.dispose()


def test_eligible_reserve_erasure_is_the_next_release_revision():
    revision = ScriptDirectory('migrations').get_revision('0161')
    assert revision.down_revision == '0160'
    assert deployment.TARGET == '0176'


@pytest.mark.asyncio
async def test_bank_cost_downgrade_preserves_populated_assignments(rent_world):
    from tests.test_bank_transaction_assign import cost_assignment_graph
    from tests.test_rent_changes import api_client
    async with api_client(rent_world) as api:
        cost, _, tids = await cost_assignment_graph(api, rent_world['property'])
        successor = await api.post(f'/api/cost-entries/{cost}/correction', json={'gross_amount': '120.00'})
        assert successor.status_code == 201, successor.text
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            before = connection.scalar(text('SELECT to_jsonb(b) FROM bank_transaction b WHERE id=CAST(:id AS uuid)'), {'id': tids[0]})
            with pytest.raises(RuntimeError, match='Retain bank cost assignments'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    ScriptDirectory('migrations').get_revision('0171').module.downgrade()
            with pytest.raises(RuntimeError, match='Retain coverage across corrected costs'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    ScriptDirectory('migrations').get_revision('0173').module.downgrade()
                    ScriptDirectory('migrations').get_revision('0172').module.downgrade()
            assert connection.scalar(text('SELECT to_jsonb(b) FROM bank_transaction b WHERE id=CAST(:id AS uuid)'), {'id': tids[0]}) == before
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_commercial_preallocation_downgrade_preserves_saved_rules(client_a_admin):
    from tests.commercial_preallocation_support import commercial_property, commercial_rule
    data = await commercial_property(client_a_admin)
    response = await client_a_admin.post(f"/api/settlements/{data['rows'][0]['run']['id']}/compute",
        json={'commercial_preallocations': [commercial_rule(data)]})
    assert response.status_code == 200, response.text
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            with pytest.raises(RuntimeError, match='Retain commercial preallocation evidence'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    ScriptDirectory('migrations').get_revision('0169').module.downgrade()
            assert connection.scalar(text('SELECT count(*) FROM commercial_preallocation WHERE cost_entry_id=:cost'),
                {'cost': uuid.UUID(data['cost']['id'])}) == 1
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_statement_note_downgrade_refuses_complete_long_explanation(client_a_admin):
    from tests.commercial_preallocation_support import commercial_property, commercial_rule
    data = await commercial_property(client_a_admin)
    reason = 'Gastronomie <Sonderlast> & ' + 'ü' * 1973
    assert len(reason) == 2000
    response = await client_a_admin.post(f"/api/settlements/{data['rows'][0]['run']['id']}/finalize",
        json={'commercial_preallocations': [commercial_rule(data, commercial_reason=reason)]})
    assert response.status_code == 200, response.text
    line = response.json()['lines'][0]
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            before = connection.execute(text('SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=:id'),
                {'id': uuid.UUID(response.json()['id'])}).one()
            with pytest.raises(RuntimeError, match='Cannot narrow stored settlement notes; repair forward'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    ScriptDirectory('migrations').get_revision('0170').module.downgrade()
            assert connection.scalar(text('SELECT note FROM settlement_line WHERE id=:id'),
                {'id': uuid.UUID(line['id'])}) == line['note']
            assert reason in line['note']
            assert connection.scalar(text("""SELECT data_type FROM information_schema.columns
                WHERE table_schema='public' AND table_name='settlement_line' AND column_name='note'""")) == 'text'
            assert connection.execute(text('SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=:id'),
                {'id': uuid.UUID(response.json()['id'])}).one() == before
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_heating_split_downgrade_refuses_finalized_zero_heating_snapshot(rent_world):
    import json
    from datetime import date
    from decimal import Decimal
    from sqlalchemy.exc import DBAPIError
    from app.engine.canonical import canonical_sha256
    from app.engine.settlement import compute_bk_settlement
    from app.engine.snapshot import build_snapshot_payload
    from app.engine.types import LeasePeriodInput, PropertyInput, SettlementInput, UnitInput

    w = rent_world
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    inp = SettlementInput(start, end, PropertyInput(w['property'], 'Zero heating fixture', 'MIET'), w['lease'],
        (UnitInput(w['unit'], '1', 'WOHNUNG', area=Decimal(100)),),
        (LeasePeriodInput(w['lease'], w['unit'], w['tenant'], start, None),), (), (), Decimal('0.00'),
        engine_version='m3.2.0', heating_advance_payments_total=Decimal('0.00'))
    payload = build_snapshot_payload(inp, compute_bk_settlement(inp))
    assert payload['inputs']['heating_advance_payments_total'] == '0.00'
    assert payload['result']['heating_advance_payments_total'] == '0.00'
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text("""INSERT INTO settlement_run(organization_id,property_id,lease_agreement_id,
                run_type,period_start,period_end,advance_payments_total,advance_payments_source,
                heating_advance_payments_total,status,engine_version,snapshot_payload,snapshot_sha256,
                total_cost,balance,finalized_at,finalized_by)
                VALUES (:org,:property,:lease,'BK_MIETER',:start,:end,0,'MANUAL',0,'finalized','m3.2.0',
                    CAST(:payload AS jsonb),:sha,0,0,now(),:admin)"""),
                {**w, 'start': start, 'end': end, 'payload': json.dumps(payload), 'sha': canonical_sha256(payload)})
            connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            with pytest.raises(DBAPIError, match='Retain finalized split-engine snapshots; repair forward'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    ScriptDirectory('migrations').get_revision('0150').module.downgrade()
            transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('populated', ['lease', 'history', 'payment', 'statement'])
async def test_heating_split_downgrade_refuses_populated_heating_data(rent_world, populated):
    from sqlalchemy.exc import DBAPIError
    from tests.test_rent_changes import api_client
    w = rent_world
    async with api_client(w) as api:
        response = await api.post('/api/bank-accounts', json={'property_id': str(w['property']),
            'account_type': 'LAUFEND', 'label': 'Heating downgrade fixture'})
        assert response.status_code == 201, response.text
        bank = uuid.UUID(response.json()['id'])
    statements = {
        'lease': 'UPDATE lease_agreement SET heating_advance_payment=90 WHERE id=:lease',
        'history': """INSERT INTO lease_rent_change(organization_id,lease_agreement_id,valid_from,
            base_rent,advance_payment,heating_advance_payment) VALUES (:org,:lease,'2026-07-01',750,150,90)""",
        'payment': """INSERT INTO payment(organization_id,bank_account_id,property_id,direction,amount,
            value_date,payment_type,tenant_profile_id,lease_agreement_id,status)
            VALUES (:org,:bank,:property,'IN',90,'2026-01-01','HK_VORAUSZAHLUNG',:tenant,:lease,'draft')""",
        'statement': """INSERT INTO settlement_run(organization_id,property_id,lease_agreement_id,run_type,
            period_start,period_end,advance_payments_source,heating_advance_payments_total,status,engine_version)
            VALUES (:org,:property,:lease,'BK_MIETER','2026-01-01','2026-12-31','MANUAL',90,'draft','m3.2.0')""",
    }
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(statements[populated]), {**w, 'bank': bank})
                with pytest.raises(DBAPIError, match='Retain heating advances'):
                    with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                        ScriptDirectory('migrations').get_revision('0150').module.downgrade()
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize('heads', [(), ('0028', '0122'), ('0122', '0139', '0140', '0141', '0142', '0143', '0144'), ('0138',), ('garbage',)])
def test_reject_unknown_or_ambiguous_database_version(heads):
    with pytest.raises(ValueError, match='schema'):
        deployment.supported_schema(heads)


def test_release_graph_is_exact_and_linear(tmp_path):
    graph = ScriptDirectory('migrations')
    assert deployment.release_head(graph) == '0176'
    (tmp_path / 'versions').mkdir()
    (tmp_path / 'versions' / 'unexpected.py').write_text(
        "revision='0156'\ndown_revision=None\nbranch_labels=None\ndepends_on=None\n")
    with pytest.raises(ValueError, match='release'):
        deployment.release_head(ScriptDirectory(str(tmp_path)))


@pytest.mark.parametrize('schema', ['0028', '0122', '0139', '0140', '0141', '0142', '0143', '0144', '0145', '0146', '0147', '0148', '0149', '0150', '0151', '0152', '0153', '0154', '0155', '0159', '0160', '0161', '0162', '0163', '0164', '0165', '0166', '0167', '0168', '0169', '0170', '0171', '0172', '0173', '0174', '0175', '0176'])  # accepted sources plus target
def test_measured_release_schema_is_a_supported_upgrade_source(schema):
    assert deployment.supported_schema((schema,)) == schema


@pytest.mark.parametrize('statement', [
    'ALTER ROLE app_rw BYPASSRLS',
    'ALTER ROLE app_worker NOLOGIN',
    'ALTER ROLE app_worker NOINHERIT',
    'ALTER ROLE app_review INHERIT',
    'GRANT app_worker TO app_review',
    'GRANT app_rw TO app_worker WITH ADMIN OPTION',
    'GRANT app_migrator TO app_rw',
    'GRANT CREATE ON SCHEMA public TO app_rw',
    'GRANT SELECT ON organization TO app_review',
])
def test_preflight_rejects_unsafe_roles_without_repairing_them(statement):
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            deployment.validate_roles(connection)
            connection.execute(text(statement))
            with pytest.raises(ValueError, match='role'):
                deployment.validate_roles(connection)
            transaction.rollback()
    finally:
        engine.dispose()


def seed_pre_split_upgrade(connection, org, actor, *, household=False, notes=False):
    """Create an actual 0149 lease/history and finalized legacy snapshot."""
    import json
    from app.engine.canonical import canonical_sha256
    ids = {key: str(uuid.uuid4()) for key in ('property', 'unit', 'person', 'tenant', 'lease', 'run', 'coverage')}
    values = {**ids, 'org': org, 'actor': actor}
    connection.execute(text("""INSERT INTO property(id,organization_id,name,management_type)
        VALUES (:property,:org,'Pre-split upgrade fixture','MIET');
        INSERT INTO unit(id,organization_id,property_id,number,usage_type,area)
        VALUES (:unit,:org,:property,'1','WOHNUNG',70);
        INSERT INTO person(id,organization_id,last_name) VALUES (:person,:org,'Pre-split tenant');
        INSERT INTO tenant_profile(id,organization_id,person_id) VALUES (:tenant,:org,:person);
        INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment)
        VALUES (:lease,:org,:unit,:tenant,'2026-01-01',750,150);
        INSERT INTO lease_rent_change(organization_id,lease_agreement_id,valid_from,base_rent,advance_payment)
        VALUES (:org,:lease,'2026-07-01',750,160)"""), values)
    payload = {'engine_version': 'm3.1.0', 'run_type': 'BK_MIETER',
        'period': {'start': '2026-01-01', 'end': '2026-12-31'},
        'inputs': {'advance_payments_total': '1800.00', 'advance_payments_source': 'MANUAL'},
        'result': {'total_cost': '2900.00', 'advance_payments_total': '1800.00', 'balance': '1100.00'},
        'financial_input_coverage': {'id': ids['coverage'], 'source_revision': '0' * 64}}
    if household:
        payload['inputs']['units'] = [{'id': ids['unit'], 'person_count': 2}]
        payload['extra_inputs'] = {'unit_metrics': [{'unit_id': ids['unit'], 'person_count': 2}]}
    if notes:
        boundary_note = ('Ältere <Begründung> & ' + 'vollständig ' * 50)[:500]
        assert len(boundary_note) == 500
        payload['result']['lines'] = [dict(line_type='COST', label='Legacy note fixture',
            distribution_key='WOHNFLAECHE', total_amount='2900.00', distribution_base_total='70',
            distribution_base_share='70', time_share_days=365, time_share_total_days=365,
            amount=amount, sort_order=index, note=note)
            for index, (amount, note) in enumerate((('2700.00', boundary_note), ('100.00', None), ('100.00', '')))]
    connection.execute(text("""INSERT INTO settlement_run(id,organization_id,property_id,lease_agreement_id,
        run_type,period_start,period_end,advance_payments_total,advance_payments_source,total_cost,balance,
        status,snapshot_payload,snapshot_sha256,engine_version,finalized_at,finalized_by)
        VALUES (:run,:org,:property,:lease,'BK_MIETER','2026-01-01','2026-12-31',1800,'MANUAL',2900,1100,
            'finalized',CAST(:payload AS jsonb),:sha,'m3.1.0',now(),:actor);
        INSERT INTO coverage_declaration(id,organization_id,settlement_run_id,property_id,period_start,period_end,
            component,revision,state,values,source_document_ids,source_revision,reason,attestor_id)
        VALUES (:coverage,:org,:run,:property,'2026-01-01','2026-12-31','BK_MIETER',1,'known',CAST(:inputs AS jsonb),
            '[]',:revision,'Pre-split upgrade fixture',:actor)"""), {**values,
        'payload': json.dumps(payload), 'sha': canonical_sha256(payload), 'inputs': json.dumps(payload['inputs']), 'revision': '0' * 64})
    if notes:
        connection.execute(text("""INSERT INTO settlement_line(organization_id,settlement_run_id,line_type,label,
            distribution_key,total_amount,distribution_base_total,distribution_base_share,time_share_days,
            time_share_total_days,amount,sort_order,note)
            VALUES (:org,:run,:line_type,:label,:distribution_key,:total_amount,:distribution_base_total,
                :distribution_base_share,:time_share_days,:time_share_total_days,:amount,:sort_order,:note)"""),
            [{**values, **line} for line in payload['result']['lines']])
    # Existing installation rows are committed before a release. Drain our
    # rollback-only fixture's deferred events before rehearsing its ALTERs.
    connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    connection.execute(text('SET CONSTRAINTS ALL DEFERRED'))
    parent = connection.scalar(text("SELECT financial_computation_state(:org,'settlement_run',:run)->>'parent_digest'"), values)
    return values, parent



def legacy_household_render(connection, values):
    from types import SimpleNamespace
    from decimal import Decimal
    from app.engine.canonical import canonical_json, canonical_sha256
    from app.engine.types import ResultLine
    from app.services.settlement_pdf import build_settlement_html
    row = connection.execute(text('SELECT * FROM settlement_run WHERE id=:run'), values).mappings().one()
    run = SimpleNamespace(**row)
    assert canonical_sha256(run.snapshot_payload) == run.snapshot_sha256
    assert run.snapshot_payload['inputs']['units'][0]['person_count'] == 2
    line = ResultLine('COST', 'Legacy Personenzahl', Decimal('2900.00'), 0,
        distribution_key='PERSONENZAHL', total_amount=Decimal('2900.00'),
        distribution_base_share=Decimal(2), distribution_base_total=Decimal(2),
        time_share_days=365, time_share_total_days=365)
    return canonical_json(run.snapshot_payload), run.snapshot_sha256, build_settlement_html(run, [line])


def legacy_note_render(connection, values):
    from types import SimpleNamespace
    from app.engine.canonical import canonical_json, canonical_sha256
    from app.services.settlement_pdf import build_settlement_html, render_settlement_pdf
    row = connection.execute(text('SELECT * FROM settlement_run WHERE id=:run'), values).mappings().one()
    lines = connection.execute(text('SELECT * FROM settlement_line WHERE settlement_run_id=:run ORDER BY sort_order'),
        values).mappings().all()
    run = SimpleNamespace(**row)
    assert canonical_sha256(run.snapshot_payload) == run.snapshot_sha256
    assert [line['note'] for line in lines] == [line['note'] for line in run.snapshot_payload['result']['lines']]
    rendered_lines = [SimpleNamespace(**line) for line in lines]
    return (lines, canonical_json(run.snapshot_payload), run.snapshot_sha256,
        build_settlement_html(run, rendered_lines), render_settlement_pdf(run, rendered_lines))


def rehearse_initial_upgrade(starting_schema='0122'):
    """Rehearse before the shared fixture reaches head or creates retained data."""
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    graph = ScriptDirectory('migrations')
    revisions = list(graph.iterate_revisions('0176', starting_schema))
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                assert MigrationContext.configure(connection).get_current_heads() == (starting_schema,)
                org = str(uuid.uuid4())
                connection.execute(text('INSERT INTO organization(id,name) VALUES (:id,:name)'),
                                   {'id': org, 'name': 'Synthetic upgrade organization'})
                actor = str(uuid.uuid4())
                values = {'id': actor, 'org': org, 'email': actor + '@upgrade.invalid'}
                connection.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name) '
                                        "VALUES (:id,:org,:email,'synthetic-not-a-login','Upgrade actor')"), values)
                connection.execute(text("INSERT INTO membership(id,organization_id,user_id,role) "
                                        "VALUES (gen_random_uuid(),:org,:id,'OWNER_ADMIN')"), values)
                connection.execute(text("INSERT INTO management_mandate(id,organization_id,mandate_type,label,start_date) "
                                        "VALUES (gen_random_uuid(),:org,'MIET','Synthetic mandate','2025-01-01')"), values)
                connection.execute(text("INSERT INTO audit_event(id,organization_id,actor_user_id,entity_type,entity_id,action) "
                                        "VALUES (gen_random_uuid(),:org,:id,'organization',:org,'org.created')"), values)
                if starting_schema == '0028':
                    document = str(uuid.uuid4())
                    draft = str(uuid.uuid4())
                    failed = str(uuid.uuid4())
                    connection.execute(text("""INSERT INTO property_document
                        (id,organization_id,doc_type,original_filename,mime_type,content_byte_size,
                         content_sha256,storage_uri,storage_backend,page_count,uploaded_by)
                        VALUES (:doc,:org,'DECLARATION','synthetic.pdf','application/pdf',32,
                                :digest,:storage,'LOCAL_FS',1,:actor)"""),
                        {'doc': document, 'org': org, 'digest': '0' * 64,
                         'storage': 'synthetic/upgrade.pdf', 'actor': actor})
                    connection.execute(text("""INSERT INTO declaration_import
                        (id,organization_id,mode,status,initiated_by,max_cost_usd)
                        VALUES (:draft,:org,'NEW_PROPERTY','DRAFT',:actor,1),
                               (:failed,:org,'NEW_PROPERTY','FAILED',:actor,1)"""),
                        {'draft': draft, 'failed': failed, 'org': org, 'actor': actor})
                    connection.execute(text("""INSERT INTO declaration_import_document
                        (organization_id,import_id,document_id,sequence_no,document_role)
                        VALUES (:org,:draft,:doc,1,'DECLARATION')"""),
                        {'org': org, 'draft': draft, 'doc': document})
                    connection.execute(text("""INSERT INTO declaration_extraction_run
                        (organization_id,import_id,status,provider,model_name,prompt_version,
                         schema_version,harness_version,error_code,completed_at)
                        VALUES (:org,:failed,'FAILED','synthetic','synthetic','v1','v1','v1',
                                'SYNTHETIC_FAILURE',now())"""),
                        {'org': org, 'failed': failed})
                if starting_schema == '0149':
                    legacy_split, legacy_parent = seed_pre_split_upgrade(connection, org, actor)
                if starting_schema in ('0163', '0164'):
                    legacy_household, _ = seed_pre_split_upgrade(connection, org, actor, household=True)
                    legacy_household_view = legacy_household_render(connection, legacy_household)
                if starting_schema == '0169':
                    legacy_notes, legacy_note_parent = seed_pre_split_upgrade(connection, org, actor, notes=True)
                    legacy_note_view = legacy_note_render(connection, legacy_notes)
                # Compare every old column of every pre-upgrade table, including
                # seeded users/memberships/mandates/audit, without normalizing data.
                tables = connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).all()
                columns = {table: connection.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table ORDER BY ordinal_position"), {'table': table}).all() for table in tables}
                quote = connection.dialect.identifier_preparer.quote
                def old_rows():
                    return {table: connection.scalars(text(
                        'SELECT row_to_json(r)::text FROM (SELECT ' + ','.join(quote(c) for c in columns[table])
                        + ' FROM ' + quote(table) + ') r ORDER BY row_to_json(r)::text')).all() for table in tables}
                before = old_rows()
                assert before['user'] and before['membership'] and before['audit_event']
                if starting_schema == '0028':
                    assert all(before[table] for table in (
                        'property_document', 'declaration_import',
                        'declaration_import_document', 'declaration_extraction_run'))
                security = connection.execute(text("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(:tables) ORDER BY c.relname"), {'tables': tables}).all()
                # Only organization INSERT is intentionally revoked by browser setup.
                with connection.begin_nested() as expected:
                    connection.execute(text("REVOKE INSERT ON public.organization FROM app_rw"))
                    if 'reserve_ledger_entry' in tables:
                        connection.execute(text('REVOKE UPDATE ON public.reserve_ledger_entry FROM app_rw'))
                    expected_security = connection.execute(text("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(:tables) ORDER BY c.relname"), {'tables': tables}).all()
                    expected.rollback()
                for revision in reversed(revisions):
                    if revision.revision == '0161' and starting_schema == '0160':
                        def reason_erasure_contract():
                            functions = connection.execute(text("""SELECT oid::regprocedure::text,pg_get_functiondef(oid),
                                proacl::text,proowner,proconfig,prosecdef FROM pg_proc
                                WHERE pronamespace='public'::regnamespace ORDER BY oid::regprocedure::text""")).all()
                            triggers = connection.execute(text("""SELECT c.relname,t.tgname,t.tgenabled::text,pg_get_triggerdef(t.oid)
                                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                                WHERE c.relnamespace='public'::regnamespace ORDER BY c.relname,t.tgname""")).all()
                            relations = connection.execute(text("""SELECT relname,relacl::text,relowner,relrowsecurity,relforcerowsecurity
                                FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY relname""")).all()
                            return {row[0]: tuple(row[1:]) for row in functions}, triggers, relations
                        previous_reason_erasure = reason_erasure_contract()
                    if revision.revision == '0160' and starting_schema == '0159':
                        deadline_signatures = ['person_retention_categories()',
                            'person_retention_bindings(uuid,uuid,text)', 'organization_retention_plan(uuid)']
                        def deadline_contract(signatures):
                            return [tuple(connection.execute(text("""SELECT pg_get_functiondef(oid),proacl::text,
                                proowner,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)"""),
                                {'signature': signature}).one()) for signature in signatures]
                        previous_deadlines = deadline_contract(deadline_signatures)
                    if revision.revision == '0159' and starting_schema == '0155':
                        retained_signatures = ['privacy_retention_facts(uuid,uuid)',
                            'privacy_financial_context_complete(uuid,uuid,text)', 'organization_retention_plan(uuid)']
                        def person_retention_contract(signatures):
                            return [tuple(connection.execute(text("""SELECT pg_get_functiondef(oid),proacl::text,
                                proowner,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)"""),
                                {'signature': signature}).one()) for signature in signatures]
                        previous_person_retention = person_retention_contract(retained_signatures)
                    if revision.revision == '0155':
                        def always_guard_contract():
                            triggers = {(row[0], row[1]): tuple(row[2:]) for row in connection.execute(text("""
                                SELECT c.relname,t.tgname,t.tgenabled::text,pg_get_triggerdef(t.oid)
                                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                                WHERE c.relnamespace='public'::regnamespace"""))}
                            relations = connection.execute(text("""SELECT relname,relowner,relacl::text,
                                relrowsecurity,relforcerowsecurity FROM pg_class
                                WHERE relnamespace='public'::regnamespace ORDER BY oid""")).all()
                            functions = connection.execute(text("""SELECT oid,prosrc,proacl::text,proowner,proconfig
                                FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY oid""")).all()
                            catalog = connection.execute(text('SELECT * FROM organization_retention_catalog() ORDER BY entity')).all()
                            return triggers, relations, functions, catalog
                        previous_always_contract = always_guard_contract()
                    if revision.revision == '0154':
                        def contribution_guard_contract():
                            return tuple(connection.execute(text("""SELECT pg_get_functiondef(oid),proacl::text,proowner
                                FROM pg_proc WHERE oid='public.guard_reserve_ledger()'::regprocedure""")).one())
                        previous_contribution_guard = contribution_guard_contract()
                    if revision.revision == '0153':
                        def bank_catalog_contract():
                            rows = {row[0]: tuple(row[1:]) for row in connection.execute(
                                text('SELECT * FROM organization_retention_catalog()'))}
                            function = tuple(connection.execute(text("""SELECT pg_get_functiondef(oid),proacl::text,proowner
                                FROM pg_proc WHERE oid='public.organization_retention_catalog()'::regprocedure""")).one())
                            return rows, function
                        previous_bank_catalog, previous_bank_function = bank_catalog_contract()
                        assert previous_bank_catalog['bank_transaction'] == (['id'], 'delete', 'false')
                    if revision.revision == '0149':
                        def retention_catalog():
                            return {row[0]: tuple(row[1:]) for row in connection.execute(
                                text('SELECT * FROM organization_retention_catalog()'))}
                        def retention_definition():
                            return tuple(connection.execute(text("""SELECT pg_get_functiondef(oid),proacl::text,
                                pg_get_userbyid(proowner) FROM pg_proc
                                WHERE oid='public.organization_retention_catalog()'::regprocedure""")).one())
                        original_catalog = retention_catalog()
                        original_catalog_definition = retention_definition()
                    if starting_schema == '0146' and revision.revision == '0147':
                        def trigger_definitions():
                            return connection.execute(text('''SELECT p.proname,pg_get_functiondef(p.oid),p.proacl::text,
                              pg_get_userbyid(p.proowner) FROM pg_proc p WHERE p.oid IN (
                                SELECT t.tgfoid FROM pg_trigger t WHERE NOT t.tgisinternal)
                                OR (p.pronamespace='public'::regnamespace AND p.proname='guard_audit_event_payload')
                              ORDER BY p.proname''')).all()
                        original_triggers = trigger_definitions()
                        # This guard is new in 0147, unlike the three preexisting
                        # payload guards. Capture its expected base definition
                        # with PostgreSQL's exact formatting/owner/ACL as well.
                        with connection.begin_nested() as guard_baseline:
                            connection.execute(text('''CREATE FUNCTION public.guard_audit_event_payload() RETURNS trigger
          LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='UPDATE' AND NEW.actor_user_id IS NULL
        AND (to_jsonb(NEW)-'actor_user_id')=(to_jsonb(OLD)-'actor_user_id') THEN
        RETURN NEW;
      END IF;
      IF TG_OP='DELETE' AND current_user='app_migrator' AND session_user='app_migrator' THEN
        RETURN OLD;
      END IF;
      RAISE EXCEPTION 'Audit events are immutable' USING ERRCODE='23514';
    END $$;'''))
                            connection.execute(text('REVOKE ALL ON FUNCTION public.guard_audit_event_payload() FROM PUBLIC,app_rw,app_worker,app_review'))
                            audit_guard = next(row for row in trigger_definitions() if row[0] == 'guard_audit_event_payload')
                            guard_baseline.rollback()
                    if revision.revision == '0150':
                        signatures = ['validate_accounting_binding_baseline(public.accounting_scope_binding)',
                            'computation_input_basis(uuid,text,uuid,boolean)', 'financial_computation_state(uuid,text,uuid)',
                            'trg_settlement_run_immutability()']
                        def split_definitions():
                            return [connection.scalar(text("SELECT pg_get_functiondef(CAST(:s AS regprocedure))"), {"s": signature}) for signature in signatures]
                        before_split = split_definitions()
                    if (starting_schema, revision.revision) == ('0171', '0172'):
                        guard_sql = text("SELECT pg_get_functiondef('guard_bank_cost_assignment()'::regprocedure)")
                        guard_before = connection.scalar(guard_sql)
                    if (starting_schema, revision.revision) == ('0172', '0173'):
                        def bank_evidence_contract():
                            return (
                                [connection.execute(text("""SELECT pg_get_functiondef(oid),proacl::text,
                                    proowner,proconfig,prosecdef FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)"""),
                                    {'signature': signature}).one() for signature, _ in revision.module.PATCHES],
                                connection.execute(text("""SELECT pg_get_triggerdef(oid),tgenabled
                                    FROM pg_trigger WHERE tgrelid='payment'::regclass AND tgname='guard_payment_cost_bank'""")).one(),
                            )
                        evidence_before = bank_evidence_contract()
                    revision.module.upgrade()
                    if (starting_schema, revision.revision) == ('0173', '0174'):
                        assert connection.scalar(text("SELECT count(*) FROM base_interest_rate")) == 50
                        assert connection.execute(text("""
                            SELECT valid_from, rate_percent FROM base_interest_rate
                            WHERE valid_from >= '2026-01-01' ORDER BY valid_from
                        """)).all() == [(date(2026, 1, 1), Decimal("1.27")), (date(2026, 7, 1), Decimal("1.52"))]
                        assert connection.scalar(text("SELECT count(*) FROM dunning_settings")) == 0
                        assert connection.scalar(text("""
                            SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
                            WHERE oid='dunning_settings'::regclass
                        """))
                        assert not connection.scalar(text("SELECT has_table_privilege('app_rw','base_interest_rate','INSERT,UPDATE,DELETE')"))
                        revision.module.downgrade()
                        assert connection.scalar(text("SELECT to_regclass('public.dunning_settings')")) is None
                        assert connection.scalar(text("SELECT to_regclass('public.base_interest_rate')")) is None
                        revision.module.upgrade()
                    if (starting_schema, revision.revision) == ('0172', '0173'):
                        evidence_after = bank_evidence_contract()
                        assert evidence_after != evidence_before
                        revision.module.downgrade()
                        assert bank_evidence_contract() == evidence_before
                        revision.module.upgrade()
                        assert bank_evidence_contract() == evidence_after
                    if (starting_schema, revision.revision) == ('0171', '0172'):
                        guard_after = connection.scalar(guard_sql)
                        revision.module.downgrade()
                        assert connection.scalar(guard_sql) == guard_before
                        assert connection.scalar(text("SELECT to_regprocedure('bank_cost_covered(uuid,uuid)')")) is None
                        revision.module.upgrade()
                        assert connection.scalar(guard_sql) == guard_after
                    if (starting_schema, revision.revision) == ('0169', '0170'):
                        note_type = text("""SELECT data_type,character_maximum_length FROM information_schema.columns
                            WHERE table_schema='public' AND table_name='settlement_line' AND column_name='note'""")
                        assert connection.execute(note_type).one() == ('text', None)
                        assert legacy_note_render(connection, legacy_notes) == legacy_note_view
                        revision.module.downgrade()
                        assert connection.execute(note_type).one() == ('character varying', 500)
                        assert legacy_note_render(connection, legacy_notes) == legacy_note_view
                        revision.module.upgrade()
                        assert connection.execute(note_type).one() == ('text', None)
                        assert legacy_note_render(connection, legacy_notes) == legacy_note_view
                        assert connection.scalar(text("SELECT financial_computation_state(:org,'settlement_run',:run)->>'parent_digest'"),
                            legacy_notes) == legacy_note_parent
                    if (starting_schema, revision.revision) in {('0163', '0164'), ('0164', '0165')}:
                        assert not connection.scalar(text('SELECT EXISTS(SELECT 1 FROM unit_household)'))
                        assert legacy_household_render(connection, legacy_household) == legacy_household_view
                    if revision.revision == '0161' and starting_schema == '0160':
                        upgraded_reason_erasure = reason_erasure_contract()
                        changed = {'apply_person_privacy(uuid,uuid,text)', 'guard_reserve_ledger()', 'guard_audit_event_payload()'}
                        added = {'reserve_erasure_name_pattern(text)', 'prepare_person_reserve_erasure(uuid)',
                                 'person_reserve_erasure_text(uuid,text)', 'erase_person_reserve_reasons()', 'lock_person_reserve_names()'}
                        assert upgraded_reason_erasure[0].keys() - previous_reason_erasure[0].keys() == added
                        assert {key: value for key, value in upgraded_reason_erasure[0].items() if key not in changed | added} == {
                            key: value for key, value in previous_reason_erasure[0].items() if key not in changed}
                        for key in changed:
                            assert upgraded_reason_erasure[0][key][1:] == previous_reason_erasure[0][key][1:]
                        assert [row for row in upgraded_reason_erasure[1] if row[1] not in {
                            'b_lock_person_reserve_names', 'z_erase_person_reserve_reasons'}] == previous_reason_erasure[1]
                        with connection.begin_nested() as reason_rehearsal:
                            revision.module.downgrade()
                            assert reason_erasure_contract() == previous_reason_erasure
                            revision.module.upgrade()
                            assert reason_erasure_contract() == upgraded_reason_erasure
                            reason_rehearsal.rollback()
                    if revision.revision == '0160' and starting_schema == '0159':
                        shared_deadline_signatures = deadline_signatures + ['retention_base_until(jsonb)',
                            'retention_source_until(text,jsonb)']
                        upgraded_deadlines = deadline_contract(shared_deadline_signatures)
                        with connection.begin_nested() as deadline_rehearsal:
                            revision.module.downgrade()
                            assert deadline_contract(deadline_signatures) == previous_deadlines
                            assert connection.scalar(text("SELECT to_regprocedure('retention_base_until(jsonb)')")) is None
                            revision.module.upgrade()
                            assert deadline_contract(shared_deadline_signatures) == upgraded_deadlines
                            deadline_rehearsal.rollback()
                    if revision.revision == '0159' and starting_schema == '0155':
                        shared_signatures = retained_signatures + ['person_retention_categories()',
                            'person_retention_bindings(uuid,uuid,text)', 'person_retention_view(uuid,uuid,text)']
                        upgraded_person_retention = person_retention_contract(shared_signatures)
                        with connection.begin_nested() as person_rehearsal:
                            revision.module.downgrade()
                            assert person_retention_contract(retained_signatures) == previous_person_retention
                            assert connection.scalar(text("SELECT to_regprocedure('person_retention_categories()')")) is None
                            revision.module.upgrade()
                            assert person_retention_contract(shared_signatures) == upgraded_person_retention
                            person_rehearsal.rollback()
                    if revision.revision == '0155':
                        upgraded_always_contract = always_guard_contract()
                        key = ('reserve_ledger_entry', 'guard_reserve_ledger')
                        assert previous_always_contract[0][key][0] == 'O'
                        expected_triggers = {**previous_always_contract[0],
                            key: ('A', previous_always_contract[0][key][1])}
                        assert upgraded_always_contract == (expected_triggers, *previous_always_contract[1:])
                        with connection.begin_nested() as always_rehearsal:
                            revision.module.downgrade()
                            assert always_guard_contract() == previous_always_contract
                            revision.module.upgrade()
                            assert always_guard_contract() == upgraded_always_contract
                            always_rehearsal.rollback()
                    if revision.revision == '0154':
                        upgraded_contribution_guard = contribution_guard_contract()
                        assert upgraded_contribution_guard[1:] == previous_contribution_guard[1:]
                        assert 'allowed:=LEAST(original.amount,' in upgraded_contribution_guard[0]
                        with connection.begin_nested() as contribution_rehearsal:
                            revision.module.downgrade()
                            assert contribution_guard_contract() == previous_contribution_guard
                            revision.module.upgrade()
                            assert contribution_guard_contract() == upgraded_contribution_guard
                            contribution_rehearsal.rollback()
                    if revision.revision == '0153':
                        expected_bank_catalog = {**previous_bank_catalog, 'bank_transaction': (['id'], 'conditional', 'false')}
                        upgraded_bank_catalog, upgraded_bank_function = bank_catalog_contract()
                        assert upgraded_bank_catalog == expected_bank_catalog
                        assert upgraded_bank_function[1:] == previous_bank_function[1:]
                        with connection.begin_nested() as bank_rehearsal:
                            revision.module.downgrade()
                            assert bank_catalog_contract() == (previous_bank_catalog, previous_bank_function)
                            revision.module.upgrade()
                            assert bank_catalog_contract() == (upgraded_bank_catalog, upgraded_bank_function)
                            bank_rehearsal.rollback()
                    if revision.revision == '0150':
                        assert connection.scalar(text("SELECT count(*) FROM lease_agreement WHERE heating_advance_payment<>0")) == 0
                        if starting_schema == '0149':
                            assert connection.scalar(text("SELECT heating_advance_payment FROM lease_agreement WHERE id=:lease"), legacy_split) == 0
                            assert connection.scalar(text("SELECT heating_advance_payment FROM lease_rent_change WHERE lease_agreement_id=:lease"), legacy_split) == 0
                            assert connection.scalar(text("SELECT heating_advance_payments_total FROM settlement_run WHERE id=:run"), legacy_split) == 0
                            assert connection.scalar(text("SELECT financial_computation_state(:org,'settlement_run',:run)->>'parent_digest'"), legacy_split) == legacy_parent
                        with connection.begin_nested() as split_rehearsal:
                            revision.module.downgrade()
                            assert split_definitions() == before_split
                            split_rehearsal.rollback()
                    if revision.revision == '0149':
                        upgraded_catalog = retention_catalog()
                        assert upgraded_catalog.pop('reserve_ledger_entry') == (['id'], 'retain', 'true')
                        assert upgraded_catalog == original_catalog
                        assert upgraded_catalog['payment'] == (['id'], 'conditional', "t.status='booked'")
                        definition, grants, owner = original_catalog_definition
                        assert retention_definition() == (definition.replace("'protect_dunning_quarantine'",
                            "'protect_dunning_quarantine','guard_reserve_payment_evidence'"), grants, owner)
                        # 0149 must restore the exact frozen 0147 definition,
                        # owner, grants and every catalog row before removal.
                        with connection.begin_nested() as catalog_rehearsal:
                            revision.module.downgrade()
                            assert retention_catalog() == original_catalog
                            assert retention_definition() == original_catalog_definition
                            catalog_rehearsal.rollback()
                    if starting_schema == '0146' and revision.revision == '0147':
                        assert connection.scalar(text('''SELECT count(*) FROM pg_trigger
                          WHERE tgrelid='public.audit_event'::regclass AND NOT tgisinternal
                          AND tgname='audit_event_immutable' AND (tgtype & 16)>0''')) == 1
                        with connection.begin_nested() as rehearsal:
                            revision.module.downgrade()
                            assert trigger_definitions() == original_triggers
                            assert connection.scalar(text("SELECT to_regprocedure('public.guard_audit_event_payload()')")) is None
                            rehearsal.rollback()
                if starting_schema == '0146':
                    # The upgrade must preserve every byte outside the narrow
                    # operator exemption, as well as owner and execute grants.
                    exemptions = {
                        'authorize_bound_financial_write': "TG_OP='DELETE'",
                        'authorize_dunning_write': "TG_OP='DELETE'",
                        'guard_financial_source_input': "TG_OP='DELETE'",
                        'guard_strict_financial_resource': "TG_OP='DELETE'",
                        'guard_knowledge_budget_policy': "TG_OP='UPDATE' AND (to_jsonb(NEW)-'reason')=(to_jsonb(OLD)-'reason')",
                        'guard_property_source_security': "TG_OP='UPDATE' AND (to_jsonb(NEW)-'reason')=(to_jsonb(OLD)-'reason')",
                    }
                    payload_guards = {'guard_audit_event_payload', 'protect_scope_evidence',
                                      'protect_master_import_batch', 'protect_dunning_batch'}
                    expected = {}
                    for name, definition, grants, owner in [*original_triggers, audit_guard]:
                        if name in exemptions:
                            clause = ("      IF current_user='app_migrator' AND session_user='app_migrator'\n"
                                      "        AND current_setting('app.organization_erasure',true)='on'\n"
                                      f"        AND {exemptions[name]} THEN\n"
                                      "        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;\n"
                                      "      END IF;\n")
                        elif name in payload_guards:
                            clause = """      IF current_user='app_migrator' AND session_user='app_migrator'
        AND current_setting('app.organization_erasure',true)='on'
        AND TG_TABLE_SCHEMA='public' AND TG_OP='UPDATE' THEN
        IF public.organization_erasure_payload_columns(TG_TABLE_NAME) IS NOT NULL
          AND (to_jsonb(NEW)-public.organization_erasure_payload_columns(TG_TABLE_NAME))
            =(to_jsonb(OLD)-public.organization_erasure_payload_columns(TG_TABLE_NAME))
          AND OLD.organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND EXISTS(SELECT 1 FROM public.organization_deletion d
            WHERE d.organization_id=OLD.organization_id AND d.status='cooling_off') THEN
          RETURN NEW;
        END IF;
      END IF;
"""
                        else:
                            continue
                        if name == 'guard_audit_event_payload':
                            clause = graph.get_revision('0161').module.AUDIT_EXCEPTION + clause
                        assert definition.count('    BEGIN\n') == 1, name
                        expected[name] = (definition.replace('    BEGIN\n', '    BEGIN\n' + clause, 1), grants, owner)
                    actual = {name: (definition, grants, owner) for name, definition, grants, owner in trigger_definitions()
                              if name in set(exemptions) | payload_guards}
                    assert set(expected) == set(exemptions) | payload_guards
                    assert actual == expected
                if starting_schema == '0142':
                    nullable = text("SELECT is_nullable FROM information_schema.columns "
                                    "WHERE table_name='letter_document' AND column_name='template_id'")
                    assert connection.scalar(nullable) == 'YES'
                    revision = graph.get_revision('0143').module
                    revision.downgrade()
                    assert connection.scalar(nullable) == 'NO'
                    revision.upgrade()
                    assert connection.scalar(nullable) == 'YES'
            assert old_rows() == before
            after_security = connection.execute(text("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(:tables) ORDER BY c.relname"), {'tables': tables}).all()
            if starting_schema in ('0122', '0139', '0140', '0141', '0142', '0143', '0144', '0145', '0146', '0147', '0148', '0149'):  # accepted sources
                assert after_security == expected_security
            else:
                before_by_name = {row.relname: row for row in security}
                assert all(
                    (not before_by_name[row.relname].relrowsecurity or row.relrowsecurity)
                    and (not before_by_name[row.relname].relforcerowsecurity or row.relforcerowsecurity)
                    for row in after_security
                )
            deployment.validate_roles(connection)
            assert connection.scalar(text("SELECT has_function_privilege('app_review',"
                "'app_validate_session_versioned(uuid,uuid,integer)','EXECUTE')"))
            assert not connection.scalar(text("SELECT has_function_privilege('app_review',"
                "'app_auth_lookup_versioned(text)','EXECUTE')"))
            transaction.rollback()
            assert MigrationContext.configure(connection).get_current_heads() == (starting_schema,)
            assert not connection.scalar(text('SELECT EXISTS(SELECT 1 FROM organization WHERE id=:id)'), {'id': org})
            return len(tables)
    finally:
        engine.dispose()


@pytest.mark.parametrize('starting_schema', ['0122', '0139', '0140', '0141', '0142', '0143', '0144', '0145', '0146', '0147', '0148', '0149', '0150', '0151', '0152', '0153', '0154', '0155', '0159', '0160', '0161', '0162', '0163', '0164', '0165', '0166', '0167', '0168', '0169', '0170', '0171', '0172', '0173', '0174', '0175'])  # accepted sources
def test_upgrade_to_0176_preserves_existing_rows_and_security(run_migrations, starting_schema):
    # The actual rehearsal ran before any retained domain-test originals existed.
    # Its SQL/data/security assertions fail fixture setup if preservation fails.
    assert run_migrations[f'upgrade_{starting_schema}_to_0176_tables'] > 50
    assert deployment.database_check('preflight') == '0176'


def test_upgrade_0028_to_0176_preserves_existing_rows_and_rls(run_migrations):
    assert run_migrations['upgrade_0028_to_0176_tables'] > 20


def test_document_archive_restore_is_exact_and_refuses_overwrite(tmp_path):
    source = tmp_path / 'source'
    (source / 'tenant' / 'ab').mkdir(parents=True)
    data = b'%PDF-1.4\nsynthetic original\x00\xff'
    (source / 'tenant' / 'ab' / 'original.bin').write_bytes(data)
    archive = io.BytesIO()
    deployment.archive_documents(source, archive)
    archive.seek(0)
    deployment.verify_documents(archive)
    archive.seek(0)
    destination = tmp_path / 'restored'
    deployment.restore_documents(archive, destination)
    assert (destination / 'tenant' / 'ab' / 'original.bin').read_bytes() == data
    archive.seek(0)
    with pytest.raises(ValueError, match='empty'):
        deployment.restore_documents(archive, destination)


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
                                      ('link', tarfile.SYMTYPE), ('hard', tarfile.LNKTYPE)])
def test_restore_rejects_unsafe_archive_before_writing(tmp_path, name, kind):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.linkname = 'outside'
        tar.addfile(item)
    archive.seek(0)
    with pytest.raises(ValueError, match='archive'):
        deployment.restore_documents(archive, tmp_path / 'restored')
    assert not (tmp_path / 'escape').exists()


def test_archive_refuses_symlinks(tmp_path):
    (tmp_path / 'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='document'):
        deployment.archive_documents(tmp_path, io.BytesIO())


@pytest.mark.parametrize('names', [('a/b', 'a//b'), ('a', './a'), ('x', 'x/y'), ('x/y', 'x')])
def test_verification_rejects_normalized_duplicates_and_file_directory_conflicts(names):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for name in names:
            tar.addfile(tarfile.TarInfo(name), io.BytesIO())
    archive.seek(0)
    with pytest.raises(ValueError, match='unsafe document archive'):
        deployment.verify_documents(archive)


def test_database_dump_restore_synthetic_schema_in_allowed_container(tmp_path):
    """Use the same pg_dump/custom-format/pg_restore commands as the host helper.

    Only this test's uniquely named schema is restored in infra-postgres-1.
    Never copy production, create a database or restore public/test fixture data.
    """
    _, _, url = require_disposable_database_settings(settings)
    from sqlalchemy.engine import make_url
    parsed = make_url(url)
    if parsed.host not in ('127.0.0.1', 'localhost') or parsed.port != 55432:
        pytest.skip('paired restore rehearsal requires the allowed local infra-postgres-1 container')
    engine = create_engine(url)
    schema = 'backup_test_' + uuid.uuid4().hex
    docker = ['docker', 'exec', '-i', 'infra-postgres-1']
    source = tmp_path / 'documents'
    source.mkdir()
    original = b'%PDF-1.4 synthetic retained original\x00\xff'
    (source / 'retained.bin').write_bytes(original)
    documents = io.BytesIO()
    deployment.archive_documents(source, documents)
    deployment.verify_documents(io.BytesIO(documents.getvalue()))
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
            connection.execute(text(f'CREATE TABLE {schema}.original(id integer PRIMARY KEY, payload bytea, storage_uri text)'))
            connection.execute(text(f"INSERT INTO {schema}.original VALUES (1,:data,'retained.bin')"), {'data': b'synthetic\x00original'})
        dump = subprocess.run(docker + ['pg_dump', '-U', 'app_migrator', '-d', 'hausverwaltung',
                                       '--format=custom', '--schema=' + schema], check=True, capture_output=True).stdout
        subprocess.run(docker + ['pg_restore', '--list'], input=dump, check=True, capture_output=True)
        # Remove ONLY our synthetic table and empty schema; no CASCADE or fixture reset.
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE {schema}.original'))
            connection.execute(text(f'DROP SCHEMA {schema}'))
        subprocess.run(docker + ['pg_restore', '-U', 'app_migrator', '-d', 'hausverwaltung',
                                 '--exit-on-error', '--single-transaction'], input=dump, check=True, capture_output=True)
        deployment.restore_documents(io.BytesIO(documents.getvalue()), tmp_path / 'restored')
        with engine.connect() as connection:
            uri = connection.scalar(text(f'SELECT storage_uri FROM {schema}.original WHERE id=1'))
            assert (tmp_path / 'restored' / uri).read_bytes() == original
            assert bytes(connection.scalar(text(f'SELECT payload FROM {schema}.original WHERE id=1'))) == b'synthetic\x00original'
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS {schema}.original'))
            connection.execute(text(f'DROP SCHEMA IF EXISTS {schema}'))
        engine.dispose()


def test_cli_preflight_and_actual_runtime_credentials_are_read_only():
    import os
    import sys

    _, app_url, _ = require_disposable_database_settings(settings)
    from sqlalchemy.engine import make_url
    runtime = make_url(app_url)
    # Whitelist the subprocess environment; never inherit developer provider keys.
    env = {name: os.environ[name] for name in ('PATH', 'LANG', 'HV_DATABASE_URL',
           'HV_DATABASE_URL_SYNC', 'HV_DATABASE_URL_MIGRATOR', 'HV_DATABASE_URL_REVIEW',
           'CI', 'HV_DECLARATION_TEST_DATABASE_PROFILE') if name in os.environ}
    env['HV_DATABASE_URL_WORKER'] = runtime.set(username='app_worker', password='worker_secret').render_as_string(hide_password=False)
    env.setdefault('HV_DATABASE_URL_REVIEW', runtime.set(username='app_review', password='review_secret').render_as_string(hide_password=False))
    for arguments, expected in [(['preflight'], '0176'), (['runtime', 'app_rw'], ''),
                                (['runtime', 'app_review'], ''), (['runtime', 'app_worker'], '')]:
        result = subprocess.run([sys.executable, '-m', 'app.deployment', *arguments],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
    env['HV_DATABASE_URL_SYNC'] = 'postgresql://invalid:synthetic_secret@127.0.0.1:1/hausverwaltung'
    result = subprocess.run([sys.executable, '-m', 'app.deployment', 'preflight'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'deployment check failed' in result.stderr
    assert 'synthetic_secret' not in result.stderr + result.stdout
    assert 'Traceback' not in result.stderr


def test_archive_verification_rejects_truncated_gzip(tmp_path):
    (tmp_path / 'original').write_bytes(b'synthetic-original')
    archive = io.BytesIO()
    deployment.archive_documents(tmp_path, archive)
    with pytest.raises((EOFError, OSError, tarfile.TarError)):
        deployment.verify_documents(io.BytesIO(archive.getvalue()[:-8]))


def test_quiescence_rejects_another_database_client():
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
            with pytest.raises(ValueError, match='clients'):
                deployment.database_check('quiescent')
    finally:
        engine.dispose()


def test_report_recipients_are_the_next_release_revision():
    revision = ScriptDirectory('migrations').get_revision('0162')
    assert revision.down_revision == '0161'
    assert deployment.TARGET == '0176'


def test_report_retention_empty_downgrade_restores_functions_and_upgrade_is_reversible(run_migrations):
    from importlib import import_module
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            signatures = ('person_retention_categories()', 'retention_source_until(text,jsonb)', 'organization_retention_plan(uuid)')
            before = [connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'), {'signature': sig}) for sig in signatures]
            snapshots_sql = text('SELECT to_jsonb(r) FROM settlement_run r ORDER BY id')
            before_snapshots = connection.execute(snapshots_sql).all()
            functions_sql = text("""SELECT p.proname,pg_get_function_identity_arguments(p.oid),
                pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.prokind IN ('f','p','w') ORDER BY 1,2""")
            before_functions = connection.execute(functions_sql).all()
            triggers_sql = text("""SELECT tgname,tgenabled,pg_get_triggerdef(oid)
                FROM pg_trigger WHERE tgrelid='settlement_run'::regclass
                AND NOT tgisinternal ORDER BY tgname""")
            before_triggers = connection.execute(triggers_sql).all()
            module = import_module('migrations.versions.0162_report_recipient_retention')
            point = connection.begin_nested()
            try:
                with Operations.context(MigrationContext.configure(connection)):
                    addressed_reports = connection.execute(text("""SELECT id FROM settlement_run
                        WHERE status='finalized' AND run_type='VERMOEGENSBERICHT'
                          AND jsonb_array_length(CASE WHEN jsonb_typeof(snapshot_payload->'addressees')='array'
                            THEN snapshot_payload->'addressees' ELSE '[]'::jsonb END)>0""")).scalars().all()
                    if addressed_reports:
                        with pytest.raises(RuntimeError, match='Cannot remove retained asset-report recipient bindings'):
                            module.downgrade()
                        assert connection.execute(snapshots_sql).all() == before_snapshots
                        assert connection.execute(functions_sql).all() == before_functions
                        assert connection.execute(triggers_sql).all() == before_triggers
                        # Earlier scenarios retain addressed reports. Only this
                        # rollback-only historical projection omits the new key.
                        connection.execute(text('ALTER TABLE settlement_run DISABLE TRIGGER USER'))
                        connection.execute(text("UPDATE settlement_run SET snapshot_payload=snapshot_payload-'addressees' WHERE id=ANY(:ids)"),
                                           {'ids': addressed_reports})
                        connection.execute(text('ALTER TABLE settlement_run ENABLE TRIGGER USER'))
                    bank_cost = import_module('migrations.versions.0171_bank_cost_assignment')
                    bank_cost.retention(reverse=True)
                    historical_before = [connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),
                        {'signature': sig}) for sig in signatures]
                    module.downgrade()
                    module.upgrade()
                    historical_after = [connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),
                        {'signature': sig}) for sig in signatures]
                    assert historical_after == historical_before
            finally:
                point.rollback()
            assert connection.execute(snapshots_sql).all() == before_snapshots
            assert connection.execute(functions_sql).all() == before_functions
            assert connection.execute(triggers_sql).all() == before_triggers
            assert [connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'), {'signature': sig}) for sig in signatures] == before
    finally:
        engine.dispose()


def test_dated_successor_dunning_is_the_next_release_revision():
    revision = ScriptDirectory('migrations').get_revision('0163')
    assert revision.down_revision == '0162'
    assert deployment.TARGET == '0176'


@pytest.mark.parametrize('column', ['distribution_base_total', 'distribution_base_share'])
@pytest.mark.parametrize('value', ['1000000.000000', '-1000000.000000'])
def test_person_day_basis_downgrade_refuses_unrepresentable_values_without_changing_state(run_migrations, column, value):
    from tests.test_session_review_access import _chain_snapshot
    revision = ScriptDirectory('migrations').get_revision('0165')
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin(), Operations.context(MigrationContext.configure(connection)):
            org, actor = str(uuid.uuid4()), str(uuid.uuid4())
            connection.execute(text("INSERT INTO organization(id,name) VALUES (:org,'Wide basis guard')"), {'org': org})
            connection.execute(text('''INSERT INTO "user"(id,organization_id,email,password_hash,display_name)
                VALUES (:actor,:org,:email,'synthetic-not-a-login','Wide basis actor')'''), {'actor': actor, 'org': org, 'email': actor+'@basis.invalid'})
            connection.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:actor,'OWNER_ADMIN')"), {'org': org, 'actor': actor})
            values, _ = seed_pre_split_upgrade(connection, org, actor)
            # A draft line isolates each column's representability boundary.
            draft = connection.scalar(text('''INSERT INTO settlement_run(organization_id,property_id,lease_agreement_id,run_type,
                period_start,period_end,status,engine_version) VALUES (:org,:property,:lease,'BK_MIETER','2026-01-01','2026-12-31','draft','test') RETURNING id'''), values)
            connection.execute(text(f'''INSERT INTO settlement_line(organization_id,settlement_run_id,line_type,label,amount,{column})
                VALUES (:org,:run,'COST','Raw basis guard',0,CAST(:value AS numeric))'''), {'org': org, 'run': draft, 'value': value})
            retained = _chain_snapshot(connection)
            with pytest.raises(RuntimeError, match='Cannot narrow stored settlement bases'):
                with connection.begin_nested():
                    revision.module.downgrade()
            assert _chain_snapshot(connection) == retained
            connection.rollback()
    finally:
        engine.dispose()


def test_property_state_migration_preserves_rows_and_refuses_lossy_downgrade(run_migrations):
    from importlib import import_module
    module = import_module('migrations.versions.0166_property_federal_state')
    assert module.down_revision == '0165'
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin() as transaction, Operations.context(MigrationContext.configure(connection)):
            org = str(uuid.uuid4())
            prop = str(uuid.uuid4())
            connection.execute(text("INSERT INTO organization(id,name) VALUES (:org,'Calendar rehearsal')"), {'org': org})
            connection.execute(text("INSERT INTO property(id,organization_id,name,management_type) VALUES (:id,:org,'Legacy','MIET')"), {'id': prop, 'org': org})
            before = connection.execute(text("SELECT to_jsonb(p)-'federal_state' FROM property p WHERE id=:id"), {'id': prop}).scalar_one()
            assert connection.scalar(text('SELECT federal_state FROM property WHERE id=:id'), {'id': prop}) is None
            # Other tests may have selected a state. The rollback-only projection
            # clears context solely to exercise the empty downgrade contract.
            connection.execute(text('UPDATE property SET federal_state=NULL WHERE federal_state IS NOT NULL'))
            connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            module.downgrade()
            module.upgrade()
            assert connection.execute(text("SELECT to_jsonb(p)-'federal_state' FROM property p WHERE id=:id"), {'id': prop}).scalar_one() == before
            connection.execute(text("UPDATE property SET federal_state='BY' WHERE id=:id"), {'id': prop})
            with pytest.raises(RuntimeError, match='Cannot discard property holiday context'):
                module.downgrade()
            assert connection.scalar(text('SELECT federal_state FROM property WHERE id=:id'), {'id': prop}) == 'BY'
            transaction.rollback()
    finally:
        engine.dispose()
