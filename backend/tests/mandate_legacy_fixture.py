"""Seed pre-cutoff legacy rows only inside the guarded disposable test database."""
import importlib
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import MIGRATOR_URL


async def seed_legacy_mandate(ids):
    engine = create_async_engine(MIGRATOR_URL)
    document = uuid.uuid4()
    try:
        async with engine.begin() as db:
            # Transactional table lock/DDL emulates a row already present at the
            # migration cutoff; no runtime connection can exploit this fixture.
            await db.execute(text('LOCK TABLE mandate_document IN ACCESS EXCLUSIVE MODE'))
            await db.execute(text('ALTER TABLE mandate_document DISABLE TRIGGER quarantine_mandate_original'))
            await db.execute(text("""INSERT INTO mandate_document(id,organization_id,management_mandate_id,doc_type,title,original_filename,
                mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,uploaded_by)
                VALUES(:document,:org,:mandate,'SONSTIGES','Hidden legacy original','secret-legacy.pdf',
                'application/pdf',1,repeat('a',64),'synthetic/unverified','TEST',:clerk)"""), {**ids, 'document': document})
            await db.execute(text(importlib.import_module('migrations.versions.0078_mandate_legacy_quarantine').OBSERVE))
            await db.execute(text('ALTER TABLE mandate_document ENABLE TRIGGER quarantine_mandate_original'))
    finally:
        await engine.dispose()
    return document
