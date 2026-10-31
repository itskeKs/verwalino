"""Validate a portable archive before restoring it under administrator RLS."""
from __future__ import annotations

import hashlib
import gzip
import json
import logging
import sys
import tarfile
import tempfile
import uuid
import zlib
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.storage import make_storage_key, put_document
from app.core.uploads import MAX_UPLOAD_BYTES
from app.deployment import TARGET
from app.services.organization_archive import FORMAT, SECRET_COLUMNS, json_bytes
from app.services.organization_export_service import inventory
from app.services.quota_service import lock_quota, require_counts

# Bounded synchronous product import; larger transfers need a worker increment.
MAX_ARCHIVE_BYTES = MAX_UPLOAD_BYTES
MAX_METADATA_BYTES = 32 * 1024**2
MAX_DOCUMENT_BYTES = 64 * 1024**2
MAX_EXPANDED_BYTES = 512 * 1024**2
MAX_MEMBERS = 10_000
MAX_ROWS = 20_000
MAX_JSON_ROW_BYTES = 2 * 1024**2
MAX_DECODED_METADATA_BYTES = 64 * 1024**2
logger = logging.getLogger(__name__)


def refuse(message, **details):
    raise HTTPException(422, {'message': message, **details})


def _metadata_size(value):
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_metadata_size(k) + _metadata_size(v) for k, v in value.items())
    elif isinstance(value, list):
        size += sum(_metadata_size(item) for item in value)
    return size


