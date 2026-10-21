"""Historical migration setup inside rollback-only, guarded test savepoints."""
from pathlib import Path
from contextlib import contextmanager
import json
from uuid import UUID

from alembic.script import ScriptDirectory
from sqlalchemy import text

from tests.conftest import clear_disposable_source_context


def source_security_function_templates(connection):
    """Capture real guards before a caller enters synthetic enforcement setup."""
    return connection.execute(text("""SELECT p.oid,p.proname,
        oidvectortypes(p.proargtypes) AS arguments,
        pg_get_functiondef(p.oid) AS definition,p.prosrc,p.proconfig,p.prosecdef,
        pg_get_userbyid(p.proowner) AS owner
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        WHERE p.pronamespace='public'::regnamespace AND l.lanname IN ('sql','plpgsql')
        ORDER BY p.oid""")).mappings().all()


@contextmanager
def historical_source_security_slots(connection, tables, functions):
    """Rehearse empty historical DDL slots beside populated runtime objects.

    Source migrations 0053/0054/0069 address their own DDL slots without a
    schema qualifier. Their shipped function bodies explicitly address public
    runtime authorities. Preserve those bodies, owners and execute privileges:
    in particular, never detach a current policy from its real guard OID.
    Nothing in public is renamed, emptied, dropped or recreated here.
    The caller must roll back its savepoint, including this private schema.
    """
    if not connection.in_nested_transaction():
        raise RuntimeError('Historical DDL slots require a rollback-only savepoint')
    schema = 'rehearsal_source_security'
    quote = connection.dialect.identifier_preparer.quote
    tables = tuple(sorted(set(table.strip('"') for table in tables)))
    previous_path = connection.scalar(text('SHOW search_path'))
    # Only these migrations' policy slots belong to this historical lifecycle.
    # Modern policies keep their original public tables/types and guard OIDs;
    # copying those tail policies here would not represent revision 0069.
    policy_names = (
        *(f'contact_source_{action}' for action in ('read','insert','update','delete')),
        *(f'history_source_{action}' for action in ('read','insert','update','delete')),
        *(f'dunning_contact_{action}' for action in ('read','insert','update','delete')),
        'financial_source_disclosure','source_audit_read',
        'property_source_security_read','property_source_security_insert',
        'knowledge_budget_policy_read','knowledge_budget_policy_insert',
        'lease_rent_change_read','lease_rent_change_insert',
    )
    policies = connection.execute(text("""SELECT * FROM pg_policies
        WHERE schemaname='public' AND tablename=ANY(:tables)
          AND policyname=ANY(:policies)
        ORDER BY tablename,policyname"""), {'tables': list(tables), 'policies': list(policy_names)}).mappings().all()
    if {policy['policyname'] for policy in policies} != set(policy_names):
        raise RuntimeError('Historical source-security policy inventory is incomplete')
    triggers = connection.execute(text("""SELECT c.relname,pg_get_triggerdef(t.oid) AS definition
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(:tables)
          AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"""), {'tables': list(tables)}).mappings().all()
    constraints = connection.execute(text("""SELECT c.relname,k.conname,
        pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k
        JOIN pg_class c ON c.oid=k.conrelid WHERE c.relnamespace='public'::regnamespace
        AND c.relname=ANY(:tables) AND k.contype IN ('p','u','x')
        ORDER BY c.relname,k.conname"""), {'tables': list(tables)}).mappings().all()
    owner = connection.scalar(text('SELECT current_user'))
    if any(function['owner'] != owner for function in functions):
        raise RuntimeError('Historical function slots must retain their actual definer owner')
    connection.execute(text(f'CREATE SCHEMA {schema}'))
    connection.execute(text(f'GRANT USAGE ON SCHEMA {schema} TO app_rw'))

    def copy_acl(kind, source_oid, destination):
        catalog, default = ('pg_proc', 'f') if kind == 'FUNCTION' else ('pg_class', 'r')
        column = 'proacl' if kind == 'FUNCTION' else 'relacl'
        owner_column = 'proowner' if kind == 'FUNCTION' else 'relowner'
        acl_sql = text(f"""SELECT a.grantee,a.grantor,a.privilege_type,a.is_grantable,
            CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS role
            FROM {catalog} c,aclexplode(COALESCE(c.{column},acldefault('{default}',c.{owner_column}))) a
            WHERE c.oid=:oid ORDER BY a.grantee,a.privilege_type,a.grantor""")
        acl = connection.execute(acl_sql, {'oid': source_oid}).mappings().all()
        regtype = 'regprocedure' if kind == 'FUNCTION' else 'regclass'
        target_oid = connection.scalar(text(f'SELECT CAST(CAST(:target AS {regtype}) AS oid)'), {'target': destination})
        target_acl = connection.execute(acl_sql, {'oid': target_oid}).mappings().all()
        for role in {entry['role'] for entry in target_acl} | {'PUBLIC'}:
            grantee = 'PUBLIC' if role == 'PUBLIC' else quote(role)
            connection.execute(text(f'REVOKE ALL ON {kind} {destination} FROM {grantee}'))
        for entry in acl:
            role = 'PUBLIC' if entry['grantee'] == 0 else quote(entry['role'])
            option = ' WITH GRANT OPTION' if entry['is_grantable'] else ''
            connection.execute(text(f'GRANT {entry["privilege_type"]} ON {kind} {destination} TO {role}{option}'))
        if connection.execute(acl_sql, {'oid': target_oid}).mappings().all() != acl:
            raise RuntimeError('Historical slot execute/table privileges changed')

    for table in tables:
        destination = f'{schema}.{quote(table)}'
        connection.execute(text(f'CREATE TABLE {destination} (LIKE public.{quote(table)} INCLUDING ALL)'))
        facts = connection.execute(text("""SELECT oid,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE oid=CAST(:table AS regclass)"""), {'table': f'public.{quote(table)}'}).one()
        copy_acl('TABLE', facts.oid, destination)
        if facts.relrowsecurity:
            connection.execute(text(f'ALTER TABLE {destination} ENABLE ROW LEVEL SECURITY'))
        if facts.relforcerowsecurity:
            connection.execute(text(f'ALTER TABLE {destination} FORCE ROW LEVEL SECURITY'))
    # LIKE retains unique/exclusion semantics but generates index/constraint
    # names. Real 0126 down/up addresses its original named unique constraint.
    assigned = set()
    for constraint in constraints:
        table, name = constraint['relname'], constraint['conname']
        candidates = connection.execute(text("""SELECT k.conname,pg_get_constraintdef(k.oid) AS definition
            FROM pg_constraint k WHERE k.conrelid=CAST(:table AS regclass) ORDER BY k.conname"""),
            {'table': f'{schema}.{quote(table)}'}).mappings().all()
        matching = [row['conname'] for row in candidates if row['definition'] == constraint['definition']
                    and (table,row['conname']) not in assigned]
        if not matching:
            raise RuntimeError(f'Historical constraint slot missing: {table}.{name}')
        actual = name if name in matching else matching[0]
        if actual != name:
            connection.execute(text(f'ALTER TABLE {schema}.{quote(table)} RENAME CONSTRAINT {quote(actual)} TO {quote(name)}'))
        assigned.add((table,name))
    for function in functions:
        prefix = f'CREATE OR REPLACE FUNCTION public.{quote(function["proname"])}('
        if not function['definition'].startswith(prefix):
            raise RuntimeError('Unexpected historical function declaration')
        declaration = function['definition'].replace(prefix,
            f'CREATE OR REPLACE FUNCTION {schema}.{quote(function["proname"])}(', 1)
        connection.execute(text(declaration))
        destination = f'{schema}.{quote(function["proname"])}({function["arguments"]})'
        copy_acl('FUNCTION', function['oid'], destination)
        facts = connection.execute(text("""SELECT prosrc,proconfig,prosecdef,pg_get_userbyid(proowner)
            FROM pg_proc WHERE oid=CAST(:function AS regprocedure)"""), {'function': destination}).one()
        if tuple(facts) != (function['prosrc'],function['proconfig'],function['prosecdef'],function['owner']):
            raise RuntimeError('Historical guard body/configuration/definer changed')
    connection.execute(text("SELECT set_config('search_path',:path,true)"), {'path': f'{schema},public'})
    for trigger in triggers:
        source = f' ON public.{quote(trigger["relname"])} '
        if trigger['definition'].count(source) != 1:
            raise RuntimeError('Unexpected historical trigger table declaration')
        definition = trigger['definition'].replace(source, f' ON {schema}.{quote(trigger["relname"])} ', 1)
        definition = definition.replace('EXECUTE FUNCTION public.', f'EXECUTE FUNCTION {schema}.')
        connection.execute(text(definition))
    for policy in policies:
        roles = ','.join('PUBLIC' if role == 'public' else quote(role) for role in policy['roles'])
        statement = (f'CREATE POLICY {quote(policy["policyname"])} ON {schema}.{quote(policy["tablename"])} '
                     f'AS {policy["permissive"]} FOR {policy["cmd"]} TO {roles}')
        if policy['qual'] is not None:
            statement += f' USING ({policy["qual"]})'
        if policy['with_check'] is not None:
            statement += f' WITH CHECK ({policy["with_check"]})'
        connection.execute(text(statement))
    yield schema
    connection.execute(text("SELECT set_config('search_path',:path,true)"), {'path': previous_path})


