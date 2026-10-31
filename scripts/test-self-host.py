"""Portable installer contracts; Docker is faked and no containers are started.

Run in backend/ with uv run python ../scripts/test-self-host.py.
"""
import hashlib
import errno
import io
import json
import os
import shlex
import shutil
import re
import runpy
import socket
import stat
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TARGET = runpy.run_path(str(ROOT / "backend/app/deployment.py"))["TARGET"]
SECRETS = ("MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD", "SESSION_SECRET")
PATH_BASH = shutil.which("bash")
PATH_BASH_VERSION = subprocess.check_output([PATH_BASH, "--version"], text=True).splitlines()[0]
MAC_BASH_VERSION = (subprocess.check_output(["/bin/bash", "--version"], text=True).splitlines()[0]
                    if Path("/bin/bash").exists() else "unavailable")
SMTP = {
    "HV_SMTP_HOST": "smtp.example.invalid", "HV_SMTP_PORT": "465",
    "HV_SMTP_FROM": "verwaltung@example.invalid", "HV_SMTP_SECURITY": "tls",
    "HV_SMTP_USERNAME": "smtp-user", "HV_SMTP_PASSWORD": "synthetic pa'ss#=word`touch marker`",
    "HV_SMTP_TIMEOUT_SECONDS": "30",
}
SMTP_DEFAULTS = {key: "" for key in SMTP} | {
    "HV_SMTP_PORT": "587", "HV_SMTP_SECURITY": "starttls", "HV_SMTP_TIMEOUT_SECONDS": "15",
}
with socket.socket() as port_probe:
    port_probe.bind(("127.0.0.1", 0))
    TEST_PORT = str(port_probe.getsockname()[1])


