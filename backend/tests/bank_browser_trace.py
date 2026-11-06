"""Bounded diagnostics for the local bank save-lifecycle browser scenario."""
import asyncio
from contextlib import closing, suppress
from contextvars import ContextVar
import json
import re
import time
from urllib.parse import parse_qs

from playwright.async_api import Error as PlaywrightError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool

HEADER = "x-test-bank-trace"
PATH = "/api/bank-transactions"
TRACE_FILE_ENV = "VERWALINO_TEST_BANK_TRACE_FILE"
_TAG = re.compile(rb"361-(1280|360)-(NEU-[12]|ALL)")
_current = ContextVar("bank_browser_trace", default=None)
_POOL_TRACE = "bank_browser_trace_checkout"


def _record(kind, **fields):
    return {"event": kind, "at": time.time(), **fields}


def _status(query):
    return parse_qs(query).get("status", ["NEU"])[0]


def _write(path, record):
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
    except OSError:
        # A diagnostic must never replace the request outcome.
        with suppress(OSError, ValueError):
            print('BANK_BROWSER_TRACE {"event":"trace_write_failed"}', flush=True)


class BankTraceLog:
    def __init__(self, path, terminal):
        self.path = path
        self.terminal = terminal
        self.failed_widths = set()

    def emit(self, record):
        with suppress(OSError, ValueError):
            self.terminal.write_line("BANK_BROWSER_TRACE " + json.dumps(record))

    def records(self, width):
        try:
            with self.path.open(encoding="utf-8") as stream:
                lines = stream.read(65536).splitlines()
        except OSError as error:
            self.emit(_record("trace_read_failed", error=type(error).__name__))
            return []
        records = []
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # The server may still be appending the final line.
            if record["tag"].startswith(f"361-{width}-"):
                records.append(record)
        return records

    def report_failures(self):
        for width in sorted(self.failed_widths):
            for record in self.records(width):
                self.emit(record)


class BankPageTrace:
    def __init__(self, log, page, width):
        self.log = log
        self.page = page
        self.width = width
        self.requests = {}
        self.events = []
        page.on("response", lambda response: self._browser_event(
            "browser_response", response.request, http=response.status))
        page.on("requestfailed", lambda request: self._browser_event("browser_failed", request))

    def _browser_event(self, kind, request, **fields):
        if tag := self.requests.get(request):
            self.events.append(_record(kind, tag=tag, **fields))

    def headers(self, request, url):
        if url.path != PATH or request.method != "GET":
            return None
        status = _status(url.query)
        if status not in {"NEU", "ALL"}:
            return None
        prefix = f"361-{self.width}-{status}"
        count = sum(tag.startswith(prefix) for tag in self.requests.values())
        if count >= (2 if status == "NEU" else 1):
            return None
        tag = f"{prefix}-{count + 1}" if status == "NEU" else prefix
        self.requests[request] = tag
        self._browser_event("browser_request", request)
        # Chromium supplies its own session cookie on continuation. Keep
        # credentials out of the override recorded by Playwright tracing.
        headers = {name: value for name, value in request.headers.items()
                   if name.lower() not in {"cookie", "authorization", "proxy-authorization"}}
        return {**headers, HEADER: tag}

    async def table_timed_out(self):
        self.log.failed_widths.add(self.width)
        record = _record("table_timeout", width=self.width)
        try:
            record["page"] = await asyncio.wait_for(self.page.evaluate("""() => {
                const select = document.querySelector('#bank-transaction-status');
                const scope = select?.closest('main');
                const visible = el => !!el.getClientRects().length;
                const hasText = text => Array.from(scope?.querySelectorAll('p') ?? [])
                    .some(el => visible(el) && el.textContent === text);
                return {
                    bank_filter_present: !!select,
                    loading: hasText('Lade Umsätze…'),
                    empty: hasText('Keine Umsätze vorhanden.'),
                    list_error: Array.from(scope?.querySelectorAll('[role="alert"]') ?? [])
                        .some(el => visible(el) && el.parentElement.querySelector('button')?.textContent === 'Erneut laden'),
                    table_present: !!scope?.querySelector('table'),
                };
            }"""), timeout=1)
        except (PlaywrightError, TimeoutError) as error:
            record["page_error"] = type(error).__name__
        self.log.emit(record)

    def snapshot(self, phase):
        if self.width not in self.log.failed_widths:
            return
        from psycopg2.extras import RealDictCursor
        from tests.conftest import MIGRATOR_SYNC_URL, _diagnostic_connect

        record = _record(phase, width=self.width, sessions=[])
        pids = sorted({row["pid"] for row in self.log.records(self.width) if row["event"] == "database"})
        record["pids"] = pids
        try:
            if pids:
                with closing(_diagnostic_connect(MIGRATOR_SYNC_URL, connect_timeout=2,
                             options="-c statement_timeout=2000 -c lock_timeout=1000")) as connection:
                    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                        cursor.execute("""SELECT pid,state,extract(epoch FROM clock_timestamp())::float8 AS sampled_at,
                            extract(epoch FROM query_start)::float8 AS query_start,
                            extract(epoch FROM xact_start)::float8 AS xact_start,
                            wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers,
                            CASE WHEN starts_with(query,'SELECT lease_agreement.organization_id,') THEN 'lease_match'
                                 WHEN starts_with(query,'SELECT pg_advisory_xact_lock') THEN 'advisory_lock'
                                 ELSE 'other' END AS operation
                            FROM pg_stat_activity WHERE datname=current_database() AND pid=ANY(%s)
                            ORDER BY pid""", (pids,))
                        record["sessions"] = cursor.fetchall()
        except Exception as error:
            record["snapshot_error"] = type(error).__name__
        self.log.emit(record)
        if phase == "after_browser_close":
            for browser_event in self.events:
                self.log.emit(browser_event)


