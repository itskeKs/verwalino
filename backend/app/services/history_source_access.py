"""Authorize proposed relationship rows before ORM writes reach source RLS."""
import json
from fastapi import HTTPException
from sqlalchemy import text


async def require_history_prepare(db, org_id, kind, row):
    profile_column = {'lease_agreement': 'tenant_profile_id',
                      'ownership_history': 'owner_profile_id'}[kind]
    context = {'organization_id': str(org_id), 'id': str(row.id),
               'unit_id': str(row.unit_id), profile_column: str(getattr(row, profile_column))}
    allowed = await db.scalar(text(
        "SELECT property_history_row_access(:org,:kind,CAST(:context AS jsonb),'prepare')"),
        {'org': org_id, 'kind': kind, 'context': json.dumps(context)})
    if not allowed:
        raise HTTPException(status_code=404, detail="Relationship source access unavailable")
