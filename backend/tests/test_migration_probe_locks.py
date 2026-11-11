import threading
import time

import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import MIGRATOR_SYNC_URL
from tests.migration_rehearsal import lock_for_rehearsal


def test_probe_waits_for_transient_table_holder_before_nowait():
    engine = create_engine(MIGRATOR_SYNC_URL)
    holder = psycopg2.connect(MIGRATOR_SYNC_URL)
    started = threading.Event()
    errors = []
    backend_pid = []
    thread = None
    try:
        with holder.cursor() as cursor:
            cursor.execute('LOCK TABLE public.person IN SHARE UPDATE EXCLUSIVE MODE')

            def probe():
                try:
                    with engine.begin() as db:
                        backend_pid.append(db.scalar(text('SELECT pg_backend_pid()')))
                        started.set()
                        lock_for_rehearsal(db, 'person')
                        db.execute(text('LOCK TABLE public.person IN ACCESS EXCLUSIVE MODE NOWAIT'))
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=probe)
            thread.start()
            assert started.wait(5)
            waiting = False
            while thread.is_alive():
                cursor.execute("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                    WHERE pid=%s AND wait_event_type='Lock')""", (backend_pid[0],))
                if cursor.fetchone()[0]:
                    waiting = True
                    break
            if not waiting and errors:
                raise errors[0]
            assert waiting
    finally:
        holder.rollback()
        if thread is not None:
            thread.join(10)
        holder.close()
        engine.dispose()
    assert not thread.is_alive()
    assert not errors


def test_probe_lock_timeout_dump_names_unreleased_holder(capfd):
    engine = create_engine(MIGRATOR_SYNC_URL)
    holder = psycopg2.connect(MIGRATOR_SYNC_URL)
    try:
        with holder.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            holder_pid = cursor.fetchone()[0]
            cursor.execute('LOCK TABLE public.person IN SHARE UPDATE EXCLUSIVE MODE')
            with engine.begin() as db:
                started = time.monotonic()
                with pytest.raises(DBAPIError) as denied:
                    lock_for_rehearsal(db, 'person')
                elapsed = time.monotonic() - started
            assert denied.value.orig.pgcode == '55P03'
            assert elapsed >= 4
            assert f'"pid": {holder_pid}' in capfd.readouterr().err
    finally:
        holder.rollback()
        holder.close()
        engine.dispose()
