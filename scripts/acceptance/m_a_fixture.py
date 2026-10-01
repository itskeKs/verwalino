"""Reset only the guarded disposable DB, rehearse M-A migration, seed browser QA.

Run with backend/.venv/bin/python and all three explicit localhost:55432 URLs.
Outputs only synthetic fixtures and evidence under --output (outside the repo).
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from alembic import command
from alembic.config import Config
import psycopg2
from psycopg2.extras import Json
from app.engine.canonical import canonical_json, sha256_hex
from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings
from app.core.storage import LocalFsDocumentStore, make_storage_key
from tests.conftest import seed_data
from tests.test_m6_zahlungen import m6_seed
from tests.test_invoice_extraction_worker import pdf_bytes
from tests.test_m5_einvoice import UBL, CII


def embedded_pdf(xml):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [(factur-x.xml) 4 0 R] >> >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << >> >>",
        b"<< /Type /Filespec /F (factur-x.xml) /EF << /F 5 0 R >> >>",
        b"<< /Type /EmbeddedFile /Length " + str(len(xml)).encode() + b" >>\nstream\n" + xml + b"\nendstream",
    ]
    data, offsets = b"%PDF-1.7\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return data + f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-disposable", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error("Evidence must be outside the repository")
    _, _, url = require_disposable_database_settings(settings)
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["hv_declaration_test_database"] = True
    command.downgrade(config, "base")
    command.upgrade(config, "0028")
    seed = m6_seed.__wrapped__(seed_data.__wrapped__(None))
    output.mkdir(parents=True, exist_ok=True)
    source = pdf_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    source_key = make_storage_key(str(seed["org_a_id"]), source_hash)
    LocalFsDocumentStore(settings.document_storage_root).put(source_key, source, content_type="application/pdf")
    legacy_run, legacy_doc, legacy_draft = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    payload = {"legacy_fixture": True, "amount": "1200.00"}
    payload_hash = sha256_hex(canonical_json(payload))
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO settlement_run (id,organization_id,property_id,run_type,period_start,period_end,advance_payments_source,status,engine_version,snapshot_payload,snapshot_sha256,finalized_at) VALUES (%s,%s,%s,'BK_MIETER','2024-01-01','2024-12-31','MANUAL','finalized','legacy',%s,%s,now())",
                    (str(legacy_run), str(seed["org_a_id"]), str(seed["prop_b"]), Json(payload), payload_hash))
        cur.execute("INSERT INTO settlement_run (id,organization_id,property_id,run_type,period_start,period_end,advance_payments_total,advance_payments_source,status,engine_version) VALUES (%s,%s,%s,'BK_MIETER','2024-01-01','2024-12-31',1200,'MANUAL','draft','legacy')",
                    (str(legacy_draft), str(seed["org_a_id"]), str(seed["prop_b"])))
        cur.execute("INSERT INTO invoice_document (id,organization_id,cost_entry_id,storage_uri,storage_backend,mime_type,content_byte_size,content_sha256,chain_root_id,released_at,extraction_payload,original_filename) VALUES (%s,%s,%s,%s,'LOCAL_FS','application/pdf',%s,%s,%s,now(),%s,'legacy.pdf')",
                    (str(legacy_doc), str(seed["org_a_id"]), str(seed["ce_grundsteuer_b"]), source_key, len(source), source_hash, str(legacy_doc), Json({"legacy": True})))
        before = {}
        for table in ("cost_entry", "payment", "settlement_run", "invoice_document"):
            cur.execute(f"SELECT count(*) FROM {table}")
            before[table] = cur.fetchone()[0]
        cur.execute("SELECT coalesce(sum(gross_amount),0)::text FROM cost_entry")
        cost_sum = cur.fetchone()[0]
        cur.execute("SELECT coalesce(sum(amount),0)::text FROM payment")
        payment_sum = cur.fetchone()[0]
    command.upgrade(config, "head")
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        for table, count in before.items():
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == count, table
        cur.execute("SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=%s", (str(legacy_run),))
        assert cur.fetchone() == (payload, payload_hash)
        cur.execute("SELECT content_sha256,extraction_payload,processing_status FROM invoice_document WHERE id=%s", (str(legacy_doc),))
        assert cur.fetchone() == (source_hash, {"legacy": True}, "legacy")
        cur.execute("SELECT count(*) FROM coverage_declaration")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT advance_payments_total::text FROM settlement_run WHERE id=%s", (str(legacy_draft),))
        assert cur.fetchone()[0] == "1200.00"
        cur.execute("SELECT coalesce(sum(gross_amount),0)::text FROM cost_entry")
        assert cur.fetchone()[0] == cost_sum
        cur.execute("SELECT coalesce(sum(amount),0)::text FROM payment")
        assert cur.fetchone()[0] == payment_sum
    assert hashlib.sha256(LocalFsDocumentStore(settings.document_storage_root).get(source_key)).hexdigest() == source_hash
    (output / "text.pdf").write_bytes(source)
    (output / "invoice.xml").write_bytes(UBL)
    (output / "embedded.pdf").write_bytes(embedded_pdf(CII))
    from PIL import Image, ImageDraw, ImageFont
    import reportlab
    image = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(Path(reportlab.__file__).parent / "fonts/Vera.ttf"), 36)
    for y, line in enumerate(["Stadtwerke Test GmbH", "Rechnungsdatum: 01.06.2025", "Gesamtbetrag: 119,00 EUR"]):
        draw.text((100, 150+y*90), line, fill="black", font=font)
    image.save(output / "scan.pdf", format="PDF", resolution=200)
    (output / "encrypted.pdf").write_bytes(pdf_bytes(encrypted=True))
    manifest = {key: str(value) for key, value in seed.items()}
    manifest.update(legacy_run=str(legacy_run), legacy_document=str(legacy_doc))
    (output / "fixture.json").write_text(json.dumps(manifest, indent=2))
    (output / "migration.json").write_text(json.dumps({"from": "0028", "to": "0031", "counts_preserved": before,
        "legacy_snapshot_hash_preserved": payload_hash, "released_source_hash_preserved": source_hash,
        "cost_sum_preserved": cost_sum, "payment_sum_preserved": payment_sum, "legacy_extraction_preserved": True, "numeric_drafts_not_backfilled": True}, indent=2))
    print(json.dumps({"output": str(output), "migration": "passed", "organization": str(seed["org_a_id"])}))


if __name__ == "__main__":
    main()
