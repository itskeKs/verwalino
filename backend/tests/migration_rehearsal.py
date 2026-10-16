"""Historical migration setup inside rollback-only, guarded test savepoints."""
from pathlib import Path
import json
from uuid import UUID

from alembic.script import ScriptDirectory
from sqlalchemy import text

from tests.conftest import clear_disposable_source_context


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
