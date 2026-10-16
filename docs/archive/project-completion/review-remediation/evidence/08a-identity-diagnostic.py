"""Read-only synthetic checks of the live disposable DB identity helper.

Not an end-to-end adoption test. Connection is supplied explicitly; no .env read.
"""
import json
import os
from urllib.parse import urlparse

import psycopg2

url = os.environ["HV_DATABASE_URL_SYNC"]
parsed = urlparse(url)
assert (parsed.hostname, parsed.port, parsed.path, parsed.username) == (
    "127.0.0.1", 55432, "/hausverwaltung", "app_migrator"
), "Only the previously verified disposable database is allowed"
base = dict(seller_name=" Example  Supplier ", document_number="R-2026-42",
            document_kind="invoice", currency="EUR", gross_amount="119.00",
            service_period_from="2026-09-01", service_period_to="2026-09-30",
            content_sha256="a" * 64)
variants = [
    ("changed hash only", dict(content_sha256="b" * 64), True),
    ("changed amount", dict(gross_amount="149.00"), False),
    ("changed period start", dict(service_period_from="2026-09-02"), False),
    ("changed period end", dict(service_period_to="2026-10-01"), False),
    ("outer number whitespace", dict(document_number=" R-2026-42 "), True),
    ("number case preserved", dict(document_number="r-2026-42"), False),
    ("number punctuation preserved", dict(document_number="R202642"), False),
    ("seller case/whitespace", dict(seller_name="example supplier"), True),
]
with psycopg2.connect(url) as connection:
    connection.set_session(readonly=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        print("disposable migration head:", cursor.fetchone()[0])
        def identity(values):
            cursor.execute("SELECT adoption_invoice_identity(jsonb_populate_record("
                           "NULL::public.invoice_review_revision,%s::jsonb))", (json.dumps(values),))
            return cursor.fetchone()[0]
        expected = identity(base)
        for label, changes, equal in variants:
            actual_equal = identity(dict(base, **changes)) == expected
            assert actual_equal is equal, (label, actual_equal, equal)
            print(f"PASS {label}: identity_equal={actual_equal}")
print("8 synthetic identity assertions passed; no financial adoption performed")
