"""Portable archive handling, invoked by self-host.sh inside the app image.

The checkout supplies this source via python -c; the selected image must provide
the app.deployment document helpers. No host Python is needed. Older images
without TARGET use the checkout release target with a warning.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile

try:
    from app.deployment import archive_documents, restore_documents, verify_documents
except ImportError:
    print('image predates backup support; update the image first', file=sys.stderr)
    raise SystemExit(1) from None


class ArchiveError(ValueError):
    """A public diagnostic containing only fixed, non-sensitive text."""


def pack(work: Path, documents: Path, schema: str, image: str, fingerprint: str) -> None:
    if not re.fullmatch(r'[0-9]{4}', schema):
        raise ArchiveError('missing or ambiguous database schema head')
    with (work / 'documents.tar.gz').open('wb') as stream:
        archive_documents(documents, stream)
    manifest = {
        'format': 'verwalino-selfhost', 'format_version': 1,
        'schema_head': schema, 'app_image': image,
        'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'config_fingerprint': fingerprint,
    }
    (work / 'manifest.json').write_text(json.dumps(manifest) + '\n')
    with tarfile.open(work / 'archive.tar', 'w') as archive:
        for name in ('db.dump', 'documents.tar.gz', 'manifest.json'):
            archive.add(work / name, arcname=name, recursive=False)


def verify(work: Path, target: str, fingerprint: str) -> dict:
    with tarfile.open(work / 'archive.tar', 'r:*') as archive:
        members = archive.getmembers()
        expected = {'db.dump', 'documents.tar.gz', 'manifest.json'}
        if (len(members) != 3 or {m.name for m in members} != expected
            or not all(m.isfile() and m.size > 0 for m in members)):
            raise ArchiveError('archive requires manifest.json, db.dump and documents.tar.gz as regular files')
        # Read only a small JSON manifest; never extract member paths from tar.
        if archive.getmember('manifest.json').size > 16384:
            raise ArchiveError('invalid manifest')
        manifest = json.load(archive.extractfile('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('format') != 'verwalino-selfhost':
            raise ArchiveError('missing or foreign manifest')
        if type(manifest.get('format_version')) is not int or manifest['format_version'] != 1:
            raise ArchiveError('unknown archive format version')
        schema = manifest.get('schema_head')
        if not isinstance(schema, str) or not re.fullmatch(r'[0-9]{4}', schema):
            raise ArchiveError('unknown or ambiguous archive schema head')
        if not re.fullmatch(r'[0-9]{4}', target):
            raise ArchiveError('unknown image release target')
        if int(schema) > int(target):
            raise ArchiveError('archive schema is newer than the selected image release target')
        if (not isinstance(manifest.get('app_image'), str) or not manifest['app_image']
            or not isinstance(manifest.get('created_at'), str)
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', manifest['created_at'])
            or not isinstance(manifest.get('config_fingerprint'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', manifest['config_fingerprint'])):
            raise ArchiveError('invalid manifest fields')
        for name in ('db.dump', 'documents.tar.gz'):
            with archive.extractfile(name) as source, (work / name).open('xb') as output:
                shutil.copyfileobj(source, output)
    with (work / 'db.dump').open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise ArchiveError('db.dump is not a PostgreSQL custom-format dump')
    with (work / 'documents.tar.gz').open('rb') as stream:
        verify_documents(stream)
    if manifest['config_fingerprint'] != fingerprint:
        print('Warning: configuration fingerprint differs; check the installation settings.', file=sys.stderr)
    return manifest


def stage_documents(work: Path, documents: Path) -> None:
    # Extract and set ownership before the database cutover, on the same volume.
    if documents.is_symlink() or not documents.is_dir():
        raise ArchiveError('invalid document root')
    temporary = Path(tempfile.mkdtemp(prefix='.restore-', dir=documents))
    (work / 'documents-stage').write_text(temporary.name)
    try:
        staged = temporary / 'documents'
        with (work / 'documents.tar.gz').open('rb') as stream:
            restore_documents(stream, staged)
        for path in staged.rglob('*'):
            os.chown(path, 10001, 10001)
        os.chown(staged, 10001, 10001)
        # Check the root ownership operation while the old contents are intact.
        os.chown(documents, 10001, 10001)
    except Exception:
        discard_documents(work, documents)
        raise


def discard_documents(work: Path, documents: Path) -> None:
    marker = work / 'documents-stage'
    if marker.exists():
        temporary = documents / marker.read_text()
        shutil.rmtree(temporary)
        marker.unlink()


def swap_documents(work: Path, documents: Path) -> None:
    temporary = documents / (work / 'documents-stage').read_text()
    staged = temporary / 'documents'
    # Persist progress before the first destructive filesystem operation so the
    # shell can describe partial replacement even if this helper is interrupted.
    (work / 'documents-state').write_text('partially swapped')
    for path in documents.iterdir():
        if path == temporary:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    for path in staged.iterdir():
        path.rename(documents / path.name)
    (work / 'documents-state').write_text('swapped')
    discard_documents(work, documents)


def replace_documents(work: Path, documents: Path) -> None:
    stage_documents(work, documents)
    swap_documents(work, documents)


def image_target(fallback: str) -> str:
    try:
        from app.deployment import TARGET
    except ImportError:
        print('Warning: image predates app.deployment.TARGET; using checkout release target.', file=sys.stderr)
        return fallback
    return TARGET


def return_to_host(work: Path) -> None:
    owner = os.environ.get('BACKUP_OWNER')
    if owner:
        try:
            uid, gid = map(int, owner.split(':'))
            for path in work.iterdir():
                os.chown(path, uid, gid)
        except OSError:
            print('Warning: cannot change backup ownership; copying may be required on this Docker host.', file=sys.stderr)


def main() -> int:
    os.umask(0o077)
    action, *arguments = sys.argv[1:]
    work, documents = map(Path, arguments[-2:])
    try:
        if action == 'pack':
            pack(work, documents, *arguments[:-2])
        elif action == 'target':
            print(image_target(*arguments[:-2]))
        elif action == 'verify':
            print(verify(work, *arguments[:-2])['schema_head'])
        elif action == 'stage-documents':
            stage_documents(work, documents)
        elif action == 'swap-documents':
            swap_documents(work, documents)
        elif action == 'discard-documents':
            discard_documents(work, documents)
        elif action == 'document-state':
            state = work / 'documents-state'
            print(state.read_text() if state.exists() else 'not swapped')
        elif action == 'restore-documents':
            replace_documents(work, documents)
        else:
            raise ArchiveError('unknown archive action')
        return_to_host(work)
    except ArchiveError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ValueError, OSError, tarfile.TarError, EOFError):
        # Exception paths can contain document names or source content.
        print('Archive operation failed: ' + action + '.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