class SelfHostTests(unittest.TestCase):
    bash = PATH_BASH

    def test_help_points_to_the_documented_unlock_command(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("admin_cli unlock", result.stdout)
        self.assertNotIn("unlock SQL", result.stdout)

    def test_login_guidance_documents_implemented_signup_controls(self):
        for name in ("DEPLOY.md", "docs/architecture.md", "docs/feature-status.md"):
            with self.subTest(document=name):
                content = (ROOT / name).read_text()
                self.assertIn("honeypot", content)
                self.assertIn("signup", content)
        section = (ROOT / "DEPLOY.md").read_text().split("## Login lockout and proxy trust\n", 1)[1].split("\n## ", 1)[0]
        self.assertNotIn("Signup throttling arrives with #19.", section)
        for scope in ("signup", "signup_resend", "signup_confirm"):
            self.assertIn(f"`{scope}`", section)

    def test_login_guidance_explains_throttle_downgrade_refusal(self):
        section = (ROOT / "DEPLOY.md").read_text().split("## Login lockout and proxy trust\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`downgrade()` of 0142 refuses while rows exist", section)
        self.assertIn("delete the `auth_throttle` rows first", section)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "checkout"
        for name in ("scripts/self-host.sh", "backend/app/selfhost_backup.py", "backend/app/deployment.py", "infra/docker-compose.yml"):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.config = self.base / ".env.selfhost"
        self.log = self.base / "docker.jsonl"
        binary = self.base / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text('''#!/usr/bin/env python3
import hashlib, json, os, signal, stat, subprocess, sys
def config_image(path):
    if not os.path.isfile(path): return None
    with open(path) as config:
        return next(line.strip().split("=", 1)[1] for line in config if line.startswith("VERWALINO_IMAGE="))
env_file = sys.argv[sys.argv.index("--env-file") + 1] if "--env-file" in sys.argv else os.environ["VERWALINO_CONFIG"]
archive_digest = None
if "verify" in sys.argv and "--volume" in sys.argv:
    with open(sys.argv[sys.argv.index("--volume") + 1].rsplit(":", 1)[0] + "/archive.tar", "rb") as archive:
        archive_digest = hashlib.sha256(archive.read()).hexdigest()
if "--volume" in sys.argv:
    mount = sys.argv[sys.argv.index("--volume") + 1].rsplit(":", 1)[0]
    if not os.path.isabs(mount) or not os.path.isdir(mount):
        sys.exit("Invalid fake Docker bind source: " + repr(mount))
# Like Compose interpolation: the caller's environment wins, --env-file fills the rest.
if os.path.isfile(env_file):
    with open(env_file) as config:
        for line in config:
            key, _, value = line.rstrip("\\n").partition("=")
            if key.startswith("HV_SMTP_"): os.environ.setdefault(key, value)
smtp_password = os.environ.get("HV_SMTP_PASSWORD", "")
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "image": os.environ.get("VERWALINO_IMAGE"), "secure": os.environ.get("VERWALINO_COOKIE_SECURE"), "hosts": os.environ.get("VERWALINO_ALLOWED_HOSTS"), "profiles": os.environ.get("COMPOSE_PROFILES"), "caller_secret_used": os.environ.get("MIGRATOR_PASSWORD") == "attacker", "config_image": config_image(os.environ["VERWALINO_CONFIG"]), "env_image": config_image(env_file), "env_mode": stat.S_IMODE(os.stat(env_file).st_mode) if os.path.exists(env_file) else None, "locked": os.path.isdir(os.environ["VERWALINO_CONFIG"] + ".lock"), "archive_digest": archive_digest,
                         "smtp": {key: value for key, value in os.environ.items() if key.startswith("HV_SMTP_") and key != "HV_SMTP_PASSWORD"},
                         "smtp_password_matches": smtp_password == os.environ.get("FAKE_SMTP_PASSWORD", "")}) + "\\n")
signal_stage = os.environ.get("FAKE_INTERRUPT_STAGE")
signal_matches = (signal_stage == "stop" and "stop" in sys.argv) or (signal_stage == "writers" and "ps" in sys.argv and "--quiet" in sys.argv)
if signal_matches and not os.path.exists(os.environ["FAKE_INTERRUPT_SENT"]):
    import signal
    with open(os.environ["FAKE_INTERRUPT_SENT"], "w") as sent: sent.write("1")
    interrupted = getattr(signal, os.environ.get("FAKE_INTERRUPT_SIGNAL", "SIGINT"))
    signal.signal(interrupted, signal.SIG_IGN)
    os.killpg(os.getpgrp(), interrupted)
    sys.exit(128 + interrupted)
with open(os.environ["FAKE_CONTAINERS"]) as state:
    containers = json.load(state)
if "stop" in sys.argv:
    for service in sys.argv[sys.argv.index("stop") + 1:] or ("app", "db", "caddy", "migrate", "bootstrap"):
        containers[service] = "exited||" + service + "-id"
if "up" in sys.argv:
    for service in ("app", "db", "caddy"):
        if service in sys.argv: containers[service] = "running|healthy|" + service + "-id"
if "run" in sys.argv and sys.argv[-1] == "migrate":
    for service in os.environ.get("FAKE_LINGERING_WRITERS", "").split():
        containers[service] = "running||" + service + "-id"
if "stop" in sys.argv or "up" in sys.argv or ("run" in sys.argv and sys.argv[-1] == "migrate"):
    with open(os.environ["FAKE_CONTAINERS"], "w") as state:
        json.dump(containers, state)
if "psql" in sys.argv and containers.get("db", "running").split("|", 1)[0] != "running":
    sys.exit(17)
tags_path = os.environ["FAKE_IMAGE_TAGS"]
with open(tags_path) as state:
    tags = json.load(state)
if sys.argv[1:3] == ["image", "tag"]: tags.append(sys.argv[-1])
if "build" in sys.argv: tags.append(os.environ["VERWALINO_IMAGE"])
if sys.argv[1:3] == ["image", "rm"]:
    if os.environ.get("FAKE_IMAGE_IN_USE") in sys.argv[3:]: sys.exit(17)
    tags = [tag for tag in tags if tag not in sys.argv[3:]]
if sys.argv[1:3] in (["image", "tag"], ["image", "rm"]) or "build" in sys.argv:
    with open(tags_path, "w") as state:
        json.dump(tags, state)
if sys.argv[1:3] == ["image", "ls"]:
    print("\\n".join(tags))
    sys.exit(0)
failure = os.environ.get("FAKE_DOCKER_FAIL")
image_matches = os.environ.get("FAKE_FAIL_IMAGE", os.environ.get("VERWALINO_IMAGE")) == os.environ.get("VERWALINO_IMAGE")
if (failure and image_matches and ((failure in sys.argv and (failure != "migrate" or "run" in sys.argv)) or (failure == "app-ready" and "up" in sys.argv and sys.argv[-1] == "app") or (failure == "health" and "exec" in sys.argv and "app" in sys.argv))) or os.environ.get("FAKE_ROLLBACK_FAIL") in sys.argv:
    if os.environ.get("FAKE_DIRTY_SCHEMA") and "migrate" in sys.argv and "run" in sys.argv:
        with open(os.environ["FAKE_SCHEMA_STATE"], "w") as schema:
            schema.write(os.environ["FAKE_DIRTY_SCHEMA"])
    sys.exit(17)
if sys.argv[1:3] == ["image", "inspect"] and "--format" in sys.argv:
    print("sha256:" + "c" * 64)
    sys.exit(0)
if sys.argv[1] == "info":
    if "-f" in sys.argv:
        print(os.environ.get("FAKE_DOCKER_ROOT", "/docker-data") if "DockerRootDir" in sys.argv[-1]
              else os.environ.get("FAKE_DOCKER_VERSION", "27.1.0"))
    sys.exit(0)
if sys.argv[1:3] == ["compose", "version"]:
    print(os.environ.get("FAKE_COMPOSE_VERSION", "2.24.0"))
    sys.exit(0)
if sys.argv[1] == "inspect":
    print(os.environ.get("FAKE_OWNER", os.environ["FAKE_CHECKOUT"] + "/infra/docker-compose.yml"))
    sys.exit(0)
if "ps" in sys.argv and "--format" in sys.argv and sys.argv[-1] == "json":
    print(json.dumps([{"Service": "app", "State": "running", "Health": "healthy", "Name": os.environ["SESSION_SECRET"]}]))
    sys.exit(0)
if "ps" in sys.argv and "--format" in sys.argv:
    service = sys.argv[-1]
    print(os.environ.get("FAKE_CONTAINER_" + service.upper(),
                         containers.get(service, "running|" + ("" if service == "caddy" else "healthy") + "|" + service + "-id")))
    sys.exit(0)
if "port" in sys.argv:
    port = os.environ.get("VERWALINO_PORT", "18080") if sys.argv[-2] == "app" else sys.argv[-1]
    print(os.environ.get("FAKE_PUBLISHED", "127.0.0.1:" + port))
    sys.exit(0)
if "find" in sys.argv and "caddy" in sys.argv:
    print(os.environ.get("FAKE_CERTIFICATE", "/data/caddy/certificates/acme/verwaltung.example.org/verwaltung.example.org.crt"))
    sys.exit(0)
if "ps" in sys.argv and sys.argv[-1] == "inbox-worker" and "--status" not in sys.argv:
    print(os.environ.get("FAKE_INBOX_CONTAINER", ""))
if "logs" in sys.argv:
    secrets = " ".join(os.environ[key] for key in ("MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD", "SESSION_SECRET"))
    secrets += " " + os.environ.get("HV_SMTP_PASSWORD", "")
    print("2026-10-30 12:00:00,123 ERROR app.test request_id=installer-failure-123 RuntimeError: " + secrets)
if ("up" in sys.argv and sys.argv[-1] == "app") or "app.diagnostics" in sys.argv:
    # Validate the app's settings parser too, without connecting to any service.
    import yaml
    with open(os.environ["FAKE_CHECKOUT"] + "/infra/docker-compose.yml") as compose_file:
        environment = yaml.safe_load(compose_file)["services"]["app"]["environment"]
    for key, value in environment.items():
        if key.startswith("HV_SMTP_"):
            default = value.split(":-", 1)[1][:-1]
            os.environ[key] = os.environ.get(key) or default
    from app.core.config import Settings
    Settings(_env_file=None)
if "app.diagnostics" in sys.argv:
    os.environ["HV_SESSION_SECRET"] = os.environ["SESSION_SECRET"]
    import app.diagnostics as diagnostics
    async def database():
        return {"status": "available", "organization_count": 1, "row_counts": {"person": 23}}
    diagnostics.collect_database = database
    sys.argv = ["diagnostics"] + sys.argv[sys.argv.index("app.diagnostics") + 1:]
    sys.exit(diagnostics.main())
if "ps" in sys.argv and "--quiet" in sys.argv:
    state = os.environ.get("FAKE_STATE", "running")
    statuses = [sys.argv[i + 1] for i, arg in enumerate(sys.argv) if arg == "--status"]
    if state in statuses:
        print(os.environ.get("FAKE_RUNNING", ""), end="")
    for service, row in containers.items():
        if service in sys.argv and row.split("|", 1)[0] in statuses: print(service + "-id")
if "exec" in sys.argv and "app" in sys.argv:
    sys.exit(0)
if "run" in sys.argv and sys.argv[-1] == "migrate":
    with open(os.environ["FAKE_SCHEMA_STATE"], "w") as schema:
        schema.write(os.environ.get("FAKE_MIGRATED_SCHEMA", "0139"))
    with open(os.environ["FAKE_DOCUMENTS"] + "/later.pdf", "wb") as document:
        document.write(b"written after backup")
    if os.environ.get("FAKE_MIGRATE_SIGNAL"):
        import signal
        os.kill(os.getppid(), getattr(signal, os.environ["FAKE_MIGRATE_SIGNAL"]))
state_path = os.environ["FAKE_DB_STATE"]
with open(state_path) as state_file:
    databases = json.load(state_file)
if "createdb" in sys.argv:
    if sys.argv[-1] in databases:
        sys.exit(17)
    databases[sys.argv[-1]] = "restored data"
dropping = sys.argv[-1] if "dropdb" in sys.argv else None
if "psql" in sys.argv and sys.argv[-1].startswith("DROP DATABASE "):
    dropping = sys.argv[-1].split()[-1]
if dropping:
    if dropping == "hausverwaltung_previous":
        from pathlib import Path
        root = Path(os.environ["FAKE_DOCUMENTS"])
        with open(os.environ["FAKE_DROP_DOCUMENTS"], "w") as snapshot:
            json.dump(sorted(str(path.relative_to(root)) for path in root.rglob("*")), snapshot)
    databases.pop(dropping, None)
if "psql" in sys.argv:
    statement = sys.stdin.read() if "-qAt" in sys.argv else sys.argv[-1]
    if statement.startswith("ALTER DATABASE"):
        _, _, source, _, _, target = statement.split()
        if source not in databases or target in databases:
            sys.exit(17)
        databases[target] = databases.pop(source)
        if source == "hausverwaltung_restore" and target == "hausverwaltung":
            with open(os.environ["FAKE_SCHEMA_STATE"], "w") as schema:
                schema.write(os.environ.get("FAKE_RESTORED_SCHEMA", "0139"))
    elif statement.startswith("DROP DATABASE "):
        pass
    elif statement.startswith("SELECT name"):
        for name in ("hausverwaltung", "hausverwaltung_restore", "hausverwaltung_previous"):
            print(name + "=" + ("present" if name in databases else "absent"))
    elif "to_regclass" in statement:
        print(os.environ.get("FAKE_SCHEMA_TABLE", "alembic_version"))
    elif "count(*) FROM organization" in statement:
        print(os.environ.get("FAKE_ORGANIZATIONS", "1"))
    elif "pg_database" in statement:
        name = statement.split("datname='")[1].split("'")[0]
        if name in databases:
            print("1")
    elif statement == "SELECT version_num FROM public.alembic_version":
        from app.deployment import TARGET
        print(TARGET)
    elif statement == "SELECT version_num FROM alembic_version":
        with open(os.environ["FAKE_SCHEMA_STATE"]) as schema:
            print(os.environ.get("FAKE_RESTORED_SCHEMA" if "hausverwaltung_restore" in sys.argv else "FAKE_SCHEMA", schema.read()))
    elif statement == "SELECT count(*) FROM public.organization":
        print(os.environ.get("FAKE_ORGANIZATION_COUNT", "1"))
    elif "-qAt" in sys.argv and "INSERT INTO public.setup_token(token_hash)" in statement:
        with open(os.environ["FAKE_TOKEN_SQL"], "w") as capture:
            capture.write(statement)
        print(os.environ.get("FAKE_TOKEN_ISSUED", "t"))
    else:
        sys.exit("Unsupported fake psql statement")
with open(state_path, "w") as state_file:
    json.dump(databases, state_file)
if os.environ.get("FAKE_SIGNAL_AFTER") in sys.argv:
    os.kill(os.getppid(), getattr(signal, os.environ["FAKE_SIGNAL"]))
    sys.exit(17)
if "pg_dump" in sys.argv:
    sys.stdout.buffer.write(b"PGDMPsynthetic-dump")
if "pg_restore" in sys.argv:
    sys.stdin.buffer.read()
if "-c" in sys.argv:
    for argument in sys.argv:
        if argument.startswith("BACKUP_OWNER="):
            os.environ["BACKUP_OWNER"] = argument.split("=", 1)[1]
    position = sys.argv.index("-c")
    args = sys.argv[position + 2:]
    if "--volume" in sys.argv:
        mount = sys.argv[sys.argv.index("--volume") + 1].rsplit(":", 1)[0]
        args = [arg.replace("/backup", mount).replace("/data/documents", os.environ["FAKE_DOCUMENTS"]) for arg in args]
    # Emulate root's container-only ownership operations without chown on the host.
    wrapper = """import os, sys
from app import deployment
targets = __import__("json").loads(os.environ.get("FAKE_IMAGE_TARGETS", "{}"))
if os.environ.get("VERWALINO_IMAGE") in targets:
    deployment.TARGET = targets[os.environ["VERWALINO_IMAGE"]]
if os.environ.get("FAKE_IMAGE_TARGET") == "missing":
    del deployment.TARGET
elif os.environ.get("FAKE_IMAGE_TARGET"):
    deployment.TARGET = os.environ["FAKE_IMAGE_TARGET"]
if os.environ.get("FAKE_OLD_IMAGE"):
    del deployment.archive_documents
def chown(path, uid, gid):
    if os.environ.get("FAKE_CHOWN_DENIED") and uid != 10001:
        raise PermissionError("sensitive path")
os.chown = chown
if "swap-documents" in sys.argv or "rollback-documents" in sys.argv:
    from pathlib import Path
    original_rename = Path.rename
    def rename(path, target):
        root = Path(os.environ["FAKE_DOCUMENTS"])
        source = str(path.relative_to(root))
        destination = str(target.relative_to(root))
        failure = os.environ.get("FAKE_SWAP_FAIL")
        if "swap-documents" in sys.argv and failure == "after-aside" and "/documents/" in source:
            raise OSError("sensitive path")
        result = original_rename(path, target)
        with open(os.environ["FAKE_DOCUMENT_MOVES"], "a") as moves:
            moves.write(json.dumps([source, destination]) + chr(10))
        if "swap-documents" in sys.argv and failure == "after-first-aside" and "/previous/" in destination:
            raise OSError("sensitive path")
        if "swap-documents" in sys.argv and failure == "after-first-install" and "/documents/" in source:
            raise OSError("sensitive path")
        return result
    Path.rename = rename
exec(compile(sys.argv.pop(1), '<archive-helper>', 'exec'))"""
    sys.exit(subprocess.call([sys.executable, "-c", wrapper, sys.argv[position + 1], *args]))
if "run" in sys.argv and "bootstrap" in sys.argv:
    password = sys.stdin.readline()
    with open(os.environ["FAKE_DOCKER_STDIN"], "w") as capture:
        capture.write(password)
''')
        docker.chmod(0o755)
        self.documents = self.base / "documents"
        self.documents.mkdir()
        (self.documents / "original.pdf").write_bytes(b"synthetic original")
        self.db_state = self.base / "databases.json"
        self.db_state.write_text(json.dumps({"hausverwaltung": "old data"}))
        self.schema_state = self.base / "schema"
        self.schema_state.write_text("0139")
        self.containers = self.base / "containers.json"
        self.containers.write_text("{}")
        self.image_tags = self.base / "tags.json"
        self.image_tags.write_text("[]")
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"), FAKE_DOCUMENTS=str(self.documents), PATH=f"{binary}:{os.environ['PATH']}",
                        FAKE_CONTAINERS=str(self.containers), FAKE_IMAGE_TAGS=str(self.image_tags),
                        FAKE_INTERRUPT_SENT=str(self.base / "interrupt-sent"),
                        FAKE_DB_STATE=str(self.db_state),
                        FAKE_SCHEMA_STATE=str(self.schema_state),
                        FAKE_CHECKOUT=str(self.root),
                        FAKE_DOCUMENT_MOVES=str(self.base / "document-moves.jsonl"),
                        FAKE_DROP_DOCUMENTS=str(self.base / "drop-documents.json"),
                        VERWALINO_CONFIG=str(self.config), FAKE_DOCKER_LOG=str(self.log),
                        FAKE_DOCKER_STDIN=str(self.base / "stdin"),
                        FAKE_TOKEN_SQL=str(self.base / "token.sql"))

    def run_script(self, *args, input=None, stderr_closed=False, stderr=subprocess.PIPE):
        if args[:1] == ("init",) and not self.config.exists() and "--port" not in args:
            args = (*args, "--port", TEST_PORT)
        command = [self.bash, str(self.root / "scripts/self-host.sh"), *args]
        if stderr_closed:
            command = [self.bash, "-c", 'exec 2>&-; exec "$@"', "closed-stderr", *command]
        # Restore diagnostics spawn many fake CLI processes; allow for host load.
        return subprocess.run(command, env=self.env, input=input,
                              text=True, stdout=subprocess.PIPE, stderr=stderr, cwd=self.base, check=False,
                              timeout=60, start_new_session=True)

    def init(self, *args):
        result = self.run_script("init", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in self.config.read_text().splitlines()
                    if line and not line.startswith("#"))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def doctor_tools(self, omit=()):
        # Isolate PATH so missing-tool tests never discover host tools or Docker.
        binary = self.base / "doctor-bin"
        binary.mkdir(exist_ok=True)
        for name in ("bash", "dirname", "stat", "awk", "sed", "sort", "mktemp", "rm", "cat", "id", "sleep",
                     "mkdir", "rmdir", "chmod", "cp", "ln", "date", "wc", "tr", "mv", "env"):
            path = binary / name
            if not path.exists():
                path.symlink_to(shutil.which(name))
        python = binary / "python3"
        python.write_text('#!/bin/bash\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o755)
        tools = '''import os, sys
name = os.path.basename(sys.argv[0])
port = sys.argv[-1].split(":")[-1]
if name == "lsof": port = next(arg.split(":", 1)[1] for arg in sys.argv if arg.startswith("-iTCP:"))
if name == "df":
    key = "FAKE_DISK_CHECKOUT" if sys.argv[-1] == os.environ["FAKE_CHECKOUT"] else "FAKE_DISK_DOCKER"
    value = os.environ.get(key, "20971520")
    if os.environ.get("FAKE_DISK_AFTER_MIGRATION"):
        with open(os.environ["FAKE_SCHEMA_STATE"]) as schema:
            if schema.read() == "0139": value = "100"
    if value == "error": sys.exit(1)
    print("Filesystem 1024-blocks Used Available Capacity Mounted on")
    print("disk 30000000 1 " + value + " 1% /")
elif name in ("ss", "lsof"):
    if os.environ.get("FAKE_PORT_TOOL_FAIL"): sys.exit(2)
    if os.environ.get("FAKE_PORT_BUSY") == port or os.environ.get("FAKE_PORT_BUSY") == "all":
        print("LISTEN 0 128 0.0.0.0:" + port)
    elif name == "lsof": sys.exit(1)
elif name == "nc":
    if os.environ.get("FAKE_LOCAL_CLOSED") == port: sys.exit(1)
    sys.exit(0 if port in ("80", "443") or os.environ.get("FAKE_PORT_BUSY") == port else 1)
elif name in ("getent", "dig", "host"):
    value = os.environ.get("FAKE_DNS", "93.184.216.34")
    if not value: sys.exit(2)
    for address in value.split():
        if name == "getent": print(address + " verwaltung.example.org")
        elif name == "host": print("verwaltung.example.org has " + ("IPv6 " if ":" in address else "") + "address " + address)
        else: print(address)
elif name in ("ip", "ifconfig"):
    for address in os.environ.get("FAKE_PUBLIC_IP", "93.184.216.34").split():
        print(("2: eth0 " if name == "ip" else "") + ("inet6 " if ":" in address else "inet ") + address + "/24 scope global")
elif name == "openssl":
    if "dgst" in sys.argv:
        import hashlib
        print("SHA2-256(stdin)= " + hashlib.sha256(sys.stdin.buffer.read()).hexdigest())
    elif "s_client" in sys.argv:
        if os.environ.get("FAKE_TLS_FAIL"): sys.exit(1)
        print("synthetic certificate")
    else:
        sys.stdin.read()
        if os.environ.get("FAKE_CERT_INVALID"): sys.exit(1)
        if "-checkend" in sys.argv and os.environ.get("FAKE_CERT_EXPIRING"): sys.exit(1)
'''
        for name in ("ss", "lsof", "nc", "getent", "dig", "host", "df", "ip", "ifconfig", "openssl"):
            path = binary / name
            if name in omit:
                path.unlink(missing_ok=True)
            else:
                path.write_text(f"#!{sys.executable}\n" + tools)
                path.chmod(0o755)
        docker = binary / "docker"
        if "docker" not in omit and not docker.exists():
            docker.symlink_to(self.base / "bin/docker")
        if "docker" in omit:
            docker.unlink(missing_ok=True)
        self.env["PATH"] = str(binary)

    def assert_doctor(self, severity, check, code=0):
        self.env.setdefault("FAKE_SCHEMA", RELEASE_TARGET)
        result = self.run_script("doctor")
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertRegex(result.stdout, rf"(?m)^{severity} {check}: .+")
        lines = result.stdout.splitlines()
        counts = {level: sum(line.startswith(level + " ") for line in lines) for level in ("OK", "WARN", "FEHLER")}
        self.assertEqual(lines[-1], f"Zusammenfassung: {counts['OK']} OK, {counts['WARN']} WARN, {counts['FEHLER']} FEHLER.")
        self.assertEqual(code, int(counts["FEHLER"] > 0))
        self.assertEqual(result.stderr, "")
        return result

    def test_doctor_all_green_is_isolated_and_keeps_data_unchanged(self):
        config = self.init("--https", "verwaltung.example.org")
        before = self.config.read_bytes(), self.db_state.read_bytes()
        self.doctor_tools()
        result = self.assert_doctor("OK", "Zertifikatslaufzeit")
        self.assertNotIn("WARN ", result.stdout)
        self.assertNotIn("FEHLER ", result.stdout)
        for key in SECRETS:
            self.assertNotIn(config[key], result.stdout + result.stderr)
        args = [call["args"] for call in self.calls()]
        self.assert_isolated(args)
        self.assertFalse(any(any(word in a for word in ("up", "stop", "build", "pull", "migrate", "bootstrap", "pack", "verify")) for a in args))
        self.assertTrue(any("from app.deployment import TARGET; print(TARGET)" in a for a in args))
        self.assertEqual(before, (self.config.read_bytes(), self.db_state.read_bytes()))
        self.assertFalse(Path(str(self.config) + ".lock").exists())

    def test_doctor_local_mode_omits_https_checks(self):
        self.init("--port", "19080")
        self.doctor_tools()
        result = self.assert_doctor("OK", "Port 19080")
        self.assertNotIn("DNS", result.stdout)
        self.assertNotIn("Zertifikat", result.stdout)

    def test_doctor_rejects_arguments_in_german(self):
        result = self.run_script("doctor", "unexpected")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Dieser Befehl akzeptiert keine zusätzlichen Argumente.\n")
        self.assertFalse(self.calls())

    def test_doctor_reports_every_check_after_missing_docker_or_daemon(self):
        self.init("--https", "verwaltung.example.org")
        for missing in ("docker", "daemon"):
            with self.subTest(missing=missing):
                self.doctor_tools(omit=("docker",) if missing == "docker" else ())
                self.env["FAKE_DOCKER_FAIL"] = "info" if missing == "daemon" else ""
                result = self.assert_doctor("FEHLER", "Docker" if missing == "docker" else "Docker-Daemon", 1)
                for check in ("Einstellungen", "Speicher Checkout", "Container db", "Migration", "Organisation", "DNS", "Zertifikatslaufzeit"):
                    self.assertIn(check + ":", result.stdout)

    def test_doctor_enforces_docker_and_compose_minimum_versions(self):
        self.init()
        self.doctor_tools()
        for variable, value, check in (("FAKE_DOCKER_VERSION", "23.0.6", "Docker-Version"),
                                       ("FAKE_COMPOSE_VERSION", "2.23.3", "Compose"),
                                       ("FAKE_COMPOSE_VERSION", "1.29.2", "Compose"),
                                       ("FAKE_COMPOSE_VERSION", "unknown", "Compose"),
                                       ("FAKE_DOCKER_FAIL", "version", "Compose")):
            with self.subTest(variable=variable, value=value):
                self.env[variable] = value
                self.assert_doctor("FEHLER", check, 1)
                del self.env[variable]

    def test_doctor_accepts_current_compose_releases(self):
        self.init()
        self.doctor_tools()
        self.env["FAKE_COMPOSE_VERSION"] = "5.6.0"
        self.assert_doctor("OK", "Compose")

    def test_doctor_checks_config_existence_permissions_and_completeness(self):
        self.init()
        self.doctor_tools()
        before = self.config.read_text()
        self.config.unlink()
        self.assert_doctor("FEHLER", "Konfiguration", 1)
        self.config.write_text(before)
        for mode in (0o644, 0o640, 0o700):
            with self.subTest(mode=mode):
                self.config.chmod(mode)
                self.assert_doctor("FEHLER", "Dateirechte", 1)
        self.config.chmod(0o400)
        self.assert_doctor("OK", "Dateirechte")
        self.config.chmod(0o600)
        self.config.write_text(before.replace("SESSION_SECRET=", "UNKNOWN="))
        self.assert_doctor("FEHLER", "Einstellungen", 1)
        self.config.write_text(before.replace(f"VERWALINO_PORT={TEST_PORT}", "VERWALINO_PORT=1"))
        self.assert_doctor("FEHLER", "Einstellungen", 1)
        self.config.write_text(before + "EVIL=$(touch marker)\n")
        self.assert_doctor("FEHLER", "Einstellungen", 1)
        self.assertFalse((self.base / "marker").exists())

    def test_doctor_rejects_symlink_config_and_reports_remaining_checks(self):
        self.init()
        self.doctor_tools()
        target = self.base / "saved"
        self.config.rename(target)
        self.config.symlink_to(target)
        self.assert_doctor("FEHLER", "Konfiguration", 1)

    def test_doctor_detects_foreign_port_and_accepts_owned_port(self):
        self.init()
        self.doctor_tools()
        self.env["FAKE_PORT_BUSY"] = TEST_PORT
        self.assert_doctor("OK", f"Port {TEST_PORT}")
        self.env["FAKE_OWNER"] = "/other/infra/docker-compose.yml"
        self.assert_doctor("FEHLER", f"Port {TEST_PORT}", 1)

    def test_doctor_port_tools_fall_back_and_warn_when_unavailable(self):
        self.init()
        self.env.update(FAKE_PUBLISHED="", FAKE_PORT_BUSY=TEST_PORT)
        for omit in (("ss",), ("ss", "lsof")):
            with self.subTest(omit=omit):
                self.doctor_tools(omit=omit)
                self.assert_doctor("FEHLER", f"Port {TEST_PORT}", 1)
        self.doctor_tools(omit=("ss", "lsof", "nc"))
        self.assert_doctor("WARN", f"Port {TEST_PORT}")

    def test_doctor_disk_thresholds_apply_independently_to_both_locations(self):
        self.init()
        self.doctor_tools()
        for variable, check in (("FAKE_DISK_DOCKER", "Speicher Docker"), ("FAKE_DISK_CHECKOUT", "Speicher Checkout")):
            for value, severity, code in (("2097151", "FEHLER", 1), ("2097152", "WARN", 0),
                                           ("10485759", "WARN", 0), ("10485760", "OK", 0), ("error", "WARN", 0)):
                with self.subTest(variable=variable, value=value):
                    self.env[variable] = value
                    self.assert_doctor(severity, check, code)
            del self.env[variable]

    def test_doctor_container_states_have_start_or_log_hints(self):
        self.init("--https", "verwaltung.example.org")
        self.doctor_tools()
        for service in ("db", "app", "caddy"):
            for state, severity, code in (("restarting||id", "FEHLER", 1), ("running|unhealthy|id", "FEHLER", 1),
                                           ("exited||id", "WARN", 0), ("", "WARN", 0), ("running|starting|id", "WARN", 0)):
                with self.subTest(service=service, state=state):
                    self.env["FAKE_CONTAINER_" + service.upper()] = state
                    result = self.assert_doctor(severity, "Container " + service, code)
                    self.assertIn("self-host.sh start", result.stdout)
                    self.assertIn("logs", result.stdout)
            del self.env["FAKE_CONTAINER_" + service.upper()]

    def test_doctor_container_query_failure_is_a_warning(self):
        self.init()
        self.doctor_tools()
        self.env["FAKE_DOCKER_FAIL"] = "ps"
        self.assert_doctor("WARN", "Container db")

    def test_doctor_compares_schema_to_image_target_and_handles_uninitialised_db(self):
        self.init()
        self.doctor_tools()
        for variable, value, severity, code in (("FAKE_SCHEMA", "0122", "WARN", 0),
                                               ("FAKE_SCHEMA", f"{int(RELEASE_TARGET) + 1:04d}", "FEHLER", 1),
                                               ("FAKE_SCHEMA", "", "WARN", 0),
                                               ("FAKE_SCHEMA_TABLE", "", "WARN", 0),
                                               ("FAKE_SCHEMA", "0138\n0139", "WARN", 0),
                                               ("FAKE_IMAGE_TARGET", "missing", "WARN", 0),
                                               ("FAKE_DOCKER_FAIL", "run", "WARN", 0),
                                               ("FAKE_DOCKER_FAIL", "psql", "WARN", 0),
                                               ("FAKE_IMAGE_TARGET", "0122", "FEHLER", 1)):
            with self.subTest(variable=variable, value=value):
                self.env[variable] = value
                self.assert_doctor(severity, "Migration", code)
                del self.env[variable]

    def test_doctor_warns_when_first_organization_is_missing_or_unreadable(self):
        self.init()
        self.doctor_tools()
        for count in ("0", "invalid"):
            self.env["FAKE_ORGANIZATIONS"] = count
            result = self.assert_doctor("WARN", "Organisation")
            self.assertIn("admin --org NAME --email EMAIL", result.stdout)

    def test_doctor_image_target_probe_is_unprivileged_and_has_no_volumes(self):
        self.init()
        self.doctor_tools()
        self.assert_doctor("OK", "Migration")
        probes = [call["args"] for call in self.calls() if "run" in call["args"]]
        self.assertEqual(len(probes), 1)
        probe = probes[0]
        self.assertTrue(all(flag not in probe for flag in ("--volume", "-v", "--mount", "--cap-add")),
                        "The image target probe must not mount data or add capabilities.")
        self.assertEqual(probe[probe.index("--user") + 1], "10001:10001")
        self.assertIn("--read-only", probe)
        self.assertEqual(probe[probe.index("--entrypoint") + 1], "python")
        self.assertEqual(probe[probe.index("-c") + 1], "from app.deployment import TARGET; print(TARGET)")

    def test_doctor_does_not_invoke_target_probe_when_image_is_absent(self):
        self.init()
        self.doctor_tools()
        self.env["FAKE_DOCKER_FAIL"] = "image"
        self.assert_doctor("WARN", "Migration")
        self.assertFalse(any("run" in c["args"] for c in self.calls()))

    def test_doctor_validates_domain_without_stopping_other_checks(self):
        self.init("--https", "verwaltung.example.org")
        self.config.write_text(self.config.read_text().replace("verwaltung.example.org", "https://invalid"))
        self.doctor_tools()
        result = self.assert_doctor("FEHLER", "Domain", 1)
        self.assertIn("Zertifikatslaufzeit:", result.stdout)

    def test_doctor_dns_fallbacks_and_failures(self):
        self.init("--https", "verwaltung.example.org")
        for omit in ((), ("getent",), ("getent", "dig")):
            with self.subTest(omit=omit):
                self.doctor_tools(omit=omit)
                self.env["FAKE_DNS"] = "93.184.216.34"
                self.assert_doctor("OK", "DNS")
                self.env["FAKE_DNS"] = ""
                self.assert_doctor("FEHLER", "DNS", 1)
        self.doctor_tools(omit=("getent", "dig", "host"))
        self.assert_doctor("WARN", "DNS")

    def test_doctor_dns_address_mismatch_and_unknown_public_address(self):
        self.init("--https", "verwaltung.example.org")
        self.doctor_tools()
        self.env["FAKE_PUBLIC_IP"] = "93.184.216.35"
        self.assert_doctor("WARN", "DNS-Ziel")
        for address in ("", "192.168.1.2", "10.0.0.1", "172.16.0.1", "127.0.0.1", "fe80::1", "fd00::1"):
            self.env["FAKE_PUBLIC_IP"] = address
            self.assert_doctor("WARN", "DNS-Ziel")
        self.env.update(FAKE_DNS="2606:4700:4700::1111", FAKE_PUBLIC_IP="2606:4700:4700::1111")
        self.assert_doctor("OK", "DNS-Ziel")

    def test_doctor_https_ports_are_checked_independently_for_occupancy_and_reachability(self):
        self.init("--https", "verwaltung.example.org")
        self.doctor_tools()
        for port in ("80", "443"):
            with self.subTest(port=port):
                self.env.update(FAKE_PORT_BUSY=port, FAKE_OWNER="/other/compose.yml")
                self.assert_doctor("FEHLER", "Port " + port, 1)
                del self.env["FAKE_PORT_BUSY"], self.env["FAKE_OWNER"]
                self.env["FAKE_LOCAL_CLOSED"] = port
                self.assert_doctor("WARN", "Erreichbarkeit " + port)
                del self.env["FAKE_LOCAL_CLOSED"]

    def test_doctor_certificate_presence_and_expiry_failures_are_warnings(self):
        self.init("--https", "verwaltung.example.org")
        self.doctor_tools()
        for variable, value, check in (("FAKE_CONTAINER_CADDY", "exited||id", "Zertifikat"),
                                       ("FAKE_CERTIFICATE", "", "Zertifikat"),
                                       ("FAKE_DOCKER_FAIL", "find", "Zertifikat"),
                                       ("FAKE_TLS_FAIL", "1", "Zertifikatslaufzeit"),
                                       ("FAKE_CERT_INVALID", "1", "Zertifikatslaufzeit"),
                                       ("FAKE_CERT_EXPIRING", "1", "Zertifikatslaufzeit")):
            with self.subTest(variable=variable):
                self.env[variable] = value
                self.assert_doctor("WARN", check)
                del self.env[variable]

    def fingerprint(self, config):
        keys = ("VERWALINO_MODE", "VERWALINO_DOMAIN", "VERWALINO_PORT", "VERWALINO_IMAGE", "VERWALINO_PROXY_SUBNET")
        return hashlib.sha256("".join(f"{key}={config[key]}\n" for key in keys).encode()).hexdigest()

    def update_setup(self, local=False, https=False):
        self.old_image = "verwalino:local" if local else "registry.example.org/verwalino@sha256:" + "a" * 64
        self.new_image = "registry.example.org/verwalino@sha256:" + "b" * 64
        self.init("--image", self.old_image, *(("--https", "verwaltung.example.org") if https else ()))
        self.schema_state.write_text("0138")
        self.env.update(FAKE_IMAGE_TARGETS=json.dumps({self.old_image: "0138", self.new_image: "0139"}),
                        FAKE_RESTORED_SCHEMA="0138", FAKE_FAIL_IMAGE=self.new_image)
        self.doctor_tools()

    def update_archives(self):
        return [p.resolve() for p in (self.root / "backups").glob("*.tar")]

    def assert_restore_cleaned(self):
        self.assertEqual(list(self.base.glob(".env.verwalino-restore.*")), [])

    def assert_update_rollback(self, result, config_mode=0o600):
        self.assertNotEqual(result.returncode, 0)
        archive, = self.update_archives()
        self.assertIn(f"rolled back to {self.old_image}, data from {archive}", result.stdout + result.stderr)
        self.assertIn("VERWALINO_IMAGE=" + self.old_image + "\n", self.config.read_text())
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), config_mode)
        calls = self.calls()
        stopped = [i for i, c in enumerate(calls) if "stop" in c["args"] and "app" in c["args"]]
        self.assertEqual(len(stopped), 2)
        verified = next(i for i, c in enumerate(calls) if "verify" in c["args"])
        restored = next(i for i, c in enumerate(calls) if "pg_restore" in c["args"])
        started = max(i for i, c in enumerate(calls) if "up" in c["args"] and c["args"][-1] == "app")
        health = max(i for i, c in enumerate(calls) if "exec" in c["args"] and "app" in c["args"])
        self.assertLess(stopped[-1], verified)
        self.assertLess(verified, restored)
        self.assertEqual(calls[verified]["archive_digest"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertLess(restored, started)
        self.assertLess(started, health)
        self.assertTrue(all(c["config_image"] == self.old_image for c in calls[verified:]))
        with tarfile.open(archive) as tar:
            self.assertEqual(json.loads(tar.extractfile("manifest.json").read())["schema_head"], "0138")
        self.assertEqual(self.schema_state.read_text(), "0138")
        self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
        self.assertFalse((self.documents / "later.pdf").exists())
        self.assertNotIn("fingerprint differs", result.stderr)
        self.assertNotIn("Type restore", result.stderr)
        self.assertFalse(Path(str(self.config) + ".lock").exists())
        self.assert_restore_cleaned()
        self.assertEqual(list(self.documents.glob(".restore-*")), [])
        self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])
        self.assertTrue(any("stop" in c["args"] and c["args"][-1] == "db" for c in calls))

    def test_update_backs_up_switches_migrates_and_checks_health_in_order(self):
        self.update_setup()
        before = self.config.read_text()
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        args = [c["args"] for c in calls]
        pull = next(i for i, a in enumerate(args) if "pull" in a)
        backup = next(i for i, a in enumerate(args) if "pack" in a)
        stop = next(i for i, a in enumerate(args) if "stop" in a)
        migrate = next(i for i, a in enumerate(args) if a[-1] == "migrate")
        start = next(i for i, a in enumerate(args) if "up" in a and a[-1] == "app")
        health = next(i for i, a in enumerate(args) if "exec" in a and "app" in a)
        doctor = [i for i, a in enumerate(args) if a == ["info"]]
        dump = next(i for i, a in enumerate(args) if "pg_dump" in a)
        self.assertEqual(sorted([doctor[0], pull, stop, dump, backup, migrate, start, health, doctor[-1]]),
                         [doctor[0], pull, stop, dump, backup, migrate, start, health, doctor[-1]])
        candidate = args[pull][args[pull].index("--env-file") + 1]
        prefix = ["compose", "--project-name", "verwalino", "--env-file", str(self.config),
                  "-f", str(self.root / "infra/docker-compose.yml"), "--profile", "ops"]
        pull_prefix = prefix.copy()
        pull_prefix[pull_prefix.index("--env-file") + 1] = candidate
        self.assertEqual(args[pull], pull_prefix + ["pull", "app"])
        self.assertEqual(args[migrate], prefix + ["run", "--rm", "--no-deps", "-T", "migrate"])
        self.assertEqual(args[start], prefix + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "app"])
        self.assertNotEqual(candidate, str(self.config))
        self.assertFalse(Path(candidate).exists())
        self.assertEqual(calls[pull]["env_image"], self.new_image)
        self.assertEqual(calls[pull]["env_mode"], 0o600)
        self.assertTrue(all(c["config_image"] == self.old_image for c in calls[:stop + 1]))
        self.assertEqual(calls[migrate]["config_image"], self.new_image)
        self.assertTrue(all(c["locked"] for c in calls))
        self.assertEqual(self.config.read_text(), before.replace(self.old_image, self.new_image))
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        archive, = self.update_archives()
        with tarfile.open(archive) as tar:
            manifest = json.loads(tar.extractfile("manifest.json").read())
            self.assertEqual(manifest["app_image"], self.old_image)
            self.assertEqual(manifest["schema_head"], "0138")
        self.assertIn(f"updated {self.old_image} → {self.new_image}, migrations 0138 → 0139, backup kept at {archive}", result.stdout)
        self.assertEqual(result.stdout.count("Zusammenfassung:"), 2)
        self.assertIn("health/live", " ".join(args[health]))
        self.assertIn("health/ready", " ".join(args[health]))
        self.assertFalse(any("stop" in a and a[-1] == "db" for a in args))
        self.assert_isolated([a for a in args if "--env-file" not in a or a[a.index("--env-file") + 1] == str(self.config)])

    def test_update_stops_all_writers_before_backup(self):
        self.update_setup()
        self.containers.write_text(json.dumps({
            "migrate": "running||migrate-id", "bootstrap": "paused||bootstrap-id",
            "inbox-worker": "running||inbox-id",
        }))
        self.env["FAKE_INBOX_CONTAINER"] = "inbox-id"
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = [c["args"] for c in self.calls()]
        dump = next(i for i, a in enumerate(args) if "pg_dump" in a)
        for service in ("app", "migrate", "bootstrap", "inbox-worker"):
            self.assertTrue(any("stop" in a and service in a[a.index("stop") + 1:] for a in args[:dump]))
        self.assertFalse(any("up" in a and "inbox-worker" in a for a in args))

    def test_update_reads_both_image_targets_without_privileges_or_volumes(self):
        self.update_setup()
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        probes = [c for c in self.calls() if "run" in c["args"] and "-c" in c["args"] and
                  ("target" in c["args"] or "from app.deployment import TARGET; print(TARGET)" in c["args"])]
        self.assertEqual(len(probes), 3)
        for call, image in zip(probes, (self.old_image, self.new_image, self.new_image), strict=True):
            self.assertEqual(call["args"], [
                "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--entrypoint", "python", image, "-c", "from app.deployment import TARGET; print(TARGET)",
            ])

    def test_update_migration_failure_restores_the_backup_and_previous_image(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_DIRTY_SCHEMA="0139")
        self.assert_update_rollback(self.run_script("update", "--image", self.new_image, "--yes", input=""))

    def test_update_broken_output_pipe_during_rollback_restores_and_logs_outcome(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_DIRTY_SCHEMA="0139")
        switched = self.base / "switched"
        closed = self.base / "output-closed"
        docker = self.base / "doctor-bin/docker"
        docker.unlink()
        docker.write_text(f'''#!{sys.executable}
import os, sys, time
from pathlib import Path
switched = Path({str(switched)!r})
if "run" in sys.argv and sys.argv[-1] == "migrate":
    switched.touch()
    deadline = time.monotonic() + 15
    while not Path({str(closed)!r}).exists():
        if time.monotonic() > deadline: sys.exit(99)
        time.sleep(0.01)
if switched.exists():
    # Like Docker, fail when a progress write reaches a disconnected terminal.
    sys.stderr.write("Container verwalino-app progress\\n")
    sys.stderr.flush()
os.execv({str(self.base / "bin/docker")!r}, ["docker", *sys.argv[1:]])
''')
        docker.chmod(0o755)
        command = [self.bash, str(self.root / "scripts/self-host.sh"), "update", "--image", self.new_image, "--yes"]
        with subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              cwd=self.base, start_new_session=True) as process:
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            os.set_blocking(process.stdout.fileno(), False)
            deadline = time.monotonic() + 30
            while not switched.exists():
                self.assertIsNone(process.poll(), "Update exited before the switch")
                self.assertLess(time.monotonic(), deadline, "Update never reached the switch")
                try:
                    os.read(process.stdout.fileno(), 65536)
                except BlockingIOError:
                    time.sleep(0.01)
            process.stdout.close()
            closed.touch()
            process.wait(timeout=60)
        self.assertEqual(process.returncode, 17)
        log, = (self.base / "logs").glob("update-*.log")
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)
        self.assertIn("Container verwalino-app progress", log.read_text())
        self.assert_update_rollback(subprocess.CompletedProcess(command, process.returncode, "", log.read_text()))

    def test_update_preserves_read_only_config_mode_on_success(self):
        self.update_setup()
        self.config.chmod(0o400)
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o400)
        self.assertTrue(all(c["env_mode"] == 0o400 for c in self.calls()))
        self.assertIn("VERWALINO_IMAGE=" + self.new_image + "\n", self.config.read_text())
        self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])

    def test_update_preserves_read_only_config_mode_on_rollback(self):
        self.update_setup()
        self.config.chmod(0o400)
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_DIRTY_SCHEMA="0139")
        self.assert_update_rollback(self.run_script("update", "--image", self.new_image, "--yes"), config_mode=0o400)
        self.assertTrue(all(c["env_mode"] == 0o400 for c in self.calls()))

    def test_update_rollback_stops_lingering_migration_and_bootstrap_writers(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_LINGERING_WRITERS="migrate bootstrap inbox-worker",
                        FAKE_INBOX_CONTAINER="inbox-id", FAKE_DIRTY_SCHEMA="0139")
        self.assert_update_rollback(self.run_script("update", "--image", self.new_image, "--yes"))
        self.assertTrue(any(c["args"][-4:] == ["stop", "app", "migrate", "bootstrap"] for c in self.calls()))
        self.assertEqual(sum(c["args"][-2:] == ["stop", "inbox-worker"] for c in self.calls()), 2)

    def test_update_health_failure_restores_the_backup_and_previous_image(self):
        self.update_setup()
        self.env["FAKE_DOCKER_FAIL"] = "health"
        self.assert_update_rollback(self.run_script("update", "--image", self.new_image, "--yes"))

    def test_update_startup_failure_restores_the_backup_and_previous_image(self):
        self.update_setup()
        self.env["FAKE_DOCKER_FAIL"] = "app-ready"
        self.assert_update_rollback(self.run_script("update", "--image", self.new_image, "--yes"))

    def test_update_config_switch_failure_restores_the_backup_and_previous_image(self):
        self.update_setup()
        self.config.chmod(0o400)
        binary = self.base / "doctor-bin/mv"
        binary.unlink()
        binary.write_text(f'''#!{sys.executable}
import os, sys
if sys.argv[-1] == os.environ["VERWALINO_CONFIG"]:
    with open(sys.argv[-2]) as source:
        if os.environ["FAKE_FAIL_IMAGE"] in source.read(): sys.exit(17)
os.execv("/bin/mv", ["mv", *sys.argv[1:]])
''')
        binary.chmod(0o755)
        self.assert_update_rollback(self.run_script("update", "--image", self.new_image, "--yes"), config_mode=0o400)
        self.assertFalse(any(c["args"][-1] == "migrate" for c in self.calls()))

    def test_update_schema_mismatch_restores_the_backup_and_previous_image(self):
        self.update_setup()
        self.env["FAKE_MIGRATED_SCHEMA"] = "0138"
        self.assert_update_rollback(self.run_script("update", "--image", self.new_image, "--yes"))

    def test_update_termination_after_migration_restores_the_backup_and_previous_image(self):
        self.update_setup()
        self.env["FAKE_MIGRATE_SIGNAL"] = "SIGTERM"
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
        self.assert_update_rollback(result)

    def test_update_hangup_during_migration_restores_and_reports_previous_state(self):
        self.update_setup()
        self.env["FAKE_MIGRATE_SIGNAL"] = "SIGHUP"
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 129, result.stdout + result.stderr)
        self.assert_update_rollback(result)
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "restored data"})
        self.assertTrue(all(c["locked"] for c in self.calls()))

    def test_update_second_signal_at_cleanup_entry_does_not_skip_rollback(self):
        self.update_setup()
        script = self.root / "scripts/self-host.sh"
        sent = self.base / "second-signal"
        # Inject a second signal after cleanup captures the original status,
        # before its first trap command. Only the disposable script is changed.
        script.write_text(script.read_text().replace("    trap '' PIPE\n", '''    if [[ "$UPDATE_ACTIVE" = 1 ]]; then
        printf '%s\\n' "$SECOND_SIGNAL" > "$FAKE_SECOND_SIGNAL_SENT"
        kill -s "$SECOND_SIGNAL" "$$"
    fi
    trap '' PIPE
''', 1))
        self.env["FAKE_SECOND_SIGNAL_SENT"] = str(sent)
        for signum, code in (("SIGHUP", 129), ("SIGINT", 130), ("SIGTERM", 143)):
            with self.subTest(signal=signum):
                self.env.update(FAKE_MIGRATE_SIGNAL=signum, SECOND_SIGNAL=signum.removeprefix("SIG"))
                self.log.unlink(missing_ok=True)
                for archive in self.update_archives():
                    archive.unlink()
                result = self.run_script("update", "--image", self.new_image, "--yes")
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual(sent.read_text().strip(), signum.removeprefix("SIG"))
                self.assert_update_rollback(result)

    def test_update_hangup_before_switch_reports_unchanged_data_and_stopped_app(self):
        self.update_setup()
        before = self.config.read_bytes()
        self.env.update(FAKE_INTERRUPT_STAGE="stop", FAKE_INTERRUPT_SIGNAL="SIGHUP")
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 129, result.stdout + result.stderr)
        self.assertIn("interrupted before the switch; config and data unchanged; app stopped", result.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.schema_state.read_text(), "0138")
        self.assertEqual(self.update_archives(), [])
        self.assertFalse(Path(str(self.config) + ".lock").exists())
        self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])

    def test_update_refuses_restore_leftovers_before_stopping_writers(self):
        self.update_setup()
        before = self.config.read_bytes()
        for leftover in ("hausverwaltung_previous", "hausverwaltung_restore", ".restore-retained"):
            with self.subTest(leftover=leftover):
                self.db_state.write_text(json.dumps({"hausverwaltung": "old data"}))
                if leftover.startswith("hausverwaltung_"):
                    self.db_state.write_text(json.dumps({"hausverwaltung": "old data", leftover: "retained data"}))
                else:
                    (self.documents / leftover / "previous").mkdir(parents=True)
                self.log.unlink(missing_ok=True)
                result = self.run_script("update", "--image", self.new_image, "--yes")
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(leftover, result.stdout + result.stderr)
                if leftover.startswith("hausverwaltung_"):
                    self.assertRegex(result.stderr, rf"(?m)^.*{leftover}.*prüfen.*manuell.*$")
                self.assertFalse(any(set(c["args"]) & {"stop", "pack", "migrate"} for c in self.calls()))
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(self.schema_state.read_text(), "0138")
                self.assertEqual(self.update_archives(), [])
                self.assertFalse(Path(str(self.config) + ".lock").exists())
                self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])

    def test_update_unhealthy_or_restarting_app_explains_stop_then_update(self):
        self.update_setup()
        for state in ("running|unhealthy|app-id", "restarting||app-id"):
            with self.subTest(state=state):
                self.env["FAKE_CONTAINER_APP"] = state
                self.log.unlink(missing_ok=True)
                result = self.run_script("update", "--image", self.new_image, "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bash scripts/self-host.sh stop", result.stderr)
                self.assertIn("then", result.stderr)
                self.assertIn("update", result.stderr)
                self.assertFalse(any(set(c["args"]) & {"pull", "stop", "pack", "migrate"} for c in self.calls()))

    def test_update_refuses_a_migration_downgrade_before_backup(self):
        self.update_setup()
        self.env["FAKE_IMAGE_TARGETS"] = json.dumps({self.old_image: "0138", self.new_image: "0137"})
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("downgrades are not supported", result.stderr)
        self.assertTrue(any("pull" in c["args"] for c in self.calls()))
        self.assertFalse(any("pack" in c["args"] or "stop" in c["args"] for c in self.calls()))
        self.assertIn("VERWALINO_IMAGE=" + self.old_image + "\n", self.config.read_text())

    def test_update_refuses_with_a_message_when_the_new_image_release_target_cannot_be_read(self):
        self.update_setup()
        before = self.config.read_bytes()
        self.env["FAKE_IMAGE_TARGET"] = "missing"
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot read the new image release target.", result.stderr)
        self.assertTrue(any("pull" in c["args"] for c in self.calls()))
        self.assertFalse(any(set(c["args"]) & {"stop", "pack", "pg_dump"} or c["args"][-1] == "migrate" for c in self.calls()))
        self.assertEqual(self.update_archives(), [])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(Path(str(self.config) + ".lock").exists())
        self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])

    def test_update_failed_writer_stop_reports_without_restoring_data(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="stop", FAKE_FAIL_IMAGE=self.old_image)
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config and data unchanged", result.stderr)
        self.assertEqual(self.update_archives(), [])
        self.assertFalse(any("verify" in c["args"] or c["args"][-1] == "migrate" for c in self.calls()))
        self.assertIn("VERWALINO_IMAGE=" + self.old_image + "\n", self.config.read_text())

    def test_update_interrupt_before_switch_reports_stopped_app_and_unchanged_data(self):
        self.update_setup()
        before = self.config.read_bytes()
        for stage in ("stop", "writers"):
            with self.subTest(stage=stage):
                Path(self.env["FAKE_INTERRUPT_SENT"]).unlink(missing_ok=True)
                self.containers.write_text(json.dumps({"app": "running|healthy|app-id"}))
                self.env["FAKE_INTERRUPT_STAGE"] = stage
                result = self.run_script("update", "--image", self.new_image, "--yes")
                self.assertEqual(result.returncode, 130, result.stderr)
                self.assertIn("interrupted before the switch; config and data unchanged; app stopped", result.stderr)
                self.assertIn("run start", result.stderr)
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(self.schema_state.read_text(), "0138")
                self.assertEqual(json.loads(self.containers.read_text())["app"], "exited||app-id")
                self.assertEqual(self.update_archives(), [])
                self.assertFalse(any("verify" in c["args"] for c in self.calls()))

    def test_update_interrupt_reports_when_cleanup_cannot_confirm_app_stopped(self):
        self.update_setup()
        self.env.update(FAKE_INTERRUPT_STAGE="stop", FAKE_DOCKER_FAIL="stop", FAKE_FAIL_IMAGE=self.old_image)
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertIn("config and data unchanged; could not confirm app stopped", result.stderr)
        self.assertIn("inspect status before running start", result.stderr)
        self.assertNotIn("unchanged; app stopped — run start", result.stderr)
        self.assertEqual(self.update_archives(), [])

    def test_update_database_query_failure_explains_how_to_recover(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="psql", FAKE_FAIL_IMAGE=self.old_image)
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database not running", result.stderr.lower())
        self.assertIn("run start/doctor", result.stderr)
        self.assertFalse(any("stop" in c["args"] or "pack" in c["args"] for c in self.calls()))

    def test_update_from_stopped_stack_skips_current_health_and_starts_database(self):
        self.update_setup()
        self.assertEqual(self.run_script("stop").returncode, 0)
        self.log.unlink()
        self.env.update(FAKE_DOCKER_FAIL="health", FAKE_FAIL_IMAGE=self.old_image)
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(c["image"] == self.old_image and "exec" in c["args"] and "app" in c["args"] for c in self.calls()))
        self.assertEqual(self.schema_state.read_text(), "0139")

    def test_update_same_digest_with_stopped_app_skips_current_health(self):
        self.update_setup()
        self.assertEqual(self.run_script("stop").returncode, 0)
        self.log.unlink()
        self.env.update(FAKE_DOCKER_FAIL="health", FAKE_FAIL_IMAGE=self.old_image)
        result = self.run_script("update", "--image", self.old_image, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("app stopped", result.stdout)
        self.assertNotIn("and healthy", result.stdout)
        self.assertFalse(any("exec" in c["args"] and "app" in c["args"] for c in self.calls()))

    def test_update_from_stopped_https_stack_restarts_caddy_after_app(self):
        self.update_setup(https=True)
        self.assertEqual(self.run_script("stop").returncode, 0)
        self.log.unlink()
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = [c["args"] for c in self.calls()]
        app = next(i for i, a in enumerate(args) if "up" in a and a[-1] == "app")
        caddy = next(i for i, a in enumerate(args) if "up" in a and a[-1] == "caddy")
        self.assertLess(app, caddy)
        self.assertIn("https", args[caddy])
        self.assertEqual(json.loads(self.containers.read_text())["caddy"], "running|healthy|caddy-id")

    def test_update_https_manual_recovery_command_restarts_caddy(self):
        self.update_setup(https=True)
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_ROLLBACK_FAIL="pg_restore")
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        command = next(line for line in result.stderr.splitlines() if line.startswith("env -u "))
        args = shlex.split(command)
        self.assertIn("https", args)
        self.assertEqual(args[-3:], ["db", "app", "caddy"])
        self.assertEqual(args[args.index("up"):],
                         ["up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "120", "db", "app", "caddy"])

    def test_update_final_doctor_blocker_reports_without_discarding_updated_data(self):
        self.update_setup()
        self.env["FAKE_DISK_AFTER_MIGRATION"] = "1"
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Update completed, but doctor reported blockers", result.stderr)
        self.assertIn("VERWALINO_IMAGE=" + self.new_image + "\n", self.config.read_text())
        self.assertFalse(any("verify" in c["args"] for c in self.calls()))
        self.assertEqual(self.schema_state.read_text(), "0139")
        self.assertTrue((self.documents / "later.pdf").exists())

    def test_update_pull_failure_preserves_config_and_makes_no_backup(self):
        self.update_setup()
        before = self.config.read_bytes()
        self.env["FAKE_DOCKER_FAIL"] = "pull"
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("pull" in c["args"] for c in self.calls()))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.update_archives(), [])
        self.assertFalse(any("pack" in c["args"] or "stop" in c["args"] for c in self.calls()))

    def test_update_doctor_blocker_refuses_before_pull(self):
        self.update_setup()
        self.env["FAKE_DISK_DOCKER"] = "100"
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FEHLER", result.stdout)
        self.assertFalse(any("pull" in c["args"] or "pack" in c["args"] for c in self.calls()))

    def test_update_same_digest_is_a_healthy_noop(self):
        self.update_setup()
        before = self.config.read_bytes()
        result = self.run_script("update", "--image", self.old_image)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already using", result.stdout)
        self.assertTrue(any("exec" in c["args"] and "app" in c["args"] for c in self.calls()))
        self.assertFalse(any(set(c["args"]) & {"pull", "pack", "stop", "up", "build"} for c in self.calls()))
        self.assertEqual(self.config.read_bytes(), before)
        self.env["FAKE_FAIL_IMAGE"] = self.old_image
        self.env["FAKE_DOCKER_FAIL"] = "health"
        self.assertNotEqual(self.run_script("update", "--image", self.old_image).returncode, 0)

    def test_update_failed_rollback_keeps_previous_config_and_reports_manual_recovery(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_ROLLBACK_FAIL="pg_restore", FAKE_DIRTY_SCHEMA="0139", FAKE_CONTAINER_APP="")
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Rollback failed", result.stderr)
        self.assertIn("Installation state:", result.stderr)
        self.assertIn("app container state: not created", result.stderr)
        self.assertIn("keep the app stopped", result.stderr)
        self.assertIn("Restore failed before cutover; old database and documents are untouched", result.stderr)
        self.assertIn("half-migrated data may remain", result.stderr)
        self.assertFalse(Path(str(self.config) + ".lock").exists())
        self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])
        self.assert_restore_cleaned()
        self.assertTrue(any("discard-documents" in c["args"] for c in self.calls()))
        self.assertTrue(any(c["args"][-2:] == ["stop", "db"] for c in self.calls()))
        self.assertIn(str(self.update_archives()[0]), result.stderr)
        self.assertIn("VERWALINO_IMAGE=" + self.old_image + "\n", self.config.read_text())
        self.assertFalse(any("up" in c["args"] and c["args"][-1] == "app" for c in self.calls()))
        self.assertEqual(self.schema_state.read_text(), "0139")

    def test_update_failed_rollback_cutover_reports_state_and_retains_staged_data(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_ROLLBACK_FAIL="swap-documents", FAKE_DIRTY_SCHEMA="0139")
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Restore failed during cutover; keep the app stopped.", result.stderr)
        self.assertIn("Installation state:", result.stderr)
        self.assertIn("hausverwaltung=present", result.stderr)
        self.assertIn("hausverwaltung_restore=present", result.stderr)
        self.assertIn("hausverwaltung_previous=absent", result.stderr)
        self.assertIn("documents=rolled back", result.stderr)
        self.assert_restore_cleaned()
        self.assertTrue(list(self.documents.glob(".restore-*/documents/original.pdf")))
        self.assertFalse(any("discard-documents" in c["args"] for c in self.calls()))
        self.assertTrue(any(c["args"][-2:] == ["stop", "db"] for c in self.calls()))
        self.assertFalse(any("up" in c["args"] and c["args"][-1] == "app" for c in self.calls()))
        self.assertFalse(Path(str(self.config) + ".lock").exists())
        self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])

    def test_update_rollback_recovers_database_and_documents_after_partial_swap(self):
        self.update_setup()
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_DIRTY_SCHEMA="0139", FAKE_SWAP_FAIL="after-first-install")
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Rollback failed at restore", result.stderr)
        self.assertIn("documents=rolled back", result.stderr)
        self.assertIn("hausverwaltung_previous=absent", result.stderr)
        self.assertEqual(json.loads(self.db_state.read_text()),
                         {"hausverwaltung": "old data", "hausverwaltung_restore": "restored data"})
        self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
        self.assertTrue(list(self.documents.glob(".restore-*/documents/original.pdf")))
        self.assertIn("VERWALINO_IMAGE=" + self.old_image + "\n", self.config.read_text())
        self.assertTrue(all(c["locked"] for c in self.calls()))
        self.assertFalse(Path(str(self.config) + ".lock").exists())
        self.assertEqual(list(self.base.glob(".env.verwalino-update.*")), [])
        self.assert_restore_cleaned()
        self.assertFalse(any("up" in c["args"] and c["args"][-1] == "app" for c in self.calls()))

    def test_update_local_manual_recovery_starts_retained_image_without_rebuilding_or_migrating(self):
        self.update_setup(local=True)
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_FAIL_IMAGE=self.old_image,
                        FAKE_ROLLBACK_FAIL="pg_restore", FAKE_DIRTY_SCHEMA="0139")
        result = self.run_script("update", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Rollback failed", result.stderr)
        command = next(line for line in result.stderr.splitlines() if line.startswith("env -u "))
        for key in SECRETS:
            secret = next(line.split("=", 1)[1] for line in self.config.read_text().splitlines() if line.startswith(key + "="))
            self.assertNotIn(secret, result.stdout + result.stderr)
        del self.env["FAKE_DOCKER_FAIL"], self.env["FAKE_ROLLBACK_FAIL"]
        archive, = self.update_archives()
        restored = self.run_script("restore", str(archive), "--yes")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.log.unlink()
        launched = subprocess.run(shlex.split(command), env=self.env, text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(launched.returncode, 0, launched.stderr)
        args, = [c["args"] for c in self.calls()]
        self.assertEqual(args[args.index("up"):],
                         ["up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "120", "db", "app"])
        self.assertNotIn("migrate", args)
        self.assertNotIn("build", args)
        self.assert_isolated([args])
        self.assertEqual(self.schema_state.read_text(), "0138")
        self.assertFalse((self.documents / "later.pdf").exists())

    def test_update_manual_recovery_command_reads_mail_settings_from_the_file_not_the_caller(self):
        self.update_setup()
        self.smtp_config(SMTP)
        self.env.update(FAKE_DOCKER_FAIL="migrate", FAKE_ROLLBACK_FAIL="pg_restore", FAKE_DIRTY_SCHEMA="0139")
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        command = next(line for line in result.stderr.splitlines() if line.startswith("env -u "))
        self.assertNotIn(SMTP["HV_SMTP_PASSWORD"], result.stdout + result.stderr)
        del self.env["FAKE_DOCKER_FAIL"], self.env["FAKE_ROLLBACK_FAIL"]
        self.env.update(HV_SMTP_HOST="attacker.invalid", HV_SMTP_PORT="25", HV_SMTP_FROM="attacker@attacker.invalid",
                        HV_SMTP_SECURITY="starttls", HV_SMTP_USERNAME="attacker", HV_SMTP_PASSWORD="wrong",
                        HV_SMTP_TIMEOUT_SECONDS="5")
        self.log.unlink()
        launched = subprocess.run(shlex.split(command), env=self.env, text=True, capture_output=True, check=False, timeout=15)
        app, = [c for c in self.calls() if "up" in c["args"] and c["args"][-1] == "app"]
        self.assertEqual(app["smtp"], {key: value for key, value in SMTP.items() if key != "HV_SMTP_PASSWORD"})
        self.assertTrue(app["smtp_password_matches"])
        self.assertEqual(launched.returncode, 0, launched.stderr)

    def test_update_backup_failure_leaves_app_stopped_and_config_unchanged(self):
        self.update_setup()
        before = self.config.read_bytes()
        self.env.update(FAKE_DOCKER_FAIL="pg_dump", FAKE_FAIL_IMAGE=self.old_image)
        result = self.run_script("update", "--image", self.new_image, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("app stopped", result.stderr)
        self.assertTrue(any("pg_dump" in c["args"] for c in self.calls()))
        self.assertTrue(any(c["args"][-4:] == ["stop", "app", "migrate", "bootstrap"] for c in self.calls()))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.update_archives(), [])

    def test_update_local_build_retains_previous_image_for_rollback(self):
        self.update_setup(local=True)
        self.env.update(FAKE_FAIL_IMAGE=self.old_image, FAKE_DOCKER_FAIL="migrate", FAKE_DIRTY_SCHEMA="0139")
        result = self.run_script("update", "--yes")
        self.assert_update_rollback(result)
        args = [c["args"] for c in self.calls()]
        build = next(i for i, a in enumerate(args) if "build" in a)
        tags = [i for i, a in enumerate(args) if a[:2] == ["image", "tag"]]
        self.assertEqual(len(tags), 3)
        self.assertLess(tags[0], build)
        self.assertGreater(tags[-1], build)
        self.assertEqual(args[tags[-1]][-1], self.old_image)

    def test_update_local_build_failure_preserves_the_configured_image(self):
        self.update_setup(local=True)
        self.env.update(FAKE_DOCKER_FAIL="build")
        del self.env["FAKE_FAIL_IMAGE"]
        before = self.config.read_bytes()
        result = self.run_script("update", "--yes")
        self.assertNotEqual(result.returncode, 0)
        build = next(c for c in self.calls() if "build" in c["args"])
        self.assertTrue(build["image"].startswith("verwalino:update-"))
        self.assertEqual(build["config_image"], "verwalino:local")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(any("stop" in c["args"] or "pack" in c["args"] for c in self.calls()))
        self.assertFalse(any(c["args"][:2] == ["image", "tag"] and c["args"][-1] == "verwalino:local" for c in self.calls()))

    def test_update_local_build_with_unchanged_schema_still_rebuilds_and_checks_health(self):
        self.update_setup(local=True)
        self.schema_state.write_text(RELEASE_TARGET)
        self.env.update(FAKE_IMAGE_TARGETS=json.dumps({self.old_image: RELEASE_TARGET}),
                        FAKE_MIGRATED_SCHEMA=RELEASE_TARGET)
        result = self.run_script("update", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any("build" in c["args"] for c in self.calls()))
        self.assertTrue(any(c["args"][:2] == ["image", "tag"] and c["args"][-1] == "verwalino:local" for c in self.calls()))
        self.assertIn(f"updated verwalino:local → verwalino:local, migrations {RELEASE_TARGET} → {RELEASE_TARGET}", result.stdout)
        self.assertEqual(result.stdout.count("Zusammenfassung:"), 2)
        self.assertEqual(len(self.update_archives()), 1)

    def test_update_success_cleans_candidate_and_keeps_only_the_latest_rollback_tag(self):
        self.update_setup(local=True)
        self.env.update(FAKE_IMAGE_TARGETS=json.dumps({self.old_image: RELEASE_TARGET}),
                        FAKE_MIGRATED_SCHEMA=RELEASE_TARGET)
        self.image_tags.write_text(json.dumps(["verwalino:rollback-older", "verwalino:rollback-oldest", "other:keep"]))
        result = self.run_script("update", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tags = json.loads(self.image_tags.read_text())
        rollback_tags = [tag for tag in tags if tag.startswith("verwalino:rollback-")]
        self.assertEqual(len(rollback_tags), 1, tags)
        retained = rollback_tags[0]
        self.assertNotIn(retained, ("verwalino:rollback-older", "verwalino:rollback-oldest"))
        self.assertFalse(any(tag.startswith("verwalino:update-") for tag in tags))
        self.assertIn("verwalino:local", tags)
        self.assertIn("other:keep", tags)
        args = [c["args"] for c in self.calls()]
        candidate = next(c["image"] for c in self.calls() if "build" in c["args"])
        self.assertIn(["image", "rm", candidate], args)
        health = next(i for i, a in enumerate(args) if "exec" in a and "app" in a)
        removed = [i for i, a in enumerate(args) if a[:2] == ["image", "rm"]]
        self.assertTrue(removed)
        self.assertTrue(all(i > health for i in removed))

    def test_update_image_tag_in_use_warns_and_still_runs_final_doctor(self):
        self.update_setup(local=True)
        self.env.update(FAKE_IMAGE_TARGETS=json.dumps({self.old_image: RELEASE_TARGET}),
                        FAKE_MIGRATED_SCHEMA=RELEASE_TARGET, FAKE_IMAGE_IN_USE="verwalino:rollback-older")
        self.image_tags.write_text(json.dumps(["verwalino:rollback-older"]))
        result = self.run_script("update", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WARN Update completed, but local image tag cleanup failed", result.stdout + result.stderr)
        self.assertIn("verwalino:rollback-older", json.loads(self.image_tags.read_text()))
        self.assertEqual(result.stdout.count("Zusammenfassung:"), 2)
        self.assertEqual(self.schema_state.read_text(), RELEASE_TARGET)
        self.assertFalse(any("pg_restore" in c["args"] for c in self.calls()))
        self.assertFalse(Path(str(self.config) + ".lock").exists())

    def test_update_requires_an_image_for_remote_config_and_confirmation(self):
        self.update_setup()
        for options in (("--yes",), ("--image", "latest", "--yes"), ("--image",), ("--unknown",)):
            with self.subTest(options=options):
                self.assertNotEqual(self.run_script("update", *options).returncode, 0)
                self.assertEqual(self.calls(), [])
        result = self.run_script("update", "--image", self.new_image, input="no\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("pull" in c["args"] for c in self.calls()))

    def test_update_respects_the_installer_lock(self):
        self.update_setup()
        lock = Path(str(self.config) + ".lock")
        lock.mkdir()
        self.assertNotEqual(self.run_script("update", "--image", self.new_image, "--yes").returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertTrue(lock.is_dir())

    def archive(self, config, **changes):
        manifest = {"format": "verwalino-selfhost", "format_version": 1, "schema_head": "0139",
                    "app_image": config["VERWALINO_IMAGE"], "created_at": "2026-10-30T12:00:00Z",
                    "config_fingerprint": self.fingerprint(config)}
        manifest.update(changes)
        documents = io.BytesIO()
        with tarfile.open(fileobj=documents, mode="w:gz") as tar:
            data = b"restored original"
            info = tarfile.TarInfo("nested/original.pdf")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        path = self.base / "input.tar"
        members = {"manifest.json": json.dumps(manifest).encode(), "db.dump": b"PGDMPsynthetic-dump",
                   "documents.tar.gz": documents.getvalue()}
        self.write_archive(path, members)
        return path, members

    def write_archive(self, path, members):
        with tarfile.open(path, "w") as tar:
            for name, content in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))

    def test_backup_archives_database_documents_and_nonsecret_manifest(self):
        config = self.init()
        output = self.base / "output with spaces"
        result = self.run_script("backup", "--out", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        archives = list(output.glob("verwalino-backup-*.tar"))
        self.assertEqual(len(archives), 1)
        path = archives[0]
        self.assertRegex(path.name, r"^verwalino-backup-\d{8}T\d{6}Z\.tar$")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertIn(str(path), result.stdout)
        self.assertIn(str(path.stat().st_size), result.stdout)
        with tarfile.open(path) as tar:
            self.assertEqual(set(tar.getnames()), {"db.dump", "documents.tar.gz", "manifest.json"})
            self.assertTrue(tar.extractfile("db.dump").read().startswith(b"PGDMP"))
            manifest_bytes = tar.extractfile("manifest.json").read()
            manifest = json.loads(manifest_bytes)
            with tarfile.open(fileobj=io.BytesIO(tar.extractfile("documents.tar.gz").read())) as docs:
                self.assertEqual(docs.extractfile("original.pdf").read(), b"synthetic original")
        self.assertEqual(manifest["format"], "verwalino-selfhost")
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(manifest["schema_head"], "0139")
        self.assertEqual(manifest["app_image"], config["VERWALINO_IMAGE"])
        self.assertRegex(manifest["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(manifest["config_fingerprint"], self.fingerprint(config))
        for key in SECRETS:
            self.assertNotIn(key.encode(), manifest_bytes)
            self.assertNotIn(config[key].encode(), path.read_bytes())
        args = [c["args"] for c in self.calls()]
        dump = next(a for a in args if "pg_dump" in a)
        self.assertEqual(dump[dump.index("exec"):],
                         ["exec", "-T", "db", "pg_dump", "-U", "app_migrator", "-d", "hausverwaltung", "-Fc"])
        self.assertLess(next(i for i, a in enumerate(args) if "pg_dump" in a),
                        next(i for i, a in enumerate(args) if "pack" in a))
        self.assertFalse(any("up" in a or "stop" in a for a in args))
        self.assert_isolated(args)

    def test_backup_defaults_to_private_checkout_backups_and_does_not_publish_failures(self):
        self.init()
        self.env["FAKE_DOCKER_FAIL"] = "pg_dump"
        self.assertNotEqual(self.run_script("backup").returncode, 0)
        self.assertEqual(list(self.base.glob("verwalino-backup-*")), [])
        self.assertEqual(list(self.base.glob(".verwalino-backup.*")), [])
        self.assertEqual(list((self.root / "backups").glob("*")), [])
        del self.env["FAKE_DOCKER_FAIL"]
        result = self.run_script("backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / "backups"
        self.assertEqual(len(list(output.glob("verwalino-backup-*"))), 1)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertEqual(list(self.base.glob("verwalino-backup-*")), [])

    def test_backup_refuses_checkout_root_and_git_ignores_private_outputs(self):
        self.init()
        result = self.run_script("backup", "--out", str(self.root / "."))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checkout root", result.stderr)
        self.assertEqual(self.calls(), [])
        paths = ["backups/private.dump", "verwalino-backup-20261030T120000Z.tar", ".verwalino-backup.abcdef/db.dump"]
        for path in paths:
            ignored = subprocess.run(["git", "check-ignore", "--no-index", path], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(ignored.returncode, 0, path)

    def test_backup_refuses_missing_or_ambiguous_schema_head(self):
        self.init()
        for schema in ("", "0122\n0139"):
            with self.subTest(schema=schema):
                self.env["FAKE_SCHEMA"] = schema
                result = self.run_script("backup")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Missing or ambiguous", result.stderr)
                self.assertFalse(any("pg_dump" in c["args"] for c in self.calls()))

    def test_backup_warns_on_chown_failure_and_copies_when_hardlink_is_denied(self):
        self.init()
        self.env["FAKE_CHOWN_DENIED"] = "1"
        binary = Path(self.env["PATH"].split(":")[0]) / "ln"
        binary.write_text('#!/bin/bash\nif [[ "$1" = */archive.tar ]]; then exit 1; fi\nexec /bin/ln "$@"\n')
        binary.chmod(0o755)
        result = self.run_script("backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Warning:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(list((self.root / "backups").glob("*.tar"))), 1)

    def test_helper_reports_images_without_backup_support_without_traceback(self):
        self.init()
        self.env["FAKE_OLD_IMAGE"] = "1"
        result = self.run_script("backup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image predates backup support; update the image first", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_restore_refuses_running_writers_before_any_changes(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.env["FAKE_RUNNING"] = "running-app-id"
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bash scripts/self-host.sh stop", result.stderr)
        self.assertTrue(self.calls())
        self.assertTrue(all("ps" in c["args"] for c in self.calls()))

    def test_restore_refuses_active_inbox_worker_before_any_changes(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.containers.write_text(json.dumps({"inbox-worker": "running||inbox-id"}))
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bash scripts/self-host.sh stop", result.stderr)
        self.assertTrue(all("ps" in c["args"] for c in self.calls()))

    def test_restore_refuses_restarting_and_paused_writers(self):
        config = self.init()
        archive, _ = self.archive(config)
        for state in ("restarting", "paused"):
            with self.subTest(state=state):
                self.env.update(FAKE_STATE=state, FAKE_RUNNING="app-id")
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(all("ps" in c["args"] for c in self.calls()))

    def test_restore_uses_image_target(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.env["FAKE_IMAGE_TARGET"] = "0122"
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("newer", result.stderr)
        self.assertFalse(any("up" in c["args"] for c in self.calls()))
        self.env["FAKE_IMAGE_TARGET"] = "0140"
        archive, _ = self.archive(config, schema_head="0140")
        self.env["FAKE_RESTORED_SCHEMA"] = "0140"
        self.assertEqual(self.run_script("restore", str(archive), "--yes").returncode, 0)

    def test_restore_refuses_newer_unknown_or_foreign_archives_before_starting_db(self):
        config = self.init()
        newer_head = f"{int(RELEASE_TARGET) + 1:04d}"
        for changes, message in [({"schema_head": newer_head}, "newer"),
                                 ({"schema_head": "unknown"}, "schema"),
                                 ({"format": "foreign"}, "manifest"),
                                 ({"format_version": 2}, "format")]:
            with self.subTest(changes=changes):
                archive, _ = self.archive(config, **changes)
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr.lower())
                self.assertFalse(any("up" in c["args"] for c in self.calls()))

    def test_restore_refuses_missing_members_and_unsafe_documents(self):
        config = self.init()
        path, members = self.archive(config)
        for missing in members:
            with self.subTest(missing=missing):
                self.write_archive(path, {k: v for k, v in members.items() if k != missing})
                self.assertNotEqual(self.run_script("restore", str(path), "--yes").returncode, 0)
        unsafe = io.BytesIO()
        with tarfile.open(fileobj=unsafe, mode="w:gz") as tar:
            info = tarfile.TarInfo("../../escape")
            tar.addfile(info, io.BytesIO())
        members["documents.tar.gz"] = unsafe.getvalue()
        self.write_archive(path, members)
        self.assertNotEqual(self.run_script("restore", str(path), "--yes").returncode, 0)
        self.assertFalse(any("up" in c["args"] for c in self.calls()))
        self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")

    def test_restore_recreates_database_replaces_documents_and_stops_db(self):
        config = self.init()
        archive, _ = self.archive(config, schema_head="0122")
        self.env["FAKE_RESTORED_SCHEMA"] = "0122"
        result = self.run_script("restore", str(archive), "--yes", input="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bash scripts/self-host.sh start", result.stdout)
        args = [c["args"] for c in self.calls()]
        stages = ["ps", "target", "verify", "up", "--list", "createdb", "--single-transaction", "SELECT version_num FROM alembic_version", "stage-documents", "ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_previous", "ALTER DATABASE hausverwaltung_restore RENAME TO hausverwaltung", "swap-documents", "DROP DATABASE hausverwaltung_previous", "discard-documents", "stop"]
        positions = [next(i for i, a in enumerate(args) if stage in a) for stage in stages]
        self.assertEqual(positions, sorted(positions))
        for a in args:
            if "up" in a or "stop" in a:
                self.assertEqual(a[-1], "db")
            if "pg_restore" in a and "--list" not in a:
                self.assertEqual(a[a.index("exec"):], ["exec", "-T", "db", "pg_restore", "-U", "app_migrator",
                                                      "-d", "hausverwaltung_restore", "--exit-on-error", "--single-transaction"])
            self.assertNotIn("dropdb", a)
            if "createdb" in a:
                self.assertEqual(a[a.index("exec"):], ["exec", "-T", "db", "createdb", "-U", "app_migrator", "-T", "template0", "hausverwaltung_restore"])
            if a[-1].startswith(("ALTER DATABASE", "DROP DATABASE")):
                self.assertEqual(a[a.index("exec"):], ["exec", "-T", "db", "psql", "-X", "--set", "ON_ERROR_STOP=1", "-U", "app_migrator", "-d", "postgres", "-Atc", a[-1]])
        documents_at_drop = json.loads((self.base / "drop-documents.json").read_text())
        temporary = next(path.split("/")[0] for path in documents_at_drop if path.startswith(".restore-"))
        self.assertIn(f"{temporary}/previous/original.pdf", documents_at_drop)
        self.assertIn("nested/original.pdf", documents_at_drop)
        moves = [json.loads(line) for line in (self.base / "document-moves.jsonl").read_text().splitlines()]
        self.assertEqual(moves, [["original.pdf", f"{temporary}/previous/original.pdf"], [f"{temporary}/documents/nested", "nested"]])
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "restored data"})
        self.assertEqual(list(self.documents.glob(".restore-*")), [])
        self.assertFalse((self.documents / "original.pdf").exists())
        self.assertEqual((self.documents / "nested/original.pdf").read_bytes(), b"restored original")
        self.assert_restore_cleaned()
        self.assert_isolated(args)

    def test_restore_warns_on_configuration_change_and_requires_confirmation(self):
        config = self.init()
        archive, _ = self.archive(config, config_fingerprint="f" * 64)
        for answer in ("", "no\n"):
            result = self.run_script("restore", str(archive), input=answer)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fingerprint differs", result.stderr)
            self.assertFalse(any("up" in c["args"] for c in self.calls()))
        result = self.run_script("restore", str(archive), input="restore\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restore_without_live_database_installs_scratch_without_previous_rename_or_drop(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.db_state.write_text('{}')
        result = self.run_script("restore", str(archive), "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        sql = [call["args"][-1] for call in self.calls() if "psql" in call["args"]]
        self.assertIn("ALTER DATABASE hausverwaltung_restore RENAME TO hausverwaltung", sql)
        self.assertNotIn("ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_previous", sql)
        self.assertNotIn("DROP DATABASE hausverwaltung_previous", sql)
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "restored data"})
        self.assertEqual((self.documents / "nested/original.pdf").read_bytes(), b"restored original")
        self.assertEqual(list(self.documents.glob(".restore-*")), [])

    def test_restore_refuses_leftover_document_directory_before_creating_scratch_database(self):
        config = self.init()
        archive, _ = self.archive(config)
        previous = self.documents / ".restore-x/previous"
        previous.mkdir(parents=True)
        (previous / "retained.pdf").write_bytes(b"previous original")
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".restore-x", result.stderr)
        self.assertIn("verify the live data, then remove the directory, then retry", result.stderr)
        self.assertFalse(any("createdb" in call["args"] for call in self.calls()))
        self.assertEqual((previous / "retained.pdf").read_bytes(), b"previous original")
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data"})

    def test_restore_without_live_database_rolls_back_an_incomplete_document_swap(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.db_state.write_text('{}')
        self.env["FAKE_SWAP_FAIL"] = "after-aside"
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung_restore": "restored data"})
        self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
        self.assertIn("No previous database existed", result.stderr)
        self.assertIn("documents=rolled back", result.stderr)
        self.assertNotIn("previous database was already dropped", result.stderr)

    def test_restore_without_live_database_keeps_completed_swap_if_document_cleanup_fails(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.db_state.write_text('{}')
        self.env["FAKE_DOCKER_FAIL"] = "discard-documents"
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "restored data"})
        self.assertEqual((self.documents / "nested/original.pdf").read_bytes(), b"restored original")
        self.assertIn("No previous database existed; restored data remains live", result.stderr)
        self.assertIn("bash scripts/self-host.sh start", result.stderr)
        self.assertNotIn("Recovery SQL", result.stderr)

    def test_restore_pre_cutover_failures_preserve_old_data_and_clean_scratch(self):
        config = self.init()
        archive, _ = self.archive(config)
        for failure in ("createdb", "--single-transaction", "stage-documents"):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                self.env["FAKE_DOCKER_FAIL"] = failure
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Restored", result.stdout)
                self.assertIn("old database and documents are untouched", result.stderr)
                self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data"})
                self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
                self.assertTrue(any("stop" in c["args"] for c in self.calls()))
                self.assertFalse(any("dropdb" in c["args"] and c["args"][-1] == "hausverwaltung" for c in self.calls()))
                if failure != "createdb":
                    cleanup = next(c["args"] for c in self.calls() if "dropdb" in c["args"])
                    self.assertEqual(cleanup[cleanup.index("exec"):], ["exec", "-T", "db", "dropdb", "-U", "app_migrator", "--if-exists", "hausverwaltung_restore"])

    def test_restore_rejects_dump_manifest_schema_mismatch_before_cutover(self):
        config = self.init()
        archive, _ = self.archive(config)
        for schema in ("0122", "", "0122\n0139"):
            with self.subTest(schema=schema):
                self.env["FAKE_RESTORED_SCHEMA"] = schema
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("does not match the manifest", result.stderr)
                self.assertIn("old database and documents are untouched", result.stderr)
                self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data"})

    def test_restore_database_cutover_failures_roll_back_and_keep_staged_documents(self):
        config = self.init()
        archive, _ = self.archive(config)
        for failure in ("ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_previous",
                        "ALTER DATABASE hausverwaltung_restore RENAME TO hausverwaltung",
                        "swap-documents", "DROP DATABASE hausverwaltung_previous"):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                for path in self.documents.glob(".restore-*"):
                    shutil.rmtree(path)
                self.db_state.write_text(json.dumps({"hausverwaltung": "old data"}))
                self.env["FAKE_DOCKER_FAIL"] = failure
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data", "hausverwaltung_restore": "restored data"})
                self.assertIn("hausverwaltung=present", result.stderr)
                self.assertIn("hausverwaltung_previous=absent", result.stderr)
                self.assertIn("keep the app stopped", result.stderr)
                self.assert_restore_cleaned()
                self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
                staged = [path for path in self.documents.glob(".restore-*") if (path / "documents/nested/original.pdf").exists()]
                self.assertTrue(staged)
                self.assertTrue(any(str(path / "documents") in result.stderr for path in staged))
                self.assertFalse(any("discard-documents" in call["args"] for call in self.calls()))

    def test_restore_document_move_failures_put_previous_documents_back(self):
        config = self.init()
        archive, _ = self.archive(config)
        (self.documents / ".hidden").write_bytes(b"hidden original")
        (self.documents / "nested").mkdir()
        (self.documents / "nested/original.pdf").write_bytes(b"old nested original")
        for failure in ("after-first-aside", "after-aside", "after-first-install"):
            with self.subTest(failure=failure):
                for path in self.documents.glob(".restore-*"):
                    shutil.rmtree(path)
                self.db_state.write_text(json.dumps({"hausverwaltung": "old data"}))
                self.env["FAKE_SWAP_FAIL"] = failure
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data", "hausverwaltung_restore": "restored data"})
                self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
                self.assertEqual((self.documents / ".hidden").read_bytes(), b"hidden original")
                self.assertEqual((self.documents / "nested/original.pdf").read_bytes(), b"old nested original")
                self.assertTrue(any((path / "documents/nested/original.pdf").read_bytes() == b"restored original" for path in self.documents.glob(".restore-*")))
                self.assertIn("documents=rolled back", result.stderr)
                self.assertNotIn("sensitive path", result.stderr)

    def test_restore_interruption_after_first_rename_recovers_old_database(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.env["FAKE_SIGNAL_AFTER"] = "ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_previous"
        for signal in ("SIGHUP", "SIGINT", "SIGTERM"):
            with self.subTest(signal=signal):
                for path in self.documents.glob(".restore-*"):
                    shutil.rmtree(path)
                self.db_state.write_text(json.dumps({"hausverwaltung": "old data"}))
                self.env["FAKE_SIGNAL"] = signal
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data", "hausverwaltung_restore": "restored data"})
                self.assertTrue(any((path / "documents/nested/original.pdf").exists() for path in self.documents.glob(".restore-*")))
                self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")

    def test_restore_failure_after_first_rename_rolls_back_with_closed_stderr(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.env["FAKE_DOCKER_FAIL"] = "ALTER DATABASE hausverwaltung_restore RENAME TO hausverwaltung"
        result = self.run_script("restore", str(archive), "--yes", stderr_closed=True)
        self.assertNotEqual(result.returncode, 0)
        calls = [call["args"] for call in self.calls()]
        rollback = ["exec", "-T", "db", "psql", "-X", "--set", "ON_ERROR_STOP=1", "-U", "app_migrator",
                    "-d", "postgres", "-Atc", "ALTER DATABASE hausverwaltung_previous RENAME TO hausverwaltung"]
        self.assertIn(rollback, [args[args.index("exec"):] for args in calls if "exec" in args])
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data", "hausverwaltung_restore": "restored data"})
        self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
        self.assertTrue(any("stop" in args for args in calls))
        self.assertFalse(Path(str(self.config) + ".lock").exists())

    def test_restore_document_failure_rolls_back_with_a_broken_stderr_pipe(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.env["FAKE_SWAP_FAIL"] = "after-aside"
        reader, writer = os.pipe()
        os.close(reader)
        try:
            result = self.run_script("restore", str(archive), "--yes", stderr=writer)
        finally:
            os.close(writer)
        self.assertNotEqual(result.returncode, 0)
        sql = [call["args"][-1] for call in self.calls() if "psql" in call["args"]]
        self.assertIn("ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_restore", sql)
        self.assertIn("ALTER DATABASE hausverwaltung_previous RENAME TO hausverwaltung", sql)
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "old data", "hausverwaltung_restore": "restored data"})
        self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
        self.assertFalse(Path(str(self.config) + ".lock").exists())

    def test_restore_refuses_previous_database_before_changing_data(self):
        config = self.init()
        archive, _ = self.archive(config)
        databases = {"hausverwaltung": "old data", "hausverwaltung_previous": "earlier data"}
        self.db_state.write_text(json.dumps(databases))
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hausverwaltung_previous already exists", result.stderr)
        self.assertIn("inspect", result.stderr)
        self.assertIn(".restore-*", result.stderr)
        self.assertEqual(json.loads(self.db_state.read_text()), databases)
        self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")
        self.assertFalse(any("createdb" in call["args"] or "pg_restore" in call["args"] or "stage-documents" in call["args"] for call in self.calls()))

    def test_restore_failed_database_rollback_reports_exact_state_and_manual_sql(self):
        config = self.init()
        archive, _ = self.archive(config)
        for failure, live, scratch in (("ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_restore", "present", "absent"),
                                       ("ALTER DATABASE hausverwaltung_previous RENAME TO hausverwaltung", "absent", "present")):
            with self.subTest(failure=failure):
                for path in self.documents.glob(".restore-*"):
                    shutil.rmtree(path)
                self.db_state.write_text(json.dumps({"hausverwaltung": "old data"}))
                self.env.update(FAKE_DOCKER_FAIL=failure, FAKE_SWAP_FAIL="after-aside")
                result = self.run_script("restore", str(archive), "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Automatic rollback failed", result.stderr)
                self.assertIn("hausverwaltung=" + live, result.stderr)
                self.assertIn("hausverwaltung_restore=" + scratch, result.stderr)
                self.assertIn("hausverwaltung_previous=present", result.stderr)
                self.assertIn("ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_restore;", result.stderr)
                self.assertIn("ALTER DATABASE hausverwaltung_previous RENAME TO hausverwaltung;", result.stderr)
                self.assertEqual(json.loads(self.db_state.read_text())["hausverwaltung_previous"], "old data")
                self.assertEqual((self.documents / "original.pdf").read_bytes(), b"synthetic original")

    def test_restore_failed_document_rollback_names_both_retained_directories(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.env.update(FAKE_DOCKER_FAIL="rollback-documents", FAKE_SWAP_FAIL="after-aside")
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Automatic rollback failed", result.stderr)
        temporary, = self.documents.glob(".restore-*")
        self.assertIn(str(temporary / "documents"), result.stderr)
        self.assertIn(str(temporary / "previous"), result.stderr)
        self.assertEqual((temporary / "previous/original.pdf").read_bytes(), b"synthetic original")
        self.assertEqual((temporary / "documents/nested/original.pdf").read_bytes(), b"restored original")

    def test_restore_interruption_after_database_drop_keeps_restored_data_and_previous_documents(self):
        config = self.init()
        archive, _ = self.archive(config)
        self.env.update(FAKE_SIGNAL_AFTER="DROP DATABASE hausverwaltung_previous", FAKE_SIGNAL="SIGHUP")
        result = self.run_script("restore", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.db_state.read_text()), {"hausverwaltung": "restored data"})
        self.assertEqual((self.documents / "nested/original.pdf").read_bytes(), b"restored original")
        temporary, = self.documents.glob(".restore-*")
        self.assertEqual((temporary / "previous/original.pdf").read_bytes(), b"synthetic original")
        self.assertIn("previous database was already dropped", result.stderr)
        self.assertIn(str(temporary), result.stderr)
        self.assertIn("After verifying the live database and documents, remove", result.stderr)
        self.assertIn("bash scripts/self-host.sh start", result.stderr)
        self.assertNotIn("Recovery SQL", result.stderr)
        self.assertNotIn("ALTER DATABASE", result.stderr)

    def test_backup_keeps_existing_output_directory_permissions(self):
        self.init()
        output = self.base / "shared backups"
        output.mkdir(mode=0o755)
        result = self.run_script("backup", "--out", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o755)

    def test_init_generates_distinct_secrets_and_private_file_without_docker(self):
        config = self.init()
        self.assertFalse(any(key.startswith("HV_SMTP_") for key in config))
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(config["VERWALINO_MODE"], "local")
        self.assertEqual(config["VERWALINO_PORT"], TEST_PORT)
        self.assertEqual(config["VERWALINO_IMAGE"], "verwalino:local")
        values = [config[key] for key in SECRETS]
        self.assertEqual(len(set(values)), len(SECRETS))
        for value in values:
            self.assertRegex(value, r"^[a-f0-9]{64}$")
        self.assertEqual(self.calls(), [])

    def test_repeat_init_preserves_bytes_and_never_prints_secrets(self):
        config = self.init("--https", "verwaltung.example.org")
        before = self.config.read_bytes()
        result = self.run_script("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        for key in SECRETS:
            self.assertNotIn(config[key], result.stdout + result.stderr)
        self.assertNotEqual(self.run_script("init", "--port", "19000").returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)

    def test_busy_port_refuses_init_and_start_before_mutating_docker(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            result = self.run_script("init", "--port", str(port))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"Port {port} is already in use", result.stderr)
            self.assertFalse(self.config.exists())
            self.assertEqual(self.calls(), [])
        self.init("--port", str(port))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen()
            result = self.run_script("start")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"Port {port} is already in use", result.stderr)
            self.assertTrue(all("ps" in call["args"] for call in self.calls()))

    def test_init_and_start_succeed_when_default_port_is_occupied(self):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", 18080))
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
            else:
                listener.listen()
            self.init()
            result = self.run_script("start")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("App ready:", result.stdout)

    def test_separate_installations_get_fresh_secrets(self):
        first = self.init()
        self.config.unlink()
        second = self.init()
        for key in SECRETS:
            self.assertNotEqual(first[key], second[key])

    def test_invalid_inputs_do_not_create_configuration(self):
        for args in [("--https", "http://example.org"), ("--https", "*.example.org"),
                     ("--https", "127.0.0.1"), ("--https", "a..example.org"),
                     ("--https", "example.org:443"), ("--https", "example.org\nEVIL=1"),
                     ("--port", "0"), ("--port", "65536"), ("--port", "abc"),
                     ("--image", "example/app:latest"), ("--unknown", "x"), ("--port",)]:
            with self.subTest(args=args):
                result = self.run_script("init", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.config.exists())
                self.assertEqual(self.calls(), [])

    def test_symlinks_and_public_configuration_are_rejected(self):
        target = self.base / "target"
        target.write_text("do not overwrite")
        self.config.symlink_to(target)
        self.assertNotEqual(self.run_script("init").returncode, 0)
        self.assertEqual(target.read_text(), "do not overwrite")
        self.config.unlink()
        self.init()
        self.config.chmod(0o644)
        self.assertNotEqual(self.run_script("start").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_missing_configuration_and_unknown_commands_do_not_invoke_docker(self):
        for args in [("start",), ("status",), ("stop",), ("destroy",), ("start", "--force")]:
            self.assertNotEqual(self.run_script(*args).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_config_is_data_not_shell_and_duplicate_keys_are_rejected(self):
        original = self.init()
        before = self.config.read_text()
        for extra in [f"SESSION_SECRET={original['SESSION_SECRET']}\n", "EVIL=$(touch marker)\n",
                      "VERWALINO_DOMAIN=$(touch marker)\n"]:
            self.config.write_text(before + extra)
            self.assertNotEqual(self.run_script("start").returncode, 0)
            self.assertFalse((self.base / "marker").exists())
            self.assertEqual(self.calls(), [])

    def test_deployment_mode_remains_an_unknown_installer_key(self):
        self.init()
        with self.config.open("a") as config:
            config.write("HV_DEPLOYMENT_MODE=hosted\n")
        result = self.run_script("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown configuration key", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_local_start_builds_migrates_then_starts_app_without_https(self):
        self.init()
        # Caller environment must not replace protected configuration or enable profiles.
        self.env.update(VERWALINO_IMAGE="attacker:latest", COMPOSE_PROFILES="https",
                        MIGRATOR_PASSWORD="attacker", VERWALINO_COOKIE_SECURE="true")
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        args = [call["args"] for call in calls]
        build = next(i for i, a in enumerate(args) if "build" in a)
        migrate = next(i for i, a in enumerate(args) if "migrate" in a)
        app = next(i for i, a in enumerate(args) if "up" in a and a[-1] == "app")
        self.assertLess(build, migrate)
        stopped_app = next(i for i, a in enumerate(args) if "stop" in a and a[-1] == "app")
        self.assertLess(stopped_app, migrate)
        self.assertLess(migrate, app)
        for call in calls:
            self.assertEqual(call["image"], "verwalino:local")
            self.assertEqual(call["secure"], "false")
            self.assertEqual(call["hosts"], "localhost,127.0.0.1")
            self.assertNotIn("https", call["args"])
            self.assertIsNone(call["profiles"])
            self.assertFalse(call["caller_secret_used"])
            self.assertEqual(call["smtp"], {key: "" for key in SMTP if key != "HV_SMTP_PASSWORD"})
            self.assertTrue(call["smtp_password_matches"])
        self.assertIn(f"http://127.0.0.1:{TEST_PORT}", result.stdout)
        self.assert_isolated(args)

    def smtp_config(self, values):
        with self.config.open("a") as config:
            config.writelines(f"{key}={value}\n" for key, value in values.items())
        self.env["FAKE_SMTP_PASSWORD"] = values.get("HV_SMTP_PASSWORD", "")

    def test_smtp_guidance_covers_hosted_follow_up_and_invalid_stop_and_status(self):
        section = (ROOT / "DEPLOY.md").read_text().split("### Ausgehende Mail\n", 1)[1].split("\n### ", 1)[0]
        self.assertIn("server's `.env.selfhost`", section)
        self.assertIn("`noreply@verwalino.de`", section)
        self.assertIn("`stop` and `status` also refuse invalid mail settings", section)

    def test_smtp_accepts_optional_settings_and_passes_values_without_shell_evaluation(self):
        self.init()
        before = self.config.read_text()
        variants = [SMTP, {**SMTP, "HV_SMTP_SECURITY": "starttls", "HV_SMTP_PORT": "587"},
                    {**SMTP, "HV_SMTP_HOST": " \tsmtp.example.invalid\t ", "HV_SMTP_FROM": " \tverwaltung@example.invalid\t "},
                    {"HV_SMTP_HOST": SMTP["HV_SMTP_HOST"], "HV_SMTP_FROM": SMTP["HV_SMTP_FROM"]},
                    {key: "" for key in SMTP},
                    {**SMTP, "HV_SMTP_USERNAME": "", "HV_SMTP_PASSWORD": ""}]
        variants += [{**SMTP, "HV_SMTP_PORT": port, "HV_SMTP_TIMEOUT_SECONDS": timeout}
                     for port, timeout in [("1", "1"), ("65535", "60")]]
        for values in variants:
            with self.subTest(values={key: value for key, value in values.items() if key != "HV_SMTP_PASSWORD"}):
                self.config.write_text(before)
                self.smtp_config(values)
                self.log.unlink(missing_ok=True)
                result = self.run_script("start")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_isolated([call["args"] for call in self.calls()])
                expected = {key: "" for key in SMTP} | values
                for key in ("HV_SMTP_HOST", "HV_SMTP_FROM"):
                    expected[key] = expected[key].strip()
                app = next(call for call in self.calls() if "up" in call["args"] and call["args"][-1] == "app")
                self.assertEqual(app["smtp"], {key: value for key, value in expected.items() if key != "HV_SMTP_PASSWORD"})
                self.assertTrue(app["smtp_password_matches"])
                self.assertFalse((self.base / "marker").exists())
                self.assertNotIn(SMTP["HV_SMTP_PASSWORD"], result.stdout + result.stderr + self.log.read_text())
                self.assertFalse(any("config" in call["args"] for call in self.calls()))

    def test_smtp_rejects_each_invalid_rule_before_calling_docker(self):
        self.init()
        before = self.config.read_text()
        invalid = [
            ({"HV_SMTP_HOST": "", "HV_SMTP_FROM": SMTP["HV_SMTP_FROM"]},
             "HV_SMTP_HOST und HV_SMTP_FROM müssen gemeinsam gesetzt oder beide leer sein."),
            ({"HV_SMTP_HOST": SMTP["HV_SMTP_HOST"], "HV_SMTP_FROM": ""},
             "HV_SMTP_HOST und HV_SMTP_FROM müssen gemeinsam gesetzt oder beide leer sein."),
            ({"HV_SMTP_USERNAME": "", "HV_SMTP_PASSWORD": SMTP["HV_SMTP_PASSWORD"]},
             "HV_SMTP_USERNAME und HV_SMTP_PASSWORD müssen gemeinsam gesetzt oder beide leer sein."),
            ({"HV_SMTP_USERNAME": SMTP["HV_SMTP_USERNAME"], "HV_SMTP_PASSWORD": ""},
             "HV_SMTP_USERNAME und HV_SMTP_PASSWORD müssen gemeinsam gesetzt oder beide leer sein."),
        ]
        invalid += [({"HV_SMTP_SECURITY": security}, "HV_SMTP_SECURITY muss starttls oder tls sein; plain ist nicht erlaubt.")
                    for security in ("plain", "STARTTLS", "ssl")]
        invalid += [({"HV_SMTP_PORT": port}, "HV_SMTP_PORT muss eine ganze Zahl zwischen 1 und 65535 sein.")
                    for port in ("0", "65536", "-1", "abc", "1.5", "999999999999999999999999")]
        invalid += [({"HV_SMTP_TIMEOUT_SECONDS": timeout}, "HV_SMTP_TIMEOUT_SECONDS muss eine ganze Zahl zwischen 1 und 60 sein.")
                    for timeout in ("0", "61", "-1", "abc", "1.5", "999999999999999999999999")]
        for values, message in invalid:
            with self.subTest(keys=tuple(values), message=message):
                self.config.write_text(before)
                self.smtp_config(SMTP | values)
                result = self.run_script("start")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stderr, message + "\n")
                self.assertEqual(self.calls(), [])
                self.assertFalse(Path(str(self.config) + ".lock").exists())
                self.assertNotIn(SMTP["HV_SMTP_PASSWORD"], result.stdout + result.stderr)

    def test_smtp_rejects_whitespace_only_host_or_sender_before_calling_docker(self):
        self.init()
        before = self.config.read_text()
        variants = [{key: value} for key in ("HV_SMTP_HOST", "HV_SMTP_FROM") for value in (" ", "\t", " \t ")]
        variants += [{"HV_SMTP_HOST": " ", "HV_SMTP_FROM": "\t"},
                     {"HV_SMTP_HOST": " ", "HV_SMTP_FROM": ""},
                     {"HV_SMTP_HOST": "", "HV_SMTP_FROM": " "}]
        for values in variants:
            for command in ("start", "stop", "status"):
                with self.subTest(values=values, command=command):
                    self.config.write_text(before)
                    self.smtp_config(SMTP | values)
                    self.log.unlink(missing_ok=True)
                    result = self.run_script(command)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(result.stderr, "HV_SMTP_HOST und HV_SMTP_FROM müssen gemeinsam gesetzt oder beide leer sein.\n")
                    self.assertEqual(self.calls(), [])

    def test_smtp_uses_only_private_config_and_ignores_caller_mail_settings(self):
        self.init()
        self.env.update(SMTP)
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        app = next(call for call in self.calls() if "up" in call["args"] and call["args"][-1] == "app")
        self.assertEqual(app["smtp"], {key: "" for key in SMTP if key != "HV_SMTP_PASSWORD"})
        self.assertTrue(app["smtp_password_matches"])

    def test_smtp_rejects_dollars_in_every_setting_before_calling_docker(self):
        self.init()
        before = self.config.read_text()
        for key in SMTP:
            for value in ("${UNSET:?synthetic-secret}", "ab$cd #x", "unmatched ${"):
                for command in ("start", "stop", "status"):
                    with self.subTest(key=key, command=command):
                        self.config.write_text(before)
                        self.smtp_config(SMTP | {key: value})
                        self.log.unlink(missing_ok=True)
                        result = self.run_script(command)
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        self.assertEqual(result.stderr, f"{key} darf kein $ enthalten (Compose-Interpolation).\n")
                        self.assertNotIn(value, result.stdout + result.stderr)
                        self.assertEqual(self.calls(), [])
                        self.assertFalse(Path(str(self.config) + ".lock").exists())

    def test_doctor_reports_only_mail_configuration_without_credentials_or_smtp_probes(self):
        self.init()
        before = self.config.read_text()
        self.doctor_tools()
        for values, expected in [({}, "Mail: aus"), (SMTP, "Mail: konfiguriert (smtp.example.invalid:465, tls)"),
                                 (SMTP | {"HV_SMTP_HOST": " smtp.example.invalid ", "HV_SMTP_FROM": " verwaltung@example.invalid "},
                                  "Mail: konfiguriert (smtp.example.invalid:465, tls)"),
                                 ({"HV_SMTP_HOST": SMTP["HV_SMTP_HOST"], "HV_SMTP_FROM": SMTP["HV_SMTP_FROM"]},
                                  "Mail: konfiguriert (smtp.example.invalid:587, starttls)")]:
            with self.subTest(expected=expected):
                self.config.write_text(before)
                self.smtp_config(values)
                self.log.unlink(missing_ok=True)
                result = self.assert_doctor("OK", "Einstellungen")
                self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("Mail:")], [expected])
                for key in ("HV_SMTP_USERNAME", "HV_SMTP_PASSWORD", "HV_SMTP_FROM"):
                    self.assertNotIn(SMTP[key], result.stdout + result.stderr)
                self.assertNotIn(SMTP["HV_SMTP_PASSWORD"], self.log.read_text())
                self.assertFalse(any(SMTP["HV_SMTP_HOST"] in argument for call in self.calls() for argument in call["args"]))

    def test_smtp_password_is_absent_from_diagnostics_and_backup_fingerprint(self):
        config = self.init()
        self.smtp_config(SMTP)
        result = self.run_script("diagnose")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(result.stdout.strip()) as bundle:
            for member in bundle.getmembers():
                if member.isfile():
                    self.assertNotIn(SMTP["HV_SMTP_PASSWORD"].encode(), bundle.extractfile(member).read())
        result = self.run_script("backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(next((self.root / "backups").glob("*.tar"))) as bundle:
            manifest = json.load(bundle.extractfile("manifest.json"))
            self.assertEqual(manifest["config_fingerprint"], self.fingerprint(config))
            for member in bundle.getmembers():
                if member.isfile():
                    self.assertNotIn(SMTP["HV_SMTP_PASSWORD"].encode(), bundle.extractfile(member).read())
        self.assertNotIn(SMTP["HV_SMTP_PASSWORD"], result.stdout + result.stderr + self.log.read_text())

    def test_fresh_start_prints_one_token_and_url_but_persists_only_its_hash(self):
        self.init()
        self.env["FAKE_ORGANIZATION_COUNT"] = "0"
        before = self.config.read_bytes()
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        match = re.search(r"^Setup token: ([a-f0-9]{64})$", result.stdout, re.M)
        self.assertIsNotNone(match, result.stdout)
        token = match.group(1)
        self.assertEqual(result.stdout.count(token), 1)
        self.assertIn(f"http://127.0.0.1:{TEST_PORT}/setup", result.stdout)
        sql = (self.base / "token.sql").read_text()
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("used_at=NULL", sql)
        self.assertIn("LOCK TABLE public.organization", sql)
        self.assertEqual(self.config.read_bytes(), before)
        for content in (result.stderr, sql, self.log.read_text(), self.config.read_text()):
            self.assertNotIn(token, content)
        args = [call["args"] for call in self.calls()]
        migrated = next(i for i, call in enumerate(args) if "migrate" in call)
        issued = next(i for i, call in enumerate(args) if "-qAt" in call)
        self.assertLess(migrated, issued)

    def test_restart_of_unclaimed_installation_rotates_the_token(self):
        self.init("--https", "verwaltung.example.org")
        self.env["FAKE_ORGANIZATION_COUNT"] = "0"
        tokens = []
        for _ in range(2):
            result = self.run_script("start")
            self.assertEqual(result.returncode, 0, result.stderr)
            tokens.append(re.search(r"^Setup token: ([a-f0-9]{64})$", result.stdout, re.M).group(1))
            self.assertIn("https://verwaltung.example.org/setup", result.stdout)
        self.assertNotEqual(*tokens)

    def test_existing_organization_does_not_issue_or_print_a_token(self):
        self.init()
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Setup token:", result.stdout)
        self.assertFalse((self.base / "token.sql").exists())

    def test_invalid_database_count_or_failed_token_storage_never_prints_a_token(self):
        self.init()
        for count, issued in [("unknown", "t"), ("0", "")]:
            self.env.update(FAKE_ORGANIZATION_COUNT=count, FAKE_TOKEN_ISSUED=issued)
            result = self.run_script("start")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Setup token:", result.stdout)
            self.assertNotIn("App ready", result.stdout)

    def assert_isolated(self, calls):
        for args in calls:
            if args[0] == "compose" and args[1:2] != ["version"]:
                self.assertIn("--project-name", args)
                self.assertEqual(args[args.index("--project-name") + 1], "verwalino")
                self.assertEqual(args[args.index("--env-file") + 1], str(self.config))
                self.assertEqual(args[args.index("-f") + 1], str(self.root / "infra/docker-compose.yml"))
                self.assertNotIn("down", args)
                self.assertNotIn("-v", args)

    def test_migration_failure_does_not_start_app(self):
        self.init()
        self.env["FAKE_DOCKER_FAIL"] = "migrate"
        result = self.run_script("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in c["args"] and "app" in c["args"] for c in self.calls()))
        self.assertNotIn(f"http://127.0.0.1:{TEST_PORT}", result.stdout)

    def test_https_uses_caddy_secure_cookies_and_explicit_host(self):
        self.init("--https", "verwaltung.example.org")
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any("caddy" in call["args"] and "up" in call["args"] for call in calls))
        for call in calls:
            self.assertEqual(call["secure"], "true")
            self.assertEqual(call["hosts"], "verwaltung.example.org,localhost,127.0.0.1")
        self.assertIn("https://verwaltung.example.org", result.stdout)
        self.assert_isolated([call["args"] for call in calls])

    def test_digest_image_is_pulled_not_built(self):
        image = "ghcr.io/example/verwalino@sha256:" + "a" * 64
        self.init("--image", image)
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("pull" in call["args"] for call in self.calls()))
        self.assertFalse(any("build" in call["args"] for call in self.calls()))

    def test_admin_passes_password_via_stdin_only_and_forbids_argument_password(self):
        self.init()
        password = "a-secret-password-123!"
        result = self.run_script("admin", "--org", "Example Company", "--email", "admin@example.org",
                                 "--password-stdin", input=password + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "stdin").read_text(), password + "\n")
        self.assertNotIn(password, self.log.read_text() + result.stdout + result.stderr)
        bootstrap = next(c["args"] for c in self.calls() if "bootstrap" in c["args"])
        self.assertIn("--password-stdin", bootstrap)
        self.assertEqual(bootstrap[bootstrap.index("--org") + 1], "Example Company")
        self.log.unlink()
        result = self.run_script("admin", "--org", "Test", "--email", "a@b.org", "--password", password)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_admin_forwards_valid_unicode_password_without_byte_length_restriction(self):
        self.init()
        password = "🔐" * 64
        result = self.run_script("admin", "--org", "Test", "--email", "admin@example.org",
                                 "--password-stdin", input=password + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "stdin").read_text(), password + "\n")
        self.assertNotIn(password, result.stdout + result.stderr + self.log.read_text())

    def test_diagnose_uses_only_read_commands_and_writes_a_private_redacted_bundle(self):
        config = self.init()
        result = self.run_script("diagnose")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(result.stdout.strip())
        self.assertEqual(path.parent, self.base / "diagnostics")
        self.assertRegex(path.name, r"^verwalino-diagnostics-.*\.tar\.gz$")
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with tarfile.open(path, "r:gz") as archive:
            members = {member.name: archive.extractfile(member).read().decode() for member in archive}
        self.assertEqual(len(members), 6)
        for content in members.values():
            for key in SECRETS:
                self.assertNotIn(config[key], content)
        self.assertIn("installer-failure-123", members["errors.log"])
        self.assertIn("RuntimeError", members["errors.log"])
        self.assertEqual(json.loads(members["database.json"])["migration_head"], RELEASE_TARGET)
        args = [call["args"] for call in self.calls()]
        self.assertEqual(len(args), 4)
        self.assertEqual(args[0][-4:], ["ps", "--all", "--format", "json"])
        self.assertTrue(any(call[-6:] == ["logs", "--no-color", "--no-log-prefix", "--tail", "2000", "app"] for call in args))
        build = next(call for call in args if "app.diagnostics" in call)
        schema = next(call for call in args if "psql" in call)
        self.assertEqual(schema[-8:], ["-U", "app_migrator", "-d", "hausverwaltung", "--set", "ON_ERROR_STOP=1", "-Atc", "SELECT version_num FROM public.alembic_version"])
        self.assertEqual(build[build.index("--schema-head") + 1], RELEASE_TARGET)
        start = build.index("run")
        self.assertEqual(build[start:start + 10], ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "app", "-m", "app.diagnostics", "--containers-json"])
        self.assert_isolated(args)

    def test_diagnose_supports_out_and_never_leaves_a_bundle_after_failure(self):
        self.init()
        output = self.base / "support packages"
        result = self.run_script("diagnose", "--out", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).parent, output)
        for command in ("ps", "logs", "app.diagnostics"):
            self.env["FAKE_DOCKER_FAIL"] = command
            failed = self.run_script("diagnose", "--out", str(self.base / command))
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(list((self.base / command).glob("*.tar.gz")), [])
        for args in [("--out",), ("--out", ""), ("--unknown", "x")]:
            self.assertNotEqual(self.run_script("diagnose", *args).returncode, 0)

    def test_diagnose_writes_a_bundle_with_unavailable_metadata_when_database_is_down(self):
        self.init()
        self.env["FAKE_DOCKER_FAIL"] = "psql"
        result = self.run_script("diagnose")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(Path(result.stdout.strip()), "r:gz") as archive:
            database = json.loads(archive.extractfile("database.json").read())
        self.assertEqual(database["status"], "unavailable")
        self.assertIsNone(database["migration_head"])
        self.assertIsNone(database["organization_count"])
        self.assertEqual(database["row_counts"], {})

    def test_diagnose_preserves_an_existing_output_directory_mode(self):
        self.init()
        output = self.base / "existing"
        output.mkdir(mode=0o755)
        result = self.run_script("diagnose", "--out", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(Path(result.stdout.strip()).stat().st_mode), 0o600)

    def test_diagnose_refuses_an_unwritable_existing_output_directory(self):
        self.init()
        output = self.base / "unwritable"
        output.mkdir(mode=0o500)
        self.addCleanup(output.chmod, 0o700)
        result = self.run_script("diagnose", "--out", str(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Output directory is not writable", result.stderr)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o500)
        self.assertEqual(self.calls(), [])

    def test_status_and_stop_are_non_destructive(self):
        self.init()
        before = self.config.read_bytes()
        for command in ("status", "stop"):
            result = self.run_script(command)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("ps" in c["args"] for c in self.calls()))
        self.assertTrue(any("stop" in c["args"] for c in self.calls()))
        self.assert_isolated([c["args"] for c in self.calls()])
        self.assertEqual(before, self.config.read_bytes())

    def test_start_stops_existing_inbox_worker_before_migration(self):
        self.init()
        self.env["FAKE_INBOX_CONTAINER"] = "existing-inbox-worker"
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [c["args"] for c in self.calls()]
        stopped = next(i for i, args in enumerate(calls) if args[-2:] == ["stop", "inbox-worker"])
        migrated = next(i for i, args in enumerate(calls) if "migrate" in args)
        self.assertLess(stopped, migrated)
        self.assertFalse(any("up" in args and args[-1] == "inbox-worker" for args in calls))
        self.assert_isolated(calls)

    def test_stop_includes_existing_inbox_worker_and_default_services(self):
        self.init()
        self.env["FAKE_INBOX_CONTAINER"] = "existing-inbox-worker"
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [c["args"] for c in self.calls()]
        self.assertTrue(any(args[-2:] == ["stop", "inbox-worker"] for args in calls))
        self.assertTrue(any(args[-1] == "stop" for args in calls))
        self.assert_isolated(calls)

    def test_absent_inbox_worker_is_not_stopped_or_started(self):
        self.init()
        for command in ("start", "stop"):
            result = self.run_script(command)
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = [c["args"] for c in self.calls()]
        self.assertFalse(any("inbox-worker" in args and ("stop" in args or "up" in args) for args in calls))

    def test_random_generation_failure_leaves_no_partial_config_or_lock(self):
        openssl = self.base / "bin" / "openssl"
        openssl.write_text("#!/bin/sh\nexit 19\n")
        openssl.chmod(0o755)
        result = self.run_script("init")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())
        self.assertFalse(Path(str(self.config) + ".lock").exists())
        self.assertEqual(list(self.base.glob(".env.verwalino-init.*")), [])
        self.assertEqual(self.calls(), [])

    def test_start_failures_never_report_success(self):
        self.init()
        for stage in ("build", "db", "app-ready"):
            with self.subTest(stage=stage):
                self.env["FAKE_DOCKER_FAIL"] = stage
                result = self.run_script("start")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("App ready", result.stdout)
        self.assertFalse(Path(str(self.config) + ".lock").exists())

    def test_incomplete_config_never_uses_inherited_passwords(self):
        config = self.init()
        self.env["MIGRATOR_PASSWORD"] = config["MIGRATOR_PASSWORD"]
        self.config.write_text("\n".join(line for line in self.config.read_text().splitlines()
                                        if not line.startswith("MIGRATOR_PASSWORD=")) + "\n")
        self.assertNotEqual(self.run_script("start").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_configuration_must_use_an_ignored_env_filename(self):
        self.env["VERWALINO_CONFIG"] = str(self.base / "credentials.py")
        self.assertNotEqual(self.run_script("init").returncode, 0)
        self.assertFalse((self.base / "credentials.py").exists())

    def test_lock_blocks_concurrent_start_and_preserves_existing_lock(self):
        self.init()
        lock = Path(str(self.config) + ".lock")
        lock.mkdir()
        self.assertNotEqual(self.run_script("start").returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertTrue(lock.is_dir())

    def test_admin_invalid_stdin_and_bootstrap_failure_propagate(self):
        self.init()
        args = ("admin", "--org", "Test", "--email", "admin@example.org", "--password-stdin")
        for password in ("short\n", "long-enough-but-no-newline", ""):
            self.assertNotEqual(self.run_script(*args, input=password).returncode, 0)
            self.assertEqual(self.calls(), [])
        self.env["FAKE_DOCKER_FAIL"] = "bootstrap"
        self.assertNotEqual(self.run_script(*args, input="long-enough-password\n").returncode, 0)

    def test_private_proxy_subnet_validation(self):
        for subnet in ("0.0.0.0/0", "8.8.8.0/24", "172.30.97.1/28", "10.0.0.0/8", "10.999.0.0/24"):
            with self.subTest(subnet=subnet):
                self.assertNotEqual(self.run_script("init", "--proxy-subnet", subnet).returncode, 0)
                self.assertFalse(self.config.exists())
        config = self.init("--proxy-subnet", "192.168.240.0/28")
        self.assertEqual(config["VERWALINO_PROXY_SUBNET"], "192.168.240.0/28")


class InstallMatrixWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / ".github/workflows/install-matrix.yaml").read_text())

    def test_prerequisites_fail_with_the_name_of_each_missing_tool(self):
        step = next(step for step in self.workflow["jobs"]["install"]["steps"] if step.get("id") == "report")
        check = step["run"].split("export DOCKER_CONFIG=", 1)[0]
        # The runner's Bash 5 guard is separate from tool availability.
        check = check.partition("\n")[2]
        tools = ("docker", "python3", "openssl", "curl", "nc")
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary)
            for missing in tools:
                with self.subTest(missing=missing):
                    for tool in tools:
                        if tool != missing:
                            (binary / tool).symlink_to(shutil.which("true"))
                    result = subprocess.run([shutil.which("bash"), "-e", "-c", check],
                                            env=dict(os.environ, PATH=str(binary)), capture_output=True,
                                            text=True, check=False, timeout=5)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(f"Missing required tool: {missing}", result.stderr)
                for tool in binary.iterdir():
                    tool.unlink()

    def test_runs_queue_by_ref_without_cancelling_active_runs(self):
        self.assertEqual(self.workflow.get("concurrency"), {
            "group": "install-matrix-${{ github.ref }}", "cancel-in-progress": False})

    def test_reports_use_verified_artifact_version(self):
        uploads = [step["uses"] for step in self.workflow["jobs"]["install"]["steps"]
                   if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(uploads, ["actions/upload-artifact@v7"])


class StackContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())

    def test_project_network_and_volume_isolation(self):
        self.assertEqual(self.stack["name"], "verwalino")
        self.assertEqual(set(self.stack["services"]), {"db", "app", "migrate", "bootstrap", "caddy", "inbox-worker"})
        for config in self.stack["volumes"].values():
            self.assertFalse(config and (config.get("external") or config.get("name")))
        self.assertTrue(self.stack["networks"]["backend"]["internal"])
        self.assertNotIn("traefik", self.stack["networks"])
        self.assertNotIn("ports", self.stack["services"]["db"])
        self.assertEqual(self.stack["services"]["app"]["ports"],
                         ["127.0.0.1:${VERWALINO_PORT:?missing port}:8000"])
        self.assertEqual(self.stack["services"]["caddy"]["profiles"], ["https"])
        self.assertEqual(self.stack["services"]["caddy"]["networks"], ["proxy"])

    def test_inbox_worker_is_opt_in_and_has_only_local_worker_authority(self):
        worker = self.stack["services"]["inbox-worker"]
        self.assertEqual(worker["profiles"], ["inbox"])
        self.assertEqual(worker["image"], self.stack["services"]["app"]["image"])
        self.assertEqual(worker["restart"], "unless-stopped")
        self.assertEqual(worker["environment"]["HV_WORKER_KIND"], "workflow_runner")
        self.assertIn("app_worker:", worker["environment"]["HV_DATABASE_URL_WORKER"])
        self.assertNotIn("HV_DATABASE_URL", worker["environment"])
        self.assertNotIn("HV_DATABASE_URL_REVIEW", worker["environment"])
        self.assertNotIn("HV_OPENAI_API_KEY", worker["environment"])
        self.assertEqual(worker["networks"], ["backend"])
        self.assertNotIn("ports", worker)
        self.assertIn("documents:/data/documents:ro", worker["volumes"])
        self.assertTrue(worker["read_only"])
        self.assertEqual(worker["cap_drop"], ["ALL"])
        self.assertIn("--healthcheck", worker["healthcheck"]["test"])

    def test_app_has_only_runtime_authority_and_optional_mail_credentials(self):
        app = self.stack["services"]["app"]
        env = app["environment"]
        self.assertIn("app_rw:", env["HV_DATABASE_URL"])
        self.assertIn("app_review:", env["HV_DATABASE_URL_REVIEW"])
        self.assertNotIn("MIGRATOR_PASSWORD", json.dumps(app))
        self.assertNotIn("HV_DATABASE_URL_SYNC", env)
        self.assertNotIn("HV_DATABASE_URL_WORKER", env)
        self.assertNotIn("HV_OPENAI_API_KEY", env)
        self.assertEqual({key: value for key, value in env.items() if key.startswith("HV_SMTP_")},
                         {key: "${" + key + ":-" + value + "}" for key, value in SMTP_DEFAULTS.items()})
        for key in ("HV_DECLARATION_IMPORT_ENABLED", "HV_DECLARATION_QA_ENABLED", "HV_INVOICE_EXTRACTION_ENABLED"):
            self.assertEqual(env[key], "false")
        self.assertEqual(env["FORWARDED_ALLOW_IPS"], "${VERWALINO_PROXY_SUBNET:?use self-host.sh}")
        self.assertEqual(env["HV_RELEASE_ID"], "${HV_RELEASE_ID:-}")
        self.assertEqual(app["user"], "10001:10001")
        self.assertTrue(app["read_only"])
        self.assertEqual(app["cap_drop"], ["ALL"])
        self.assertIn("health/ready", " ".join(app["healthcheck"]["test"]))
        self.assertEqual(app["volumes"], ["documents:/data/documents"])

    def test_app_defaults_parse_as_selfhost_with_mail_disabled(self):
        from app.core.config import Settings
        from app.services.mail_service import MailConfigurationError, validate_configuration

        environment = self.stack["services"]["app"]["environment"]
        defaults = {key.removeprefix("HV_").lower(): value.split(":-", 1)[1][:-1]
                    for key, value in environment.items() if key.startswith("HV_SMTP_")}
        config = Settings(_env_file=None, **defaults)
        self.assertEqual(config.deployment_mode, "selfhost")
        self.assertEqual((config.smtp_port, config.smtp_security, config.smtp_timeout_seconds), (587, "starttls", 15))
        self.assertEqual(config.smtp_host, "")
        self.assertEqual(config.smtp_from, "")
        with self.assertRaisesRegex(MailConfigurationError, "SMTP ist nicht konfiguriert"):
            validate_configuration(config)

    def test_privileged_operations_are_explicit_and_do_not_mount_documents(self):
        for service in ("migrate", "bootstrap"):
            config = self.stack["services"][service]
            self.assertEqual(config["profiles"], ["ops"])
            self.assertEqual(config["restart"], "no")
            self.assertNotIn("volumes", config)
            self.assertEqual(set(config["environment"]), {"HV_DATABASE_URL_SYNC"})
        self.assertEqual(self.stack["services"]["migrate"]["command"], ["alembic", "upgrade", "head"])
        self.assertEqual(self.stack["services"]["bootstrap"]["entrypoint"], ["python", "-m", "app.admin_cli"])
        mount = self.stack["services"]["db"]["volumes"][1]
        self.assertTrue(mount.startswith("./db/prod-init.sh:"))
        self.assertTrue(mount.endswith(":ro"))

    def test_caddy_keeps_perimeter_and_persistent_certificates(self):
        caddy = self.stack["services"]["caddy"]
        self.assertIn("caddy_data:/data", caddy["volumes"])
        content = (ROOT / "infra/Caddyfile").read_text()
        for expected in ("{$VERWALINO_DOMAIN}", "reverse_proxy app:8000", "max_size 30MB",
                         "Strict-Transport-Security", "Content-Security-Policy", "X-Content-Type-Options"):
            self.assertIn(expected, content)
        self.assertNotRegex(content, r"(?m)^\s*[A-Za-z0-9.-]+\.[A-Za-z]{2,}\s*\{")

    def test_nested_config_secrets_are_excluded_from_image_context(self):
        self.assertIn("**/.env.*", (ROOT / ".dockerignore").read_text().splitlines())


class SiteExtensionTests(unittest.TestCase):
    def setUp(self):
        self.base = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())
        self.extension = yaml.safe_load((ROOT / "infra/docker-compose.site.yml").read_text())

    def test_site_has_no_host_or_application_authority(self):
        site = self.extension["services"]["site"]
        self.assertTrue(site["read_only"])
        self.assertEqual(site["user"], "10001:10001")
        self.assertEqual(site["cap_drop"], ["ALL"])
        self.assertEqual(site["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(site["networks"], ["site"])
        for key in ("ports", "volumes", "secrets", "environment", "env_file", "privileged", "build"):
            self.assertNotIn(key, site)
        self.assertTrue(self.extension["networks"]["site"]["internal"])
        self.assertEqual(set(self.extension["services"]), {"site", "caddy"})
        self.assertNotIn("site", self.base["services"])
        self.assertEqual(self.base["services"]["caddy"]["networks"], ["proxy"])
        self.assertEqual(self.extension["services"]["caddy"]["networks"], ["proxy", "site"])

    def test_caddy_import_is_opt_in_and_headers_protect_both_site_hosts(self):
        content = (ROOT / "infra/Caddyfile.site").read_text()
        self.assertIn("verwalino.de {", content)
        self.assertIn("www.verwalino.de {", content)
        self.assertIn("reverse_proxy site:8080", content)
        self.assertIn("redir https://verwalino.de{uri} 308", content)
        self.assertIn("script-src 'self'", content)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", content)
        self.assertIn("font-src 'self'", content)
        self.assertEqual(content.count("import site_headers"), 2)
        for header in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options",
                       "Referrer-Policy", "Content-Security-Policy", "-Server"):
            self.assertIn(header, content)
        self.assertNotIn("import", (ROOT / "infra/Caddyfile").read_text())
        mounts = self.extension["services"]["caddy"]["volumes"]
        self.assertTrue(all(mount["read_only"] and not mount["bind"]["create_host_path"] for mount in mounts))


class ReleaseBundleTests(unittest.TestCase):
    def test_release_assembly_covers_payloads_and_compose_bind_mounts(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-image.yaml").read_text())
        bundle = workflow["jobs"]["installer"]
        self.assertEqual(bundle["needs"], "release")
        self.assertEqual(bundle["permissions"], {"contents": "write", "id-token": "write"})
        step = next(step for step in bundle["steps"] if step.get("id") == "bundle")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ("install.sh", "scripts/self-host.sh", "scripts/build-release-bundle.sh", "infra/docker-compose.yml",
                         "infra/Caddyfile", "infra/db/prod-init.sh", ".env.example"):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / path).read_bytes())
            digest = "sha256:" + "a" * 64
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]],
                                    cwd=root, env=dict(os.environ, DIGEST=digest),
                                    text=True, capture_output=True, check=False, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            assets = root / "release-assets"
            self.assertEqual((assets / "IMAGE_DIGEST").read_text(), digest + "\n")
            lines = (assets / "SHA256SUMS").read_text().splitlines()
            self.assertEqual(len(lines), 7)
            self.assertEqual({line.split("  ")[1] for line in lines}, {
                "install.sh", "self-host.sh", "docker-compose.yml", "Caddyfile",
                "prod-init.sh", "env.example", "IMAGE_DIGEST"})
            for line in lines:
                checksum, name = line.split("  ")
                self.assertEqual(checksum, hashlib.sha256((assets / name).read_bytes()).hexdigest())
            self.assertEqual((assets / "prod-init.sh").read_bytes(), (ROOT / "infra/db/prod-init.sh").read_bytes())


@unittest.skipUnless("version 3." in MAC_BASH_VERSION, "/bin/bash is unavailable or is not Bash 3.x")
class Bash32UpdateRestoreTests(SelfHostTests):
    bash = "/bin/bash"


def load_tests(loader, tests, pattern):
    suite = loader.loadTestsFromTestCase(SelfHostTests)
    suite.addTests(loader.loadTestsFromTestCase(InstallMatrixWorkflowTests))
    suite.addTests(loader.loadTestsFromTestCase(StackContractTests))
    suite.addTests(loader.loadTestsFromTestCase(SiteExtensionTests))
    suite.addTests(loader.loadTestsFromTestCase(ReleaseBundleTests))
    suite.addTests(Bash32UpdateRestoreTests(name) for name in loader.getTestCaseNames(SelfHostTests)
                   if name.startswith(("test_update_", "test_restore_")))
    return suite


if __name__ == "__main__":
    print(f"PATH bash: {PATH_BASH_VERSION}\n/bin/bash: {MAC_BASH_VERSION}", file=sys.stderr)
    unittest.main()
