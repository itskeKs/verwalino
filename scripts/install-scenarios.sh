#!/usr/bin/env bash
# Run only on a disposable VM/runner with a clean Docker context, never production.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Python stdlib handles JSON, monotonic timings and cleanup; all installation
# operations execute the checkout's Bash installer and its shipped helper.
python3 - "$ROOT" "$@" <<'PY'
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

root = Path(sys.argv[1])
parser = argparse.ArgumentParser(description="Install scenarios on a disposable, clean Docker VM.")
parser.add_argument("--bundle", type=Path, required=True)
parser.add_argument("--version", required=True, help="Local checkout label, e.g. v0.0.0")
parser.add_argument("--report", type=Path, default=Path("report"))
args = parser.parse_args(sys.argv[2:])
args.bundle, args.report = args.bundle.resolve(), args.report.resolve()
args.report.mkdir(parents=True, exist_ok=True)
env = {key: value for key, value in os.environ.items()
       if key != "USER" and not key.startswith(("VERWALINO_", "COMPOSE_"))}


def now():
    return datetime.now(timezone.utc).isoformat()


class Failure(Exception):
    def __init__(self, kind, message):
        self.kind = "install." + kind
        super().__init__(message)


def require(condition, message):
    if not condition:
        raise Failure("AssertionError", message)


def command(*argv, input=None, timeout=480, check=True, environment=None):
    result = subprocess.run(argv, input=input, text=True, capture_output=True,
                            env=environment or env, cwd=root, timeout=timeout, check=False)
    if check and result.returncode:
        # Never include installer output, setup tokens, passwords or session data.
        raise Failure("CommandError", f"{Path(argv[0]).name} exited with status {result.returncode}.")
    return result


def docker(*argv, **kwargs):
    return command("docker", *argv, **kwargs)


def helper(*argv, **kwargs):
    return command("bash", str(installation / "scripts/self-host.sh"), *argv, **kwargs)


def install(directory, *options):
    return command("bash", str(root / "install.sh"), "--version", args.version,
                   "--offline-bundle", str(args.bundle), "--dir", str(directory), *options,
                   check=False)


def http(path, *, data=None):
    options = ["--fail", "--silent", "--show-error", "--max-time", "5"]
    if data is not None:
        options += ["--header", "Content-Type: application/json", "--data-binary", "@-"]
    return command("curl", *options, url + path,
                   input=json.dumps(data) if data is not None else None, timeout=10).stdout


def ready():
    deadline = time.monotonic() + 60
    while True:
        try:
            http("/api/health/ready")
            return
        except Failure:
            if time.monotonic() >= deadline:
                raise Failure("ReadinessTimeout", "Readiness endpoint did not succeed within 60 seconds.")
            time.sleep(1)


def preflight():
    # Do not touch an existing project, even a stopped one or orphaned volumes.
    for options in (("ps", "-aq", "--filter", "label=com.docker.compose.project=verwalino"),
                    ("volume", "ls", "-q", "--filter", "label=com.docker.compose.project=verwalino")):
        require(not docker(*options).stdout.strip(), "Use a clean Docker context with no verwalino resources.")


def fresh():
    result = install(installation, "--port", str(port))
    require(result.returncode == 0, "Fresh install command must succeed.")
    require(re.search(r"^Setup token: [a-f0-9]{64}$", result.stdout, re.M), "Installer must print a setup token.")
    require(url + "/setup" in result.stdout, "Installer must print the setup URL.")


def setup():
    require(json.loads(http("/api/setup")).get("required") is True, "Fresh instance must require setup.")
    require("<html" in http("/setup").lower(), "Setup page must serve HTML.")


def second():
    before = {path.relative_to(installation): path.read_bytes()
              for path in installation.rglob("*") if path.is_file()}
    refused = install(installation, "--port", str(port))
    require(refused.returncode != 0 and "Configuration exists; use --force" in refused.stderr,
            "Repeat install must refuse existing configuration with a clear --force message.")
    retried = install(installation, "--force")
    require(retried.returncode == 0, "Retry with --force must succeed.")
    after = {path.relative_to(installation): path.read_bytes()
             for path in installation.rglob("*") if path.is_file()}
    require(before == after, "Retry must keep every installed file byte for byte.")