@contextmanager
def historical_financial_scope_lookup(connection):
    """Expose 0127's actual pre-levy delegate to earlier adapter migrations.

    Keep retained levy/attachment rows and every current wrapper in place.
    Renames preserve function identity, privileges and configuration; the
    caller's savepoint restores the complete current schema after rehearsal.
    """
    if not connection.in_nested_transaction():
        raise RuntimeError('Historical adapter rehearsal requires a rollback-only savepoint')
    delegate = connection.scalar(text(
        "SELECT to_regprocedure('public.financial_scope_before_special_levy(uuid,text,uuid)')"))
    if delegate is None:
        yield
        return
    wrapper = connection.scalar(text(
        "SELECT pg_get_functiondef('public.financial_scope_id(uuid,text,uuid)'::regprocedure)"))
    if 'public.financial_scope_before_special_levy(org,kind,resource)' not in wrapper:
        raise RuntimeError('Unexpected current financial scope delegate')
    connection.execute(text('ALTER FUNCTION public.financial_scope_id(uuid,text,uuid) RENAME TO rehearsal_current_financial_scope_id'))
    connection.execute(text('ALTER FUNCTION public.financial_scope_before_special_levy(uuid,text,uuid) RENAME TO financial_scope_id'))
    yield
    # On failure the caller rolls back its savepoint; do not mask the original
    # migration error by trying DDL in an aborted PostgreSQL transaction.
    connection.execute(text('ALTER FUNCTION public.financial_scope_id(uuid,text,uuid) RENAME TO financial_scope_before_special_levy'))
    connection.execute(text('ALTER FUNCTION public.rehearsal_current_financial_scope_id(uuid,text,uuid) RENAME TO financial_scope_id'))


