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
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TARGET = runpy.run_path(str(ROOT / "backend/app/deployment.py"))["TARGET"]
SECRETS = ("MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD", "SESSION_SECRET")
with socket.socket() as port_probe:
    port_probe.bind(("127.0.0.1", 0))
    TEST_PORT = str(port_probe.getsockname()[1])


class SelfHostTests(unittest.TestCase):
    def test_help_points_to_the_documented_unlock_command(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("admin_cli unlock", result.stdout)
        self.assertNotIn("unlock SQL", result.stdout)

    def test_login_guidance_defers_unimplemented_signup_controls(self):
        for name in ("DEPLOY.md", "docs/architecture.md", "docs/feature-status.md"):
            with self.subTest(document=name):
                content = (ROOT / name).read_text()
                self.assertNotIn("honeypot", content)
                self.assertNotIn("`signup` scope", content)
                self.assertNotIn("`signup:`", content)
        section = (ROOT / "DEPLOY.md").read_text().split("## Login lockout and proxy trust\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Signup throttling arrives with #19.", section)

    def test_login_guidance_explains_throttle_downgrade_refusal(self):
        section = (ROOT / "DEPLOY.md").read_text().split("## Login lockout and proxy trust\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`downgrade()` of 0142 refuses while rows exist", section)
        self.assertIn("delete the `auth_throttle` rows first", section)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "checkout"
        for name in ("scripts/self-host.sh", "backend/app/selfhost_backup.py", "backend/app/deployment.py"):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.config = self.base / ".env.selfhost"
        self.log = self.base / "docker.jsonl"
        binary = self.base / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text('''#!/usr/bin/env python3
import json, os, signal, subprocess, sys
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "image": os.environ.get("VERWALINO_IMAGE"), "secure": os.environ.get("VERWALINO_COOKIE_SECURE"), "hosts": os.environ.get("VERWALINO_ALLOWED_HOSTS"), "profiles": os.environ.get("COMPOSE_PROFILES"), "caller_secret_used": os.environ.get("MIGRATOR_PASSWORD") == "attacker"}) + "\\n")
failure = os.environ.get("FAKE_DOCKER_FAIL")
if failure and (failure in sys.argv or (failure == "app-ready" and "up" in sys.argv and sys.argv[-1] == "app")):
    sys.exit(17)
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
                         "running|" + ("" if service == "caddy" else "healthy") + "|" + service + "-id"))
    sys.exit(0)
if "port" in sys.argv:
    port = os.environ.get("VERWALINO_PORT", "18080") if sys.argv[-2] == "app" else sys.argv[-1]
    print(os.environ.get("FAKE_PUBLISHED", "127.0.0.1:" + port))
    sys.exit(0)
if "find" in sys.argv and "caddy" in sys.argv:
    print(os.environ.get("FAKE_CERTIFICATE", "/data/caddy/certificates/acme/verwaltung.example.org/verwaltung.example.org.crt"))
    sys.exit(0)
if "ps" in sys.argv and sys.argv[-1] == "inbox-worker":
    print(os.environ.get("FAKE_INBOX_CONTAINER", ""))
if "logs" in sys.argv:
    secrets = " ".join(os.environ[key] for key in ("MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD", "SESSION_SECRET"))
    print("2026-10-30 12:00:00,123 ERROR app.test request_id=installer-failure-123 RuntimeError: " + secrets)
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
        print(os.environ.get("FAKE_RESTORED_SCHEMA" if "hausverwaltung_restore" in sys.argv else "FAKE_SCHEMA", "0139"))
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
if os.environ.get("FAKE_IMAGE_TARGET"):
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
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"), FAKE_DOCUMENTS=str(self.documents), PATH=f"{binary}:{os.environ['PATH']}",
                        FAKE_DB_STATE=str(self.db_state),
                        FAKE_CHECKOUT=str(self.root),
                        FAKE_DOCUMENT_MOVES=str(self.base / "document-moves.jsonl"),
                        FAKE_DROP_DOCUMENTS=str(self.base / "drop-documents.json"),
                        VERWALINO_CONFIG=str(self.config), FAKE_DOCKER_LOG=str(self.log),
                        FAKE_DOCKER_STDIN=str(self.base / "stdin"),
                        FAKE_TOKEN_SQL=str(self.base / "token.sql"))

    def run_script(self, *args, input=None, stderr_closed=False, stderr=subprocess.PIPE):
        if args[:1] == ("init",) and not self.config.exists() and "--port" not in args:
            args = (*args, "--port", TEST_PORT)
        command = ["bash", str(self.root / "scripts/self-host.sh"), *args]
        if stderr_closed:
            command = ["bash", "-c", 'exec 2>&-; exec "$@"', "closed-stderr", *command]
        return subprocess.run(command, env=self.env, input=input,
                              text=True, stdout=subprocess.PIPE, stderr=stderr, cwd=self.base, check=False, timeout=15)

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
        for name in ("bash", "dirname", "stat", "awk", "sed", "sort", "mktemp", "rm", "cat", "id", "sleep"):
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
    if "s_client" in sys.argv:
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
        self.assertIn(f"http://127.0.0.1:{TEST_PORT}", result.stdout)
        self.assert_isolated(args)

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

    def test_no_privileged_or_external_service_credentials_in_app(self):
        app = self.stack["services"]["app"]
        env = app["environment"]
        self.assertIn("app_rw:", env["HV_DATABASE_URL"])
        self.assertIn("app_review:", env["HV_DATABASE_URL_REVIEW"])
        self.assertNotIn("MIGRATOR_PASSWORD", json.dumps(app))
        self.assertNotIn("HV_DATABASE_URL_SYNC", env)
        self.assertNotIn("HV_DATABASE_URL_WORKER", env)
        self.assertNotIn("HV_OPENAI_API_KEY", env)
        self.assertFalse(any(key.startswith("HV_SMTP_") for key in env))
        for key in ("HV_DECLARATION_IMPORT_ENABLED", "HV_DECLARATION_QA_ENABLED", "HV_INVOICE_EXTRACTION_ENABLED"):
            self.assertEqual(env[key], "false")
        self.assertEqual(env["FORWARDED_ALLOW_IPS"], "${VERWALINO_PROXY_SUBNET:?use self-host.sh}")
        self.assertEqual(env["HV_RELEASE_ID"], "${HV_RELEASE_ID:-}")
        self.assertEqual(app["user"], "10001:10001")
        self.assertTrue(app["read_only"])
        self.assertEqual(app["cap_drop"], ["ALL"])
        self.assertIn("health/ready", " ".join(app["healthcheck"]["test"]))
        self.assertEqual(app["volumes"], ["documents:/data/documents"])

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


if __name__ == "__main__":
    unittest.main()
