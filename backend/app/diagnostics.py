"""Small, explicitly requested diagnostics; no data upload or privileged DB role."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from datetime import datetime, timezone
from importlib.metadata import version
import io
import json
import os
import platform
import re
import sys
import tarfile
from typing import Iterable, Mapping
from uuid import UUID

from app.core.scrub import MAX_SCRUB_CHARS, scrub_text
from app.deployment import TARGET

DEFAULT_LOG_LINES = 500
# Docker supplies at most 2,000 physical lines. Bound stdin/file work as well.
MAX_SOURCE_LINES = 2000
APP_VERSION = version('hausverwaltung-backend')
_LOG_HEADER = re.compile(
    r'^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) '
    r'(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) (?:[\w.]+ )?'
    r'request_id=(?P<id>-|[A-Za-z0-9._-]{8,128})(?: |$)'
)
_EXCEPTION = re.compile(r'^(?:[\w]+\.)*((?:[A-Za-z][A-Za-z0-9_]{0,95})?(?:Error|Exception)|ExceptionGroup|BaseExceptionGroup|SystemExit|KeyboardInterrupt)(?=:|$)')
_REVISION = re.compile(r'(?:[0-9]{4}|[a-f0-9]{12,32})')
_RELEASE = re.compile(r'(?:[a-f0-9]{7,64}|\d{8}T\d{6}Z|v?\d+\.\d+\.\d+(?:[-.][a-z0-9.]+)?)')
_DIGEST = re.compile(r'(?:^|@)(sha256:[a-f0-9]{64})$')
_CONFIG_KEY = re.compile(r'HV_[A-Z][A-Z0-9_]{0,95}')
_SERVICES = frozenset({'app', 'db', 'caddy', 'migrate', 'bootstrap'})
_STATES = frozenset({'running', 'exited', 'created', 'restarting', 'paused', 'removing', 'dead'})
_HEALTH = frozenset({'healthy', 'unhealthy', 'starting', ''})
# Deliberate allowlist: deployment/security switches are operator metadata.
_TENANT_SWITCHES = frozenset({
    'HV_ENABLE_API_DOCS', 'HV_FINANCIAL_COMPUTATION_ENABLED',
    'HV_INVOICE_EXTRACTION_ENABLED', 'HV_DECLARATION_IMPORT_ENABLED', 'HV_DECLARATION_QA_ENABLED',
})


def bundle_filename(now: datetime | None = None) -> str:
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f'verwalino-diagnostics-{instant:%Y%m%dT%H%M%S%fZ}.tar.gz'


def config_shapes(config: Mapping) -> dict:
    """Values never cross this boundary, including non-secret deployment facts."""
    result = {}
    for key, value in config.items():
        if not isinstance(key, str) or not _CONFIG_KEY.fullmatch(key):
            continue
        secret = hasattr(value, 'get_secret_value')
        if secret:
            value = value.get_secret_value()
        if value is None or value == '':
            result[key] = '<unset>'
        elif secret or any(part in key for part in ('SECRET', 'PASSWORD', 'TOKEN', 'DSN', 'KEY')) or (
            '_URL' in key and '@' in str(value)
        ):
            result[key] = '<set>'
        elif isinstance(value, bool):
            result[key] = '<bool>'
        elif isinstance(value, str):
            result[key] = f'<set, {len(value)} chars>'
        else:
            result[key] = '<number>' if isinstance(value, (int, float)) else '<set>'
    return result


def diagnostic_log_lines(lines: Iterable[str], limit: int = DEFAULT_LOG_LINES) -> list[str]:
    """Scrub each line, then keep structural fields only, never arbitrary text.

    Continuation lines may supply an exception class to the preceding record;
    traceback paths, source, SQL, parameters and messages are deliberately lost.
    UUIDs remain opaque; other IDs must also pass personal-data/credential scrubbing.
    """
    records = deque(maxlen=min(max(limit, 0), DEFAULT_LOG_LINES))
    current = None
    for number, raw in enumerate(lines):
        if number >= MAX_SOURCE_LINES:
            break
        raw = raw[:MAX_SCRUB_CHARS]
        clean = scrub_text(raw, error_context=True)
        header = _LOG_HEADER.match(raw)
        if header:
            current = None
            identity = header['id']
            try:
                UUID(identity)
                opaque = True
            except ValueError:
                opaque = False
            if not opaque and scrub_text(identity, error_context=True) != identity:
                continue
            if header['level'] in {'WARNING', 'ERROR', 'CRITICAL'}:
                current = [header['time'], header['level'], f"request_id={header['id']}"]
                records.append(current)
                exception = _EXCEPTION.match(scrub_text(raw[header.end():], error_context=True))
                if exception:
                    current.append(exception[1])
        elif current is not None:
            exception = _EXCEPTION.match(clean)
            if exception:
                current[3:] = [exception[1]]
    return [' '.join(record) for record in records]


def container_states(containers: list | None) -> list | None:
    if containers is None:
        return None
    return [
        {'service': row['Service'],
         'state': row.get('State') if row.get('State') in _STATES else 'unknown',
         'health': row.get('Health') if row.get('Health') in _HEALTH else 'unknown'}
        for row in containers if isinstance(row, dict) and row.get('Service') in _SERVICES
    ]


def database_summary(database: Mapping | None) -> dict:
    from app.models import Base

    database = database or {}
    head = database.get('migration_head')
    count = database.get('organization_count')
    return {
        'status': 'available' if database.get('status') == 'available' else 'unavailable',
        'scope': 'organization' if database.get('scope') == 'organization' else 'installation',
        'migration_head': head if database.get('scope') != 'organization' and isinstance(head, str) and _REVISION.fullmatch(head) else None,
        'organization_count': count if database.get('scope') != 'organization' and type(count) is int and count >= 0 else None,
        'row_counts': {key: value for key, value in database.get('row_counts', {}).items()
                       if key in Base.metadata.tables and type(value) is int and value >= 0},
    }


def build_bundle(*, config: Mapping | None = None, log_lines: Iterable[str] = (),
                 containers: list | None = None, database: Mapping | None = None,
                 release_id: str = '', image: str = '', now: datetime | None = None) -> bytes:
    """Build six fixed members from allowlisted metadata; no I/O or raw snapshots."""
    config = config or {}
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    digest = _DIGEST.search(image)
    manifest = {
        'format_version': 1, 'created_at': instant.isoformat(), 'app_version': APP_VERSION,
        'release_target': TARGET,
        'release_id': release_id if _RELEASE.fullmatch(release_id) else None,
        'image_digest': digest[1] if digest else None,
    }
    from app.core.config import Settings
    switches = {f'HV_{name.upper()}' for name, field in Settings.model_fields.items() if field.annotation is bool}
    environment = {
        'python': platform.python_version(), 'os': platform.system(),
        'config': config_shapes(config),
        'feature_flags': {key: value for key, value in config.items()
                          if key in switches and type(value) is bool},
    }
    if database and database.get('scope') == 'organization':
        label = config.get('HV_ERROR_TRACKING_ENVIRONMENT', '')
        mode = label if label in {'selfhost', 'hosted'} else (
            'hosted' if isinstance(label, str) and label.startswith('hosted-') else None
        )
        environment = {
            'deployment_mode': mode, 'app_version': APP_VERSION,
            'feature_flags': {key: value for key, value in config.items()
                              if key in _TENANT_SWITCHES and type(value) is bool},
        }
    errors = diagnostic_log_lines(log_lines)
    members = {
        'manifest.json': json.dumps(manifest, indent=2),
        'environment.json': json.dumps(environment, indent=2),
        'containers.json': json.dumps(container_states(containers), indent=2),
        'database.json': json.dumps(database_summary(database), indent=2),
        'errors.log': '\n'.join(errors) + ('\n' if errors else ''),
        'README.txt': (
            'Manually attach this archive to your support report. Nothing was sent.\n'
            'Include steps, expected/actual behavior and the displayed request ID separately.\n'
            'Logs contain only time, severity, request ID and exception class (last 500 records).\n'
            'No messages, bodies, source, identities, addresses, credentials, config values,\n'
            'container names/commands/ports, DB rows or organization names are included.\n'
            'API: current-organization logs across live workers on this host; no containers.\n'
            'Buffer: latest 500 overall plus latest 50 per organization; no multi-host aggregation.\n'
            'API schema head is null: app_rw lacks SELECT on alembic_version; no privilege probe.\n'
            'CLI schema head is supplied by the installer via migrator psql.\n'
            'CLI: up to 2000 source lines from Docker, installation-wide numeric counts.\n'
            'Unavailable metadata is null/marked unavailable, never a successful probe.\n'
            'Review before sharing. Request IDs and release identifiers must contain no personal data.\n'
        ),
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, value in members.items():
            data = value.encode('utf-8')
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o600, int(instant.timestamp())
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def runtime_config() -> dict:
    from app.core.config import settings

    # Include effective defaults as well as extra HV_* deployment keys, shapes only.
    return {**{key: value for key, value in os.environ.items() if key.startswith('HV_')},
            **{f'HV_{key.upper()}': getattr(settings, key) for key in type(settings).model_fields}}


async def collect_database(organization_id: UUID | None = None, actor_id: UUID | None = None) -> dict:
    """Read through app_rw and RLS; only numbers and schema revisions leave here."""
    from sqlalchemy import text
    from app.core.db import get_engine
    from app.models import Base

    result = {'status': 'unavailable', 'scope': 'organization' if organization_id else 'installation'}
    try:
        async with get_engine().connect() as connection, connection.begin():
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            await connection.execute(text("SELECT set_config('statement_timeout', '5000', true)"))
            if not await connection.scalar(text("SELECT current_user='app_rw' AND session_user=current_user")):
                return result
            result['migration_head'] = None
            if organization_id is None:
                # Optional for direct CLI callers; the installer supplies the
                # authoritative head separately without changing runtime grants.
                try:
                    async with connection.begin_nested():
                        heads = list((await connection.execute(text('SELECT version_num FROM alembic_version'))).scalars())
                        result['migration_head'] = heads[0] if len(heads) == 1 else None
                except Exception:
                    pass
            await connection.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {'actor': str(actor_id or UUID(int=0))})
            if organization_id is None:
                result['organization_count'] = await connection.scalar(text('SELECT count(*) FROM organization'))
            # OIDs remain valid even if the planner checks privileges before
            # filtering schemas; unqualified pg_tables names need not resolve.
            tables = list((await connection.execute(text("""SELECT c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p') AND has_table_privilege(c.oid, 'SELECT')"""))).scalars())
            # Only known tenant tables; global auth/setup metadata never becomes a row payload.
            tables = sorted(name for name in tables if name in Base.metadata.tables and
                            'organization_id' in Base.metadata.tables[name].c)
            query = ' UNION ALL '.join(f'SELECT \'{name}\' AS name, count(*) AS n FROM "{name}" WHERE organization_id=:org' for name in tables)
            organizations = [organization_id] if organization_id else list((await connection.execute(text('SELECT id FROM organization'))).scalars())
            counts = dict.fromkeys(tables, 0)
            for identity in organizations:
                await connection.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {'org': str(identity)})
                if query:
                    for name, count in await connection.execute(text(query), {'org': identity}):
                        counts[name] += count
            result.update(status='available', row_counts=counts)
    except Exception:
        # Neither driver errors, SQL text, URLs nor exception messages enter the archive.
        pass
    return result


