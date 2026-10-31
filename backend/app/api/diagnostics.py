"""Administrator-only, manually downloaded diagnostics; no stored archives."""
from datetime import datetime, timezone
import logging
import os

from fastapi import APIRouter, Depends, Response
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import recent_diagnostics
from app.core.tenant import AuthContext, require_admin_dep
from app.diagnostics import build_bundle, bundle_filename, collect_database, runtime_config

router = APIRouter(prefix='/api/admin/diagnostics', tags=['diagnostics'])
logger = logging.getLogger(__name__)


@router.get('')
async def support_information(response: Response, auth: AuthContext = Depends(require_admin_dep)):
    response.headers['Cache-Control'] = 'no-store'
    return {'support_url': str(settings.support_url)}


@router.post('', response_class=Response)
async def download_diagnostics(auth: AuthContext = Depends(require_admin_dep)):
    now = datetime.now(timezone.utc)
    database = await collect_database(auth.organization_id, auth.user_id)
    logs = await run_in_threadpool(recent_diagnostics, auth.organization_id)
    data = await run_in_threadpool(
        build_bundle, config=runtime_config(), database=database,
        log_lines=logs,
        release_id=os.environ.get('HV_RELEASE_ID', ''), image=os.environ.get('HV_IMAGE_DIGEST', ''), now=now,
    )
    # Correlation is supplied by the logging factory; never log user/config data.
    logger.info('diagnostics_download')
    return Response(data, media_type='application/gzip', headers={
        'Content-Disposition': f'attachment; filename="{bundle_filename(now)}"',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    })
