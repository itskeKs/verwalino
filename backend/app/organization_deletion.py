"""Operator-only organization-deletion-run; never exposed through HTTP."""
import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.locking import bound_lock_waits, is_lock_timeout
from app.services.organization_deletion_service import ErasureFileCleanupError, erase_unretained_files, run_deletion

logger = logging.getLogger(__name__)


@dataclass
class ErasureResult:
    completed: int = 0
    failed: list[UUID] = field(default_factory=list)
    cleanup_pending: list[UUID] = field(default_factory=list)


async def run(*, organization_ids=None):
    engine = create_async_engine(settings.database_url_migrator)
    result = ErasureResult()
    try:
        async with AsyncSession(engine) as db:
            async with db.begin():
                if await db.scalar(text('SELECT session_user')) != 'app_migrator':
                    raise RuntimeError('Operator database role required')
                organizations = (await db.execute(text('''SELECT organization_id FROM organization_deletion
                  WHERE status IN ('cooling_off','deleted_pending_retention')
                  AND (:all_orgs OR organization_id=ANY(CAST(:orgs AS uuid[])))
                  ORDER BY organization_id'''),
                  {'all_orgs': organization_ids is None, 'orgs': list(organization_ids or [])})).scalars().all()
        for org in organizations:
            erased = False
            try:
                # A fresh session also isolates a failed after-commit file
                # cleanup; neither failure can poison the next organization.
                async with AsyncSession(engine) as db:
                    async with db.begin():
                        await db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(org)})
                        erased = await run_deletion(db, org, nowait=True)
            except ErasureFileCleanupError:
                result.completed += bool(erased)
                result.cleanup_pending.append(org)
                logger.exception('Organization erased, file cleanup pending; retry on the next run: %s', org)
                continue
            except Exception as error:
                result.failed.append(org)
                if is_lock_timeout(error):
                    logger.warning('Organization lock held; retry on the next run: %s', org)
                else:
                    logger.exception('Organization erasure failed; retry on the next run: %s', org)
                continue
            result.completed += bool(erased)
            try:
                async with AsyncSession(engine) as db:
                    async with db.begin():
                        await bound_lock_waits(db)
                        await retry_file_cleanup(db, org)
            except Exception:
                result.cleanup_pending.append(org)
                logger.exception('Organization erased, file cleanup pending; retry on the next run: %s', org)
        return result
    finally:
        await engine.dispose()


async def retry_file_cleanup(db, org):
    state = (await db.execute(text('SELECT status FROM organization_deletion WHERE organization_id=:org'), {'org': org})).scalar_one()
    if state == 'deleted_pending_retention':
        # Include mail originals in retained audit payloads too.
        uris = set((await db.execute(text('''SELECT payload_diff->>'storage_uri' FROM audit_event
          WHERE organization_id=:org AND payload_diff->>'storage_uri' IS NOT NULL'''), {'org': org})).scalars())
        tables = (await db.execute(text('''SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='storage_uri')
          AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='organization_id')'''))).scalars()
        for name in tables:
            uris.update((await db.execute(text(f'SELECT storage_uri FROM "{name}" WHERE organization_id=:org AND storage_uri IS NOT NULL'), {'org': org})).scalars())
        erase_unretained_files(org, uris)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['organization-deletion-run'])
    parser.parse_args()
    result = asyncio.run(run())
    print(f'Organizations erased: {result.completed}')
    if result.failed:
        print('Organizations failed: ' + ', '.join(map(str, result.failed)), file=sys.stderr)
    if result.cleanup_pending:
        print('Organizations erased, file cleanup pending/retry: ' + ', '.join(map(str, result.cleanup_pending)), file=sys.stderr)
    if result.failed or result.cleanup_pending:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
