"""Exercise the bank browser observer without PostgreSQL or a browser."""
import asyncio
import errno
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from tests import bank_browser_trace as trace
from tests.bdd import browser as harness


def _disk_full(*args, **kwargs):
    raise OSError(errno.ENOSPC, "synthetic output failure")


async def _unused(*args):
    raise AssertionError("unexpected ASGI message")


def _scope():
    return {"headers": [(trace.HEADER.encode(), b"361-1280-ALL")]}


def test_failed_trace_output_preserves_dispatch_and_pool_checkin(monkeypatch):
    monkeypatch.setattr(trace, "print", _disk_full, raising=False)
    path = SimpleNamespace(open=_disk_full)
    entry = SimpleNamespace(record_info={}, driver_connection=SimpleNamespace(get_server_pid=lambda: 42))
    ran = []

    async def app(scope, receive, send):
        connection = SimpleNamespace(connection=entry)
        trace._sql_started(connection, None, "SELECT 1", (), SimpleNamespace(), False)
        ran.append("dispatched")

    asyncio.run(trace.trace_bank_request(app, _scope(), _unused, _unused, path))
    trace._connection_returned(object(), entry)
    assert ran == ["dispatched"]
    assert not entry.record_info
    trace.BankTraceLog(path, SimpleNamespace(write_line=_disk_full)).emit({"event": "test"})


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(None, "returned"), (RuntimeError("private error"), "raised"),
     (asyncio.CancelledError("private cancellation"), "cancelled")],
)
def test_trace_distinguishes_request_outcomes_after_response_start(tmp_path, error, outcome):
    path = tmp_path / "trace"
    response = {"type": "http.response.start", "status": 200}
    forwarded = []

    async def send(message):
        forwarded.append(message)

    async def app(scope, receive, send):
        await send(response)
        if error is not None:
            raise error

    async def call():
        await trace.trace_bank_request(app, _scope(), _unused, send, path)

    if error is None:
        asyncio.run(call())
    else:
        with pytest.raises(type(error)) as caught:
            asyncio.run(call())
        assert caught.value is error

    assert forwarded == [response]
    output = path.read_text()
    result = json.loads(output.splitlines()[-1])
    assert (result["event"], result["http"], result["outcome"]) == ("request_end", 200, outcome)
    assert "private" not in output


def test_bank_trace_copies_only_non_sensitive_request_headers():
    class Request:
        method = "GET"
        headers = {"accept": "application/json", "Cookie": "private-cookie",
                   "authorization": "private-auth", "proxy-authorization": "private-proxy-auth"}

        async def all_headers(self):
            pytest.fail("trace read full request headers")

    page = SimpleNamespace(on=lambda *args: None)
    observer = trace.BankPageTrace(None, page, 1280)
    headers = observer.headers(Request(), urlsplit(trace.PATH + "?status=ALL"))
    assert headers == {"accept": "application/json", trace.HEADER: "361-1280-ALL"}


def test_browser_context_closes_even_if_preclose_diagnostics_raise(monkeypatch):
    events = []

    async def start(**kwargs):
        pass

    async def close():
        events.append("close")

    def snapshot(phase):
        events.append(phase)
        if phase == "before_browser_close":
            raise RuntimeError("synthetic observer failure")

    world = SimpleNamespace(start=start, run=asyncio.run, errors=[],
                            context=SimpleNamespace(close=close),
                            bank_trace=SimpleNamespace(snapshot=snapshot))
    monkeypatch.setattr(harness, "BrowserWorld", lambda *args: world)
    monkeypatch.setattr(harness, "artifact_directory", lambda node: None)
    request = SimpleNamespace(node=object(),
                              config=SimpleNamespace(pluginmanager=SimpleNamespace(getplugin=lambda name: None)))
    fixture = harness.browser_world.__wrapped__(None, None, request)
    assert next(fixture) is world
    with pytest.raises(RuntimeError, match="synthetic observer failure"):
        next(fixture)
    assert events == ["before_browser_close", "close", "after_browser_close"]
