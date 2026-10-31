"""RLS-bound, statement-snapshot organization export and guarded download."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import uuid
import logging
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.deployment import TARGET

from app.core.storage import LocalFsDocumentStore, get_store, make_storage_key
from app.models.audit_event import AuditEvent
from app.repositories.audit_repo import AuditRepository
from app.services.organization_archive import (
    ENTITY_ORDER, FORMAT, README, SECRET_COLUMNS, VERSION, add_bytes, add_member, json_bytes, without_credentials,
)

EXPORT_LIFETIME = timedelta(hours=24)
EXPORT_INTERVAL = timedelta(minutes=1)
SPOOL_BYTES = 8 * 1024**2


def export_store():
    store = get_store()
    if not isinstance(store, LocalFsDocumentStore):
        raise HTTPException(503, "Exportablage nicht verfügbar.")
    return LocalFsDocumentStore(store._root / ".organization-exports")


def cleanup_expired_exports(*, now=None):
    """Expiry applies to bytes too; only this dedicated artifact root is used."""
    store = export_store()
    deadline = ((now or datetime.now(timezone.utc)) - EXPORT_LIFETIME).timestamp()
    if not store._root.exists():
        return 0
    removed = 0
    for directory in store._root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            uuid.UUID(directory.name)
        except ValueError:
            continue
        for path in [*directory.glob("*.tar.gz"), *directory.glob("*.pending")]:
            if not path.is_symlink() and path.is_file() and path.stat().st_mtime <= deadline:
                try:
                    uuid.UUID(path.name.removesuffix(".tar.gz").removesuffix(".pending"))
                except ValueError:
                    continue
                path.unlink(missing_ok=True)
                removed += 1
    return removed


@event.listens_for(Session, "after_commit")
def _replace_committed_export(session):
    if session.get_nested_transaction() is None:
        publication = session.info.pop("publishing_organization_export", None)
        if publication:
            pending, published = publication
            try:
                pending.rename(published)
            except OSError:
                pending.unlink(missing_ok=True)
                raise
        for path in session.info.pop("replaced_organization_exports", []):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("Could not remove a replaced organization export")


@event.listens_for(Session, "after_soft_rollback")
def _keep_previous_export(session, previous_transaction):
    session.info.pop("replaced_organization_exports", None)
    session.info.pop("publishing_organization_export", None)


async def inventory(db):
    """Catalog metadata only. Data queries below always run as the caller."""
    rows = (await db.execute(text("""SELECT c.relname AS name,
        array_agg(a.attname ORDER BY a.attnum) AS columns,
        array_agg(a.attname ORDER BY a.attnum) FILTER
          (WHERE a.atttypid IN ('numeric'::regtype,'float4'::regtype,'float8'::regtype)) AS numeric_columns,
        array_agg(a.attname ORDER BY a.attnum) FILTER
          (WHERE has_column_privilege(current_user,c.oid,a.attnum,'SELECT')) AS readable
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
      WHERE n.nspname='public' AND c.relkind='r'
        AND (c.relname IN ('organization','cost_category') OR EXISTS(
          SELECT 1 FROM pg_attribute org WHERE org.attrelid=c.oid AND org.attname='organization_id'))
      GROUP BY c.oid,c.relname ORDER BY c.relname"""))).mappings().all()
    # Password-reset links are global authentication infrastructure, never data.
    return {r["name"]: r for r in rows if r["name"] != "password_reset_request"}


def export_predicate(name, columns):
    """Reuse the existing capability predicates in addition to SELECT RLS."""
    checks = []
    if name == "property":
        checks.append("property_master_access(:org,t.id,'export')")
    elif name == "person":
        checks.append("person_source_access(:org,t.id,'export')")
    elif name in {"owner_profile", "tenant_profile", "vendor"}:
        checks.append(f"profile_source_access(:org,'{name}',t.id,'export')")
    elif name in {"payment", "cost_entry", "bank_account", "settlement_run", "economic_plan"}:
        checks.append(f"financial_scope_access(:org,'{name}',t.id,'export')")
        checks.append("property_financial_disclosure_access(:org,t.property_id,'export')")
    elif name in {"lease_agreement", "ownership_history"}:
        checks.append(f"property_history_row_access(:org,'{name}',to_jsonb(t),'export')")
    elif name == "invoice_document":
        checks.append("invoice_source_access(:org,t.id,'export')")
    elif name == "property_document":
        checks.append("property_source_export_access(:org,t.id,'SOURCE')")
    elif name == "mandate_document":
        checks.append("mandate_source_access(:org,t.accounting_scope_id,'export')")
    elif name == "intake_original":
        checks.append("intake_original_export_access(:org,t.id)")
    elif name == "attachment":
        checks.append("attachment_source_access(:org,t.property_id,t.unit_id,t.lease_agreement_id,t.person_id,'export')")
    elif name == "letter_document":
        checks.append("letter_source_access(:org,t.property_id,t.person_id,t.tenant_profile_id,t.lease_agreement_id)")
    elif name == "dunning_notice":
        checks.append("dunning_scope_access(:org,t.id,'export',t.tenant_profile_id,t.owner_profile_id)")
        checks.append("dunning_source_access(:org,t.id,'export')")
    elif name == "special_levy":
        checks.append("special_levy_sources(:org,t.property_id,t.id,t.resolution_id,'export')")
    elif name in {"owners_meeting", "agenda_item", "resolution", "resolution_register_entry"}:
        checks.append(f"property_meeting_row_access(:org,'{name}',to_jsonb(t),'export')")
    elif name in {"declaration_import", "declaration_import_document", "declaration_extraction_run", "declaration_import_pass",
                  "declaration_candidate", "declaration_candidate_evidence", "declaration_candidate_issue",
                  "property_legal_reference", "property_assignment", "property_rule", "property_rule_source",
                  "property_rule_relation", "property_document_relation", "approved_fact_source", "unit"}:
        checks.append(f"property_source_permitted(:org,'{name}',t.id,'export')")
    if "bank_account_id" in columns:
        checks.append("(t.bank_account_id IS NULL OR financial_scope_access(:org,'bank_account',t.bank_account_id,'export'))")
    for column in ("scope_id", "accounting_scope_id", "scope_hint_id"):
        if column in columns:
            checks.append(f"(t.{column} IS NULL OR has_scope_capability(:org,t.{column},'export'))")
    for column, fn in (("property_id", "property_master_access"), ("person_id", "person_source_access")):
        if column in columns:
            checks.append(f"(t.{column} IS NULL OR {fn}(:org,t.{column},'export'))")
    for column, kind in (("tenant_profile_id", "tenant_profile"), ("owner_profile_id", "owner_profile"), ("vendor_id", "vendor")):
        if column in columns:
            checks.append(f"(t.{column} IS NULL OR profile_source_access(:org,'{kind}',t.{column},'export'))")
    return " AND ".join(checks) or "true"


def row_query(name, spec):
    columns = [c for c in spec["readable"] or [] if c not in SECRET_COLUMNS]
    required = "id" if name == "organization" else "organization_id"
    if required not in columns and name != "cost_category":
        return None
    # Names originate exclusively in pg_catalog, never in request input.
    numeric = set(spec["numeric_columns"] or [])
    projection = ",".join(f't."{c}"::text AS "{c}"' if c in numeric else f't."{c}"' for c in columns)
    where = "t.id=:org" if name == "organization" else (
        "true" if name == "cost_category" else "t.organization_id=:org")
    predicate = export_predicate(name, columns)
    return (f"SELECT '{name}' AS entity,row_to_json(exported)::text AS data "
            f'FROM (SELECT {projection} FROM public."{name}" t '
            f'WHERE {where} AND ({predicate})) exported')


async def visible_snapshot(db, org, specs):
    queries = [query for name, spec in specs.items() if (query := row_query(name, spec))]
    result = {name: [] for name in specs}
    # One SQL statement means one PostgreSQL snapshot across all entities, even
    # while ordinary master-data writers commit in other transactions.
    rows = await db.execute(text("SELECT * FROM (" + " UNION ALL ".join(queries)
                                 + ") snapshot ORDER BY entity,data"), {"org": org})
    for name, row in rows:
        result[name].append(without_credentials(json.loads(row, parse_float=Decimal)))
    # A child's readable free text must not circumvent a parent's export grant.
    # Missing/private parents also make that child non-portable. No privileged
    # lookup is used to determine whether a missing parent exists.
    foreign_keys = (await db.execute(text("""SELECT child.relname,parent.relname,
        array_agg(ca.attname ORDER BY key.position),array_agg(pa.attname ORDER BY key.position)
      FROM pg_constraint fk JOIN pg_class child ON child.oid=fk.conrelid
      JOIN pg_class parent ON parent.oid=fk.confrelid JOIN pg_namespace ns ON ns.oid=child.relnamespace
      CROSS JOIN LATERAL unnest(fk.conkey,fk.confkey) WITH ORDINALITY key(cnum,pnum,position)
      JOIN pg_attribute ca ON ca.attrelid=child.oid AND ca.attnum=key.cnum
      JOIN pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=key.pnum
      WHERE fk.contype='f' AND ns.nspname='public' GROUP BY fk.oid,child.relname,parent.relname"""))).all()
    changed = True
    while changed:
        changed = False
        for child, parent, child_columns, parent_columns in foreign_keys:
            if child not in result or parent not in result:
                continue
            available = {tuple(row.get(c) for c in parent_columns) for row in result[parent]}
            kept = []
            for row in result[child]:
                key = tuple(row.get(c) for c in child_columns)
                # MATCH SIMPLE: a nullable edge has no dependency. Columns denied
                # by column privileges stay explicitly absent from the archive.
                if any(value is None for value in key) or key in available:
                    kept.append(row)
            if len(kept) != len(result[child]):
                result[child], changed = kept, True
        identities = {name: {row.get("id") for row in rows} for name, rows in result.items()}
        exported_uris = {row["storage_uri"] for rows in result.values() for row in rows if row.get("storage_uri")}
        def subject_exported(kind, identity):
            return identity is not None and identity in identities.get(kind, set())
        def semantic_access(name, row):
            if name == "audit_event":
                kind, identity = row["entity_type"], row.get("entity_id")
                if not subject_exported(kind, identity):
                    return False
                # Dispatch copies retain the original recipient, which can differ
                # from the document's current addressee after a master-data edit.
                payload = row.get("payload_diff") or {}
                if row["action"] == "quota_recount":
                    return payload.get("storage_uri") in exported_uris
                if row["action"] in {"mail_prepared", "mail_completed"} and payload.get("person_id"):
                    return subject_exported("person", payload["person_id"])
            elif name == "master_import_batch":
                return all(subject_exported(item["kind"], item["id"]) for item in row["result"]["rows"])
            elif name == "dunning_batch":
                return all(subject_exported("dunning_notice", item["notice_id"])
                    and subject_exported("lease_agreement", item["lease_agreement_id"]) for item in row["result"])
            return True
        for name in ("audit_event", "master_import_batch", "dunning_batch"):
            before = result.get(name, [])
            kept = [row for row in before if semantic_access(name, row)]
            if len(kept) != len(before):
                result[name], changed = kept, True
    return result


async def create_export(db, auth, *, app_version):
    locked = await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                             {"key": f"organization-export:{auth.organization_id}"})
    if not locked:
        raise HTTPException(429, "Ein Export läuft bereits. Bitte versuchen Sie es später erneut.")
    now = datetime.now(timezone.utc)
    store = export_store()
    directory = store._resolve_key(str(auth.organization_id))
    # Lifecycle audit events may themselves be source-restricted. The published
    # artifact is a durable admission timestamp without a privileged audit read.
    if directory.is_dir() and any(p.stat().st_mtime > (now - EXPORT_INTERVAL).timestamp()
                                 for p in directory.glob("*.tar.gz") if not p.is_symlink()):
        raise HTTPException(429, "Bitte warten Sie eine Minute, bevor Sie erneut exportieren.", headers={"Retry-After": "60"})
    cleanup_expired_exports(now=now)
    specs = await inventory(db)
    snapshot = await visible_snapshot(db, auth.organization_id, specs)
    order = [n for n in ENTITY_ORDER if n in specs] + sorted(set(specs) - set(ENTITY_ORDER))
    manifest = {"format": FORMAT, "version": VERSION, "created": now.isoformat(),
                "app_version": app_version,
                "schema_head": TARGET,
                "organization": None, "entity_order": [], "row_counts": {}, "sha256": {},
                "documents": [], "excluded_entities": [], "excluded_columns": {}}
    hashes = manifest["sha256"]
    with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as output:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name in order:
                spec = specs[name]
                readable = set(spec["readable"] or []) - SECRET_COLUMNS
                if not readable or (name not in {"organization", "cost_category"} and "organization_id" not in readable):
                    manifest["excluded_entities"].append(name)
                    continue
                excluded = sorted(set(spec["columns"]) - readable)
                if excluded:
                    manifest["excluded_columns"][name] = excluded
                rows = snapshot[name]
                manifest["entity_order"].append(name)
                manifest["row_counts"][name] = len(rows)
                if name == "organization":
                    if not rows:
                        raise HTTPException(404, "Organisation nicht gefunden.")
                    manifest["organization"] = {k: rows[0][k] for k in ("id", "name")}
                content = b"".join(json_bytes(row) + b"\n" for row in rows)
                add_bytes(archive, f"entities/{name}.jsonl", content, hashes)
                for row in rows:
                    if name == "audit_event" and row["action"] == "mail_prepared":
                        payload = row.get("payload_diff") or {}
                        row = {**payload, "id": row["id"], "content_byte_size": payload.get("bytes"),
                               "mime_type": "application/pdf"}
                    uri, digest = row.get("storage_uri"), row.get("content_sha256")
                    if not uri or not digest:
                        continue
                    if uri != make_storage_key(str(auth.organization_id), digest):
                        raise HTTPException(409, "Dokumentablage muss vor dem Export geprüft werden.")
                    member = f"documents/{row['id']}"
                    if member in hashes:
                        raise HTTPException(409, "Mehrdeutige Dokumentkennung. Bitte prüfen Sie die Quelldaten.")
                    with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as original:
                        sha, size = hashlib.sha256(), 0
                        try:
                            for chunk in get_store().iter_bytes(uri):
                                original.write(chunk)
                                sha.update(chunk)
                                size += len(chunk)
                        except FileNotFoundError as exc:
                            raise HTTPException(409, "Ein Originaldokument fehlt.") from exc
                        expected_size = row.get("content_byte_size", row.get("size_bytes"))
                        if sha.hexdigest() != digest or expected_size not in {None, size}:
                            raise HTTPException(409, "Die Prüfsumme eines Originaldokuments stimmt nicht.")
                        original.seek(0)
                        add_member(archive, member, original, size)
                    hashes[member] = digest
                    manifest["documents"].append({"entity": name, "id": row["id"], "member": member})
                if name in {"payment", "cost_entry"}:
                    # The established renderers retain spreadsheet-injection protection.
                    from app.api.payments import _render_payments_csv
                    from app.api.cost_entries import _render_cost_entries_csv
                    from app.models import Payment, CostEntry
                    from datetime import date
                    from decimal import Decimal
                    model, render = (Payment, _render_payments_csv) if name == "payment" else (CostEntry, _render_cost_entries_csv)
                    items = []
                    for row in rows:
                        values = {k: v for k, v in row.items() if k in model.__table__.columns}
                        for column in model.__table__.columns:
                            if values.get(column.name) is None:
                                continue
                            if column.type.python_type is Decimal:
                                values[column.name] = Decimal(str(values[column.name]))
                            elif column.type.python_type is date:
                                values[column.name] = date.fromisoformat(values[column.name])
                        items.append(model(**values))
                    add_bytes(archive, f"csv/{name}.csv", render(items), hashes)
            add_bytes(archive, "README.md", README.encode(), hashes)
            add_bytes(archive, "manifest.json", json_bytes(manifest), {})
        output.seek(0)
        # The response is streamed from disk. The export is separate from originals
        # and never contributes duplicate document bytes to the organization quota.
        store, export_id = export_store(), uuid.uuid4()
        published = store._resolve_key(f"{auth.organization_id}/{export_id}.tar.gz")
        key = f"{auth.organization_id}/{export_id}.pending"
        path = store._resolve_key(key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        store._root.chmod(0o700)
        path.parent.chmod(0o700)
        try:
            with path.open("xb") as target:
                import shutil
                shutil.copyfileobj(output, target)
            path.chmod(0o600)
            transaction = db.sync_session.get_transaction()
            db.info.setdefault("uncommitted_storage", []).append((store, key, transaction))
            db.info["replaced_organization_exports"] = [p for p in path.parent.glob("*.tar.gz") if p != path]
            db.info["publishing_organization_export"] = (path, published)
            await AuditRepository(db, auth.organization_id).create(AuditEvent(
                id=export_id, actor_user_id=auth.user_id, entity_type="organization", entity_id=auth.organization_id,
                action="organization.export.created", payload_diff={"expires_at": (now + EXPORT_LIFETIME).isoformat()},
            ))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return export_id, path


async def download_export(db, auth, export_id):
    path = export_store()._resolve_key(f"{auth.organization_id}/{export_id}.tar.gz")
    if not path.is_file():
        raise HTTPException(404, "Export nicht gefunden oder abgelaufen.")
    # Grants can be withdrawn between creation and download. Do not serve stale
    # authority from an archive created by a different administrator.
    specs = await inventory(db)
    snapshot = await visible_snapshot(db, auth.organization_id, specs)
    with tarfile.open(path, "r:gz") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        if datetime.fromisoformat(manifest["created"]) + EXPORT_LIFETIME <= datetime.now(timezone.utc):
            raise HTTPException(404, "Export nicht gefunden oder abgelaufen.")
        for name in manifest["entity_order"]:
            expected = [json.loads(line, parse_float=Decimal) for line in archive.extractfile(f"entities/{name}.jsonl")]
            visible = snapshot[name]
            # Full values, not only IDs: a changed/restricted row invalidates the
            # old download. The administrator can request a current export.
            available = {json_bytes(row) for row in visible}
            if any(json_bytes(row) not in available for row in expected):
                raise HTTPException(404, "Export nicht mehr verfügbar. Bitte erstellen Sie einen neuen Export.")
    return path
