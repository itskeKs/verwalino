"""Final T57 regressions: isolated review runtime and unknown legacy roles."""
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import MIGRATOR_URL
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_rbac import t57_user, t57_guest

pytestmark = pytest.mark.asyncio


async def test_review_runtime_validates_generation_without_password_lookup(rent_world):
    w = rent_world
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text('SET LOCAL ROLE app_review'))
            assert await connection.scalar(text("SELECT has_function_privilege(current_user,'public.app_auth_lookup_versioned(text)','EXECUTE')")) is False
            result = await connection.execute(text('SELECT role FROM public.app_validate_session_versioned(:actor,:org,0)'), {'actor': w['admin'], 'org': w['org']})
            assert result.scalars().all() == ['OWNER_ADMIN']
        async with api_client(w) as admin:
            response = await admin.post(f"/api/users/{w['admin']}/reset-password", json={'password': 'Final-Review-Reset-Password-123'})
            assert response.status_code == 204, response.text
        async with engine.begin() as connection:
            await connection.execute(text('SET LOCAL ROLE app_review'))
            for generation, expected in [(0, []), (1, ['OWNER_ADMIN'])]:
                result = await connection.execute(text('SELECT role FROM public.app_validate_session_versioned(:actor,:org,:generation)'), {'actor': w['admin'], 'org': w['org'], 'generation': generation})
                assert result.scalars().all() == expected
    finally:
        await engine.dispose()


async def test_unknown_legacy_role_is_visible_as_unknown_but_cannot_login(rent_world):
    w = rent_world
    async with api_client(w) as admin:
        credentials, target = await t57_user(admin)
        engine = create_async_engine(MIGRATOR_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text('UPDATE membership SET role=\'UNKNOWN\' WHERE organization_id=:org AND user_id=:id'), {'org': w['org'], 'id': uuid.UUID(target['id'])})
        finally:
            await engine.dispose()
        response = await admin.get('/api/users')
        assert response.status_code == 200, response.text
        row = next(item for item in response.json() if item['id'] == target['id'])
        assert row['role'] is None
        assert (await admin.get(f"/api/users/{target['id']}")).json()['role'] is None
        async with t57_guest() as user:
            assert (await user.post('/api/auth/login', json={key: credentials[key] for key in ('email', 'password')})).status_code == 401


