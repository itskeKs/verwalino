"""Version two of the portable product archive, independent of DB backups."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

FORMAT = "verwalino-organization-export"
VERSION = 2
# Stable product order. Additional visible provenance follows alphabetically.
ENTITY_ORDER = (
    "organization", "management_mandate", "property", "unit", "person",
    "tenant_profile", "owner_profile", "vendor", "lease_agreement",
    "lease_rent_change", "ownership_history", "cost_category", "cost_entry",
    "bank_account", "bank_transaction", "payment", "settlement_run",
    "settlement_line", "economic_plan", "economic_plan_line", "special_levy",
    "special_levy_allocation", "dunning_notice", "meter", "meter_reading",
    "letter_template", "letter_document", "invoice_document", "attachment",
    "property_document", "mandate_document", "intake_original", "document_ref",
    "user", "membership", "audit_event",
)
SECRET_COLUMNS = frozenset({"password_hash", "token_hash", "token", "lease_token", "session_version"})
README = """# Verwalino organization export, version 2

Canonical data is UTF-8 JSON Lines in entities/<entity>.jsonl. Each line is one
row; source_id preserves its original identity across imports. Version-one imports use id\nas the source identity. UUIDs are strings, exact decimal amounts are strings, dates and timestamps
use ISO 8601. manifest.json gives the entity order, row counts, schema head and
SHA-256 digest of every other member. It cannot hash itself. documents/<UUID>
contains original bytes; manifest.documents links each file to its entity.
CSV companions use the existing payment and cost export renderers.

This is a product export, not a PostgreSQL backup. It excludes authentication
credentials and private runtime state. Ordinary administrator row and column
permissions apply: inaccessible data is absent, never fetched through a
privileged connection. excluded_entities and excluded_columns disclose schema
visibility limits without disclosing hidden rows or their counts. An import
must refuse unresolved dependencies rather than silently lose data.

Keep this archive private: it contains personal data and retained originals.
See docs/operations/organization-export.md in the matching application release.
"""


def _json_default(value):
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported archive value: {type(value).__name__}")


def without_credentials(value):
    """Also exclude credential fields nested in structured audit payloads."""
    if isinstance(value, dict):
        return {k: without_credentials(v) for k, v in value.items() if k not in SECRET_COLUMNS}
    if isinstance(value, list):
        return [without_credentials(v) for v in value]
    return value


def json_bytes(value) -> bytes:
    # JSONB snapshots can themselves contain exact numeric values. Python's
    # standard float decoder/encoder would round these before the archive exists.
    def encode(item):
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("Non-finite archive number")
            return str(item)
        if isinstance(item, dict):
            return "{" + ",".join(encode(k) + ":" + encode(v) for k, v in sorted(item.items())) + "}"
        if isinstance(item, list):
            return "[" + ",".join(encode(v) for v in item) + "]"
        return json.dumps(item, default=_json_default, ensure_ascii=False, allow_nan=False)
    return encode(value).encode("utf-8")


def add_member(archive, name, source, size):
    """Never preserve host paths, owners, permissions or timestamps."""
    info = tarfile.TarInfo(name)
    info.size, info.mode = size, 0o600
    archive.addfile(info, source)


def add_bytes(archive, name, content, hashes):
    add_member(archive, name, io.BytesIO(content), len(content))
    hashes[name] = hashlib.sha256(content).hexdigest()
