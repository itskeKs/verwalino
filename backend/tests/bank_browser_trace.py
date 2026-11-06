"""Temporary bank request measurements for the local browser harness (#361)."""
from contextvars import ContextVar
import json
import re
import time
from urllib.parse import parse_qs

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool


PREFIX = "BANK_BROWSER_TRACE "
HEADER = "x-test-bank-trace"
PATH = "/api/bank-transactions"
_TAG = re.compile(rb"361-(1280|360)-[1-3]")
_current = ContextVar("bank_browser_trace", default=None)


def line(kind, **fields):
    return PREFIX + json.dumps({"event": kind, "at": round(time.time(), 3), **fields})


def status_filter(query):
    status = parse_qs(query).get("status", ["NEU"])[0]
    return status if status in {"ALL", "NEU", "IGNORIERT", "ZUGEORDNET"} else "other"


def _operation(statement, parameters):
    if statement.startswith("SELECT lease_agreement.organization_id,") and "JOIN tenant_profile" in statement:
        return "lease_match"
    if statement.startswith("SELECT pg_advisory_xact_lock") and parameters:
        key = parameters[0]
        if isinstance(key, str) and key.startswith("scope-acl:"):
            return "scope_acl"
        if isinstance(key, str) and key.startswith("financial-inputs:"):
            return "financial_inputs"
    return "other_sql"


def _sql_started(conn, cursor, statement, parameters, context, executemany):
    trace = _current.get()
    if trace is None:
        return
    operation = _operation(statement, parameters)
    if trace["pid"] is None and operation in {"financial_inputs", "lease_match"}:
        pid = trace["pid"] = conn.connection.driver_connection.get_server_pid()
        print(line("database", tag=trace["tag"], pid=pid), flush=True)
    stats = trace["sql"].setdefault(operation, [0, 0, 0.0, 0.0])
    stats[0] += 1
    context.bank_trace_span = (stats, time.monotonic())


def _sql_completed(conn, cursor, statement, parameters, context, executemany):
    span = getattr(context, "bank_trace_span", None)
    if span is not None:
        stats, started = span
        elapsed = time.monotonic() - started
        stats[1] += 1
        stats[2] += elapsed
        stats[3] = max(stats[3], elapsed)


def _connection_returned(dbapi_connection, connection_record):
    trace = _current.get()
    driver = connection_record.driver_connection
    if trace is not None and driver is not None and driver.get_server_pid() == trace["pid"]:
        trace["connection_returned_at"] = round(time.time(), 3)


def install_database_trace():
    event.listen(Engine, "before_cursor_execute", _sql_started)
    event.listen(Engine, "after_cursor_execute", _sql_completed)
    event.listen(Pool, "checkin", _connection_returned)


async def trace_bank_request(app, scope, receive, send):
    tag = dict(scope["headers"]).get(HEADER.encode(), b"")
    if not _TAG.fullmatch(tag):
        return await app(scope, receive, send)
    tag = tag.decode()
    status = status_filter(scope["query_string"].decode())
    trace = {"tag": tag, "pid": None, "sql": {}, "connection_returned_at": None}
    response = None
    disconnected = False
    token = _current.set(trace)
    print(line("request", tag=tag, status=status), flush=True)

    async def track_send(message):
        nonlocal response
        if message["type"] == "http.response.start":
            response = message["status"]
        await send(message)

    async def track_receive():
        nonlocal disconnected
        message = await receive()
        if message["type"] == "http.disconnect":
            disconnected = True
        return message

    try:
        await app(scope, track_receive, track_send)
    finally:
        _current.reset(token)
        timings = {
            name: {"started": started, "completed": completed,
                   "total_ms": round(total * 1000, 1), "max_ms": round(maximum * 1000, 1)}
            for name, (started, completed, total, maximum) in trace.pop("sql").items()
        }
        print(line("request_end", status=status, http=response, disconnect_read=disconnected,
                   sql=timings, **trace), flush=True)