async def test_review_validation_grant_roundtrip_preserves_data_and_security(rent_world):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template, issue_body

    w = rent_world
    async with api_client(w) as admin:
        template = await create_template(admin)
        issued = await admin.post('/api/letters/issue', json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        url = issued.json()[0]['pdf_url']
        original = (await admin.get(url)).content
        assert original.startswith(b'%PDF')
    path = Path(__file__).parents[1] / 'migrations/versions/0139_review_session_validation.py'
    spec = importlib.util.spec_from_file_location('review_session_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ('0139', '0138')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            def snapshot():
                tables = connection.execute(text("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname")).scalars().all()
                rows = {name: connection.execute(text(f'SELECT to_jsonb(t)::text FROM "{name}" t ORDER BY to_jsonb(t)::text')).scalars().all() for name in tables}
                queries = (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),CASE WHEN p.proname='app_validate_session_versioned' THEN NULL ELSE p.proacl::text END,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' ORDER BY p.proname,p.oid",
                )
                return rows, [connection.execute(text(query)).all() for query in queries]

            def allowed(function):
                return connection.scalar(text("SELECT has_function_privilege('app_review',:function,'EXECUTE')"), {'function': function})

            try:
                before = snapshot()
                assert before[0]['lease_agreement'] and before[0]['letter_document']
                assert allowed('public.app_validate_session_versioned(uuid,uuid,integer)')
                assert not allowed('public.app_auth_lookup_versioned(text)')
                acl = connection.scalar(text("SELECT proacl::text FROM pg_proc WHERE oid='public.app_validate_session_versioned(uuid,uuid,integer)'::regprocedure"))
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                    assert not allowed('public.app_validate_session_versioned(uuid,uuid,integer)')
                    assert snapshot() == before
                    migration.upgrade()
                    assert allowed('public.app_validate_session_versioned(uuid,uuid,integer)')
                    assert not allowed('public.app_auth_lookup_versioned(text)')
                    assert snapshot() == before
                    assert connection.scalar(text("SELECT proacl::text FROM pg_proc WHERE oid='public.app_validate_session_versioned(uuid,uuid,integer)'::regprocedure")) == acl
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(w) as admin:
        assert (await admin.get(url)).content == original


_CHAIN_TABLES = {
    'letter_document', 'letter_template', 'sepa_mandate', 'sepa_creditor',
    'master_import_batch', 'dunning_batch',
}
_CHAIN_COLUMNS = {
    'user': {'session_version'}, 'property': {'construction_year', 'total_area'},
    'bank_account': {'is_active'}, 'vendor': {'is_active'},
}
_CHAIN_FUNCTIONS = {
    'letter_source_access', 'letter_recipients_access', 'letter_request_access',
    'letter_document_immutable', 'sepa_bank_access', 'sepa_mandate_access',
    'sepa_input_lock', 'sepa_identity_guard', 'protect_master_import_batch',
    'protect_dunning_batch', 't55_input_lock', 't55_bank_details_guard',
    't55_active_reference_guard', 'master_delete_json_has_id',
    'master_delete_reference_kind', 't57_lock_user_session_change',
    't57_advance_user_session_version', 'app_auth_lookup_versioned',
    'app_validate_session_versioned',
}


def _chain_snapshot(connection):
    """Logical public application state, excluding physical OIDs/column slots.

    Internal FK trigger names contain fresh OIDs after recreation; retain their
    constraint identity, definition and enablement instead of that generated name.
    Sequence reads are passive: nextval/setval would not be rollback-safe.
    """
    def rows(query):
        return [tuple(row) for row in connection.execute(text(query))]

    result = {}
    result['relations'] = rows("""SELECT c.relname,c.relkind,c.relrowsecurity,
        c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner),c.reloptions
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S','i','I')
        ORDER BY c.relname""")
    result['columns'] = rows("""SELECT c.relname,a.attname,
        format_type(a.atttypid,a.atttypmod),a.attnotnull,
        pg_get_expr(d.adbin,d.adrelid),cn.nspname,co.collname,a.attidentity,a.attgenerated,
        a.attacl::text
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        LEFT JOIN pg_collation co ON co.oid=a.attcollation
        LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace
        WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped
        AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname,a.attnum""")
    result['views'] = rows("""SELECT c.relname,pg_get_viewdef(c.oid,false)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('v','m') ORDER BY c.relname""")
    result['constraints'] = rows("""SELECT c.relname,k.conname,
        pg_get_constraintdef(k.oid,false),k.convalidated,k.condeferrable,k.condeferred
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname,k.conname""")
    result['indexes'] = rows("""SELECT c.relname,i.relname,pg_get_indexdef(x.indexrelid),
        x.indisvalid,x.indisready,x.indislive,x.indisreplident,x.indisclustered
        FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid
        JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname,i.relname""")
    result['policies'] = rows("""SELECT tablename,policyname,permissive,roles::text,
        cmd,qual,with_check FROM pg_policies WHERE schemaname='public'
        ORDER BY tablename,policyname""")
    result['triggers'] = rows("""SELECT c.relname,
        CASE WHEN t.tgisinternal THEN 'internal:'||k.conname ELSE t.tgname END,
        CASE WHEN t.tgisinternal THEN replace(pg_get_triggerdef(t.oid,false),
          quote_ident(t.tgname),quote_ident('internal:'||k.conname))
          ELSE pg_get_triggerdef(t.oid,false) END,
        t.tgenabled,t.tgisinternal,source.relname
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_constraint k ON k.oid=t.tgconstraint
        LEFT JOIN pg_class source ON source.oid=k.conrelid
        WHERE n.nspname='public' ORDER BY 1,2,3""")
    result['functions'] = rows("""SELECT p.proname,pg_get_function_identity_arguments(p.oid),
        pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind IN ('f','p','w') ORDER BY 1,2""")
    result['schemas'] = rows("""SELECT nspname,nspacl::text,pg_get_userbyid(nspowner)
        FROM pg_namespace WHERE nspname<>'information_schema' AND nspname!~'^pg_'
        ORDER BY nspname""")
    result['default_acls'] = rows("""SELECT pg_get_userbyid(d.defaclrole),
        n.nspname,d.defaclobjtype,d.defaclacl::text FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace ORDER BY 1,2,3""")
    result['sequences'] = rows("""SELECT sequencename,sequenceowner,data_type,
        start_value,min_value,max_value,increment_by,cycle,cache_size
        FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename""")
    quote = connection.dialect.identifier_preparer.quote_identifier
    result['positions'] = {
        row[0]: tuple(connection.execute(text(
            f'SELECT last_value,is_called FROM public.{quote(row[0])}'
        )).one()) for row in result['sequences']
    }
    result['data'] = {
        name: sorted((json.loads(row, parse_float=Decimal) for row in connection.execute(
            text(f'SELECT to_jsonb(t)::text FROM public.{quote(name)} t')
        ).scalars()), key=repr) for name, kind, *_ in result['relations'] if kind in ('r', 'p')
    }
    return result


def _chain_0131_projection(head):
    """Subtract only the objects/columns explicitly introduced by 0132–0139."""
    new_relations = _CHAIN_TABLES | {
        row[1] for row in head['indexes'] if row[0] in _CHAIN_TABLES
    }
    constraints = {
        ('user', 'ck_user_session_version_nonnegative'),
        ('property', 'ck_property_ck_property_construction_year'),
        ('property', 'ck_property_ck_property_total_area'),
    }
    triggers = {('user', name) for name in (
        't57_lock_user_session_change', 't57_advance_user_session_version',
    )} | {('bank_account', 't55_bank_details_guard')}
    triggers |= {(table, 'a_t55_input_lock') for table in (
        'bank_account', 'vendor', 'payment', 'bank_transaction',
        'sepa_creditor', 'sepa_mandate', 'cost_entry',
    )}
    triggers |= {(table, 't55_active_reference_guard') for table in (
        'payment', 'bank_transaction', 'sepa_creditor', 'sepa_mandate', 'cost_entry',
    )}
    removed_columns = {(r[0], r[1]) for r in head['columns']
                       if r[1] in _CHAIN_COLUMNS.get(r[0], set())}
    assert removed_columns == {(table, column) for table, columns in _CHAIN_COLUMNS.items()
                               for column in columns}
    projected = dict(head)
    projected['relations'] = [r for r in head['relations'] if r[0] not in new_relations]
    for facet in ('columns', 'constraints', 'indexes', 'policies', 'triggers'):
        projected[facet] = [r for r in head[facet] if r[0] not in _CHAIN_TABLES]
    projected['columns'] = [r for r in projected['columns']
                            if r[1] not in _CHAIN_COLUMNS.get(r[0], set())]
    projected['constraints'] = [r for r in projected['constraints'] if r[:2] not in constraints]
    projected['triggers'] = [r for r in projected['triggers']
                             if r[:2] not in triggers and r[5] not in _CHAIN_TABLES]
    functions = [r for r in head['functions'] if r[0] in _CHAIN_FUNCTIONS]
    assert len(functions) == len(_CHAIN_FUNCTIONS)
    assert {r[0] for r in functions} == _CHAIN_FUNCTIONS
    projected['functions'] = [r for r in head['functions'] if r[0] not in _CHAIN_FUNCTIONS]
    projected['data'] = {}
    for table, values in head['data'].items():
        if table in _CHAIN_TABLES or table == 'alembic_version':
            continue
        columns = {r[1] for r in projected['columns'] if r[0] == table}
        projected['data'][table] = sorted(
            ({k: v for k, v in row.items() if k in columns} for row in values), key=repr
        )
    return projected


async def test_revision_chain_0148_0131_0148_preserves_full_state_and_issued_pdf(rent_world):
    """Rollback-only whole-chain rehearsal, not a production downgrade strategy."""
    import os
    from pathlib import Path

    from alembic.config import Config
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import DBAPIError

    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import contract_body, create_template, issue_body

    expected = ('127.0.0.1', 55432, 'hausverwaltung', 'app_migrator')
    for raw in (MIGRATOR_SYNC_URL, os.environ['HV_DATABASE_URL_SYNC']):
        url = make_url(raw)
        assert url.drivername in ('postgresql', 'postgresql+psycopg2')
        assert (url.host, url.port, url.database, url.username) == expected
        assert not url.query, 'No alternate host/service/connection options admitted'
    assert os.environ.get('HV_DECLARATION_TEST_DATABASE_PROFILE', 'local') == 'local'
    w = rent_world
    async with api_client(w) as admin:
        template = await create_template(admin)
        issued = await admin.post('/api/letters/issue', json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        pdf_url = issued.json()[0]['pdf_url']
        pdf = await admin.get(pdf_url)
        assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
        original_pdf = pdf.content
        builtin = await admin.post(f"/api/leases/{w['lease']}/contract", json=contract_body())
        assert builtin.status_code == 201 and builtin.json()['template_id'] is None, builtin.text
        builtin_url = builtin.json()['pdf_url']
        builtin_pdf = await admin.get(builtin_url)
        assert builtin_pdf.status_code == 200
        bank = await admin.post('/api/bank-accounts', json={
            'property_id': str(w['property']), 'account_type': 'LAUFEND',
            'label': 'Synthetic chain bank', 'iban': 'DE89370400440532013000',
        })
        assert bank.status_code == 201, bank.text
        bank_id = bank.json()['id']
        ended_bank = await admin.patch(f'/api/bank-accounts/{bank_id}', json={'is_active': False})
        assert ended_bank.status_code == 200 and ended_bank.json()['is_active'] is False
        vendor = await admin.post('/api/vendors', json={'person_id': issued.json()[0]['person_id']})
        assert vendor.status_code == 201, vendor.text
        vendor_id = vendor.json()['id']
        ended_vendor = await admin.put(f'/api/vendors/{vendor_id}', json={'is_active': False})
        assert ended_vendor.status_code == 200 and ended_vendor.json()['is_active'] is False
        facts = await admin.put(f"/api/properties/{w['property']}", json={
            'construction_year': 1984, 'total_area': '1234.56',
        })
        assert facts.status_code == 200, facts.text
        reset = await admin.post(f"/api/users/{w['reader']}/reset-password", json={
            'password': 'Chain-Rehearsal-Password-345',
        })
        assert reset.status_code == 204, reset.text

    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'migrations'))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ['0148']
    assert [r.revision for r in scripts.iterate_revisions('0148', '0131')] == [
        f'{number:04}' for number in range(148, 131, -1)
    ]
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(text("""SELECT current_database(),session_user,
                    current_user,inet_server_port(),pg_get_userbyid(datdba)
                    FROM pg_database WHERE datname=current_database()""")).one() == (
                    'hausverwaltung', 'app_migrator', 'app_migrator', 5432, 'app_migrator',
                )
                assert connection.scalar(text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user"))
                connection.execute(text('SET LOCAL search_path=public,pg_catalog'))
                connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))

                def version():
                    return tuple(connection.execute(text(
                        'SELECT version_num FROM public.alembic_version ORDER BY version_num'
                    )).scalars())

                def migrate(target, *, downgrade):
                    start = version()
                    seen = []

                    def steps(current, context):
                        assert tuple(current) == start
                        operation = scripts._downgrade_revs if downgrade else scripts._upgrade_revs
                        return operation(target, current)

                    def applied(ctx, step, heads, run_args):
                        assert version() == tuple(sorted(heads))
                        seen.append(version())

                    # env.py creates its own engine; configure the Alembic runtime
                    # directly so every real revision/version-table step uses this TX.
                    with EnvironmentContext(config, scripts, fn=steps) as env:
                        env.configure(connection=connection, transaction_per_migration=False,
                                      version_table_schema='public', on_version_apply=applied)
                        assert env.get_context().bind is connection
                        env.run_migrations()
                    assert transaction.is_active and connection.in_transaction()
                    assert version() == (target,)
                    stop, first = int(target), int(start[0])
                    numbers = range(first - 1, stop - 1, -1) if downgrade else range(first + 1, stop + 1)
                    assert seen == [(f'{number:04}',) for number in numbers]

                def projection_0131():
                    # The historical projection describes 0132–0139. Peel off
                    # the parent constraints and bootstrap additions within this savepoint.
                    with connection.begin_nested() as historical:
                        connection.execute(text('TRUNCATE public.letter_document'))
                        migrate('0139', downgrade=True)
                        projected = _chain_0131_projection(_chain_snapshot(connection))
                        historical.rollback()
                    return projected

                assert version() == ('0148',)
                retained = _chain_snapshot(connection)
                with pytest.raises(DBAPIError) as refusal:
                    with connection.begin_nested():
                        migrate('0142', downgrade=True)
                assert refusal.value.orig.pgcode == '23502'
                assert _chain_snapshot(connection) == retained
                # Only this rollback-only rehearsal excludes template-less originals
                # after proving 0143 refuses them; every retained row is restored below.
                connection.execute(text('CREATE TEMP TABLE chain_templated_letters ON COMMIT DROP AS '
                                        'SELECT * FROM public.letter_document WHERE template_id IS NOT NULL'))
                connection.execute(text('TRUNCATE public.letter_document'))
                connection.execute(text('INSERT INTO public.letter_document SELECT * FROM chain_templated_letters'))
                original = _chain_snapshot(connection)
                original_0131 = projection_0131()
                assert original['data']['lease_agreement'] and original['data']['letter_document']
                generation = connection.scalar(text(
                    'SELECT session_version FROM public."user" WHERE id=:id'
                ), {'id': w['reader']})
                assert generation > 0
                with pytest.raises(DBAPIError, match='would resurrect invalidated sessions'):
                    with connection.begin_nested():
                        migrate('0131', downgrade=True)
                assert _chain_snapshot(connection) == original

                # The only counter reset admitted by the invoker trigger; no
                # password, active flag, user deletion or trigger bypass involved.
                connection.execute(text('UPDATE public."user" SET session_version=0 WHERE session_version<>0'))
                for retained_fact in ('bank_account', 'vendor', 'property'):
                    with connection.begin_nested() as scenario:
                        connection.execute(text('UPDATE public.property SET construction_year=NULL,total_area=NULL'))
                        for table in ('bank_account', 'vendor'):
                            connection.execute(text(f'UPDATE public.{table} SET is_active=true WHERE NOT is_active'))
                        if retained_fact == 'property':
                            connection.execute(text('UPDATE public.property SET construction_year=1984,total_area=1234.56 WHERE id=:id'), {'id': w['property']})
                        else:
                            record_id = bank_id if retained_fact == 'bank_account' else vendor_id
                            connection.execute(text(f'UPDATE public.{retained_fact} SET is_active=false WHERE id=:id'), {'id': record_id})
                        connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                        guarded = _chain_snapshot(connection)
                        with pytest.raises(DBAPIError, match='Cannot remove populated T55 lifecycle or property facts'):
                            with connection.begin_nested():
                                migrate('0131', downgrade=True)
                        assert _chain_snapshot(connection) == guarded
                        scenario.rollback()
                connection.execute(text('UPDATE public.property SET construction_year=NULL,total_area=NULL '
                                        'WHERE construction_year IS NOT NULL OR total_area IS NOT NULL'))
                for table in ('bank_account', 'vendor'):
                    connection.execute(text(f'UPDATE public.{table} SET is_active=true WHERE NOT is_active'))
                connection.execute(text('TRUNCATE public.sepa_mandate,public.sepa_creditor,'
                                        'public.master_import_batch,public.dunning_batch'))
                connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                with_letters = _chain_snapshot(connection)
                with pytest.raises(DBAPIError, match='Cannot remove populated letter tables'):
                    with connection.begin_nested():
                        migrate('0131', downgrade=True)
                assert _chain_snapshot(connection) == with_letters
                connection.execute(text('TRUNCATE public.letter_document,public.letter_template'))
                connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                head = _chain_snapshot(connection)
                old = projection_0131()
                # Neutralization must not touch a single pre-0132 column/row.
                assert old == original_0131
                migrate('0131', downgrade=True)
                down = _chain_snapshot(connection)
                down['data'].pop('alembic_version')
                assert down == old
                migrate('0148', downgrade=False)
                connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                assert _chain_snapshot(connection) == head
            finally:
                transaction.rollback()
            assert _chain_snapshot(connection) == retained
            assert connection.scalar(text(
                'SELECT session_version FROM public."user" WHERE id=:id'
            ), {'id': w['reader']}) == generation
    finally:
        engine.dispose()
    async with api_client(w) as admin:
        restored = await admin.get(pdf_url)
        assert restored.status_code == 200 and restored.content == original_pdf
        restored_builtin = await admin.get(builtin_url)
        assert restored_builtin.status_code == 200 and restored_builtin.content == builtin_pdf.content
