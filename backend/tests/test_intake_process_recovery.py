"""Actual process death before/after the intake extraction commit boundary."""
import asyncio
import os
from pathlib import Path
import signal
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_intake_extraction_worker import source
from tests.conftest import APP_RW_URL

CHILD='''
import asyncio,sys,time,uuid
from pathlib import Path
sys.path.insert(0,sys.argv[4])
from app.core.db import get_worker_session_factory
from app.core.intake_extraction import extract_logical
from app.workers.intake_extraction import process_once
org=uuid.UUID(sys.argv[1]);marker=Path(sys.argv[2]);stage=sys.argv[3]
async def main():
    if stage=='before':
        def processor(*args):
            marker.write_text('processor_started')
            time.sleep(30)
            return extract_logical(*args)
        await process_once(get_worker_session_factory(),org,processor=processor)
    else:
        assert await process_once(get_worker_session_factory(),org,processor=extract_logical)
        marker.write_text('committed')
        await asyncio.sleep(30)
asyncio.run(main())
'''


async def kill_at_marker(ids,root,marker,stage):
    env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','TMPDIR'}}
    env.update(HV_DATABASE_URL=APP_RW_URL,
        HV_DATABASE_URL_WORKER=make_url(APP_RW_URL).set(username='app_worker',password='worker_secret').render_as_string(hide_password=False),HV_DOCUMENT_STORAGE_ROOT=str(root))
    runtime=marker.parent/'worker-runtime'
    runtime.mkdir(exist_ok=True)
    child=await asyncio.create_subprocess_exec(sys.executable,'-c',CHILD,str(ids['org']),str(marker),stage,str(Path(__file__).resolve().parents[1]),
        cwd=runtime,env=env,start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
    try:
        async def ready():
            while not marker.exists():
                if child.returncode is not None:
                    raise AssertionError('Worker exited before the controlled boundary')
                await asyncio.sleep(.02)
        await asyncio.wait_for(ready(),10)
        os.killpg(child.pid,signal.SIGKILL)
        await asyncio.wait_for(child.wait(),5)
        assert child.returncode==-signal.SIGKILL
    finally:
        if child.returncode is None:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        await child.communicate()


@pytest.mark.asyncio
async def test_sigkill_reclaims_uncommitted_work_and_preserves_committed_effect(migrator_session,scope_seed,store,tmp_path):
    from app.core.db import get_worker_session_factory
    from app.workers.intake_extraction import process_once
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:ref,logical=await source(api,ids)
    await kill_at_marker(ids,store._root,tmp_path/'before.marker','before')
    assert await read_value('SELECT status FROM intake_extraction_job WHERE organization_id=:org',ids)=='running'
    assert await read_value('SELECT count(*) FROM extraction_revision WHERE organization_id=:org',ids)==0
    # Simulated clock advance, not an actual 180-second test sleep.
    await mutate(text("UPDATE intake_extraction_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE organization_id=:org"),ids)
    await kill_at_marker(ids,store._root,tmp_path/'after.marker','after')
    assert await read_value('SELECT status FROM intake_extraction_job WHERE organization_id=:org',ids)=='succeeded'
    assert await read_value('SELECT count(*) FROM extraction_revision WHERE organization_id=:org',ids)==1
    assert await read_value("SELECT count(*) FROM outbox_event WHERE organization_id=:org AND event_type='document.extracted'",ids)==1
    assert await read_value("SELECT count(*) FROM intake_extraction_attempt WHERE organization_id=:org AND result->>'error_code'='lease_expired'",ids)==1
    assert await read_value('SELECT count(*) FROM intake_extraction_attempt WHERE organization_id=:org AND finished_at IS NOT NULL',ids)==2
    assert not await process_once(get_worker_session_factory(),ids['org'])
    async with client(ids) as api:
        history=(await api.get('/api/document-refs/'+ref['id']+'/extractions',params={'logical_document_id':logical['id']})).json()['items']
        assert len(history)==1 and history[0]['status']=='succeeded'
