"""Read-only deployment gates and paired document backup/restore primitives.

No migration, downgrade, role provisioning or database restoration is performed
here. CLI errors deliberately exclude driver/URL/SQL details and source data.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

TARGET = '0143'
SUPPORTED = ('0028', '0122', '0139', '0140', '0141', '0142', TARGET)


def release_head(graph: ScriptDirectory) -> str:
    if graph.get_heads() != [TARGET]:
        raise ValueError('unsupported release head')
    revisions = list(graph.iterate_revisions(TARGET, '0028'))
    if [(r.revision, r.down_revision) for r in revisions] != [
        (f'{n:04}', f'{n - 1:04}') for n in range(int(TARGET), 28, -1)
    ]:
        raise ValueError('unsupported release upgrade graph')
    return TARGET


def supported_schema(heads) -> str:
    if len(heads) != 1 or heads[0] not in SUPPORTED:
        raise ValueError('unsupported or ambiguous database schema')
    return heads[0]


def validate_roles(connection) -> None:
    from app.core.db import REVIEW_ROLE_CHECK

    safe = connection.scalar(text("""SELECT
      current_user='app_migrator' AND session_user=current_user
      AND has_schema_privilege(current_user,'public','CREATE')
      AND (SELECT pg_get_userbyid(datdba)=current_user FROM pg_database WHERE datname=current_database())
      AND (SELECT count(*)=3 FROM pg_roles WHERE rolname IN ('app_rw','app_worker','app_review')
        AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
        AND NOT rolcreatedb AND NOT rolreplication)
      AND (SELECT rolinherit FROM pg_roles WHERE rolname='app_worker')
      AND NOT EXISTS(SELECT 1 FROM pg_auth_members m
        WHERE (m.roleid IN (SELECT oid FROM pg_roles WHERE rolname IN ('app_rw','app_worker','app_review','app_migrator'))
          OR m.member IN (SELECT oid FROM pg_roles WHERE rolname IN ('app_rw','app_worker','app_review','app_migrator')))
        AND NOT (m.roleid=(SELECT oid FROM pg_roles WHERE rolname='app_rw')
          AND m.member=(SELECT oid FROM pg_roles WHERE rolname='app_worker') AND NOT m.admin_option))
      AND pg_has_role('app_worker','app_rw','USAGE')
      AND NOT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname IN ('app_rw','app_worker') AND (
        has_schema_privilege(r.oid,'public','CREATE')
        OR EXISTS(SELECT 1 FROM pg_class WHERE relowner=r.oid)
        OR EXISTS(SELECT 1 FROM pg_proc WHERE proowner=r.oid)
        OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner=r.oid)
        OR EXISTS(SELECT 1 FROM pg_database WHERE datdba=r.oid)))
    """))
    # Evaluate the existing review topology/ACL predicate as the migrator without
    # impersonating a session or granting any privilege to the review role.
    review = REVIEW_ROLE_CHECK.replace(
        "current_user='app_review' AND session_user=current_user", 'true'
    ).replace('r.rolname=current_user', "r.rolname='app_review'")
    if not safe or not connection.scalar(text(review)):
        raise ValueError('unsafe or missing deployment database roles')


def database_check(mode: str) -> str:
    from app.core.config import settings

    release_head(ScriptDirectory(str(Path(__file__).resolve().parents[1] / 'migrations')))
    engine = create_engine(settings.database_url_sync, connect_args={'connect_timeout': 10})
    try:
        with engine.connect() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            schema = supported_schema(MigrationContext.configure(connection).get_current_heads())
            if mode == 'preflight':
                validate_roles(connection)
            if mode == 'quiescent' and connection.scalar(text("""SELECT EXISTS(
                SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                AND pid<>pg_backend_pid() AND backend_type='client backend')""")):
                raise ValueError('database clients remain during maintenance')
            return schema
    finally:
        engine.dispose()


async def runtime_check(role: str) -> None:
    from app.core.db import REVIEW_ROLE_CHECK, get_worker_unscoped_session
    from sqlalchemy.ext.asyncio import create_async_engine

    if role == 'app_worker':
        async with get_worker_unscoped_session():
            return
    key = {'app_rw': 'HV_DATABASE_URL', 'app_review': 'HV_DATABASE_URL_REVIEW'}[role]
    engine = create_async_engine(os.environ[key], connect_args={'timeout': 10})
    try:
        async with engine.connect() as connection:
            valid = await connection.scalar(text(REVIEW_ROLE_CHECK if role == 'app_review' else
                "SELECT current_user='app_rw' AND session_user=current_user"))
            if not valid:
                raise ValueError('invalid runtime database role')
    finally:
        await engine.dispose()


def archive_documents(root: Path, stream) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ValueError('invalid document root')
    with tarfile.open(fileobj=stream, mode='w|gz') as archive:
        # Do not dereference links, sockets or special files, even at nested paths.
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError('unsupported document entry')
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)


def _read_documents(stream, destination: Path | None) -> None:
    names = set()
    files = set()
    directories = set()
    with gzip.GzipFile(fileobj=stream, mode='rb') as compressed:
        with tarfile.open(fileobj=compressed, mode='r|') as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                name = path.as_posix()
                parents = {parent.as_posix() for parent in path.parents if parent.parts}
                if (path.is_absolute() or '..' in path.parts or not path.parts
                    or any(part in ('', '.') for part in member.name.split('/'))
                    or name in names or parents & files or (member.isfile() and name in directories)
                    or not (member.isfile() or member.isdir())):
                    raise ValueError('unsafe document archive')
                names.add(name)
                directories.update(parents)
                (files if member.isfile() else directories).add(name)
                target = destination.joinpath(*path.parts) if destination else None
                if member.isdir():
                    if target:
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError('invalid document archive')
                with source:
                    if target:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        with target.open('xb') as output:
                            shutil.copyfileobj(source, output)
                        target.chmod(0o600)
                    else:
                        while source.read(1024 * 1024):
                            pass
        # Consume the gzip trailer too: a truncated archive must fail verification.
        while compressed.read(1024 * 1024):
            pass


def verify_documents(stream) -> None:
    _read_documents(stream, None)


def restore_documents(stream, destination: Path) -> None:
    if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
        raise ValueError('document restore requires an empty destination')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    _read_documents(stream, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'current', 'quiescent', 'runtime',
                                         'archive-documents', 'verify-documents', 'restore-documents'])
    parser.add_argument('argument', nargs='?')
    args = parser.parse_args()
    try:
        if args.action in ('preflight', 'current', 'quiescent'):
            print(database_check(args.action))
        elif args.action == 'runtime':
            asyncio.run(runtime_check(args.argument))
        elif args.action == 'archive-documents':
            archive_documents(Path(os.environ.get('HV_DOCUMENT_STORAGE_ROOT', '/data/documents')), sys.stdout.buffer)
        elif args.action == 'verify-documents':
            verify_documents(sys.stdin.buffer)
        else:
            if not args.argument:
                raise ValueError('empty restore destination required')
            restore_documents(sys.stdin.buffer, Path(args.argument))
    except Exception:
        print('deployment check failed: ' + args.action + ' (check schema, roles, storage or archive)', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
