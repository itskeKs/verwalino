"""Archive decoding remains bounded before any domain transaction writes."""
import hashlib
import io
import json
import tarfile
import uuid

import pytest
from fastapi import HTTPException

from app.deployment import TARGET
from app.services import organization_import_service as service
from app.services.organization_archive import FORMAT


def archive_bytes(*, rows=None, extra=None):
    identity = str(uuid.uuid4())
    entities = {'organization': [{'id': identity, 'name': 'Synthetic'}], **(rows or {})}
    files = {f'entities/{kind}.jsonl': b''.join(json.dumps(row).encode()+b'\n' for row in values)
             for kind, values in entities.items()}
    files.update(extra or {})
    manifest = {'format': FORMAT, 'version': 2, 'schema_head': TARGET,
                'organization': {'id': identity, 'name': 'Synthetic'}, 'entity_order': list(entities),
                'row_counts': {kind: len(values) for kind, values in entities.items()}, 'documents': [],
                'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files['manifest.json'] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    output.seek(0)
    return output


def test_plain_export_headers_remain_importable():
    with service.read_archive(archive_bytes()) as (_, entities, _members):
        assert entities['organization'][0]['name'] == 'Synthetic'


def test_hashed_entity_omitted_from_entity_order_is_refused():
    with pytest.raises(HTTPException) as error:
        with service.read_archive(archive_bytes(extra={'entities/person.jsonl': b'{"last_name":"Must not disappear"}\n'})):
            pytest.fail('Unlisted entity was silently skipped')
    assert error.value.status_code == 422


def test_member_limit_refuses_before_extended_spooling(monkeypatch):
    monkeypatch.setattr(service, 'MAX_MEMBERS', 2)
    with pytest.raises(HTTPException) as error:
        with service.read_archive(archive_bytes(extra={'README.md': b'one'})):
            pytest.fail('Expected refusal')
    assert 'zu viele Dateien' in error.value.detail['message']


def test_row_limit_is_checked_during_line_decoding(monkeypatch):
    monkeypatch.setattr(service, 'MAX_ROWS', 2)
    with pytest.raises(HTTPException) as error:
        with service.read_archive(archive_bytes(rows={'person': [{}, {}]})):
            pytest.fail('Expected refusal')
    assert 'zu viele Datensätze' in error.value.detail['message']


def test_decoded_metadata_budget_refuses_object_expansion(monkeypatch):
    monkeypatch.setattr(service, 'MAX_DECODED_METADATA_BYTES', 1)
    with pytest.raises(HTTPException) as error:
        with service.read_archive(archive_bytes()):
            pytest.fail('Expected refusal')
    assert 'Arbeitsspeichergrenze' in error.value.detail['message']


def test_row_size_is_refused_before_json_decoding(monkeypatch):
    monkeypatch.setattr(service, 'MAX_JSON_ROW_BYTES', 1024)
    with pytest.raises(HTTPException) as error:
        with service.read_archive(archive_bytes(rows={'person': [{'last_name': 'x' * 4096}]})):
            pytest.fail('Expected refusal')
    assert 'Datenzeile' in error.value.detail['message']


def test_extended_headers_are_refused_before_their_payload_is_read():
    with pytest.raises(HTTPException) as error:
        with service.read_archive(archive_bytes(extra={'x' * 200: b'extension'})):
            pytest.fail('Expected refusal')
    assert 'unzulässige Dateieinträge' in error.value.detail['message']


def test_gzip_name_extensions_are_refused_before_scanning_them():
    original = archive_bytes().getvalue()
    extended = original[:3] + bytes([8]) + original[4:10] + b'x' * 1024 + b'\0' + original[10:]
    with pytest.raises(HTTPException) as error:
        with service.read_archive(io.BytesIO(extended)):
            pytest.fail('Expected refusal')
    assert 'Komprimierungskopf' in error.value.detail['message']