def _sql_started(conn, cursor, statement, parameters, context, executemany):
    trace = _current.get()
    if trace is None:
        return
    connection = conn.connection
    if _POOL_TRACE not in connection.record_info:
        pid = connection.driver_connection.get_server_pid()
        connection.record_info[_POOL_TRACE] = (trace["path"], trace["tag"], pid)
        _write(trace["path"], _record("database", tag=trace["tag"], pid=pid))
    # Keep only fixed operation labels and aggregates. SQL and binds stay private.
    if statement.startswith("SELECT lease_agreement.organization_id,") and "JOIN tenant_profile" in statement:
        operation = "lease_match"
    elif (statement.startswith("SELECT pg_advisory_xact_lock(") and parameters
          and isinstance(parameters[0], str) and parameters[0].startswith("financial-inputs:")):
        operation = "financial_inputs"
    else:
        operation = "other_sql"
    stats = trace["sql"].setdefault(operation, [0, 0, 0.0, 0.0])
    stats[0] += 1
    context.bank_trace_span = (stats, time.monotonic())


def _sql_completed(conn, cursor, statement, parameters, context, executemany):
    if span := getattr(context, "bank_trace_span", None):
        stats, started = span
        elapsed = time.monotonic() - started
        stats[1] += 1
        stats[2] += elapsed
        stats[3] = max(stats[3], elapsed)


def _connection_returned(dbapi_connection, connection_record):
    # Use the pool entry, even if cancellation moved cleanup outside the ASGI context.
    if checkout := connection_record.record_info.pop(_POOL_TRACE, None):
        path, tag, pid = checkout
        _write(path, _record("pool_return", tag=tag, pid=pid,
                             connection_available=dbapi_connection is not None))


def install_database_trace():
    event.listen(Engine, "before_cursor_execute", _sql_started)
    event.listen(Engine, "after_cursor_execute", _sql_completed)
    event.listen(Pool, "checkin", _connection_returned)


async def trace_bank_request(app, scope, receive, send, path):
    tag = dict(scope["headers"]).get(HEADER.encode(), b"")
    if not _TAG.fullmatch(tag):
        return await app(scope, receive, send)
    tag = tag.decode()
    trace = {"tag": tag, "path": path, "sql": {}}
    response = None
    disconnect_read = False
    token = _current.set(trace)
    _write(path, _record("request", tag=tag))

    async def track_send(message):
        nonlocal response
        if message["type"] == "http.response.start":
            response = message["status"]
        await send(message)

    async def track_receive():
        nonlocal disconnect_read
        message = await receive()
        if message["type"] == "http.disconnect":
            disconnect_read = True
        return message

    outcome = "raised"
    try:
        await app(scope, track_receive, track_send)
        outcome = "returned"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        _current.reset(token)
        timings = {
            name: {"started": started, "completed": completed,
                   "total_ms": round(total * 1000, 1), "max_ms": round(maximum * 1000, 1)}
            for name, (started, completed, total, maximum) in trace["sql"].items()
        }
        _write(path, _record("request_end", tag=tag, http=response, outcome=outcome,
                             disconnect_read=disconnect_read, sql=timings))
