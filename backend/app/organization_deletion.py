"""Operator-only organization-deletion-run; never exposed through HTTP."""
import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.services.organization_deletion_service import erase_unretained_files, run_deletion


async def run():
    engine = create_async_engine(settings.database_url_migrator)
    completed = 0
    try:
        async with AsyncSession(engine) as db:
            async with db.begin():
                if await db.scalar(text('SELECT session_user')) != 'app_migrator':
                    raise RuntimeError('Operator database role required')
                organizations = (await db.execute(text('''SELECT organization_id FROM organization_deletion
                  WHERE status IN ('cooling_off','deleted_pending_retention') ORDER BY organization_id'''))).scalars().all()
            for org in organizations:
                async with db.begin():
                    await db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(org)})
                    completed += bool(await run_deletion(db, org))
                async with db.begin():
                    state = (await db.execute(text('SELECT status,retention FROM organization_deletion WHERE organization_id=:org'), {'org': org})).one()
                    if state.status == 'deleted_pending_retention':
                        # Include mail originals in retained audit payloads too.
                        uris = set((await db.execute(text('''SELECT payload_diff->>'storage_uri' FROM audit_event
                          WHERE organization_id=:org AND payload_diff->>'storage_uri' IS NOT NULL'''), {'org': org})).scalars())
                        tables = (await db.execute(text('''SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                          WHERE n.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='storage_uri')
                          AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='organization_id')'''))).scalars()
                        for name in tables:
                            uris.update((await db.execute(text(f'SELECT storage_uri FROM "{name}" WHERE organization_id=:org AND storage_uri IS NOT NULL'), {'org': org})).scalars())
                        erase_unretained_files(org, uris)
        return completed
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['organization-deletion-run'])
    parser.parse_args()
    print(f'Organizations erased: {asyncio.run(run())}')


if __name__ == '__main__':
    main()
