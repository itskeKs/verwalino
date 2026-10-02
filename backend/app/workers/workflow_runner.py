"""Serial round-robin dispatch over the existing durable, fenced job adapters.

Queues retain their own provenance and leases. This runner never copies jobs,
releases a live lease, or opens a transaction around processor/provider work.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable,Callable,Iterable
from dataclasses import dataclass
import logging
import uuid

logger=logging.getLogger(__name__)
STAGES=('intake_inspection','invoice_extraction','declaration_import','outbox')
IDLE_CODES={'IDLE','DECLARATION_IMPORT_DISABLED'}


@dataclass(frozen=True)
class DispatchResult:
    code:str
    organization_id:uuid.UUID|None=None
    stage:str|None=None


class WorkflowRunner:
    def __init__(self,organizations:Callable[[],Awaitable[Iterable[uuid.UUID]]],
                 adapters:dict[str,Callable[[uuid.UUID],Awaitable[str]]]):
        if not adapters:raise ValueError('At least one workflow adapter is required')
        self._organizations=organizations
        self._adapters=dict(adapters)
        self._last_pair:tuple[uuid.UUID,str]|None=None
        self._lock=asyncio.Lock()

    async def run_once(self)->DispatchResult:
        async with self._lock:
            try:organizations=tuple(dict.fromkeys(await self._organizations()))
            except asyncio.CancelledError:raise
            except Exception:
                logger.warning('workflow result=ORGANIZATION_DISCOVERY_ERROR')
                return DispatchResult('ORGANIZATION_DISCOVERY_ERROR')
            pairs=[(org,stage) for org in organizations for stage in self._adapters]
            if not pairs:return DispatchResult('IDLE')
            if self._last_pair in pairs:
                start=pairs.index(self._last_pair)+1
                pairs=pairs[start:]+pairs[:start]
            for org,stage in pairs:
                # Advance even on a failed adapter so it cannot monopolize dispatch.
                self._last_pair=(org,stage)
                try:code=await self._adapters[stage](org)
                except asyncio.CancelledError:raise
                except Exception:
                    logger.warning('workflow result=STAGE_ERROR stage=%s organization_id=%s',stage,org)
                    return DispatchResult('STAGE_ERROR',org,stage)
                if code not in IDLE_CODES:return DispatchResult(code,org,stage)
            return DispatchResult('IDLE')


def build_adapters(stages,*,session_factory=None,declaration_worker=None):
    """Construction performs no database or provider request."""
    from app.core.config import settings
    from app.core.db import get_worker_session_factory
    from app.workers.workflow import process_once as inspect_once
    from app.workers.invoice_extraction import process_once as invoice_once
    from app.workers.outbox import process_once as outbox_once
    stages=tuple(dict.fromkeys(stages))
    if not stages or any(stage not in STAGES for stage in stages):raise ValueError('Unknown or empty workflow stages')
    factory=session_factory or get_worker_session_factory()
    async def inspection(org):return 'PROCESSED' if await inspect_once(factory,org) else 'IDLE'
    async def invoice(org):return 'PROCESSED' if await invoice_once(factory,org) else 'IDLE'
    async def outbox(org):return 'PROCESSED' if await outbox_once(factory,org) else 'IDLE'
    async def declaration(org):
        nonlocal declaration_worker
        if not settings.declaration_import_enabled:return 'DECLARATION_IMPORT_DISABLED'
        if declaration_worker is None:
            from app.declarations.openai_provider import OpenAiDeclarationProvider
            from app.declarations.pipeline import DeclarationPipeline
            from app.workers.declaration_import import DeclarationImportWorker
            declaration_worker=DeclarationImportWorker(DeclarationPipeline(OpenAiDeclarationProvider()))
        return (await declaration_worker.run_once(organization_id=org)).code
    available={'intake_inspection':inspection,'invoice_extraction':invoice,'declaration_import':declaration,'outbox':outbox}
    return {stage:available[stage] for stage in stages}


async def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    organizations=parser.add_mutually_exclusive_group(required=True)
    organizations.add_argument('--organization',action='append',type=uuid.UUID)
    organizations.add_argument('--all-organizations',action='store_true')
    parser.add_argument('--stage',action='append',choices=STAGES,required=True)
    parser.add_argument('--once',action='store_true',help='Run at most one non-idle stage; existing queues remain durable.')
    args=parser.parse_args(argv)
    async def organization_ids():
        if args.organization:return args.organization
        from sqlalchemy import select
        from app.core.db import get_worker_unscoped_session
        from app.models.organization import Organization
        async with get_worker_unscoped_session() as session:
            return (await session.execute(select(Organization.id).order_by(Organization.id))).scalars().all()
    runner=WorkflowRunner(organization_ids,build_adapters(args.stage))
    while True:
        result=await runner.run_once()
        logger.info('workflow result=%s stage=%s organization_id=%s',result.code,result.stage,result.organization_id)
        if args.once:return
        await asyncio.sleep(2)


if __name__=='__main__':asyncio.run(main())
