"""Organization archive steps use HTTP for every product operation."""
import hashlib
import io
import json
import tarfile

from pytest_bdd import given, when, then

from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401


@given("a separate organization has rental records and an original for export")
def export_organization(world, rent_world, monkeypatch, tmp_path):
    from app.core import storage
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path / "originals"))
    source = api_client(rent_world)
    world.actor = source
    world.data["export_source"] = rent_world
    original = b"%PDF-1.4\n% Original for the portable archive\n%%EOF\n"
    answer = world.act("POST", "/api/attachments", data={"property_id": str(rent_world["property"])},
                       files={"file": ("Original.pdf", original, "application/pdf")})
    assert answer.status_code == 201, world.describe_response()
    world.data["export_original"] = original
    world.data["export_document_id"] = answer.json()["id"]


@when("its administrator downloads the organization archive")
def download_archive(world):
    answer = world.act("POST", "/api/organizations/export")
    assert answer.status_code == 200, world.describe_response()
    with tarfile.open(fileobj=io.BytesIO(answer.content), mode="r:gz") as archive:
        world.data["export_files"] = {member.name: archive.extractfile(member).read() for member in archive}
    world.data["export_id"] = answer.headers["X-Export-ID"]


@then("the archive contains its rental records and the unchanged original")
def archive_has_records(world):
    files, source = world.data["export_files"], world.data["export_source"]
    manifest = json.loads(files["manifest.json"])
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest
    for entity, key in (("property", "property"), ("person", "person"), ("lease_agreement", "lease")):
        rows = [json.loads(line) for line in files[f"entities/{entity}.jsonl"].splitlines()]
        assert str(source[key]) in {row["id"] for row in rows}
    assert files[f"documents/{world.data['export_document_id']}"] == world.data["export_original"]


@then("the archive contains no credentials or data of the other organization")
def archive_is_private(world):
    files, source = world.data["export_files"], world.data["export_source"]
    content = b"".join(files.values())
    assert str(source["foreign_org"]).encode() not in content
    assert str(source["foreign_person"]).encode() not in content
    assert b"unused-synthetic-password" not in content
    assert all("password_hash" not in row for row in
               [json.loads(line) for line in files["entities/user.jsonl"].splitlines()])
    async def refuse_foreign():
        async with api_client(source, foreign=True) as foreign:
            return await foreign.get(f"/api/organizations/export/{world.data['export_id']}")
    assert world.run(refuse_foreign()).status_code == 404
    world.run(world.actor.aclose())
