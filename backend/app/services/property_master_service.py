"""Direct master-data effects use the same authority as database row policies."""
from fastapi import HTTPException
from sqlalchemy import text


async def require_property_master(db, org_id, property_id, capability='prepare'):
    allowed = await db.scalar(text('SELECT property_master_access(:org,:property,:capability)'),
        {'org':org_id,'property':property_id,'capability':capability})
    if not allowed:
        raise HTTPException(404, 'Property data not accessible')