def listen():
    global listener
    listener = subprocess.Popen(["nc", "-l", "-k", "127.0.0.1", str(port)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 5
    while listener.poll() is None and time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise Failure("ListenerError", "Dummy listener did not bind within five seconds.")


def unlisten():
    global listener
    if listener is not None:
        listener.terminate()
        try:
            listener.wait(timeout=5)
        except subprocess.TimeoutExpired:
            listener.kill()
            listener.wait(timeout=5)
        listener = None


def busy():
    rejected = install(work / "busy", "--port", str(port))
    message = f"Port {port} is already in use"
    require(rejected.returncode != 0 and message in rejected.stderr,
            "Init must refuse an occupied port with the documented message.")
    require(not (work / "busy/.env.selfhost").exists(), "Refused init must not create private configuration.")
    rejected = helper("start", check=False)
    require(rejected.returncode != 0 and message in rejected.stderr,
            "Start must refuse an occupied port with the documented message.")
    require(not compose("ps", "--status", "running", "--quiet").stdout.strip(),
            "Port refusal must leave no installation containers running.")


def compose(*options, **kwargs):
    return docker("compose", "--project-name", "verwalino", "--env-file", str(installation / ".env.selfhost"),
                  "-f", str(installation / "infra/docker-compose.yml"), "--profile", "ops", *options,
                  environment=env | {"VERWALINO_ALLOWED_HOSTS": "localhost,127.0.0.1",
                                     "VERWALINO_COOKIE_SECURE": "false", "VERWALINO_COOKIE_NAME": "hv_session"},
                  **kwargs)


def admin():
    helper("admin", "--org", "Install matrix synthetic organization", "--email", "install@example.org",
           "--password-stdin", input=password + "\n")


def login():
    global principal
    body = json.loads(http("/api/auth/login", data={"email": "install@example.org", "password": password}))
    require(body.get("role") == "OWNER_ADMIN" and body.get("user_id") and body.get("organization_id"),
            "CLI-created administrator must be able to log in.")
    if principal is not None:
        require(principal == body, "Restart must preserve administrator and organization identity.")
    principal = body


def artifacts(row):
    if not owned or not (installation / ".env.selfhost").exists():
        return
    directory = args.report / "artifacts" / str(rows.index(row) + 1)
    directory.mkdir(parents=True, exist_ok=True)
    for name, capture in (("status.txt", lambda: helper("status", check=False)),
                          ("compose.log", lambda: compose("logs", "--no-color", "--tail", "200", check=False))):
        path = directory / name
        try:
            result = capture()
            path.write_text(result.stdout + result.stderr)
        except Exception as error:
            path.write_text(f"Capture unavailable: {type(error).__name__}\n")
        relative = path.relative_to(args.report).as_posix()
        if relative not in row["artifacts"]:
            row["artifacts"].append(relative)


def step(row, name, action):
    started = time.monotonic()
    record = {"keyword": "Then", "name": name, "outcome": "passed", "duration_s": 0.0, "error": None}
    row["steps"].append(record)
    try:
        action()
    except Exception as error:
        detail = {"type": error.kind if isinstance(error, Failure) else "install." + type(error).__name__,
                  "message": str(error) if isinstance(error, Failure) else "Scenario operation failed."}
        record.update(outcome="failed", error=detail)
        if row["outcome"] != "failed":
            row.update(outcome="failed", error=detail)
        artifacts(row)
        raise
    finally:
        record["duration_s"] = round(time.monotonic() - started, 6)
        row["duration_s"] = round(sum(item["duration_s"] for item in row["steps"]), 6)


work = None
installation = None
listener = None
owned = False
principal = None
password = secrets.token_hex(24)
run = {"started": now(), "finished": None, "git_sha": os.environ.get("GITHUB_SHA"),
       "mode": "selfhost", "runner": "github-actions:" + os.environ["GITHUB_RUN_ID"]
       if os.environ.get("GITHUB_RUN_ID") else "local"}
if not run["git_sha"] and shutil.which("git"):
    run["git_sha"] = command("git", "rev-parse", "HEAD", check=False).stdout.strip() or None
names = ["Fresh install reaches setup", "Second run is harmless", "Busy port is refused", "Restart keeps data"]
rows = [{"feature": "install", "scenario": name, "tags": [], "steps": [], "outcome": "skipped",
         "duration_s": 0.0, "error": None, "artifacts": []} for name in names]
active = rows[0]
exitcode = 0


def interrupted(signum, _frame):
    raise Failure("Interrupted", f"Run interrupted by signal {signum}.")


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
try:
    step(active, "Require a clean Docker context", preflight)
    work = Path(tempfile.mkdtemp(prefix="verwalino-install-"))
    installation = work / "instance"
    # Checkout bundles have checksums but no release signature. Give installer
    # children a deterministic tool PATH without cosign; release tests cover it.
    toolbin = work / "bin"
    toolbin.mkdir()
    for name in ("bash", "cat", "chmod", "cp", "dirname", "grep", "ln", "mkdir", "mktemp", "mv",
                 "openssl", "rm", "rmdir", "sed", "sha256sum", "shasum", "stat", "docker", "curl", "nc"):
        if executable := shutil.which(name):
            (toolbin / name).symlink_to(executable)
    env["PATH"] = str(toolbin)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    owned = True
    scenarios = [
        [("Install in one command and print setup URL/token", fresh), ("Wait for readiness", ready),
         ("Setup is required and the setup page is reachable", setup)],
        [("Refuse a repeat, then retry with --force without file changes", second), ("Instance remains ready", ready)],
        [("Stop the instance", lambda: helper("stop")), ("Occupy its port", listen),
         ("Init and start refuse the port without leaving containers running", busy), ("Stop the dummy listener", unlisten)],
        [("Start the instance", lambda: helper("start")), ("Wait for readiness", ready),
         ("Create administrator via CLI", admin), ("Log in before restart", login),
         ("Stop the instance", lambda: helper("stop")), ("Start the instance again", lambda: helper("start")),
         ("Wait for readiness after restart", ready), ("Log in with the same identity after restart", login)],
    ]
    for active, actions in zip(rows, scenarios):
        for name, action in actions:
            step(active, name, action)
        active["outcome"] = "passed"
except Exception as error:
    exitcode = 1
    if active["outcome"] != "failed":
        active.update(outcome="failed", error={"type": "install." + type(error).__name__, "message": "Run setup failed."})
    print(active["error"]["message"], file=sys.stderr)
finally:
    try:
        unlisten()
        if owned and (installation / ".env.selfhost").exists():
            step(active, "Always stop the test installation", lambda: helper("stop"))
    except Exception:
        exitcode = 1
    finally:
        if work is not None:
            shutil.rmtree(work)
        run["finished"] = now()
        (args.report / "scenarios.json").write_text(json.dumps({"run": run, "scenarios": rows}, indent=2) + "\n")
        lines = ["# Scenario results", "", "| Feature | Scenario | Outcome | Duration (s) |", "|---|---|---|---|"]
        lines += [f"| install | {row['scenario']} | {row['outcome']} | {row['duration_s']:.3f} |" for row in rows]
        (args.report / "summary.md").write_text("\n".join(lines) + "\n")
sys.exit(exitcode)
PY
