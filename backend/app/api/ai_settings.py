"""Administrator-only organization AI settings and model discovery."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext, get_current_user, get_db
from app.core.live_principal import require_live_principal
from app.schemas.ai_settings import AiSettingsBody, AiSettingsOut, AiModelsBody, AiModelsOut
from app.services import ai_settings_service

router = APIRouter(prefix='/api/organizations/{organization_id}/ai-settings', tags=['ai-settings'])


async def admin_db(organization_id: uuid.UUID, auth: AuthContext = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db, scope='function')):
    if organization_id != auth.organization_id or auth.role != 'OWNER_ADMIN':
        raise HTTPException(404, 'Einstellungen nicht gefunden')
    try:
        await require_live_principal(db, auth, writer=False, administrator=True)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(404, 'Einstellungen nicht gefunden') from None
        raise
    return db


@router.get('', response_model=AiSettingsOut)
async def read(organization_id: uuid.UUID, db: AsyncSession = Depends(admin_db)):
    return await ai_settings_service.read_settings(db, organization_id)


@router.put('', response_model=AiSettingsOut)
async def write(organization_id: uuid.UUID, body: AiSettingsBody,
                auth: AuthContext = Depends(get_current_user),
                db: AsyncSession = Depends(admin_db)):
    return await ai_settings_service.write_settings(db, organization_id, body, auth)


@router.post('/models', response_model=AiModelsOut)
async def models(organization_id: uuid.UUID, body: AiModelsBody, db: AsyncSession = Depends(admin_db)):
    return await ai_settings_service.discover_models(db, organization_id, body)