_GRAPH_TABLES = (
    'property', 'property_document', 'declaration_import',
    'declaration_import_document', 'declaration_candidate',
    'declaration_extraction_run', 'unit',
    'declaration_candidate_issue', 'declaration_candidate_evidence',
)


def prepare_historical_revision(connection, revision, *, graphs=(), actor=None):
    if not connection.in_nested_transaction():
        raise RuntimeError('Historical rehearsal requires a rollback-only savepoint')
    snapshots = {}
    for table in _GRAPH_TABLES:
        ids = [graph[table] for graph in graphs]
        if ids:
            snapshots[table] = connection.scalars(text(
                f'SELECT to_jsonb(t)::text FROM {table} t WHERE id = ANY(:ids)'
            ), {'ids': ids}).all()
    imports = [json.loads(row) for row in snapshots.get('declaration_import', ())]
    queue_rows = connection.scalars(text(
        'SELECT to_jsonb(q)::text FROM declaration_queue_request q '
        'WHERE import_id = ANY(:ids) ORDER BY created_at'
    ), {'ids': [UUID(row['id']) for row in imports]}).all() if imports else []
    clear_disposable_source_context(connection.connection.cursor())
    versions = ScriptDirectory(str(Path(__file__).resolve().parents[1] / 'migrations'))
    for newer in versions.iterate_revisions('head', revision):
        newer.module.downgrade()
    if actor is not None:
        connection.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
                           {'actor': str(actor)})
    for table, rows in snapshots.items():
        for row in rows:
            if table == 'declaration_import':
                original = json.loads(row)
                row = json.dumps({**original, 'status': 'DRAFT', 'queue_request_id': None})
            connection.execute(text(
                f'INSERT INTO {table} SELECT * FROM jsonb_populate_record(NULL::{table}, CAST(:row AS jsonb))'
            ), {'row': row})
    for row in queue_rows:
        queue = json.loads(row)
        if queue['operation'] != 'START' or queue['previous_request_id'] is not None:
            raise RuntimeError('Link rehearsal only supports initial queue graphs')
        connection.execute(text(
            'INSERT INTO declaration_queue_request SELECT * FROM '
            'jsonb_populate_record(NULL::declaration_queue_request, CAST(:row AS jsonb))'
        ), {'row': row})
    for row in imports:
        connection.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
                           {'org': row['organization_id']})
        connection.execute(text(
            'UPDATE declaration_import SET status=:status,queue_request_id=:request WHERE id=:id'
        ), {'status': row['status'], 'request': row['queue_request_id'], 'id': row['id']})
