"""Upgrade/backup checks using only the guarded synthetic database and files."""
import io
import subprocess
import tarfile
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app import deployment
from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings


@pytest.mark.parametrize('heads', [(), ('0028', '0122'), ('0122', '0139', '0140', '0141', '0142', '0143', '0144'), ('0138',), ('garbage',)])
def test_reject_unknown_or_ambiguous_database_version(heads):
    with pytest.raises(ValueError, match='schema'):
        deployment.supported_schema(heads)


def test_release_graph_is_exact_and_linear(tmp_path):
    graph = ScriptDirectory('migrations')
    assert deployment.release_head(graph) == '0149'
    (tmp_path / 'versions').mkdir()
    (tmp_path / 'versions' / 'unexpected.py').write_text(
        "revision='0150'\ndown_revision=None\nbranch_labels=None\ndepends_on=None\n")
    with pytest.raises(ValueError, match='release'):
        deployment.release_head(ScriptDirectory(str(tmp_path)))


@pytest.mark.parametrize('schema', ['0028', '0122', '0139', '0140', '0141', '0142', '0143', '0144', '0145', '0146', '0147', '0148', '0149'])  # accepted sources plus target
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


def rehearse_initial_upgrade(starting_schema='0122'):
    """Rehearse before the shared fixture reaches head or creates retained data."""
    _, _, url = require_disposable_database_settings(settings)
    engine = create_engine(url)
    graph = ScriptDirectory('migrations')
    revisions = list(graph.iterate_revisions('0149', starting_schema))
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
                    expected_security = connection.execute(text("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(:tables) ORDER BY c.relname"), {'tables': tables}).all()
                    expected.rollback()
                for revision in reversed(revisions):
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
                    revision.module.upgrade()
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
            if starting_schema in ('0122', '0139', '0140', '0141', '0142', '0143', '0144', '0145', '0146', '0147', '0148'):  # accepted sources
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


@pytest.mark.parametrize('starting_schema', ['0122', '0139', '0140', '0141', '0142', '0143', '0144', '0145', '0146', '0147', '0148'])  # accepted sources
def test_upgrade_to_0149_preserves_existing_rows_and_security(run_migrations, starting_schema):
    # The actual rehearsal ran before any retained domain-test originals existed.
    # Its SQL/data/security assertions fail fixture setup if preservation fails.
    assert run_migrations[f'upgrade_{starting_schema}_to_0149_tables'] > 50
    assert deployment.database_check('preflight') == '0149'


def test_upgrade_0028_to_0149_preserves_existing_rows_and_rls(run_migrations):
    assert run_migrations['upgrade_0028_to_0149_tables'] > 20


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
    for arguments, expected in [(['preflight'], '0149'), (['runtime', 'app_rw'], ''),
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
