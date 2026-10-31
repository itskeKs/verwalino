"""External BDD routing must not require a local database or start servers."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

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


def test_external_features_use_api_and_skip_isolation_and_fresh_setup(pytester, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(backend))
    # Invalid DB settings make any accidental local fixture access fail loudly.
    for name in ("HV_DATABASE_URL", "HV_DATABASE_URL_MIGRATOR", "HV_DATABASE_URL_SYNC", "HV_DATABASE_URL_REVIEW"):
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
            "--scenario-report", str(destination), "-q", "--tb=short",
        )
        result.assert_outcomes(passed=2, skipped=14)
        assert users["nightly@example.test"] == "bootstrap-password"
        assert len(users) == 3
        assert calls.count("/api/users") == 2
        assert calls[:2] == ["/api/auth/login", "/api/properties"]
        assert calls.count("/api/properties") == 1
    report = json.loads((destination / "scenarios.json").read_text())
    assert report["run"]["target_url"] == url
    rows = report["scenarios"]
    isolation = [row for row in rows if row["feature"] == "Organization isolation"]
    assert len(isolation) == 11
    assert all("two organizations" in row["error"]["message"] for row in isolation)
    setup = [row for row in rows if row["feature"] == "First run setup"]
    assert len(setup) == 3
    assert all(row["outcome"] == "skipped" and "fresh installation" in row["error"]["message"] for row in setup)


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
        fixture = browser_app.__wrapped__({}, SimpleNamespace())
        assert next(fixture) == url
        with pytest.raises(StopIteration):
            next(fixture)
        assert calls == []


def test_external_selection_cannot_fall_through_to_local_database_migrations(monkeypatch):
    from tests import conftest
    monkeypatch.setattr(conftest, "MIGRATOR_SYNC_URL", None)
    with pytest.raises(RuntimeError, match="supported only by tests/bdd"):
        next(conftest.run_migrations.__wrapped__())
