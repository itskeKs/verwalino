"""Read-only administrative inventory, independent from source-data disclosure."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.live_principal import get_live_admin_db
from app.core.tenant import AuthContext, require_admin_dep
from app.schemas.access_preparation import AccessPreparationOut

router = APIRouter(prefix='/api/access-preparation', tags=['access_preparation'])


@router.get('', response_model=AccessPreparationOut)
async def preparation_inventory(auth: AuthContext = Depends(require_admin_dep),
                                db: AsyncSession = Depends(get_live_admin_db)):
    return await db.scalar(text('SELECT access_preparation_inventory(:org)'), {'org': auth.organization_id})
