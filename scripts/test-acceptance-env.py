"""Acceptance environment contracts: stdlib only, fake uv/npm/alembic/uvicorn, no database.

Run from the repository root: python3 -B scripts/test-acceptance-env.py
"""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from installer_test_process import install_interrupt_handlers, managed_process, run, unittest_main

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/acceptance-env.sh"
BASH = shutil.which("bash")
SUBCOMMANDS = ("start", "create-organization", "erase-organization", "stop")
with socket.socket() as port_probe:
    port_probe.bind(("127.0.0.1", 0))
    TEST_PORT = str(port_probe.getsockname()[1])

# One source for every fake binary; the file name selects the tool. The state directory is baked in
# because the script runs the venv tools in a cleaned environment.
FAKE_TOOL = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
STATE = pathlib.Path("@STATE@")
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]

def log(**extra):
    with open(STATE / "calls.jsonl", "a") as handle:
        handle.write(json.dumps({"tool": name, "args": args, "cwd": os.getcwd(), **extra}) + "\n")

def read(file, default=""):
    path = STATE / file
    return path.read_text().strip() if path.exists() else default

def write(file, value):
    (STATE / file).write_text(value)

if name == "uv":
    venv = pathlib.Path(os.environ["UV_PROJECT_ENVIRONMENT"])
    log(venv=str(venv))
    for tool in ("python", "uvicorn", "alembic"):
        target = venv / "bin" / tool
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(pathlib.Path(__file__).read_text())
        target.chmod(0o755)
elif name == "npm":
    log()
    if args[:1] == ["ci"]:
        pathlib.Path("node_modules").mkdir()
