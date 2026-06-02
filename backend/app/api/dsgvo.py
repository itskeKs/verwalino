"""DSGVO admin API -- M9 (OWNER_ADMIN only).

Auskunft (Art. 15/20), Loeschung/Einschraenkung (Art. 17/18). All endpoints
require the OWNER_ADMIN role: these are privileged data-protection operations.
Foreign / unknown person id -> 404. A retention-bound person cannot be
anonymized (409) -- the service enforces the ADR-0012 invariant; a person with
a live contractual relationship cannot be erased (409).

These endpoints mutate ONLY the person row; they never alter a retained Beleg.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_db, require_admin_dep
from app.services.dsgvo_service import DsgvoService

router = APIRouter(prefix="/api/dsgvo", tags=["dsgvo"])


@router.get("/persons/{person_id}/status")
async def dsgvo_status(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Preview the DSGVO state + what a deletion request would do (no mutation)."""
    svc = DsgvoService(db, auth.organization_id, auth.user_id)
    return await svc.status(person_id)


@router.get("/persons/{person_id}/export")
async def dsgvo_export(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Art. 15/20 data export: stored PII + roles + retention bindings."""
    svc = DsgvoService(db, auth.organization_id, auth.user_id)
    return await svc.export_person_data(person_id)


@router.post("/persons/{person_id}/request-deletion")
async def dsgvo_request_deletion(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Art. 17 request: RESTRICT if retention-bound, else ANONYMIZE immediately."""
    svc = DsgvoService(db, auth.organization_id, auth.user_id)
    return await svc.request_deletion(person_id)


@router.post("/persons/{person_id}/anonymize")
async def dsgvo_anonymize(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Explicit anonymization / purge. Refused (409) while retention-bound."""
    svc = DsgvoService(db, auth.organization_id, auth.user_id)
    return await svc.anonymize(person_id)
