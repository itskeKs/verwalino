"""Canary contracts against an in-process synthetic HTTP server; no live services."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import canary

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
PROPERTY = "a" * 8 + "-aaaa-aaaa-aaaa-" + "a" * 12
LEASE = "b" * 8 + "-bbbb-bbbb-bbbb-" + "b" * 12
UNIT = "c" * 8 + "-cccc-cccc-cccc-" + "c" * 12
SCOPE = "d" * 8 + "-dddd-dddd-dddd-" + "d" * 12


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.fail_path = None
        self.malformed_path = None
        self.bad_json = False
        self.no_cookie = False
        self.redirect = False
        self.ambiguous = False
        self.bad_ledger = False
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                case.calls.append((self.command, self.path, json.loads(body) if body else None))
                if self.path == case.malformed_path:
                    self.wfile.write(b"private malformed status fake-password\r\n")
                    return
                if self.path == "/api/auth/login":
                    if case.redirect:
                        self.send_response(302)
                        self.send_header("Location", "/stolen")
                        self.end_headers()
                        return
                else:
                    if self.headers.get("Cookie") != "hv_session=synthetic-session":
                        self.send_response(401)
                        self.end_headers()
                        return
                if self.path == case.fail_path:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b"private response including password fake-password")
                    return
                status, result = 200, {}
                if self.path == "/api/auth/login":
                    result = {"user_id": "synthetic", "organization_id": "canary-org", "role": "OWNER_ADMIN"}
                elif self.command == "POST" and self.path == "/api/properties":
                    status, result = 201, {"id": PROPERTY}
                elif self.path == "/api/leases":
                    result = [{"id": LEASE, "unit_id": UNIT}] * (2 if case.ambiguous else 1)
                elif self.path == f"/api/units/{UNIT}":
                    result = {"id": UNIT, "property_id": PROPERTY}
                elif self.path.startswith("/api/accounting-scopes/page?"):
                    result = {"items": [{"id": SCOPE}], "next_cursor": None}
                elif self.path.startswith(f"/api/payments/mietkonto/lease/{LEASE}?"):
                    result = {"open_items": [], "soll_total": "0.00", "ist_total": "0.00", "balance": "0.00"}
                    if case.bad_ledger:
                        result.pop("balance")
                elif self.command == "DELETE" and self.path == f"/api/properties/{PROPERTY}":
                    status, result = 204, None
                else:
                    status = 404
                self.send_response(status)
                if self.path == "/api/auth/login" and not case.no_cookie:
                    self.send_header("Set-Cookie", "hv_session=synthetic-session; HttpOnly; Path=/")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if result is not None:
                    self.wfile.write(b"invalid" if case.bad_json else json.dumps(result).encode())

            do_POST = do_GET = do_DELETE = request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.report = Path(self.temp.name) / "canary.json"

    def run_canary(self, *args, **env):
        with patch.dict(os.environ, {"CANARY_EMAIL": "canary@example.invalid", "CANARY_PASSWORD": "fake-password", **env}):
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                status = canary.main(["--base-url", self.base, "--report", str(self.report), *args])
        self.assertNotIn("fake-password", stdout.getvalue() + stderr.getvalue())
        return status, json.loads(self.report.read_text()) if self.report.exists() else None

    def test_login_create_ledger_and_delete_use_the_cookie_and_explicit_scope(self):
        status, report = self.run_canary()
        self.assertEqual(status, 0)
        self.assertEqual(report["scenarios"][0]["outcome"], "passed")
        self.assertEqual(self.calls[0][0:2], ("POST", "/api/auth/login"))
        self.assertEqual(self.calls[1][2]["management_type"], "MIET")
        ledger = next(path for method, path, _ in self.calls if "/mietkonto/" in path)
        self.assertIn(f"accounting_scope_id={SCOPE}", ledger)
        self.assertIn("period_start=2026-01-01", ledger)
        self.assertEqual(self.calls[-1][0:2], ("DELETE", f"/api/properties/{PROPERTY}"))
        self.assertNotIn("fake-password", json.dumps(report))
        self.assertNotIn("synthetic-session", json.dumps(report))

    def test_ledger_setup_failure_still_deletes_the_created_property_and_reports_a_stable_kind(self):
        self.fail_path = "/api/leases"
        status, report = self.run_canary()
        self.assertEqual(status, 1)
        self.assertEqual(self.calls[-1][0], "DELETE")
        failure, = [step for step in report["scenarios"][0]["steps"] if step["outcome"] == "failed"]
        self.assertEqual(failure["name"], "tenant-ledger")
        self.assertEqual(failure["error"]["type"], "HTTP503")
        self.assertNotIn("private response", json.dumps(report))

    def test_cleanup_failure_fails_the_canary(self):
        self.fail_path = f"/api/properties/{PROPERTY}"
        status, report = self.run_canary()
        self.assertEqual(status, 1)
        self.assertEqual(report["scenarios"][0]["steps"][-1]["name"], "cleanup")
        self.assertEqual(report["scenarios"][0]["steps"][-1]["outcome"], "failed")

    def test_malformed_status_line_writes_a_sanitized_report_and_still_cleans_up(self):
        self.malformed_path = "/api/leases"
        status, report = self.run_canary()
        self.assertEqual(status, 1)
        failure, = [step for step in report["scenarios"][0]["steps"] if step["outcome"] == "failed"]
        self.assertEqual(failure["name"], "tenant-ledger")
        self.assertEqual(failure["error"]["type"], "BadStatusLine")
        self.assertEqual(self.calls[-1][:2], ("DELETE", f"/api/properties/{PROPERTY}"))
        self.assertNotIn("fake-password", json.dumps(report))

    def test_create_property_failure_reports_without_deleting_an_unknown_property(self):
        self.fail_path = "/api/properties"
        status, report = self.run_canary()
        self.assertEqual(status, 1)
        self.assertEqual([step["name"] for step in report["scenarios"][0]["steps"]], ["login", "create-property"])
        self.assertEqual(report["scenarios"][0]["steps"][-1]["error"]["type"], "HTTP503")
        self.assertEqual([call[:2] for call in self.calls], [("POST", "/api/auth/login"), ("POST", "/api/properties")])
        self.assertNotIn("fake-password", json.dumps(report))

    def test_redirects_are_not_followed_and_never_receive_credentials(self):
        self.redirect = True
        status, report = self.run_canary()
        self.assertEqual(status, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(report["scenarios"][0]["steps"][0]["error"]["type"], "HTTP302")

    def test_invalid_json_and_missing_session_fail_without_writes(self):
        for name in ("bad_json", "no_cookie"):
            with self.subTest(name=name):
                setattr(self, name, True)
                status, report = self.run_canary()
                self.assertEqual(status, 1)
                self.assertEqual(self.calls[-1][1], "/api/auth/login")
                setattr(self, name, False)

    def test_ambiguous_fixture_or_invalid_ledger_cannot_report_healthy(self):
        for name in ("ambiguous", "bad_ledger"):
            with self.subTest(name=name):
                setattr(self, name, True)
                status, report = self.run_canary()
                self.assertEqual(status, 1)
                self.assertEqual(self.calls[-1][0], "DELETE")
                setattr(self, name, False)

    def test_dry_run_needs_no_credentials_and_makes_no_requests_or_report(self):
        status, report = self.run_canary("--dry-run", CANARY_EMAIL="", CANARY_PASSWORD="")
        self.assertEqual(status, 0)
        self.assertIsNone(report)
        self.assertEqual(self.calls, [])

    def test_missing_credentials_produce_a_configuration_failure_report(self):
        status, report = self.run_canary(CANARY_PASSWORD="")
        self.assertEqual(status, 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(report["scenarios"][0]["steps"][0]["error"]["type"], "Configuration")

    def test_transport_timeout_has_a_sanitized_structured_error(self):
        client = canary.Client(self.base)
        with patch.object(client.opener, "open", side_effect=URLError(TimeoutError("private details"))):
            with self.assertRaises(canary.CanaryError) as raised:
                client.request("GET", "/api/leases")
        self.assertEqual(raised.exception.kind, "Timeout")

    def test_credentials_cannot_be_sent_to_plain_http_or_a_url_with_userinfo(self):
        for url in ("http://app.verwalino.de", "https://user:password@app.verwalino.de", "https://app.verwalino.de/path"):
            with self.subTest(url=url), self.assertRaises(canary.CanaryError):
                canary.Client(url)


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class WorkflowTests(unittest.TestCase):
    def test_schedule_budget_and_both_first_failure_alerts(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/canary.yaml").read_text())
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(triggers["schedule"], [{"cron": "*/15 * * * *"}])
        job = workflow["jobs"]["canary"]
        self.assertEqual(job["timeout-minutes"], 5)
        self.assertEqual(job["permissions"], {"contents": "read", "issues": "write"})
        steps = {step["name"]: step for step in job["steps"]}
        self.assertEqual(steps["Run production canary"]["timeout-minutes"], 2)
        self.assertIn("https://app.verwalino.de", steps["Run production canary"]["run"])
        mail = steps["Send owner email"]
        self.assertRegex(mail["uses"], r"^dawidd6/action-send-mail@[a-f0-9]{40}$")
        self.assertEqual(mail["with"]["to"], "${{ secrets.ALERT_EMAIL }}")
        self.assertEqual(mail["if"], "${{ failure() && steps.issue.outputs.canary_issue_created == 'true' }}")
        self.assertEqual(steps["File canary issue"]["if"], "${{ failure() }}")
        self.assertEqual(steps["File canary issue"]["id"], "issue")
        self.assertLess(list(steps).index("File canary issue"), list(steps).index("Send owner email"))
        self.assertIn("scripts/scenario_issues.py", steps["File canary issue"]["run"])
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])


if __name__ == "__main__":
    unittest.main()
