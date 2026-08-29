"""Mandate document tests -- upload / list / download / delete + RBAC + RLS.

Documents are attached to a management mandate (Verwaltervertrag, Bankkonto-
Unterlagen, …). They reuse the content-addressed document store. Unlike GoBD
invoice documents they are deletable.
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


@pytest.fixture(scope="session")
def mandate_doc_seed(seed_data):
    import psycopg2

    org_a = str(seed_data["org_a_id"])
    org_b = str(seed_data["org_b_id"])
    ids = {"mandate_a": uuid.uuid4(), "mandate_b": uuid.uuid4()}

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO management_mandate (id, organization_id, mandate_type, label, start_date) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(ids["mandate_a"]), org_a, "WEG", "Doc-Test WEG", date(2024, 1, 1)),
        )
        cur.execute(
            "INSERT INTO management_mandate (id, organization_id, mandate_type, label, start_date) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(ids["mandate_b"]), org_b, "MIET", "Doc-Test B", date(2024, 1, 1)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: str(v) for k, v in ids.items()}}


def _pdf(name: str, content: bytes, doc_type: str, title: str = ""):
    return {
        "files": {"file": (name, content, "application/pdf")},
        "data": {"doc_type": doc_type, "title": title},
    }


class TestMandateDocuments:
    @pytest.mark.asyncio
    async def test_multipart_upload_over_limit_is_rejected(
        self, client_a_admin, mandate_doc_seed, monkeypatch
    ):
        import app.api.mandates as mandate_api

        monkeypatch.setattr(mandate_api, "MAX_UPLOAD_BYTES", 4, raising=False)
        response = await client_a_admin.post(
            f"/api/mandates/{mandate_doc_seed['mandate_a']}/documents",
            **_pdf("too-large.pdf", b"abcde", "SONSTIGES"),
        )

        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_upload_and_list(self, client_a_admin, mandate_doc_seed):
        mid = mandate_doc_seed["mandate_a"]
        up = _pdf("verwaltervertrag.pdf", b"%PDF-1.4 contract A", "VERWALTERVERTRAG", "Verwaltervertrag 2024")
        r = await client_a_admin.post(f"/api/mandates/{mid}/documents", **up)
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["doc_type"] == "VERWALTERVERTRAG"
        assert d["original_filename"] == "verwaltervertrag.pdf"
        assert d["title"] == "Verwaltervertrag 2024"
        assert d["content_byte_size"] > 0
        doc_id = d["id"]

        r = await client_a_admin.get(f"/api/mandates/{mid}/documents")
        assert r.status_code == 200
        assert any(x["id"] == doc_id for x in r.json())

    @pytest.mark.asyncio
    async def test_download_roundtrip(self, client_a_admin, mandate_doc_seed):
        mid = mandate_doc_seed["mandate_a"]
        content = b"%PDF-1.4 bankvertrag-eindeutige-bytes-123"
        r = await client_a_admin.post(
            f"/api/mandates/{mid}/documents", **_pdf("bank.pdf", content, "BANKKONTO")
        )
        assert r.status_code == 201
        doc_id = r.json()["id"]

        r = await client_a_admin.get(f"/api/mandates/{mid}/documents/{doc_id}/download")
        assert r.status_code == 200
        assert r.content == content
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-length"] == str(len(content))
        assert r.headers["content-disposition"].startswith('attachment; filename="bank.pdf"')

    @pytest.mark.asyncio
    async def test_invalid_doc_type_rejected(self, client_a_admin, mandate_doc_seed):
        mid = mandate_doc_seed["mandate_a"]
        r = await client_a_admin.post(
            f"/api/mandates/{mid}/documents", **_pdf("x.pdf", b"abc", "BOGUS")
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_to_unknown_mandate_404(self, client_a_admin, mandate_doc_seed):
        r = await client_a_admin.post(
            f"/api/mandates/{uuid.uuid4()}/documents",
            **_pdf("x.pdf", b"abc", "SONSTIGES"),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_delete(self, client_a_admin, mandate_doc_seed):
        mid = mandate_doc_seed["mandate_a"]
        r = await client_a_admin.post(
            f"/api/mandates/{mid}/documents",
            **_pdf("weg.pdf", b"%PDF to delete", "SONSTIGES"),
        )
        doc_id = r.json()["id"]
        r = await client_a_admin.delete(f"/api/mandates/{mid}/documents/{doc_id}")
        assert r.status_code == 204
        r = await client_a_admin.get(f"/api/mandates/{mid}/documents/{doc_id}/download")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_readonly_cannot_upload(self, client_a_readonly, mandate_doc_seed):
        mid = mandate_doc_seed["mandate_a"]
        r = await client_a_readonly.post(
            f"/api/mandates/{mid}/documents", **_pdf("x.pdf", b"abc", "SONSTIGES")
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_can_list(self, client_a_readonly, mandate_doc_seed):
        mid = mandate_doc_seed["mandate_a"]
        r = await client_a_readonly.get(f"/api/mandates/{mid}/documents")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_cross_org_mandate_not_visible(self, client_b_admin, mandate_doc_seed):
        # org B cannot see / use org A's mandate -> 404 (RLS fail-closed).
        mid = mandate_doc_seed["mandate_a"]
        r = await client_b_admin.get(f"/api/mandates/{mid}/documents")
        assert r.status_code == 404