elif name == "npx":
    log()
    if (STATE / "fail_build").exists():
        sys.exit("fake build failure")
    out = pathlib.Path(args[args.index("--outDir") + 1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text("<!doctype html>")
elif name == "alembic":
    log()
    print("INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.", file=sys.stderr)
    head, current, known = read("head", "0002"), read("db_rev"), read("known").split(",")
    if args == ["heads"]:
        print(head + " (head)")
    elif args[:1] == ["show"]:
        sys.exit(0 if args[1] in known else 255)
    elif args == ["upgrade", "head"]:
        write("db_rev", head)
    else:
        sys.exit(98)
elif name == "uvicorn":
    from http.server import BaseHTTPRequestHandler, HTTPServer
    log(env={k: v for k, v in os.environ.items() if k.startswith(("HV_", "VERWALINO_"))})
    write("uvicorn.pid", str(os.getpid()))
    port = int(args[args.index("--port") + 1])

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(200 if self.path == "/api/health/live" else 404, {"status": "ok"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            accounts = json.loads(read("accounts.json", "{}"))
            ok = self.path == "/api/auth/login" and accounts.get(body["email"].lower()) == body["password"]
            self.reply(200 if ok else 401, {})

        def log_message(self, *_):
            pass

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
elif name == "python":
    if args[:2] == ["-m", "app.admin_cli"]:
        password = sys.stdin.readline().rstrip("\n")
        org, email = args[args.index("--org") + 1], args[args.index("--email") + 1]
        log(password_from_stdin="--password-stdin" in args, env={k: v for k, v in os.environ.items() if k.startswith("HV_DATABASE")})
        accounts = json.loads(read("accounts.json", "{}"))
        if email.lower() in accounts:
            print("User already exists - nothing to do.")
        else:
            accounts[email.lower()] = password
            write("accounts.json", json.dumps(accounts))
            print("Created organization '%s'." % org)
    elif args[:1] == ["-c"]:
        log()
        if read("db_rev"):
            print(read("db_rev"))
    elif args[:1] == ["-"]:
        write("erase_helper.py", sys.stdin.read())
        log(env={k: v for k, v in os.environ.items() if k.startswith(("HV_DATABASE", "HV_DOCUMENT"))})
        rc = int(read("erase_rc", "0"))
        if rc == 0:
            print("erased: 1, failed: 0, file cleanup pending: 0")
        sys.exit(rc)
    else:
        sys.exit(99)
else:
    sys.exit(97)
'''


class AcceptanceEnvCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.state = self.base / "state"
        self.state.mkdir()
        self.acceptance_lock = self.base / "acceptance.lock"
        self.testdb_lock = self.base / "testdb.lock"
        self.scratch_prefix = self.base / "scratch"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for tool in ("uv", "npm", "npx"):
            path = self.bin / tool
            path.write_text(FAKE_TOOL.replace("@STATE@", str(self.state)))
            path.chmod(0o755)
        (self.state / "known").write_text("0001,0002")
        (self.state / "db_rev").write_text("0002")
        self.checkout = self.base / "checkout"
        self.make_checkout()
        self.addCleanup(self.stop_leftover_app)

    def make_checkout(self):
        text = SCRIPT.read_text()
        # The script keeps its four fixed locations in constants; the sandbox rewrites exactly those lines.
        for name, value in (("ACCEPTANCE_LOCK", self.acceptance_lock), ("TESTDB_LOCK", self.testdb_lock),
                            ("SCRATCH_PREFIX", self.scratch_prefix), ("PORT", TEST_PORT)):
            text, replaced = re.subn(rf"^{name}=.*$", lambda _: f"{name}={shlex.quote(str(value))}", text, flags=re.M)
            self.assertEqual(replaced, 1, name)
        self.script_path = self.checkout / "scripts/acceptance-env.sh"
        self.script_path.parent.mkdir(parents=True)
        self.script_path.write_text(text)
        shutil.copyfile(ROOT / "scripts/test-env.sh", self.checkout / "scripts/test-env.sh")
        (self.checkout / "Makefile").write_text('check-testdb:\n\t@test ! -e "$(FAKE_STATE)/no-testdb"\n')
        for name in ("backend/pyproject.toml", "frontend/package.json"):
            (self.checkout / name).parent.mkdir(parents=True, exist_ok=True)
            (self.checkout / name).write_text("{}\n")
        git = ["git", "-C", str(self.checkout), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        run(git + ["init", "-q"], check=True)
        run(git + ["add", "."], check=True)
        run(git + ["commit", "-q", "-m", "fixture"], check=True)
        self.commit = run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    def stop_leftover_app(self):
        pid_file = self.state / "uvicorn.pid"
        if not pid_file.exists():
            return
        pid = int(pid_file.read_text())
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        if str(self.base) in command:
            os.kill(pid, 9)

    def script(self, *args, env=None, timeout=120):
        environment = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_STATE=str(self.state))
        environment.update(env or {})
        return run([BASH, str(self.script_path), *args], cwd=self.base, env=environment,
                   capture_output=True, text=True, timeout=timeout)

    def calls(self, tool=None):
        path = self.state / "calls.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if tool is None or row["tool"] == tool]

    def commands(self):
        """Bootstrap and erasure runs of the commit's Python; the revision read (-c) is not one."""
        return [row for row in self.calls("python") if row["args"][:1] != ["-c"]]

    def start(self, **kwargs):
        result = self.script("start", self.commit, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def instance(self):
        fields = dict(line.split("=", 1) for line in (self.acceptance_lock / "instance").read_text().splitlines())
        return fields

    def health(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}/api/health/live", timeout=2) as answer:
                return answer.status
        except OSError:
            return None

    def alive(self, pid):
        return subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0

    def scratch_dirs(self):
        return sorted(self.base.glob("scratch.*"))

    def assertNothingLeft(self):
        self.assertFalse(self.acceptance_lock.exists())
        self.assertFalse(self.testdb_lock.exists())
        self.assertEqual(self.scratch_dirs(), [])
        self.assertIsNone(self.health())

    def sign_in(self, email, password):
        request = urllib.request.Request(
            f"http://127.0.0.1:{TEST_PORT}/api/auth/login", method="POST",
            data=json.dumps({"email": email, "password": password}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=2) as answer:
                return answer.status
        except urllib.error.HTTPError as error:
            return error.code


class StartStopTests(AcceptanceEnvCase):
    def test_start_serves_the_commit_with_both_locks_and_a_second_start_is_refused(self):
        result = self.start(env={"HV_DEPLOYMENT_MODE": "hosted", "HV_DATABASE_URL": "postgresql://prod.invalid/x"})
        scratch = Path(self.instance()["scratch"])
        pid = int(self.instance()["pid"])
        self.assertEqual(self.health(), 200)
        self.assertIn(f"http://127.0.0.1:{TEST_PORT}/", result.stdout)
        self.assertIn(str(pid), result.stdout)
        self.assertIn(str(scratch), result.stdout)
        self.assertTrue(scratch.is_dir() and scratch.parent == self.base)
        for lock in (self.acceptance_lock, self.testdb_lock):
            self.assertEqual((lock / "info").read_text().split()[0], self.commit)
        self.assertEqual((self.acceptance_lock / "info").read_text(), (self.testdb_lock / "info").read_text())
        # The commit is unpacked outside the checkout and installed and built from there.
        self.assertTrue((scratch / "src/backend/pyproject.toml").is_file())
        self.assertEqual([row["args"] for row in self.calls("uv")], [["sync", "--frozen", "--extra", "ops"]])
        self.assertEqual(self.calls("uv")[0]["cwd"], str(scratch / "src/backend"))
        self.assertEqual([row["args"][0] for row in self.calls("npm")], ["ci"])
        self.assertEqual(self.calls("npx")[0]["args"][:3], ["--no-install", "vite", "build"])
        self.assertTrue((scratch / "dist/index.html").is_file())
        # Self-hosted mode on the shared test database, whatever the caller exported.
        env = self.calls("uvicorn")[0]["env"]
        self.assertEqual(env["HV_DEPLOYMENT_MODE"], "selfhost")
        self.assertIn("127.0.0.1:55432", env["HV_DATABASE_URL"])
        self.assertEqual(env["HV_FRONTEND_DIST"], str(scratch / "dist"))
        self.assertEqual(env["HV_DOCUMENT_STORAGE_ROOT"], str(scratch / "documents"))
        self.assertNotEqual(env["HV_SESSION_SECRET"], "CHANGE-ME-IN-PRODUCTION")
        self.assertEqual(self.calls("uvicorn")[0]["args"], ["app.main:app", "--host", "127.0.0.1", "--port", TEST_PORT])
        # A database at the commit's head is left alone; its revision is read directly, since
        # `alembic current` fails for a revision the commit does not know.
        self.assertEqual([row["args"] for row in self.calls("alembic")], [["heads"]])
        self.assertEqual([row["args"][0] for row in self.calls("python")], ["-c"])

        before = {path: (path / "info").read_text() for path in (self.acceptance_lock, self.testdb_lock)}
        second = self.script("start", self.commit)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual({path: (path / "info").read_text() for path in before}, before)
        self.assertEqual(self.scratch_dirs(), [scratch])
        self.assertEqual(len(self.calls("uv")), 1)
        self.assertTrue(self.alive(pid))
        self.assertEqual(self.health(), 200)

        stopped = self.script("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIn(f"port {TEST_PORT} is free", stopped.stdout)
        self.assertFalse(self.alive(pid))
        self.assertNothingLeft()

    def test_start_is_refused_while_either_lock_is_held_and_leaves_the_foreign_lock_alone(self):
        for held, taken in ((self.acceptance_lock, self.testdb_lock), (self.testdb_lock, self.acceptance_lock)):
            with self.subTest(held=held.name):
                held.mkdir()
                (held / "info").write_text("someone-else 2026-11-16T10:00:00Z\n")
                result = self.script("start", self.commit)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((held / "info").read_text(), "someone-else 2026-11-16T10:00:00Z\n")
                # A lock this attempt took before it found the other one held is given back.
                self.assertFalse(taken.exists())
                self.assertEqual(self.scratch_dirs(), [])
                self.assertEqual(self.calls("uv"), [])
                self.assertIsNone(self.health())
                (held / "info").unlink()
                held.rmdir()

    def test_start_is_refused_when_the_port_is_occupied(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", int(TEST_PORT)))
            listener.listen()
            result = self.script("start", self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertNothingLeft()

    def test_start_is_refused_when_the_database_is_ahead_of_the_commit(self):
        (self.state / "db_rev").write_text("0009")
        result = self.script("start", self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("uvicorn"), [])
        self.assertNotIn(["upgrade", "head"], [row["args"] for row in self.calls("alembic")])
        self.assertEqual((self.state / "db_rev").read_text(), "0009")
        self.assertNothingLeft()

    def test_start_migrates_a_database_behind_the_commit(self):
        (self.state / "db_rev").write_text("0001")
        self.start()
        self.assertIn(["upgrade", "head"], [row["args"] for row in self.calls("alembic")])
        self.assertEqual((self.state / "db_rev").read_text(), "0002")
        self.assertEqual(self.health(), 200)
        self.assertEqual(self.script("stop").returncode, 0)

    def test_start_is_refused_without_the_test_database_or_an_unknown_commit(self):
        (self.state / "no-testdb").write_text("")
        self.assertNotEqual(self.script("start", self.commit).returncode, 0)
        self.assertNothingLeft()
        (self.state / "no-testdb").unlink()
        for argument in ("0" * 40, "--help", ""):
            with self.subTest(commit=argument):
                self.assertNotEqual(self.script("start", argument).returncode, 0)
                self.assertNothingLeft()
        self.assertEqual(self.calls(), [])

    def test_a_failed_start_removes_what_it_created(self):
        (self.state / "fail_build").write_text("")
        result = self.script("start", self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("uvicorn"), [])
        self.assertNothingLeft()

    def test_stop_refuses_a_process_it_did_not_start(self):
        self.start()
        pid = int(self.instance()["pid"])
        instance = (self.acceptance_lock / "instance").read_text()
        with managed_process(["sleep", "300"]) as foreign:
            tampered = {
                "another number": instance.replace(f"pid={pid}\n", f"pid={foreign.pid}\n"),
                "a reused number": instance.replace(f"pid={pid}\n", f"pid={foreign.pid}\n")
                                   .replace(self.instance()["started"], "Thu Jan  1 00:00:00 1970"),
                "another start time": re.sub(r"^started=.*$", "started=Thu Jan  1 00:00:00 1970", instance, flags=re.M),
            }
            for label, text in tampered.items():
                with self.subTest(label):
                    (self.acceptance_lock / "instance").write_text(text)
                    result = self.script("stop")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIsNone(foreign.poll())
                    self.assertTrue(self.alive(pid))
                    self.assertTrue(self.acceptance_lock.is_dir() and self.testdb_lock.is_dir())
                    self.assertEqual(len(self.scratch_dirs()), 1)
        (self.acceptance_lock / "instance").write_text(instance)
        self.assertEqual(self.script("stop").returncode, 0)
        self.assertNothingLeft()

    def test_stop_recognizes_a_launch_that_never_recorded_its_identity(self):
        self.start()
        pid = int(self.instance()["pid"])
        instance = (self.acceptance_lock / "instance").read_text()
        recorded_pid_only = "".join(line + "\n" for line in instance.splitlines() if line.split("=")[0] in ("scratch", "pid"))
        with managed_process(["sleep", "300"]) as foreign:
            (self.acceptance_lock / "instance").write_text(recorded_pid_only.replace(f"pid={pid}\n", f"pid={foreign.pid}\n"))
            self.assertNotEqual(self.script("stop").returncode, 0)
            self.assertIsNone(foreign.poll())
            self.assertTrue(self.acceptance_lock.is_dir())
        (self.acceptance_lock / "instance").write_text(recorded_pid_only)
        self.assertEqual(self.script("stop").returncode, 0)
        self.assertFalse(self.alive(pid))
        self.assertNothingLeft()

    def test_stop_leaves_a_lock_that_is_not_its_own(self):
        self.acceptance_lock.mkdir()
        (self.acceptance_lock / "info").write_text("someone-else 2026-11-16T10:00:00Z\n")
        self.assertNotEqual(self.script("stop").returncode, 0)
        self.assertEqual((self.acceptance_lock / "info").read_text(), "someone-else 2026-11-16T10:00:00Z\n")

    def test_stop_without_an_instance_has_nothing_to_do(self):
        result = self.script("stop")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNothingLeft()

    def test_stop_finishes_a_run_whose_app_is_already_gone(self):
        self.start()
        pid = int(self.instance()["pid"])
        os.kill(pid, 9)
        deadline = time.monotonic() + 10
        while self.alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.script("stop").returncode, 0)
        self.assertNothingLeft()


class OrganizationTests(AcceptanceEnvCase):
    def test_create_organization_prints_a_sign_in_that_works(self):
        self.start()
        result = self.script("create-organization", "Verwaltung Abnahme", "Admin@Example.de")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        password = re.search(r"^Password: (\S+)$", result.stdout, flags=re.M).group(1)
        self.assertIn("admin@example.de", result.stdout.lower())
        self.assertIn(f"http://127.0.0.1:{TEST_PORT}/", result.stdout)
        self.assertEqual(self.sign_in("admin@example.de", password), 200)
        # The bootstrap command gets the password on stdin, in the backend directory of the unpacked commit.
        call = self.commands()[0]
        self.assertTrue(call["password_from_stdin"])
        self.assertTrue(call["cwd"].endswith("src/backend"))
        self.assertNotIn(password, (self.state / "calls.jsonl").read_text())
        self.assertEqual(self.script("stop").returncode, 0)

    def test_create_organization_refuses_an_address_that_already_exists(self):
        self.start()
        self.assertEqual(self.script("create-organization", "Erste", "admin@example.de").returncode, 0)
        result = self.script("create-organization", "Zweite", "admin@example.de")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Password:", result.stdout)
        self.assertEqual(self.script("stop").returncode, 0)

    def test_organization_commands_need_a_running_instance_and_arguments(self):
        self.assertNotEqual(self.script("create-organization", "Name", "a@example.de").returncode, 0)
        self.assertNotEqual(self.script("erase-organization", "Name").returncode, 0)
        self.assertEqual(self.calls(), [])
        self.start()
        for command in (["create-organization"], ["create-organization", "Name"], ["create-organization", "", "a@example.de"],
                        ["create-organization", "Name", "a@example.de", "x"], ["erase-organization"],
                        ["erase-organization", "a", "b"], ["stop", "now"]):
            with self.subTest(command=command):
                self.assertNotEqual(self.script(*command).returncode, 0)
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.script("stop").returncode, 0)

    def test_erase_organization_runs_only_for_an_organization_this_run_created(self):
        self.start()
        refused = self.script("erase-organization", "Eine fremde Organisation")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.script("create-organization", "Verwaltung Abnahme", "admin@example.de").returncode, 0)
        result = self.script("erase-organization", "Verwaltung Abnahme")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("waiting time", result.stdout.lower())
        self.assertIn("simulated", result.stdout.lower())
        call = self.commands()[-1]
        self.assertEqual(call["args"], ["-", "Verwaltung Abnahme"])
        self.assertTrue(call["cwd"].endswith("src/backend"))
        self.assertIn("127.0.0.1:55432", call["env"]["HV_DATABASE_URL_MIGRATOR"])
        self.assertEqual(call["env"]["HV_DOCUMENT_STORAGE_ROOT"], str(Path(self.instance()["scratch"]) / "documents"))
        # The helper names the organization and its requested deletion; it never runs without a selection.
        helper = (self.state / "erase_helper.py").read_text()
        compile(helper, "erase-helper", "exec")
        self.assertIn("cooling_off", helper)
        self.assertIn("organization_ids=[", helper)
        self.assertEqual(self.script("stop").returncode, 0)

    def test_erase_organization_fails_when_the_erasure_does_not_complete(self):
        self.start()
        self.assertEqual(self.script("create-organization", "Verwaltung Abnahme", "admin@example.de").returncode, 0)
        (self.state / "erase_rc").write_text("1")
        result = self.script("erase-organization", "Verwaltung Abnahme")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("erased organization", result.stdout.lower())
        self.assertEqual(self.script("stop").returncode, 0)


class SurfaceTests(AcceptanceEnvCase):
    def test_usage_names_exactly_the_four_subcommands(self):
        for arguments in ([], ["bogus"], ["--help"]):
            with self.subTest(arguments=arguments):
                result = self.script(*arguments)
                self.assertEqual(result.returncode, 0 if arguments == ["--help"] else 2, result.stderr)
                text = result.stdout + result.stderr
                for name in SUBCOMMANDS:
                    self.assertIn(name, text)
        self.assertEqual(self.calls(), [])
        self.assertNothingLeft()

    def test_script_never_touches_docker_or_production(self):
        text = SCRIPT.read_text()
        self.assertNotIn("docker", text)
        self.assertNotIn("COMPOSE_PROJECT_NAME", text)
        self.assertIn("/tmp/hausverwaltung-acceptance.lock", text)
        self.assertIn("/tmp/verwalino-testdb.lock", text)
        self.assertRegex(text, r"(?m)^PORT=18000$")

    def test_contract_test_is_part_of_the_installer_target(self):
        result = run(["make", "-n", "test-installer"], cwd=ROOT, check=True, capture_output=True, text=True)
        self.assertIn("python3 -B scripts/test-acceptance-env.py", result.stdout)


if __name__ == "__main__":
    install_interrupt_handlers()
    unittest_main()