@contextmanager
def read_archive(source):
    source.seek(0, 2)
    if source.tell() > MAX_ARCHIVE_BYTES:
        refuse('Das Archiv überschreitet die Importgrenze von 25 MiB.')
    source.seek(0)
    gzip_header = source.read(10)
    if len(gzip_header) != 10 or gzip_header[:3] != b'\x1f\x8b\x08' or gzip_header[3] != 0:
        refuse('Der Komprimierungskopf des Produktarchivs ist ungültig.')
    source.seek(0)
    with tempfile.TemporaryDirectory(prefix='organization-import-') as directory:
        members, total, metadata = {}, 0, 0
        try:
            # Official v1/v2 exports use plain regular headers. Decode each
            # fixed-size header before reading payloads: tarfile's extended-header
            # decoder can otherwise allocate attacker-declared PAX/GNU sizes.
            with gzip.GzipFile(fileobj=source, mode='rb') as compressed:
                while True:
                    header = compressed.read(512)
                    if len(header) != 512:
                        refuse('Das Exportarchiv ist unvollständig.')
                    if header == bytes(512):
                        if compressed.read(512) != bytes(512):
                            refuse('Das Archivende ist beschädigt.')
                        while tail := compressed.read(64 * 1024):
                            total += len(tail)
                            if total > MAX_EXPANDED_BYTES or any(tail):
                                refuse('Das Archiv enthält unzulässige Nachlaufdaten.')
                        break
                    member = tarfile.TarInfo.frombuf(header, encoding='utf-8', errors='strict')
                    path = PurePosixPath(member.name)
                    if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.size < 0
                        or member.name in members or path.is_absolute()
                        or any(p in {'', '.', '..'} for p in member.name.split('/')) or '\\' in member.name):
                        refuse('Das Archiv enthält unzulässige Dateieinträge.')
                    if len(members) >= MAX_MEMBERS:
                        refuse('Das Archiv enthält zu viele Dateien (maximal 10.000).')
                    total += member.size + 512
                    document = member.name.startswith('documents/')
                    if document and member.size > MAX_DOCUMENT_BYTES:
                        refuse('Ein Original überschreitet die Importgrenze von 64 MiB.')
                    metadata += 0 if document else member.size
                    if total > MAX_EXPANDED_BYTES or metadata > MAX_METADATA_BYTES:
                        refuse('Das entpackte Archiv überschreitet die Importgrenze.')
                    if member.name == 'manifest.json' and member.size > MAX_JSON_ROW_BYTES:
                        refuse('Das Inhaltsverzeichnis überschreitet die Importgrenze.')
                    destination = Path(directory) / str(len(members))
                    digest, remaining = hashlib.sha256(), member.size
                    with destination.open('xb') as output:
                        while remaining:
                            chunk = compressed.read(min(64 * 1024, remaining))
                            if not chunk:
                                refuse('Eine Archivdatei ist unvollständig.')
                            remaining -= len(chunk)
                            digest.update(chunk)
                            output.write(chunk)
                    padding = (-member.size) % 512
                    if len(compressed.read(padding)) != padding:
                        refuse('Eine Archivdatei ist unvollständig.')
                    total += padding
                    members[member.name] = (destination, digest.hexdigest())
            manifest = json.loads(members['manifest.json'][0].read_bytes())
            if manifest['format'] != FORMAT or type(manifest['version']) is not int or manifest['version'] not in (1, 2):
                refuse('Dieses Exportformat wird nicht unterstützt.')
            graph = ScriptDirectory(str(Path(__file__).resolve().parents[2] / 'migrations'))
            compatible = {TARGET, *(r.revision for r in graph.iterate_revisions(TARGET, 'base'))}
            if manifest['schema_head'] not in compatible:
                refuse('Der Export stammt aus einem neueren oder unbekannten Datenbankschema.')
            if set(manifest['sha256']) != set(members) - {'manifest.json'}:
                refuse('Das Inhaltsverzeichnis des Archivs ist unvollständig.')
            for name, digest in manifest['sha256'].items():
                if digest != members[name][1]:
                    refuse('Die Prüfsumme einer Archivdatei stimmt nicht.')
            entities, row_count, decoded_bytes = {}, 0, 0
            for name in manifest['entity_order']:
                if name in entities:
                    refuse('Das Archiv enthält doppelte Entitäten.')
                rows = []
                with members[f'entities/{name}.jsonl'][0].open('rb') as lines:
                    while line := lines.readline(MAX_JSON_ROW_BYTES + 1):
                        if len(line) > MAX_JSON_ROW_BYTES:
                            refuse('Eine Datenzeile überschreitet die Importgrenze von 2 MiB.')
                        row_count += 1
                        if row_count > MAX_ROWS:
                            refuse('Das Archiv enthält zu viele Datensätze (maximal 20.000).')
                        row = json.loads(line, parse_float=Decimal)
                        decoded_bytes += _metadata_size(row)
                        if decoded_bytes > MAX_DECODED_METADATA_BYTES:
                            refuse('Die eingelesenen Metadaten überschreiten die Arbeitsspeichergrenze.')
                        if not isinstance(row, dict):
                            refuse('Eine Archivzeile enthält keinen Datensatz.')
                        rows.append(row)
                if len(rows) != manifest['row_counts'][name]:
                    refuse('Die Datensatzzahl im Archiv stimmt nicht.')
                entities[name] = rows
            if len(entities.get('organization', [])) != 1:
                refuse('Das Archiv muss genau eine Organisation enthalten.')
            yield manifest, entities, members
        except (KeyError, ValueError, TypeError, tarfile.TarError, EOFError, UnicodeError, RecursionError, InvalidOperation, gzip.BadGzipFile, zlib.error) as exc:
            refuse('Das Exportarchiv ist beschädigt oder unvollständig.')


async def foreign_keys(db):
    return (await db.execute(text('''SELECT child.relname,parent.relname,
        array_agg(ca.attname ORDER BY key.position),array_agg(pa.attname ORDER BY key.position)
      FROM pg_constraint fk JOIN pg_class child ON child.oid=fk.conrelid
      JOIN pg_class parent ON parent.oid=fk.confrelid JOIN pg_namespace ns ON ns.oid=child.relnamespace
      CROSS JOIN LATERAL unnest(fk.conkey,fk.confkey) WITH ORDINALITY key(cnum,pnum,position)
      JOIN pg_attribute ca ON ca.attrelid=child.oid AND ca.attnum=key.cnum
      JOIN pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=key.pnum
      WHERE fk.contype='f' AND ns.nspname='public'
      GROUP BY fk.oid,child.relname,parent.relname'''))).all()