def parse_containers(value: str) -> list:
    """Compose versions emit either a JSON array or one JSON object per line."""
    if not value.strip():
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        return [json.loads(line) for line in value.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--containers-json', default='[]')
    parser.add_argument('--image', default=os.environ.get('HV_IMAGE_DIGEST', ''))
    parser.add_argument('--log-file', help='Read logs from this file instead of stdin')
    parser.add_argument('--schema-head', help='Schema revision read by the installer migrator')
    args = parser.parse_args()
    try:
        source = open(args.log_file, encoding='utf-8', errors='replace') if args.log_file else sys.stdin
        # readline's size bound prevents a single malicious line allocating unbounded memory.
        def lines():
            for _ in range(MAX_SOURCE_LINES):
                line = source.readline(MAX_SCRUB_CHARS + 1)
                if not line:
                    break
                yield line
        try:
            database = asyncio.run(collect_database())
            if args.schema_head is not None:
                database['migration_head'] = args.schema_head
            data = build_bundle(config=runtime_config(), log_lines=lines(),
                                containers=parse_containers(args.containers_json),
                                database=database,
                                release_id=os.environ.get('HV_RELEASE_ID', ''),
                                image=args.image)
        finally:
            if args.log_file:
                source.close()
        sys.stdout.buffer.write(data)
    except Exception:
        print('Diagnostics collection failed; no bundle created.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
