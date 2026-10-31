"""Organization archive steps use HTTP for every product operation."""
import hashlib
import io
import json
import tarfile

import pytest
from pytest_bdd import given, when, then

from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_organization_export import export_store  # noqa: F401


@pytest.fixture
def export_client(world, rent_world, export_store):
    source = api_client(rent_world)
    try:
        yield source
    finally:
        world.run(source.aclose())


@given("a separate organization has rental records and an original for export")
def export_organization(world, rent_world, export_client):
    world.actor = export_client
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


@pytest.fixture
async def empty_import_organization(rent_db, rent_world):
    from tests.test_organization_import import import_target
    # Reuse the same fixture implementation; all product operations use HTTP.
    return await import_target.__wrapped__(rent_db, rent_world)


@when('the archive is imported into an empty organization')
def import_archive(world, empty_import_organization):
    from tests.test_organization_import import repack
    async def restore():
        async with api_client(empty_import_organization) as target:
            return await target.post('/api/organizations/import', files={'file': ('organization.tar.gz', repack(world.data['export_files']))})
    answer = world.run(restore())
    assert answer.status_code == 200, answer.text
    world.data['import_result'] = answer.json()
    world.data['import_target'] = empty_import_organization


@then('the imported organization has equal entity counts and normalized content')
def import_equals_source(world):
    from app.services.organization_archive import json_bytes
    source = world.data['export_files']
    async def export_target():
        async with api_client(world.data['import_target']) as target:
            return await target.post('/api/organizations/export')
    answer = world.run(export_target())
    assert answer.status_code == 200, answer.text
    from tests.test_organization_export import unpack
    target = unpack(answer.content)
    # Account credentials/custody and operation audit are deliberately local.
    # Every domain entity is compared after reversing its transfer identity map.
    mapping = world.data['import_result']['id_map']
    from app.services.organization_import_service import remap
    reverse = {v: k for values in mapping.values() for k, v in values.items()}
    for name, count in json.loads(source['manifest.json'])['row_counts'].items():
        actual_count = json.loads(target['manifest.json'])['row_counts'][name]
        assert actual_count == count + (1 if name == 'audit_event' else 0), name
        if name in {'user', 'membership', 'audit_event'}:
            continue
        original = [json.loads(line) for line in source[f'entities/{name}.jsonl'].splitlines()]
        restored = [remap(json.loads(line), reverse) for line in target[f'entities/{name}.jsonl'].splitlines()]
        assert len(restored) == count, name
        def normalize(rows):
            return sorted(json_bytes({k: v for k, v in row.items() if k not in {'created_by', 'storage_uri', 'registered_at'}}) for row in rows)
        assert hashlib.sha256(b''.join(normalize(original))).digest() == hashlib.sha256(b''.join(normalize(restored))).digest(), name


@when('a required person is removed from the archive before import')
def missing_person(world, empty_import_organization):
    from tests.test_organization_import import repack
    files = dict(world.data['export_files'])
    files['entities/person.jsonl'] = b''
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['person'] = 0
    files['manifest.json'] = json.dumps(manifest).encode()
    async def restore():
        async with api_client(empty_import_organization) as target:
            return await target.post('/api/organizations/import', files={'file': ('organization.tar.gz', repack(files))})
    world.data['missing_import'] = world.run(restore())
    world.data['import_target'] = empty_import_organization


@then('the import lists missing dependencies and writes nothing')
def missing_dependency_refusal(world):
    answer = world.data['missing_import']
    assert answer.status_code == 422, answer.text
    assert any(item['entity'] == 'person' and item['count'] == 2 for item in answer.json()['detail']['missing_dependencies'])
    async def properties():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/properties')
    assert world.run(properties()).json() == []