def remap(value, identities):
    """Resolve UUID references, including typed UUIDs embedded in JSON snapshots."""
    if isinstance(value, dict):
        return {identities.get(k, k): (v if k == 'source_ids' else remap(v, identities)) for k, v in value.items()}
    if isinstance(value, list):
        return [remap(v, identities) for v in value]
    return identities.get(value, value) if isinstance(value, str) else value


async def admission(db, org, identities=None):
    return await db.scalar(text('SELECT organization_import_admission(:org,CAST(:ids AS jsonb))'),
                           {'org': org, 'ids': json.dumps(identities or {})})


async def import_organization(db, auth, source):
    # ACL exclusive comes first (user relinking), followed by the ordinary quota
    # lock. All emptiness checks, row inserts and audit commit in this transaction.
    state = await admission(db, auth.organization_id)
    if not state['empty']:
        raise HTTPException(409, 'Daten können nur in eine leere Organisation importiert werden.')
    await lock_quota(db, auth.organization_id)
    with read_archive(source) as (manifest, entities, members):
        specs = await inventory(db)
        source_org = manifest['organization']['id']
        if entities['organization'][0]['id'] != source_org:
            refuse('Die Organisationskennung im Archiv stimmt nicht.')
        for name, rows in entities.items():
            if name not in specs or name == 'organization_deletion':
                if rows:
                    refuse('Das Archiv enthält nicht importierbare Systemdaten.', entity=name)
                continue
            columns = set(specs[name]['columns'])
            ids = set()
            for row in rows:
                if set(row) - columns - {'source_id'} or set(row) & SECRET_COLUMNS:
                    refuse('Das Archiv enthält unzulässige Spalten.', entity=name)
                if name not in {'organization', 'cost_category'} and row.get('organization_id') != source_org:
                    refuse('Das Archiv enthält Daten einer anderen Organisation.')
                if 'id' in columns and not isinstance(row.get('id'), str):
                    refuse('Eine Datensatzkennung fehlt oder ist ungültig.', entity=name)
                if row.get('id'):
                    uuid.UUID(row['id'])
                    if not isinstance(row.get('source_id', row['id']), str):
                        refuse('Eine Quellkennung ist ungültig.', entity=name)
                    uuid.UUID(row.get('source_id', row['id']))
                if name == 'audit_event' and row.get('payload_diff') is not None and not isinstance(row['payload_diff'], dict):
                    refuse('Ein Änderungsnachweis ist ungültig.', entity=name)
                if row.get('id'):
                    if row['id'] in ids:
                        refuse('Das Archiv enthält doppelte Datensatzkennungen.', entity=name)
                    ids.add(row['id'])
        keys = await foreign_keys(db)
        missing = {}
        categories = (await db.execute(text('SELECT id,code,plan_version FROM cost_category'))).mappings().all()
        catalog = {(r['code'], r['plan_version']): str(r['id']) for r in categories}
        id_map = {'organization': {source_org: str(auth.organization_id)}, 'cost_category': {}}
        for row in entities.get('cost_category', []):
            target = catalog.get((row['code'], row['plan_version']))
            if target is None:
                missing.setdefault('cost_category', set()).add(row['id'])
            else:
                id_map['cost_category'][row['id']] = target
        for child, parent, child_columns, parent_columns in keys:
            if child not in entities:
                continue
            available = {tuple(row.get(c) for c in parent_columns) for row in entities.get(parent, [])}
            if parent == 'cost_category':
                for category in categories:
                    values = dict(category)
                    values['id'] = str(values['id'])
                    available.add(tuple(values.get(c) for c in parent_columns))
                    id_map['cost_category'].setdefault(values['id'], values['id'])
            for row in entities[child]:
                key = tuple(row.get(c) for c in child_columns)
                if all(v is not None for v in key) and key not in available:
                    missing.setdefault(parent, set()).add(str(row.get(child_columns[parent_columns.index('id')])) if 'id' in parent_columns else str(key))
        available_ids = {name: {row.get('id') for row in rows} for name, rows in entities.items()}
        def need(kind, identity):
            if identity is not None and identity not in available_ids.get(kind, set()):
                missing.setdefault(kind, set()).add(identity)
        # Runtime invoice insertion requires a private native upload admission.
        # It cannot be restored from administrator-visible archival evidence.
        for row in entities.get('invoice_document', []):
            need('invoice_upload_admission', row['id'])
        for row in entities.get('audit_event', []):
            need(row['entity_type'], row.get('entity_id'))
            payload = row.get('payload_diff') or {}
            if row['action'] in {'mail_prepared', 'mail_completed'} and payload.get('person_id'):
                need('person', payload['person_id'])
        for row in entities.get('master_import_batch', []):
            for item in row['result']['rows']:
                need(item['kind'], item['id'])
        for row in entities.get('dunning_batch', []):
            for item in row['result']:
                need('dunning_notice', item['notice_id'])
                need('lease_agreement', item['lease_agreement_id'])
        if missing:
            details = [{'entity': name, 'source_ids': sorted(ids), 'count': len(ids)} for name, ids in sorted(missing.items())]
            logger.warning('organization import missing_dependencies=%s', json.dumps(details))
            refuse('Fehlende Abhängigkeiten: Der Export enthält nicht alle benötigten Datensätze.', missing_dependencies=details)
        identity_rows = {n: [r['id'] for r in rows if r.get('id')] for n, rows in entities.items() if n not in id_map}
        state = await admission(db, auth.organization_id, identity_rows)
        existing_users = (await db.execute(text('SELECT id,email FROM "user" WHERE organization_id=:org'),
                                          {'org': auth.organization_id})).all()
        users_by_email = {email.strip().lower(): str(identity) for identity, email in existing_users}
        reused_users, reused_other_users = set(), set()
        for name, ids in identity_rows.items():
            collisions = set(state['collisions'].get(name, []))
            id_map[name] = {identity: str(uuid.uuid4()) if identity in collisions else identity for identity in ids}
        for row in entities.get('user', []):
            target = users_by_email.get(row['email'].strip().lower())
            if target:
                id_map['user'][row['id']] = target
                reused_users.add(row['id'])
                if target != str(auth.user_id):
                    reused_other_users.add(target)
        flat = {}
        for mapping in id_map.values():
            for identity, target in mapping.items():
                if identity in flat and flat[identity] != target:
                    refuse('Das Archiv enthält mehrdeutige Kennungen zwischen Entitäten.')
                flat[identity] = target
        # Request UUIDs have no FK but are identities too; preserve their internal
        # grouping while avoiding a collision on composite request constraints.
        for rows in entities.values():
            for row in rows:
                for column in ('request_key', 'request_id'):
                    if row.get(column) and row[column] not in flat:
                        flat[row[column]] = str(uuid.uuid4())
        await require_counts(db, auth.organization_id, properties=len(entities.get('property', [])), units=len(entities.get('unit', [])))
        pending, inserted = [], set()
        documents = {(d['entity'], d['id']): d['member'] for d in manifest['documents']}
        if len(documents) != len(manifest['documents']):
            refuse('Das Archiv enthält doppelte Originalzuordnungen.')
        storage_map, original_sizes = {}, {}
        for (kind, identity), member in documents.items():
            record = next((r for r in entities.get(kind, []) if r.get('id') == identity), None)
            if kind == 'audit_event' and record and record.get('action') == 'mail_prepared':
                record = record.get('payload_diff')
            if not record or member not in members:
                refuse('Eine Originalzuordnung ist unvollständig.', entity=kind)
            digest = record.get('content_sha256')
            size = record.get('content_byte_size', record.get('size_bytes', record.get('bytes')))
            if (not digest or record.get('storage_uri') != make_storage_key(source_org, digest)
                or members[member][1] != digest or (size is not None and
                (type(size) is not int or size != members[member][0].stat().st_size))):
                refuse('Größe, Ablage oder Prüfsumme eines Originals stimmt nicht.', entity=kind)
            storage_map[record['storage_uri']] = make_storage_key(str(auth.organization_id), digest)
            original_sizes[record['storage_uri']] = members[member][0].stat().st_size
        for name, rows in entities.items():
            if name in specs and 'storage_uri' in specs[name]['columns']:
                if any((name, row.get('id')) not in documents for row in rows):
                    refuse('Ein Originaldokument fehlt im Archiv.', entity=name)
        for row in entities.get('audit_event', []):
            if row['action'] in {'mail_prepared', 'quota_recount'}:
                payload = row.get('payload_diff') or {}
                uri, size = payload.get('storage_uri'), payload.get('bytes')
                if not isinstance(uri, str) or uri not in original_sizes or type(size) is not int or size != original_sizes[uri]:
                    logger.warning('organization import invalid original ledger entity=audit_event source_id=%s', row['id'])
                    refuse('Ein Speicher- oder Versandnachweis passt zu keinem geprüften Original.',
                           entity='audit_event', source_ids=[row['id']], count=1)

        organization = {k: v for k, v in entities['organization'][0].items() if k not in {'id', 'source_id'}}
        columns = ','.join(f'"{c}"' for c in organization)
        projection = ','.join(f'(v)."{c}"' for c in organization)
        try:
            if reused_other_users:
                await db.execute(text("UPDATE \"user\" SET is_active=false,password_hash='!' "
                    'WHERE organization_id=:org AND id=ANY(CAST(:ids AS uuid[]))'),
                    {'org': auth.organization_id, 'ids': sorted(reused_other_users)})
            await db.execute(text(f'UPDATE organization SET ({columns})=(SELECT {projection} '
                'FROM json_populate_record(NULL::public.organization,CAST(:row AS json)) v) WHERE id=:org'),
                {'org': auth.organization_id, 'row': json_bytes(organization).decode()})
        except DBAPIError:
            refuse('Die Organisationsdaten im Archiv sind ungültig.', entity='organization', source_ids=[source_org], count=1)
        for name, rows in entities.items():
            if name in {'organization', 'cost_category', 'organization_deletion'}:
                continue
            for row in rows:
                if name == 'user' and row['id'] in reused_users:
                    inserted.add((name, row['id']))
                    continue
                if name == 'membership' and row['user_id'] in reused_users:
                    existing = await db.scalar(text('SELECT id FROM membership WHERE organization_id=:org AND user_id=:user'),
                                               {'org': auth.organization_id, 'user': uuid.UUID(flat[row['user_id']])})
                    id_map[name][row['id']] = str(existing)
                    flat[row['id']] = str(existing)
                    inserted.add((name, row['id']))
                    continue
                pending.append((name, row))
        registrations = {}
        for row in entities.get('document_ref', []):
            kind = row['source_kind']
            registrations.setdefault(kind, {})[flat[row[kind + '_id']]] = {
                'id': flat[row['id']]}
        await db.execute(text("SELECT set_config('app.organization_import_registrations',:registrations,true)"),
                         {'registrations': json.dumps(registrations)})
        try:
            while pending:
                progress = False
                for name, original in pending[:]:
                    dependencies = []
                    for child, parent, child_columns, parent_columns in keys:
                        if child == name and 'id' in parent_columns and parent not in {'organization', 'cost_category'}:
                            value = original.get(child_columns[parent_columns.index('id')])
                            if value is not None:
                                dependencies.append((parent, value))
                    if any(edge not in inserted for edge in dependencies):
                        continue
                    if name == 'document_ref':
                        kind = original['source_kind']
                        if kind not in {'invoice_document', 'property_document', 'mandate_document', 'intake_original'}:
                            refuse('Unbekannte Originalreferenz.', entity=name)
                        generated = await db.scalar(text(f'SELECT id FROM document_ref WHERE organization_id=:org AND "{kind}_id"=:source'),
                                                    {'org': auth.organization_id, 'source': uuid.UUID(flat[original[kind + '_id']])})
                        if generated is None or str(generated) != flat[original['id']]:
                            refuse('Die Originalreferenz konnte nicht wiederhergestellt werden.', entity=name)
                        inserted.add((name, original['id']))
                        pending.remove((name, original))
                        progress = True
                        continue
                    row = remap({k: v for k, v in original.items() if k != 'source_id'}, {**flat, **storage_map})
                    if row.get('snapshot_payload') is not None and row.get('snapshot_sha256'):
                        from app.engine.canonical import canonical_sha256
                        if canonical_sha256(original['snapshot_payload']) != original['snapshot_sha256']:
                            refuse('Die Prüfsumme eines eingefrorenen Datenstands stimmt nicht.', entity=name)
                        row['snapshot_sha256'] = canonical_sha256(row['snapshot_payload'])
                    if name == 'user':
                        row.update(password_hash='!', is_active=False)
                    if name == 'person':
                        row['created_by'] = str(auth.user_id)
                    member = documents.get((name, original.get('id')))
                    if row.get('storage_uri') and row.get('content_sha256'):
                        if member is None or members[member][1] != row['content_sha256']:
                            refuse('Ein Originaldokument fehlt oder seine Prüfsumme stimmt nicht.', entity=name)
                        ref = await put_document(db, auth.organization_id, members[member][0].read_bytes(), content_type=row.get('mime_type', 'application/octet-stream'))
                        row['storage_uri'] = ref.uri
                        if 'size_bytes' in specs[name]['columns']:
                            row['size_bytes'] = ref.byte_size
                        if 'storage_backend' in specs[name]['columns']:
                            row['storage_backend'] = ref.backend
                        if 'storage_version' in row:
                            row['storage_version'] = ref.version
                    if name == 'audit_event':
                        if row['action'] == 'organization.import.completed':
                            row.setdefault('payload_diff', {})['imported_receipt'] = True
                        if row['action'] == 'mail_prepared':
                            payload = row.get('payload_diff') or {}
                            if payload.get('storage_uri'):
                                if member is None or members[member][1] != payload.get('content_sha256'):
                                    refuse('Ein Versandoriginal fehlt oder ist beschädigt.', entity=name)
                                ref = await put_document(db, auth.organization_id, members[member][0].read_bytes(), content_type='application/pdf')
                                payload['storage_uri'] = ref.uri
                                payload['bytes'] = ref.byte_size
                    # PostgreSQL performs exact schema-native type conversion,
                    # including numeric and nested JSON values, without float loss.
                    columns = ','.join(f'"{c}"' for c in row)
                    projection = ','.join(f'(v)."{c}"' for c in row)
                    await db.execute(text(f'INSERT INTO public."{name}" ({columns}) SELECT {projection} '
                        f'FROM json_populate_record(NULL::public."{name}",CAST(:row AS json)) v'),
                        {'row': json_bytes(row).decode()})
                    inserted.add((name, original.get('id')))
                    pending.remove((name, original))
                    progress = True
                if not progress:
                    refuse('Das Archiv enthält zyklische oder nicht auflösbare Abhängigkeiten.')
        except DBAPIError as exc:
            # Structured driver codes only; no SQL, target identities or values.
            code = getattr(exc.orig, 'sqlstate', None)
            logger.warning('organization import rejected by database sqlstate=%s', code)
            refuse('Die Daten können unter den aktuellen Integritäts- und Freigaberegeln nicht importiert werden.', code='IMPORT_INTEGRITY_REFUSAL', entity=name, source_ids=[original.get('id', original.get('request_key'))], count=1)
        counts = {name: len(rows) for name, rows in entities.items()}
        source_ids = {name: {id_map[name][r['id']]: r.get('source_id', r['id']) for r in rows if r.get('id')}
                      for name, rows in entities.items() if name in id_map}
        await db.execute(text('''INSERT INTO audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff,occurred_at)
          VALUES (:org,:actor,'organization',:org,'organization.import.completed',CAST(:payload AS jsonb),clock_timestamp())'''),
          {'org': auth.organization_id, 'actor': auth.user_id, 'payload': json_bytes({'source_ids': source_ids, 'row_counts': counts}).decode()})
        return {'row_counts': counts, 'id_map': id_map}
