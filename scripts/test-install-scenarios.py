"""Install matrix contracts: real installer/helper, stdlib shims, no Docker or DB."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64
NAMES = ["Fresh install reaches setup", "Second run is harmless",
         "Busy port is refused", "Restart keeps data"]

SHIM = r'''
import hashlib, json, os, pathlib, signal, socket, sys, time
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
base = pathlib.Path(os.environ["MATRIX_TEST_STATE"])
state_file = base / "state.json"
state = json.loads(state_file.read_text()) if state_file.exists() else {}
with (base / "calls.jsonl").open("a") as log:
    log.write(json.dumps([name, args]) + "\n")
def save():
    state_file.write_text(json.dumps(state))
if name == "nc":
    with socket.socket() as server:
        server.bind(("127.0.0.1", int(args[-1])))
        server.listen()
        (base / "listener.pid").write_text(str(os.getpid()))
        def stop(*_):
            (base / "listener.stopped").touch()
            sys.exit(0)
        signal.signal(signal.SIGTERM, stop)
        while True:
            conn, _ = server.accept()
            conn.close()
elif name == "docker":
    if args == ["compose", "version", "--short"]:
        print("2.24.0")
    elif args == ["info"]:
        pass
    elif args[0] in ("ps", "volume"):
        if os.environ.get("MATRIX_EXISTING"):
            print("existing-installation")
    elif args[0] != "compose":
        sys.exit("Unexpected Docker command")
    elif "logs" in args:
        print("synthetic compose logs")
    elif "ps" in args:
        if "--quiet" in args or "-q" in args:
            if state.get("running") or (os.environ.get("MATRIX_BUSY_RUNNING") and "app" not in args):
                print("synthetic-app")
        else:
            print("synthetic status")
    elif "psql" in args:
        if any("SELECT count(*)" in arg for arg in args):
            print("1" if state.get("admin") else "0")
        else:
            sys.stdin.read()
            print("t")
    elif "bootstrap" in args:
        state["admin"] = hashlib.sha256(sys.stdin.readline().strip().encode()).hexdigest()
        save()
    elif "stop" in args:
        if args[-1] != "app":
            state["running"] = False
            state["cleanups"] = state.get("cleanups", 0) + 1
            save()
    elif "up" in args and args[-1] == "app":
        state["running"] = True
        save()
elif name == "curl":
    url = args[-1]
    if not state.get("running"):
        sys.exit(7)
    if url.endswith("/api/health/ready"):
        print('{"status":"ready"}')
    elif url.endswith("/api/setup"):
        print(os.environ.get("MATRIX_SETUP", '{"required":true}'))
    elif url.endswith("/setup"):
        print('<!doctype html><html><div id="root"></div></html>')
    elif url.endswith("/api/auth/login"):
        body = json.loads(sys.stdin.read())
        state["logins"] = state.get("logins", 0) + 1
        save()
        if (os.environ.get("MATRIX_FAIL_LOGIN") == str(state["logins"])
                or hashlib.sha256(body["password"].encode()).hexdigest() != state.get("admin")):
            sys.exit(22)
        print('{"user_id":"test-user","organization_id":"test-org","role":"OWNER_ADMIN"}')
    else:
        sys.exit("Unexpected URL")
'''


class InstallScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name in ("bash", "cat", "chmod", "cp", "dirname", "grep", "ln", "mkdir",
                     "mktemp", "mv", "openssl", "rm", "rmdir", "sed", "shasum", "stat"):
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / "python3").symlink_to(sys.executable)
        for name in ("docker", "curl", "nc"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + SHIM)
            path.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("USER",) and not key.startswith(("COMPOSE_", "VERWALINO_", "MATRIX_"))}
        self.env.update(PATH=str(self.bin), MATRIX_TEST_STATE=str(self.base), TMPDIR=str(self.base),
                        GITHUB_SHA="b" * 40)
        self.bundle = self.base / "bundle with spaces"
        self.report = self.base / "report with spaces"

    def build_bundle(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/build-release-bundle.sh"), str(self.bundle)],
                                env=dict(self.env, DIGEST=DIGEST), capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Checkout bundles are unsigned; production release signing stays in its workflow.
        (self.bundle / "SHA256SUMS.sigstore.json").write_text("{}\n")

    def run_scenarios(self):
        self.build_bundle()
        return subprocess.run(["bash", str(ROOT / "scripts/install-scenarios.sh"),
                               "--bundle", str(self.bundle), "--version", "v0.0.0",
                               "--report", str(self.report)], env=self.env,
                              capture_output=True, text=True, check=False, timeout=30)

    def rows(self):
        report = json.loads((self.report / "scenarios.json").read_text())
        self.assertEqual(set(report), {"run", "scenarios"})
        self.assertEqual(set(report["run"]), {"started", "finished", "git_sha", "mode", "runner"})
        self.assertIsNotNone(report["run"]["finished"])
        rows = report["scenarios"]
        self.assertEqual([row["scenario"] for row in rows], NAMES)
        for row in rows:
            self.assertEqual(set(row), {"feature", "scenario", "tags", "steps", "outcome",
                                        "duration_s", "error", "artifacts"})
            self.assertEqual(row["feature"], "install")
            self.assertGreaterEqual(row["duration_s"], 0)
            for step in row["steps"]:
                self.assertEqual(set(step), {"keyword", "name", "outcome", "duration_s", "error"})
        return rows

    def calls(self):
        return [json.loads(line) for line in (self.base / "calls.jsonl").read_text().splitlines()]

    def assert_cleaned(self):
        state = json.loads((self.base / "state.json").read_text())
        self.assertFalse(state["running"])
        self.assertGreater(state["cleanups"], 0)
        self.assertEqual(list(self.base.glob("verwalino-install-*")), [])
        for name, args in self.calls():
            if name == "docker" and args[0] == "compose" and "-f" in args:
                self.assertEqual(args[args.index("--project-name") + 1], "verwalino")
                self.assertIn("verwalino-install-", args[args.index("-f") + 1])
                self.assertNotIn("down", args)
                self.assertNotIn("-v", args)

    def test_four_scenarios_pass_with_reporter_compatible_steps_and_cleanup(self):
        result = self.run_scenarios()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = self.rows()
        self.assertEqual([row["outcome"] for row in rows], ["passed"] * 4)
        for row in rows:
            self.assertTrue(row["steps"])
            self.assertTrue(all(step["outcome"] == "passed" for step in row["steps"]))
            self.assertIsNone(row["error"])
            self.assertEqual(row["artifacts"], [])
        self.assertEqual(json.loads((self.base / "state.json").read_text())["logins"], 2)
        self.assertTrue((self.base / "listener.stopped").exists())
        self.assertIn("Restart keeps data | passed", (self.report / "summary.md").read_text())
        self.assertNotIn("Setup token:", result.stdout + result.stderr)
        self.assert_cleaned()

    def test_failed_step_has_structured_error_status_logs_and_failure_cleanup(self):
        self.env["MATRIX_FAIL_LOGIN"] = "2"
        result = self.run_scenarios()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        row = self.rows()[-1]
        self.assertEqual(row["outcome"], "failed")
        self.assertEqual(row["error"]["type"], "install.CommandError")
        self.assertTrue(any(step["outcome"] == "failed" for step in row["steps"]))
        self.assertEqual(len(row["artifacts"]), 2)
        artifacts = [(self.report / path).read_text() for path in row["artifacts"]]
        self.assertTrue(any("synthetic status" in text for text in artifacts))
        self.assertTrue(any("synthetic compose logs" in text for text in artifacts))
        self.assert_cleaned()

    def test_fresh_install_failure_skips_dependents_and_still_cleans_up(self):
        self.env["MATRIX_SETUP"] = '{"required":false}'
        result = self.run_scenarios()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        rows = self.rows()
        self.assertEqual([row["outcome"] for row in rows], ["failed", "skipped", "skipped", "skipped"])
        self.assertEqual(rows[0]["error"]["type"], "install.AssertionError")
        self.assertEqual(len(rows[0]["artifacts"]), 2)
        self.assert_cleaned()

    def test_busy_port_failure_also_stops_the_dummy_listener(self):
        self.env["MATRIX_BUSY_RUNNING"] = "1"
        result = self.run_scenarios()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        row = self.rows()[2]
        self.assertEqual(row["outcome"], "failed")
        self.assertEqual(len(row["artifacts"]), 2)
        self.assertTrue((self.base / "listener.stopped").exists())
        self.assert_cleaned()

    def test_existing_installation_is_refused_without_stopping_it(self):
        self.env["MATRIX_EXISTING"] = "1"
        result = self.run_scenarios()
        self.assertEqual(result.returncode, 1)
        self.assertIn("clean Docker context", result.stdout + result.stderr)
        self.assertFalse(any(name == "docker" and "stop" in args for name, args in self.calls()))
        self.assertEqual(list(self.base.glob("verwalino-install-*")), [])


if __name__ == "__main__":
    unittest.main()
