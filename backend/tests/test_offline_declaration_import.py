"""The offline entry the user check of a pull request runs against an acceptance instance (issue #499).

It processes the queued imports of one organization with the offline providers and takes both model ids from the
organization's saved settings; the command line is run here exactly as by hand (a subprocess on the test database).
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import text

from app.core import storage
from app.core.storage import LocalFsDocumentStore
from tests.ai_settings_support import configure_ai

ROOT = Path(__file__).resolve().parents[2]
SCAN = ROOT / "docs/acceptance/data/teilungserklaerung-lindenallee-12.pdf"


async def _queue_scan(client, documents: Path, monkeypatch) -> tuple[str, str]:
    monkeypatch.setattr(storage, "_store", LocalFsDocumentStore(documents))
    created = await client.post("/api/declaration-imports", json={"mode": "NEW_PROPERTY"})
    import_id = created.json()["id"]
    uploaded = await client.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("teilung.pdf", SCAN.read_bytes(), "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert uploaded.status_code == 201, uploaded.text
    started = await client.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert started.status_code == 200 and started.json()["status"] == "QUEUED", started.text
    return import_id, uploaded.json()["id"]


async def test_the_command_line_entry_processes_the_queued_import_with_the_models_saved_for_the_organization(
    seed_data, client_a_admin, migrator_session, tmp_path, monkeypatch
) -> None:
    organization = seed_data["org_a_id"]
    # Two models that no constant of the entry knows: they can only come from the saved settings.
    configure_ai(organization, model="saved-extraction-model", document_model="saved-reading-model", import_enabled=True)
    import_id, document_id = await _queue_scan(client_a_admin, tmp_path, monkeypatch)
    name = await migrator_session.scalar(text("SELECT name FROM organization WHERE id=:id"), {"id": organization})

    done = subprocess.run(
        [sys.executable, "-m", "tests.offline_declaration_import", "--organization", name, "--documents", str(tmp_path)],
        cwd=ROOT / "backend", capture_output=True, text=True, timeout=240, check=False,
    )

    lines = done.stdout.splitlines()
    # Other tests of the session may have left queued imports of this organization; they are processed too.
    states = [line.rsplit(": ", 1)[1] for line in lines if line.startswith("import ")]
    assert done.returncode == (0 if set(states) == {"NEEDS_REVIEW"} else 1), done.stdout + done.stderr
    assert f"import {import_id}: NEEDS_REVIEW" in lines
    assert "  models from the saved settings: reading saved-reading-model, extraction saved-extraction-model" in lines
    # One page is read by the reading model; every extraction call names the extraction model and that page.
    assert f"  reading     model=saved-reading-model document={document_id} page=1" in lines
    extraction = [line for line in lines if line.startswith("  extraction ")]
    assert extraction and all(" model=saved-extraction-model " in line for line in extraction)
    assert any(f" call=inventory pages={document_id}:1" in line for line in extraction)
    assert "saved-reading-model" not in "".join(extraction) and "saved-extraction-model" not in "".join(
        line for line in lines if line.startswith("  reading "))
    assert lines[-1] == "worker results: " + ", ".join(["PROCESSED"] * len(states) + ["IDLE"])
    state = await client_a_admin.get(f"/api/declaration-imports/{import_id}")
    assert state.json()["status"] == "NEEDS_REVIEW"
    assert [(stage["stage"], stage["model"]) for stage in state.json()["runs"][0]["stages"]] == [
        ("READING", "saved-reading-model"), ("EXTRACTION", "saved-extraction-model")]


async def test_the_entry_leaves_an_import_alone_when_the_reading_model_is_not_saved(
    seed_data, client_a_admin, migrator_session, tmp_path, monkeypatch
) -> None:
    organization = seed_data["org_a_id"]
    configure_ai(organization, model="saved-extraction-model", document_model="saved-reading-model", import_enabled=True)
    import_id, _document = await _queue_scan(client_a_admin, tmp_path, monkeypatch)
    # The reading model is cleared after the import was queued: the worker's own readiness decision applies.
    await migrator_session.execute(text("UPDATE organization_ai_settings SET document_model=NULL WHERE organization_id=:id"), {"id": organization})
    await migrator_session.commit()

    from tests.offline_declaration_import import process_queued_imports
    run = await process_queued_imports(organization, store=LocalFsDocumentStore(tmp_path))

    assert [result.code for result in run.results] == ["DECLARATION_IMPORT_DISABLED"] and run.imports == []
    assert (await client_a_admin.get(f"/api/declaration-imports/{import_id}")).json()["status"] == "QUEUED"
