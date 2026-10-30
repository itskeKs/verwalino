"""Portable installer contracts; Docker is faked and no containers are started.

Run in backend/ with uv run python ../scripts/test-self-host.py.
"""
import hashlib
import io
import json
import hashlib
import os
import shutil
import re
import stat
import subprocess
import tempfile
import tarfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ("MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD", "SESSION_SECRET")


class SelfHostTests(unittest.TestCase):
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
    statement = sys.argv[-1]
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
    elif "pg_database" in statement:
        name = statement.split("datname='")[1].split("'")[0]
        if name in databases:
            print("1")
    else:
        print(os.environ.get("FAKE_RESTORED_SCHEMA" if "hausverwaltung_restore" in sys.argv else "FAKE_SCHEMA", "0139"))
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
if "exec" in sys.argv and "psql" in sys.argv:
    if any("SELECT count(*) FROM public.organization" in arg for arg in sys.argv):
        print(os.environ.get("FAKE_ORGANIZATION_COUNT", "1"))
    else:
        with open(os.environ["FAKE_TOKEN_SQL"], "w") as capture:
            capture.write(sys.stdin.read())
        print(os.environ.get("FAKE_TOKEN_ISSUED", "t"))
''')
        docker.chmod(0o755)
        self.documents = self.base / "documents"
        self.documents.mkdir()
        (self.documents / "original.pdf").write_bytes(b"synthetic original")
        self.db_state = self.base / "databases.json"
        self.db_state.write_text(json.dumps({"hausverwaltung": "old data"}))
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"), FAKE_DOCUMENTS=str(self.documents), PATH=f"{binary}:{os.environ['PATH']}",
                        FAKE_DB_STATE=str(self.db_state),
                        FAKE_DOCUMENT_MOVES=str(self.base / "document-moves.jsonl"),
                        FAKE_DROP_DOCUMENTS=str(self.base / "drop-documents.json"),
                        VERWALINO_CONFIG=str(self.config), FAKE_DOCKER_LOG=str(self.log),
                        FAKE_DOCKER_STDIN=str(self.base / "stdin"),
                        FAKE_TOKEN_SQL=str(self.base / "token.sql"))

    def run_script(self, *args, input=None, stderr_closed=False, stderr=subprocess.PIPE):
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
        for changes, message in [({"schema_head": "0140"}, "newer"),
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
        self.assertEqual(config["VERWALINO_PORT"], "18080")
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
        self.assertIn("http://127.0.0.1:18080", result.stdout)
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
        self.assertIn("http://127.0.0.1:18080/setup", result.stdout)
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
            if args[0] == "compose" and args[1:] != ["version"]:
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
        self.assertNotIn("http://127.0.0.1:18080", result.stdout)

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


class StackContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())

    def test_project_network_and_volume_isolation(self):
        self.assertEqual(self.stack["name"], "verwalino")
        self.assertEqual(set(self.stack["services"]), {"db", "app", "migrate", "bootstrap", "caddy"})
        for config in self.stack["volumes"].values():
            self.assertFalse(config and (config.get("external") or config.get("name")))
        self.assertTrue(self.stack["networks"]["backend"]["internal"])
        self.assertNotIn("traefik", self.stack["networks"])
        self.assertNotIn("ports", self.stack["services"]["db"])
        self.assertEqual(self.stack["services"]["app"]["ports"],
                         ["127.0.0.1:${VERWALINO_PORT:?missing port}:8000"])
        self.assertEqual(self.stack["services"]["caddy"]["profiles"], ["https"])
        self.assertEqual(self.stack["services"]["caddy"]["networks"], ["proxy"])

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
        self.assertNotIn("verwaltung.karat.immo", content)

    def test_nested_config_secrets_are_excluded_from_image_context(self):
        self.assertIn("**/.env.*", (ROOT / ".dockerignore").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
