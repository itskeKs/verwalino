"""Durable seven-day cooling-off and operator-only retention reconciliation."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import text

from app.core.storage import LocalFsDocumentStore, get_store
from app.services.organization_archive import json_bytes
from app.services.organization_export_service import export_store

COOLING_OFF = timedelta(days=7)
NOTICE = ('Nach der Löschung ist keine Anmeldung mehr möglich; aufbewahrungspflichtige '
          'Unterlagen bleiben für mindestens acht Jahre ab dem maßgeblichen Jahresende '
          'beim Betreiber und sind ausschließlich für den Betreiber zugänglich. '
          'Maßgeblich sind die unten angegebenen Aufbewahrungsfristen. '
          'Erstellen Sie unmittelbar vor der Bestätigung einen neuen Export.')

# Mutable configuration retained for structural/delete-privilege reasons, not
# frozen voucher content. Canonical rule/fact fields remain guard-protected.
ERASURE_SCRUB_COLUMNS = {
    'tenant_profile': ('notes',),
    'lease_agreement': ('notes',),
    'vendor': ('notes', 'tax_id', 'vat_id'),
    'owner_profile': ('notes',),
    'letter_template': ('name', 'subject', 'body'),
    'property_document': ('deed_reference',),
    'property_assignment': ('stable_key', 'designation', 'location', 'description', 'details_json'),
    'property_legal_reference': ('reference_type', 'district', 'cadastral_district', 'field_number', 'parcel', 'sheet', 'details_json'),
    'property_rule': ('search_text',),
    'property_rule_source': ('printed_page', 'section', 'quote', 'field_path'),
    'approved_fact_source': ('search_text',),
    'knowledge_budget_policy': ('reason',),
    'property_source_security': ('reason',),
}


def retained_hash(name, row):
    scrubbed = set(ERASURE_SCRUB_COLUMNS.get(name, ()))
    if scrubbed:
        row = {k: v for k, v in row.items() if k not in scrubbed | {'updated_at', 'search_vector'}}
    if name == 'person':
        row = {k: v for k, v in row.items() if k not in {'processing_restricted', 'deletion_requested_at', 'anonymized_at', 'updated_at'}}
    return hashlib.sha256(json_bytes(row)).hexdigest()


PERSONAL_FIELDS = frozenset({
    'person_id', 'email', 'recipient_email', 'email_address', 'name', 'full_name',
    'recipient_name', 'first_name', 'last_name', 'company_name', 'address',
    'street', 'zip_code', 'postal_code', 'city', 'phone', 'telephone',
    'iban', 'bic', 'bank_name',
})
PAYLOAD_METADATA_FIELDS = frozenset({
    'id', 'kind', 'status', 'created_at', 'completed_at', 'content_sha256',
    'storage_uri', 'bytes', 'message_id', 'request_id', 'request_key',
    'request_digest', 'operation', 'version', 'count', 'counts',
    'error_code', 'processor_version',
})


def payload_scrubber(persons, retained_person_ids, user_emails, now):
    """Capture pre-anonymization values; preserve explicitly bound subobjects."""
    retained_ids = set(retained_person_ids)
    erased = {row['id'] for row in persons if row['id'] not in retained_ids}
    values = {str(row[field]) for row in persons if row['id'] in erased
              for field in PERSONAL_FIELDS | {'id'} if row.get(field)}
    emails = {email for email in user_emails if email}

    def pattern(tokens):
        if not tokens:
            return None
        return re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(v) for v in sorted(tokens, key=lambda v: (-len(v), v)))
                          + r')(?!\w)', re.IGNORECASE)

    personal_pattern, email_pattern = pattern(values), pattern(emails)
    marker = 'Gelöscht'

    def neutralize(value):
        if isinstance(value, dict):
            return {key: neutralize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [neutralize(item) for item in value]
        return marker if value is not None else None

    def scrub(value, context=None):
        if isinstance(context, str):
            context = True if context in erased else False if context in retained_ids else None
        if isinstance(value, dict):
            typed_person = value.get('kind') == 'person'
            person = value.get('person_id', value.get('id') if typed_person else None)
            if isinstance(person, str) and person in erased:
                context = True
            elif isinstance(person, str) and person in retained_ids:
                context = False
            personal_fields = PERSONAL_FIELDS | {'id', 'key'} if typed_person else PERSONAL_FIELDS
            result = {}
            for key, item in value.items():
                new_key = key if key in personal_fields | PAYLOAD_METADATA_FIELDS else scrub(key, context)
                # Several erased UUID dictionary keys must not collapse a map.
                if new_key in result:
                    new_key = f'{new_key} {len(result)}'
                result[new_key] = (neutralize(item) if context is True and key in personal_fields
                                   else item if key in PAYLOAD_METADATA_FIELDS
                                   else scrub(item, context))
            if result != value:
                result['_organization_erasure'] = {'scrubbed_at': now.isoformat()}
            return result
        if isinstance(value, list):
            return [scrub(item, context) for item in value]
        if isinstance(value, str):
            if email_pattern:
                value = email_pattern.sub(marker, value)
            if context is not False and personal_pattern:
                value = personal_pattern.sub(marker, value)
        return value

    return scrub


async def scrub_retained_payloads(db, org, tables, hashes, scrub):
    columns = (await db.execute(text('''SELECT r.entity,a.attname,
        a.atttypid IN ('json'::regtype,'jsonb'::regtype) AS is_json
      FROM organization_retention_catalog() r JOIN pg_attribute a
        ON a.attrelid=to_regclass('public.'||r.entity)
        AND a.attname=ANY(organization_erasure_payload_columns(r.entity))
      WHERE a.attnum>0 AND NOT a.attisdropped'''))).all()
    payloads_by_table = {}
    for name, column, is_json in columns:
        payloads_by_table.setdefault(name, {})[column] = is_json
    keys_by_table = dict(tables)
    for name, payload_columns in payloads_by_table.items():
        rows = (await db.execute(text(f'SELECT to_jsonb(t) FROM public."{name}" t WHERE organization_id=:org'),
                                 {'org': org})).scalars().all()
        for row in rows:
            identity = {key: row[key] for key in keys_by_table[name]}
            key = (name, json_bytes(identity))
            if key not in hashes or retained_hash(name, row) != hashes[key]:
                raise RuntimeError('Retained record changed before payload scrub')
            context = row.get('entity_id') if row.get('entity_type') == 'person' else None
            changed = {column: scrub(row[column], context) for column in payload_columns if row[column] is not None}
            changed = {column: value for column, value in changed.items() if value != row[column]}
            if not changed:
                continue
            assignments, params = [], {'org': org, 'key': json_bytes(identity).decode()}
            for column, value in changed.items():
                is_json = payload_columns[column]
                assignments.append(f'"{column}"=CAST(:{column} AS ' + ('jsonb)' if is_json else 'text)'))
                params[column] = json_bytes(value).decode() if is_json else value
            await db.execute(text(f'UPDATE public."{name}" t SET ' + ','.join(assignments)
                + ' WHERE organization_id=:org AND to_jsonb(t) @> CAST(:key AS jsonb)'), params)
            # Verify every field against the expected redacted row, rather than
            # excluding entire payloads from the integrity comparison.
            hashes[key] = retained_hash(name, {**row, **changed})


async def lifecycle_lock(db, org):
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {'key': f'scope-acl:{org}'})
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {'key': f'organization-lifecycle:{org}'})


async def audit(db, org, actor, action):
    identity = uuid.uuid4()
    await db.execute(text('''INSERT INTO audit_event(id,organization_id,actor_user_id,entity_type,entity_id,action)
      VALUES (:id,:org,:actor,'organization',:org,:action)'''),
      {'id': identity, 'org': org, 'actor': actor, 'action': action})
    return identity


async def deletion_status(db, org):
    row = (await db.execute(text('''SELECT status,execute_after FROM organization_deletion
      WHERE organization_id=:org'''), {'org': org})).mappings().one_or_none()
    return dict(row) if row else None


async def request_deletion(db, auth):
    await lifecycle_lock(db, auth.organization_id)
    state = await deletion_status(db, auth.organization_id)
    if state and state['status'] in {'cooling_off', 'deleted_pending_retention'}:
        raise HTTPException(409, 'Die Löschung wurde bereits bestätigt.')
    preview = await db.scalar(text('SELECT organization_deletion_preview(:org)'), {'org': auth.organization_id})
    await db.execute(text('''INSERT INTO organization_deletion(organization_id,status,retention)
      VALUES (:org,'requested',CAST(:retention AS jsonb)) ON CONFLICT(organization_id) DO UPDATE
      SET status='requested',requested_at=clock_timestamp(),confirmed_at=NULL,execute_after=NULL,
          completed_at=NULL,retention=EXCLUDED.retention'''), {'org': auth.organization_id, 'retention': json.dumps(preview)})
    await audit(db, auth.organization_id, auth.user_id, 'organization.deletion.requested')
    name = await db.scalar(text('SELECT name FROM organization WHERE id=:org'), {'org': auth.organization_id})
    return {'status': 'requested', 'organization_name': name, 'retention': preview,
            'export_url': '/api/organizations/export', 'notice': NOTICE}


async def confirm_deletion(db, auth, organization_name, *, now=None):
    await lifecycle_lock(db, auth.organization_id)
    state = await deletion_status(db, auth.organization_id)
    if not state or state['status'] != 'requested':
        raise HTTPException(409, 'Fordern Sie zunächst die Aufbewahrungsvorschau an.')
    name = await db.scalar(text('SELECT name FROM organization WHERE id=:org'), {'org': auth.organization_id})
    if organization_name != name:
        raise HTTPException(422, 'Bitte geben Sie den Organisationsnamen exakt ein.')
    now = now or datetime.now(timezone.utc)
    # Recompute after the final export and confirmation; never trust a stale
    # client preview or a client-supplied retention selection.
    preview = await db.scalar(text('SELECT organization_deletion_preview(:org)'), {'org': auth.organization_id})
    await db.execute(text('''UPDATE organization_deletion SET status='cooling_off',confirmed_at=:now,
      execute_after=:due,retention=CAST(:retention AS jsonb) WHERE organization_id=:org'''),
      {'org': auth.organization_id, 'now': now, 'due': now + COOLING_OFF, 'retention': json.dumps(preview)})
    await audit(db, auth.organization_id, auth.user_id, 'organization.deletion.confirmed')
    return {'status': 'cooling_off', 'execute_after': now + COOLING_OFF}


async def cancel_deletion(db, auth, *, now=None):
    await lifecycle_lock(db, auth.organization_id)
    state = await deletion_status(db, auth.organization_id)
    # The deadline makes erasure due; it does not strand a failed erasure.
    # This lock serializes cancellation with the operator's actual erasure.
    if not state or state['status'] != 'cooling_off':
        raise HTTPException(409, 'Es gibt keine widerrufbare Löschung.')
    await db.execute(text("UPDATE organization_deletion SET status='cancelled',execute_after=NULL WHERE organization_id=:org"),
                     {'org': auth.organization_id})
    await audit(db, auth.organization_id, auth.user_id, 'organization.deletion.cancelled')
    return {'status': 'cancelled'}


async def run_deletion(db, org, *, now=None):
    """Operator maintenance only. Never called by an HTTP/runtime connection."""
    if await db.scalar(text('SELECT session_user')) != 'app_migrator':
        raise RuntimeError('Operator database role required')
    now = now or datetime.now(timezone.utc)
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(org)})
    await lifecycle_lock(db, org)
    state = (await db.execute(text('SELECT * FROM organization_deletion WHERE organization_id=:org FOR UPDATE'), {'org': org})).mappings().one_or_none()
    if not state or state['status'] != 'cooling_off' or state['execute_after'] > now:
        return False
    # Transaction-local and useful only for an actual operator login. Evidence
    # guards permit only their explicitly listed personal-payload UPDATEs.
    await db.execute(text("SET LOCAL app.organization_erasure = 'on'"))
    store = get_store()
    if not isinstance(store, LocalFsDocumentStore):
        raise RuntimeError('Local document storage required for erasure')
    erasure_directories(store, org)
    plan = (await db.execute(text('SELECT * FROM organization_retention_plan(:org)'), {'org': org})).mappings().all()
    retained = {}
    for item in plan:
        retained.setdefault(item['entity'], []).append({
            'key': item['identity'], 'id': item['identity'].get('id'),
            'until': item['retention_until'].isoformat(), 'basis': item['legal_basis']})
    person_ids = [item['id'] for item in retained.get('person', []) if date.fromisoformat(item['until']) >= now.date()]
    persons = (await db.execute(text('SELECT to_jsonb(p) FROM person p WHERE organization_id=:org'), {'org': org})).scalars().all()
    user_emails = (await db.execute(text('SELECT email FROM "user" WHERE organization_id=:org'), {'org': org})).scalars().all()
    scrub = payload_scrubber(persons, person_ids, user_emails, now)
    tables = (await db.execute(text('''SELECT c.relname,array_agg(a.attname ORDER BY k.ordinality) AS pk
      FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
      JOIN pg_constraint p ON p.conrelid=c.oid AND p.contype='p'
      CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(num,ordinality)
      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num
      WHERE ns.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute org
        WHERE org.attrelid=c.oid AND org.attname='organization_id')
      AND c.relname NOT IN ('organization_deletion','password_reset_request')
      GROUP BY c.relname'''))).all()
    # Snapshot hashes are stored before erasure and checked again before commit.
    # A retention dependency has the same protection as its immutable root.
    hashes, retained_uris, unretained_tables = {}, set(), set()
    for name, keys in tables:
        rows = (await db.execute(text(f'SELECT to_jsonb(t) FROM public."{name}" t WHERE organization_id=:org'), {'org': org})).scalars().all()
        keep = {json_bytes(item['key']) for item in retained.get(name, [])}
        for row in rows:
            key = json_bytes({k: row[k] for k in keys})
            if key not in keep:
                unretained_tables.add(name)
            if key in keep and name not in {'user', 'membership'} and (name != 'person' or row['id'] in person_ids):
                hashes[(name, key)] = retained_hash(name, row)
                if row.get('storage_uri'):
                    retained_uris.add(row['storage_uri'])
                payload = row.get('payload_diff') or {}
                if isinstance(payload, dict) and payload.get('storage_uri'):
                    retained_uris.add(payload['storage_uri'])
    # Person identity remains structurally stable, as ADR-0012 requires.
    await db.execute(text('''UPDATE person SET processing_restricted=true,deletion_requested_at=:now
      WHERE organization_id=:org'''), {'org': org, 'now': now})
    await db.execute(text('''UPDATE person SET first_name='',last_name='Anonymisiert',company_name=NULL,
      email=NULL,phone=NULL,street='',zip_code='',city='',iban=NULL,bic=NULL,bank_name=NULL,anonymized_at=:now
      WHERE organization_id=:org AND NOT(id=ANY(CAST(:retained AS uuid[])))'''), {'org': org, 'now': now, 'retained': person_ids})
    await scrub_retained_payloads(db, org, tables, hashes, scrub)
    # Account ids are audit identities, never an entitlement to retain credentials
    # or live contact data. The generation trigger invalidates every old cookie.
    await db.execute(text('''UPDATE "user" SET email=id::text||'@deleted.invalid',display_name='Gelöscht',
        password_hash='!',is_active=false WHERE organization_id=:org'''), {'org': org})
    await db.execute(text('DELETE FROM password_reset_request WHERE organization_id=:org'), {'org': org})
    for name, columns in ERASURE_SCRUB_COLUMNS.items():
        assignments = []
        for column in columns:
            # Multiple sources can share rule/document/page. NULL paths retain
            # their rows without colliding on the location uniqueness constraint.
            value = ('NULL' if (name, column) == ('property_rule_source', 'field_path') else
                     "'{}'::jsonb" if column == 'details_json' else
                     "'Gelöscht '||id::text" if column == 'stable_key' else 'CAST(:marker AS text)')
            assignments.append(f'"{column}"={value}')
        await db.execute(text(f'UPDATE public."{name}" SET ' + ','.join(assignments) + ' WHERE organization_id=:org'),
                         {'org': org, 'marker': 'Gelöscht'})
    # Dependency order is obtained from real FKs. Retained parent closure means
    # only wholly unretained branches are removed. A guard failure rolls back.
    from app.services.organization_import_service import foreign_keys
    edges = await foreign_keys(db)
    pending = {name: keys for name, keys in tables if name in unretained_tables and name not in {'user', 'membership', 'person', 'audit_event'}}
    while pending:
        leaves = [name for name in pending if not any(parent == name and child in pending and child != name
                                                    for child, parent, *_ in edges)]
        if not leaves:
            # Cyclic tables are only safely removed if there are no unretained
            # rows. Refuse rather than disabling FK or immutability enforcement.
            leaves = list(pending)
        progress = False
        for name in leaves:
            keys = pending[name]
            keep = [item['key'] for item in retained.get(name, [])]
            key_sql = 'jsonb_build_object(' + ','.join(f"'{key}',t.\"{key}\"" for key in keys) + ')'
            await db.execute(text(f'DELETE FROM public."{name}" t WHERE organization_id=:org '
                                  f'AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(CAST(:keep AS jsonb)) k WHERE k={key_sql})'),
                             {'org': org, 'keep': json.dumps(keep)})
            del pending[name]
            progress = True
        if not progress:
            raise RuntimeError('Unresolved erasure dependencies')
    # Required dependency rows and immutable roots must remain byte-for-byte.
    for (name, key), digest in hashes.items():
        row = await db.scalar(text(f'SELECT to_jsonb(t) FROM public."{name}" t WHERE organization_id=:org '
            'AND to_jsonb(t) @> CAST(:key AS jsonb)'), {'org': org, 'key': key.decode()})
        if row is None or retained_hash(name, row) != digest:
            raise RuntimeError('Retained record changed during erasure')
    await db.execute(text("UPDATE organization SET name='Gelöschte Organisation '||id::text WHERE id=:org"), {'org': org})
    completion_id = await audit(db, org, None, 'organization.deletion.completed')
    retained.setdefault('audit_event', []).append({'id': str(completion_id), 'key': {'id': str(completion_id)},
        'until': f'{now.year + 8}-12-31', 'basis': 'GoBD / ADR-0012: erasure audit evidence'})
    retention = {'retained': retained, 'record_hashes': {f'{n}:{k.decode()}': d for (n, k), d in hashes.items()}}
    await db.execute(text('''UPDATE organization_deletion SET status='deleted_pending_retention',completed_at=:now,
      retention=CAST(:retention AS jsonb) WHERE organization_id=:org'''), {'org': org, 'now': now, 'retention': json.dumps(retention)})
    # Files are reconciled only AFTER the database transaction commits. Failure
    # leaves the organization inaccessible and cleanup retryable by the CLI.
    db.info.setdefault('organization_erasure_files', []).append((org, retained_uris))
    return True


def erasure_directories(store, org):
    # Check the raw tenant partition before resolution: an alias to another
    # partition must never turn this organization's cleanup into foreign erasure.
    if (store._root / str(org)).is_symlink() or (store._root / '.organization-exports').is_symlink():
        raise RuntimeError('Unexpected link in organization document storage')
    artifacts_store = export_store()
    if (artifacts_store._root / str(org)).is_symlink():
        raise RuntimeError('Unexpected link in organization export storage')
    directories = (store._resolve_key(str(org)), artifacts_store._resolve_key(str(org)))
    for directory in directories:
        if directory.exists() and any(path.is_symlink() for path in directory.rglob('*')):
            raise RuntimeError('Unexpected link in organization document storage')
    return directories


def erase_unretained_files(org, retained_uris):
    store = get_store()
    if not isinstance(store, LocalFsDocumentStore):
        raise RuntimeError('Local document storage required for erasure')
    directory, artifacts = erasure_directories(store, org)
    if directory.exists():
        for path in directory.rglob('*'):
            if path.is_symlink():
                raise RuntimeError('Unexpected link in organization document storage')
            if path.is_file() and path.relative_to(store._root).as_posix() not in retained_uris:
                path.unlink()
    if artifacts.exists():
        for path in artifacts.iterdir():
            if path.is_file() and not path.is_symlink():
                path.unlink()


# A failed database transaction must never erase a file. The CLI also retries
# post-commit cleanup for completed organizations on every invocation.
from sqlalchemy import event
from sqlalchemy.orm import Session


class ErasureFileCleanupError(RuntimeError):
    """The database erasure committed, but its file cleanup needs a retry."""


@event.listens_for(Session, 'after_commit')
def _erase_files_after_commit(session):
    if session.get_nested_transaction() is None:
        for org, uris in session.info.pop('organization_erasure_files', []):
            try:
                erase_unretained_files(org, uris)
            except Exception as exc:
                raise ErasureFileCleanupError(str(org)) from exc


@event.listens_for(Session, 'after_soft_rollback')
def _keep_files_on_rollback(session, previous_transaction):
    session.info.pop('organization_erasure_files', None)
