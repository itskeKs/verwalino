"""Preflight contact effects before ORM writes reach source RLS."""
from fastapi import HTTPException
from sqlalchemy import text


async def require_contact_creation(db, org_id):
    if await db.scalar(text('SELECT property_source_security_mode(:org)'), {'org':org_id})=='PAUSED':
        raise HTTPException(409, 'Source effects are paused')


async def require_person_prepare(db, org_id, person_id):
    if not await db.scalar(text("SELECT person_source_access(:org,:person,'prepare')"),{'org':org_id,'person':person_id}):
        raise HTTPException(404, 'Contact preparation access unavailable')
