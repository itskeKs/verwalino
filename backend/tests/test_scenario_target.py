"""External BDD routing must not require a local database or start servers."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest
from pytest_bdd.parser import FeatureParser

pytest_plugins = ["pytester"]


@pytest.fixture(scope="session", autouse=True)
def run_migrations():
    """Only a synthetic HTTP target is used."""


@pytest.fixture(autouse=True)
def inject_test_engine():
    """No local application engine is used."""


@contextmanager
def synthetic_target(properties=()):
    users = {"nightly@example.test": "bootstrap-password"}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(self.path)
            status, result = 200, {}
            if self.path == "/api/auth/login":
                if users.get(body["email"]) != body["password"]:
                    status = 401
                else:
                    result = {"user_id": "admin-id", "organization_id": "org-id", "role": "OWNER_ADMIN"}
            elif self.path == "/api/users":
                users[body["email"]] = body["password"]
                status, result = 201, {"id": body["email"], "email": body["email"]}
            elif self.path == "/api/auth/password":
                email = self.headers.get("Cookie", "").removeprefix("identity=")
                if users.get(email) != body["old_password"]:
                    status = 401
                else:
                    users[email] = body["new_password"]
                    status = 204
            else:
                status = 404
            self.send_response(status)
            if self.path == "/api/auth/login" and status == 200:
                self.send_header("Set-Cookie", f"identity={body['email']}; Path=/")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if status != 204:
                self.wfile.write(json.dumps(result).encode())

        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.path == "/api/properties":
                assert self.headers.get("Cookie") == "identity=nightly@example.test"
                result = list(properties) if isinstance(properties, tuple) else properties
            elif self.path == "/api/organizations/me":
                result = {"name": "A real installation name", "id": "org-id"}
            else:
                result = {"user_id": "admin-id", "organization_id": "org-id"}
            self.wfile.write(json.dumps(result).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", users, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_external_features_use_api_and_skip_local_only_scenarios(pytester, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    expected_counts = {}
    for filename in (
        "organization_isolation", "first_run_setup", "inbox_review_chain", "password_change",
        "organization_deletion", "organization_export_import", "password_reset",
    ):
        feature = FeatureParser(str(backend.parent / "features"), f"{filename}.feature").parse()
        # Each example row is a collected test; plain scenarios contribute one.
        expected_counts[feature.name] = sum(
            sum(len(examples.examples) for examples in scenario.examples) or 1
            for scenario in feature.scenarios.values()
        )
    expected_passed = expected_counts["Password change"]
    expected_skipped = sum(expected_counts[name] for name in (
        "Organization isolation", "First run setup", "Inbox review chain",
        "Organization deletion", "Organization export import", "Password reset",
    ))
    monkeypatch.setenv("PYTHONPATH", str(backend))
    # Invalid DB settings make any accidental local fixture access fail loudly.
    for name in (
        "HV_DATABASE_URL", "HV_DATABASE_URL_MIGRATOR", "HV_DATABASE_URL_SYNC", "HV_DATABASE_URL_REVIEW",
        "VERWALINO_MAILPIT_API",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERWALINO_TARGET_ADMIN_EMAIL", "nightly@example.test")
    monkeypatch.setenv("VERWALINO_TARGET_ADMIN_PASSWORD", "bootstrap-password")
    destination = pytester.path / "report"
    with synthetic_target() as (url, users, calls):
        monkeypatch.setenv("VERWALINO_TARGET_URL", url + "/")
        result = pytester.runpytest_subprocess(
            str(backend / "tests/bdd/test_password_change.py"),
            str(backend / "tests/bdd/test_organization_isolation.py"),
            str(backend / "tests/bdd/test_first_run_setup.py"),
            str(backend / "tests/bdd/test_inbox_review_chain.py"),
            str(backend / "tests/bdd/test_organization_deletion.py"),
            str(backend / "tests/bdd/test_organization_export_import.py"),
            str(backend / "tests/bdd/test_password_reset.py"),
            "--run-browser", "--scenario-report", str(destination), "-q", "--tb=short",
        )
        result.assert_outcomes(passed=expected_passed, skipped=expected_skipped, failed=0, errors=0)
        assert users["nightly@example.test"] == "bootstrap-password"
        assert len(users) == 1 + expected_passed
        assert calls.count("/api/users") == expected_passed
        assert calls[:2] == ["/api/auth/login", "/api/properties"]
        assert calls.count("/api/properties") == 1
    report = json.loads((destination / "scenarios.json").read_text())
    assert report["run"]["target_url"] == url
    rows = report["scenarios"]
    assert len(rows) == sum(expected_counts.values())
    for feature_name, reason in (
        ("Organization isolation", "two organizations"),
        ("First run setup", "fresh installation"),
        ("Inbox review chain", "needs the in-process invoice worker and direct database fixtures"),
        ("Organization deletion", "needs direct database fixtures and the in-process deletion CLI"),
        ("Organization export import", "needs direct database fixtures and in-process archive storage"),
        ("Password reset", "needs the Mailpit test mailbox"),
    ):
        skipped = [row for row in rows if row["feature"] == feature_name]
        assert len(skipped) == expected_counts[feature_name]
        assert all(row["outcome"] == "skipped" and reason in row["error"]["message"] for row in skipped)
    password_changes = [row for row in rows if row["feature"] == "Password change"]
    assert len(password_changes) == expected_passed
    assert all(row["outcome"] == "passed" and row["error"] is None for row in password_changes)
    outcomes = result.parseoutcomes()
    assert outcomes["passed"] + outcomes["skipped"] == len(rows)


@pytest.mark.parametrize("properties", [[{"id": "existing-property"}], {"unexpected": "response"}])
def test_external_session_refuses_non_fresh_targets_before_scenario_writes(pytester, monkeypatch, properties):
    backend = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(backend))
    monkeypatch.setenv("VERWALINO_TARGET_ADMIN_EMAIL", "nightly@example.test")
    monkeypatch.setenv("VERWALINO_TARGET_ADMIN_PASSWORD", "bootstrap-password")
    with synthetic_target(properties) as (url, users, calls):
        monkeypatch.setenv("VERWALINO_TARGET_URL", url)
        result = pytester.runpytest_subprocess(str(backend / "tests/bdd/test_password_change.py"), "-q")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(["*target is not a fresh installation; refusing to write scenario data*"])
        assert calls == ["/api/auth/login", "/api/properties"]
        assert users == {"nightly@example.test": "bootstrap-password"}


def test_normalized_empty_target_keeps_the_disposable_database_guard(monkeypatch):
    import runpy
    from app.core import declaration_test_database

    monkeypatch.setenv("VERWALINO_TARGET_URL", "///")
    def refuse(settings):
        raise RuntimeError("disposable database guard was called")
    monkeypatch.setattr(declaration_test_database, "require_disposable_database_settings", refuse)
    with pytest.raises(RuntimeError, match="disposable database guard was called"):
        runpy.run_path(str(Path(__file__).with_name("conftest.py")))


def test_external_browser_app_does_not_start_local_processes(monkeypatch):
    from tests.bdd.browser import browser_app
    def forbidden(*args, **kwargs):
        pytest.fail("external browser target started a local process")
    monkeypatch.setattr("subprocess.Popen", forbidden)
    from types import SimpleNamespace
    with synthetic_target() as (url, _, calls):
        monkeypatch.setenv("VERWALINO_TARGET_URL", url)
        fixture = browser_app.__wrapped__({}, SimpleNamespace(), browser_storage=None)
        assert next(fixture) == url
        with pytest.raises(StopIteration):
            next(fixture)
        assert calls == []


def test_external_selection_cannot_fall_through_to_local_database_migrations(monkeypatch):
    from tests import conftest
    monkeypatch.setattr(conftest, "MIGRATOR_SYNC_URL", None)
    with pytest.raises(RuntimeError, match="supported only by tests/bdd"):
        next(conftest.run_migrations.__wrapped__())


@pytest.mark.parametrize('isolated', [None, 'supplier', 'anna', 'eva'])
def test_retention_setup_uses_only_http_and_keeps_privacy_archives_in_empty_organizations(monkeypatch, isolated):
    import asyncio
    from types import SimpleNamespace
    import httpx
    from tests.bdd.world import World
    from tests.bdd.steps.person_retention import retention_setup

    monkeypatch.setenv('VERWALINO_TARGET_URL', 'http://synthetic.invalid')
    prefix = {'supplier': 'VERWALINO_TARGET_RETENTION', 'anna': 'VERWALINO_TARGET_RETENTION_ANNA',
        'eva': 'VERWALINO_TARGET_RETENTION_EVA'}.get(isolated, 'VERWALINO_TARGET_RETENTION')
    monkeypatch.setenv(prefix + '_EMAIL', 'supplier@example.test')
    monkeypatch.setenv(prefix + '_PASSWORD', 'disposable-supplier-password')
    requests = []

    def answer(request):
        requests.append(request)
        if request.url.path == '/api/auth/login':
            assert json.loads(request.content) == {
                'email': 'supplier@example.test', 'password': 'disposable-supplier-password'}
            return httpx.Response(200, json={'user_id': 'supplier-admin', 'organization_id': 'supplier-org'})
        if request.url.path == '/api/properties' and request.method == 'GET':
            return httpx.Response(200, json=[])
        assert request.url.path == '/api/properties' and request.method == 'POST'
        grant = json.loads(request.content)['data_audience']['grants'][0]
        assert grant['user_id'] == ('supplier-admin' if isolated else 'alpha-admin')
        assert set(grant['capabilities']) == {'read', 'prepare', 'approve', 'export'}
        return httpx.Response(201, json={'id': 'retention-property'})

    factory = httpx.AsyncClient
    def client(**kwargs):
        return factory(transport=httpx.MockTransport(answer), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    def forbidden(name):
        pytest.fail(f'External retention requested local fixture {name}')
    with asyncio.Runner() as runner:
        admin = client(base_url='http://synthetic.invalid')
        world = World(runner.get_loop(), [admin], admin)
        world._organizations = {'Verwaltung Alpha': admin}
        request = SimpleNamespace(getfixturevalue=forbidden)
        source = retention_setup(world, request, {'org_a_id': 'alpha-org', 'user_a_admin_id': 'alpha-admin'},
            isolated=isolated)
        try:
            context = next(source)
            assert context['property'] == 'retention-property'
            assert context['org'] == ('supplier-org' if isolated else 'alpha-org')
            assert world.actor is context['client']
        finally:
            source.close()
            runner.run(admin.aclose())
    assert [r.url.path for r in requests] == (
        ['/api/auth/login', '/api/properties', '/api/properties'] if isolated else ['/api/properties'])


@pytest.mark.parametrize('statuses,header,expected_sleeps', [
    ([429, 200], '60', [60]),
    ([429, 429], '60', [60]),
    ([429], None, []),
    ([429], '61', []),
    ([403], '60', []),
])
def test_external_privacy_export_waits_once_only_for_the_supported_cooldown(monkeypatch, statuses, header, expected_sleeps):
    import asyncio
    import io
    import tarfile
    import httpx
    from tests.bdd.test_maintenance_reserve_ledger import privacy_export

    monkeypatch.setenv('VERWALINO_TARGET_URL', 'http://synthetic.invalid')
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for entity in ('person', 'payment', 'reserve_ledger_entry', 'audit_event', 'settlement_run'):
            content = json.dumps({'id': entity}).encode()
            info = tarfile.TarInfo(f'entities/{entity}.jsonl')
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    calls, sleeps = [], []
    async def sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    class World:
        def act(self, method, path):
            assert (method, path) == ('POST', '/api/organizations/export')
            calls.append(path)
            status = statuses[len(calls) - 1]
            return httpx.Response(status, content=archive.getvalue() if status == 200 else b'refused',
                headers={'Retry-After': header} if header else {})
        def run(self, coroutine):
            return asyncio.run(coroutine)
    if statuses[-1] == 200:
        assert privacy_export(World())['person'] == {'person': {'id': 'person'}}
    else:
        with pytest.raises(AssertionError):
            privacy_export(World())
    assert sleeps == expected_sleeps
    assert len(calls) == len(statuses)
