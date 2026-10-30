"""Hosted-server script contracts; Docker, git, curl and sleep are faked, nothing leaves the temp dir.

Run in backend/ with uv run python ../scripts/test-server-deploy.py.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/server-bootstrap.sh"
BACKUP = ROOT / "scripts/server-backup.sh"
DEPLOY = ROOT / "scripts/deploy-server.sh"
INSTALLER = ROOT / "scripts/self-host.sh"
SCRIPTS = ("server-bootstrap.sh", "server-backup.sh", "deploy-server.sh")
OLD_DIGEST = "ghcr.io/itskeks/verwalino@sha256:" + "b" * 64
NEW_DIGEST = "ghcr.io/itskeks/verwalino@sha256:" + "a" * 64

# One script serves as docker, git, curl and sleep; it logs every call and fails on demand.
FAKE_TOOL = r'''#!/usr/bin/env python3
import json, os, sys, tarfile
tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
env = os.environ
with open(env["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"tool": tool, "args": args}) + "\n")
fail = env.get("FAKE_FAIL", "").split(",")


def out(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def counter(name):
    path = env["FAKE_STATE"] + "/" + name
    count = int(open(path).read()) + 1 if os.path.exists(path) else 1
    open(path, "w").write(str(count))
    return count


if tool == "sleep":
    sys.exit(0)

if tool == "curl":
    if env.get("FAKE_HEALTH") == "never" or counter("curl") <= int(env.get("FAKE_HEALTH_FAILS", "0")):
        sys.exit(22)
    sys.exit(0)

if tool == "git":
    rest = list(args)
    while rest and rest[0] in ("-c", "-C"):
        rest = rest[2:]
    sub = rest[0]
    if sub in fail:
        sys.exit(17)
    if sub == "rev-parse" and "--verify" in rest:
        if env.get("FAKE_MISSING_TAG"):
            sys.exit(1)
        out("0123456789abcdef\n")
    elif sub == "rev-parse":
        out("abc1234\n")
    elif sub == "describe":
        out("v0.0.9\n")
    elif sub == "status":
        out(env.get("FAKE_DIRTY", ""))
    elif sub == "merge-base" and env.get("FAKE_NOT_ON_MAIN"):
        sys.exit(1)
    sys.exit(0)

# docker
if args[0] == "compose":
    if "exec" in args and "psql" in args:
        if any("SELECT count(*) FROM public.organization" in arg for arg in args):
            out(env.get("FAKE_ORGANIZATION_COUNT", "1") + "\n")
        else:
            sys.stdin.read()
            out("t\n")
    if env.get("FAKE_FAIL_COMPOSE") and env["FAKE_FAIL_COMPOSE"] in args:
        sys.exit(17)
    sys.exit(0)
if args[0] == "pull":
    sys.exit(17 if "pull" in fail else 0)
if args[0] == "image":
    if not env.get("FAKE_NO_DIGEST"):
        out("other/repo@sha256:" + "c" * 64 + "\nghcr.io/itskeks/verwalino@sha256:" + "a" * 64 + "\n")
    sys.exit(0)
if args[0] == "ps":
    if "label=com.docker.compose.service=db" in args and not env.get("FAKE_NO_DB"):
        out("dbcontainer\n")
    elif "label=com.docker.compose.service=app" in args:
        out("appcontainer\n")
    sys.exit(0)
if args[0] == "volume":
    out("verwalino_documents\n" + ("other_documents\n" if env.get("FAKE_TWO_VOLUMES") else ""))
    sys.exit(0)
if args[0] == "inspect":
    out("postgres:16-alpine@sha256:" + "d" * 64 + "\n")
    sys.exit(0)
if args[0] == "exec" and "pg_dump" in args:
    if "pg_dump" in fail:
        sys.exit(17)
    sys.stdout.buffer.write(b"PGDMP fake custom dump")
    sys.exit(0)
if args[0] == "exec" and "pg_restore" in args:
    sys.stdin.buffer.read()
    sys.exit(17 if "pg_restore" in fail else 0)
if args[0] == "run" and "tar" in args:
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
        info = tarfile.TarInfo("invoice.pdf")
        info.size = 4
        import io
        archive.addfile(info, io.BytesIO(b"%PDF"))
    sys.exit(0)
if args[0] == "logs":
    for number in range(1, 4):
        out("FAKE-APP-LOG line %d\n" % number)
    sys.exit(0)
sys.exit(0)
'''


class ServerCase(unittest.TestCase):
    """A fake server root with config, backups and logs, and fake docker/git/curl/sleep on PATH."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.srv = self.base / "srv"
        for name in ("config", "backups", "logs"):
            (self.srv / name).mkdir(parents=True)
        self.config = self.srv / "config/.env.selfhost"
        self.log = self.base / "calls.jsonl"
        (self.base / "state").mkdir()
        binary = self.base / "bin"
        binary.mkdir()
        for tool in ("docker", "git", "curl", "sleep"):
            (binary / tool).write_text(FAKE_TOOL)
            (binary / tool).chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("VERWALINO_", "FAKE_", "COMPOSE_", "SSH_"))}
        self.env.update(PATH=f"{binary}:{os.environ['PATH']}", VERWALINO_SERVER_ROOT=str(self.srv),
                        VERWALINO_CONFIG=str(self.config), FAKE_LOG=str(self.log),
                        FAKE_STATE=str(self.base / "state"))

    def install_config(self, image=OLD_DIGEST):
        """Create the installer config the way a maintainer does."""
        result = subprocess.run(["bash", str(INSTALLER), "init", "--https", "verwaltung.example.org", "--image", image],
                                env=self.env, capture_output=True, text=True, cwd=self.base, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_script(self, script, *args, **env):
        return subprocess.run(["bash", str(script), *args], env=dict(self.env, **env), capture_output=True,
                              text=True, cwd=self.base, timeout=30)

    def calls(self, tool=None):
        entries = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [e for e in entries if tool is None or e["tool"] == tool]

    def first(self, predicate, tool=None):
        for index, entry in enumerate(self.calls(tool)):
            if predicate(entry["args"]):
                return index
        self.fail("expected call not found")

    def config_values(self):
        return dict(line.split("=", 1) for line in self.config.read_text().splitlines()
                    if line and not line.startswith("#"))


class ScriptHygieneTests(unittest.TestCase):
    def test_scripts_parse_and_are_executable(self):
        for name in SCRIPTS:
            path = ROOT / "scripts" / name
            if not path.exists():
                continue
            with self.subTest(script=name):
                self.assertTrue(os.access(path, os.X_OK), "script must be executable")
                self.assertEqual(path.read_text().splitlines()[0], "#!/usr/bin/env bash")
                result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck is not installed")
    def test_scripts_are_shellcheck_clean(self):
        paths = [str(ROOT / "scripts" / name) for name in SCRIPTS if (ROOT / "scripts" / name).exists()]
        result = subprocess.run(["shellcheck", *paths], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


class BootstrapTests(unittest.TestCase):
    """Argument handling only: everything after the checks needs root and a real host."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key = Path(self.temp.name) / "admin.pub"
        self.key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExample0 admin@example\n")

    def run_script(self, *args):
        return subprocess.run(["bash", str(BOOTSTRAP), *args], capture_output=True, text=True, timeout=15)

    def test_usage_requires_admin_user_and_key_file(self):
        for args in [(), ("admin",), ("admin", str(self.key), "host", "extra")]:
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage: server-bootstrap.sh ADMIN_USER PUBKEY_FILE [HOSTNAME]", result.stderr)

    def test_unusable_admin_user_key_file_or_hostname_is_refused(self):
        empty = Path(self.temp.name) / "empty.pub"
        empty.write_text("")
        for args, message in [
            (("root", str(self.key)), "ADMIN_USER"),
            (("Admin", str(self.key)), "ADMIN_USER"),
            (("a b", str(self.key)), "ADMIN_USER"),
            (("-x", str(self.key)), "ADMIN_USER"),
            (("admin", str(self.temp.name) + "/missing.pub"), "PUBKEY_FILE"),
            (("admin", str(empty)), "PUBKEY_FILE"),
            (("admin", str(self.key), "Bad_Host"), "HOSTNAME"),
            (("admin", str(self.key), "a.b"), "HOSTNAME"),
        ]:
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "never run the privileged part of the bootstrap in a test")
    def test_valid_arguments_still_require_root(self):
        result = self.run_script("admin", str(self.key), "verwalino")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Run as root", result.stderr)

    def test_no_key_is_embedded_and_the_documented_steps_are_present(self):
        text = BOOTSTRAP.read_text()
        self.assertIsNone(re.search(r"ssh-(ed25519|rsa) AAAA|ecdsa-sha2-\S+ AAAA", text))
        self.assertIn("AllowUsers %s", text)
        self.assertNotRegex(text, r"AllowUsers (?!%s)\w")
        for expected in ("PasswordAuthentication no", "PermitRootLogin no", "ufw limit 22/tcp",
                         "ufw allow 80/tcp", "ufw allow 443/tcp", "fail2ban", "unattended-upgrades",
                         "download.docker.com", '"max-size":"10m"', "SystemMaxUse=", "count=4096",
                         "timedatectl set-timezone", "hostnamectl set-hostname", "NOPASSWD:ALL"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)


class BackupTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.install_config()

    def backup_dirs(self):
        return sorted(p.name for p in (self.srv / "backups").iterdir())

    def test_backup_holds_dump_documents_config_and_checksums(self):
        result = self.run_script(BACKUP)
        self.assertEqual(result.returncode, 0, result.stderr)
        [name] = self.backup_dirs()
        self.assertRegex(name, r"^\d{8}T\d{6}Z$")
        target = self.srv / "backups" / name
        self.assertEqual(sorted(p.name for p in target.iterdir()),
                         ["MANIFEST", "SHA256SUMS", "config.env", "db.dump", "documents.tar"])
        self.assertEqual((target / "db.dump").read_bytes(), b"PGDMP fake custom dump")
        with tarfile.open(target / "documents.tar") as archive:
            self.assertEqual(archive.getnames(), ["invoice.pdf"])
        self.assertEqual((target / "config.env").read_bytes(), self.config.read_bytes())
        self.assertIn(f"image={OLD_DIGEST}", (target / "MANIFEST").read_text())
        for line in (target / "SHA256SUMS").read_text().splitlines():
            digest, filename = line.split()
            self.assertEqual(hashlib.sha256((target / filename).read_bytes()).hexdigest(), digest)
        # Secrets and patient data are for the owner and the admin group only.
        self.assertEqual(stat.S_IMODE(target.stat().st_mode) & 0o007, 0)
        for path in target.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640, path.name)
        self.assertFalse((self.srv / "backups/.lock").exists())

    def test_containers_are_found_by_compose_label_and_documents_are_mounted_read_only(self):
        self.assertEqual(self.run_script(BACKUP).returncode, 0)
        docker = [c["args"] for c in self.calls("docker")]
        self.assertIn(["ps", "--quiet", "--filter", "label=com.docker.compose.project=verwalino",
                       "--filter", "label=com.docker.compose.service=db", "--filter", "status=running"], docker)
        dump = next(a for a in docker if "pg_dump" in a)
        self.assertEqual(dump, ["exec", "dbcontainer", "pg_dump", "--format=custom", "--username", "app_migrator",
                                "--dbname", "hausverwaltung"])
        run = next(a for a in docker if a[0] == "run")
        for expected in ("--rm", "--network", "none", "--volume", "verwalino_documents:/data:ro"):
            self.assertIn(expected, run)
        self.assertLess(docker.index(dump), docker.index(run), "dump first, then the documents")
        self.assertFalse(any(a[:2] == ["compose", "--project-name"] for a in docker))

    def test_label_becomes_part_of_the_name_and_is_validated(self):
        self.assertEqual(self.run_script(BACKUP, "pre-v1.2.3").returncode, 0)
        [name] = self.backup_dirs()
        self.assertRegex(name, r"^\d{8}T\d{6}Z-pre-v1\.2\.3$")
        for args in [("a;b",), ("../x",), ("-x",), ("a b",), ("a", "b")]:
            with self.subTest(args=args):
                result = self.run_script(BACKUP, *args)
                self.assertEqual(result.returncode, 2)
        self.assertEqual(self.backup_dirs(), [name])

    def test_failures_leave_no_partial_backup_and_release_the_lock(self):
        for env, message in [({"FAKE_NO_DB": "1"}, "exactly one running container 'db'"),
                             ({"FAKE_FAIL": "pg_dump"}, ""),
                             ({"FAKE_FAIL": "pg_restore"}, "cannot be read back"),
                             ({"FAKE_TWO_VOLUMES": "1"}, "exactly one running volume 'documents'")]:
            with self.subTest(env=env):
                result = self.run_script(BACKUP, **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.backup_dirs(), [])

    def test_missing_config_backup_dir_or_running_backup_is_refused(self):
        (self.srv / "backups/.lock").mkdir()
        self.assertIn("another backup is running", self.run_script(BACKUP).stderr)
        self.assertTrue((self.srv / "backups/.lock").exists(), "a foreign lock must stay")
        (self.srv / "backups/.lock").rmdir()
        self.config.unlink()
        self.assertIn("missing or unreadable", self.run_script(BACKUP).stderr)
        shutil.rmtree(self.srv / "backups")
        self.assertIn("does not exist", self.run_script(BACKUP).stderr)
        self.assertEqual(self.calls("docker"), [])

    def test_retention_keeps_14_days_8_weeks_and_10_labelled_backups(self):
        backups = self.srv / "backups"
        day = datetime.date(2025, 11, 1)
        plain = []
        while day <= datetime.date(2026, 2, 28):  # crosses the year and several month ends
            plain.append(f"{day:%Y%m%d}T010000Z")
            day += datetime.timedelta(days=1)
        # Two backups on some days: only the newest one of a day may count.
        extra = [f"{d[:8]}T180000Z" for d in plain[::9]]
        labelled = [f"{datetime.date(2026, 3, 1) + datetime.timedelta(days=i):%Y%m%d}T020000Z-pre-v0.0.{i}" for i in range(12)]
        unrelated = ["notes", "20260101", "20260101T010000Z.txt", ".keep", "20260101T010000", "x20260101T010000Z"]
        for name in plain + extra + labelled + [u for u in unrelated if not u.endswith('.txt')]:
            (backups / name).mkdir()
        (backups / unrelated[2]).write_text("a file with a backup-like name")
        self.assertEqual(self.run_script(BACKUP).returncode, 0)

        survivors = {p.name for p in backups.iterdir()}
        [new] = [n for n in survivors if n not in plain + extra + labelled + unrelated]
        plain_names = sorted(plain + extra + [new], reverse=True)
        keep, days, weeks = set(), [], []
        for name in plain_names:
            date = datetime.datetime.strptime(name[:8], "%Y%m%d").date()
            week = date.isocalendar()[:2]
            if name[:8] not in days and len(days) < 14:
                days.append(name[:8]); keep.add(name)
            if week not in weeks and len(weeks) < 8:
                weeks.append(week); keep.add(name)
        keep.update(sorted(labelled, reverse=True)[:10])
        self.assertEqual({n for n in survivors if re.match(r"^\d{8}T\d{6}Z(-.+)?$", n) and (backups / n).is_dir()}, keep)
        self.assertLessEqual(len(keep) - 10, 14 + 8)
        for name in unrelated:
            self.assertTrue((backups / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
