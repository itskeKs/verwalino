"""Hosted-server contracts; service operations, git fetch, curl, sleep and date are faked.

Real git archive only reads local inputs; Docker is always faked.

Run in backend/ with uv run python ../scripts/test-server-deploy.py.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # the workflow contracts need PyYAML; make test-installer provides it
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/server-bootstrap.sh"
BACKUP = ROOT / "scripts/server-backup.sh"
DEPLOY = ROOT / "scripts/deploy-server.sh"
INSTALLER = ROOT / "scripts/self-host.sh"
with socket.socket() as port_probe:  # init refuses an occupied port, so never depend on the default one
    port_probe.bind(("127.0.0.1", 0))
    TEST_PORT = str(port_probe.getsockname()[1])
SCRIPTS = ("server-bootstrap.sh", "server-backup.sh", "deploy-server.sh")
OLD_DIGEST = "ghcr.io/itskeks/verwalino@sha256:" + "b" * 64
NEW_DIGEST = "ghcr.io/itskeks/verwalino@sha256:" + "a" * 64
OLD_COMMIT = "abc1234"
NEW_COMMIT = "0123456789abcdef"
SITE_SHA = "1" * 40
PREVIOUS_SITE_SHA = "2" * 40
SITE_REPO = "ghcr.io/itskeks/verwalino-site"
SITE_DIGEST = SITE_REPO + "@sha256:" + "d" * 64

# One script serves as docker, git, curl, sleep and date; service calls are logged and fail on demand.
FAKE_TOOL = r'''#!/usr/bin/env python3
import datetime, io, json, os, sys, tarfile, time
tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
env = os.environ
if tool == "date":
    assert len(args) == 2 and args[0] == "-u" and args[1] in (
        "+%Y%m%dT%H%M%SZ", "+%Y-%m-%dT%H:%M:%SZ"), args
    instant = datetime.datetime.fromtimestamp(int(env["FAKE_DATE_EPOCH"]), datetime.timezone.utc)
    print(instant.strftime(args[1][1:]))
    sys.exit(0)

with open(env["FAKE_LOG"], "a") as log:
    entry = {"tool": tool, "args": args, "site_image": env.get("VERWALINO_SITE_IMAGE")}
    if tool == "docker" and args[:1] == ["compose"]:
        entry.update(image=env.get("VERWALINO_IMAGE"), release_id=env.get("HV_RELEASE_ID"))
        entry.update(mode=env.get("HV_DEPLOYMENT_MODE"), public_host=env.get("HV_PUBLIC_HOST"),
                     allowed_hosts=env.get("HV_ALLOWED_HOSTS"), smtp_host=env.get("HV_SMTP_HOST"),
                     smtp_from=env.get("HV_SMTP_FROM"), installer_allowed_hosts=env.get("VERWALINO_ALLOWED_HOSTS"))
    log.write(json.dumps(entry) + "\n")
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
    head = env["FAKE_STATE"] + "/HEAD"
    rest = list(args)
    while rest and rest[0] in ("-c", "-C"):
        rest = rest[2:]
    sub = rest[0]
    if sub in fail:
        sys.exit(17)
    if sub == "rev-parse" and "--verify" in rest:
        if env.get("FAKE_MISSING_TAG"):
            sys.exit(1)
        out(env.get("FAKE_REVISION", "0123456789abcdef") + "\n")
    elif sub == "rev-parse":
        out(open(head).read() + "\n")
    elif sub == "describe":
        out("v0.0.9\n" if open(head).read() == "abc1234" else "v1.2.3\n")
    elif sub == "checkout":
        open(head, "w").write("0123456789abcdef" if rest[-1].startswith("refs/tags/") else rest[-1])
    elif sub == "status":
        out(env.get("FAKE_DIRTY", ""))
    elif sub == "merge-base" and env.get("FAKE_NOT_ON_MAIN"):
        sys.exit(1)
    elif sub == "archive":
        if env.get("FAKE_REAL_ARCHIVE"):
            os.execv(env["REAL_GIT"], [env["REAL_GIT"], "-C", env["FAKE_ROOT"], "archive", "HEAD", *rest[2:]])
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
            for name in rest[2:]:
                data = open(os.path.join(env["FAKE_ROOT"], name), "rb").read()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    sys.exit(0)

# docker
if args[:2] == ["network", "inspect"]:
    sys.exit(17 if env.get("FAKE_MISSING_STAGING_NETWORK") else 0)
if args[0] == "compose":
    if env.get("FAKE_FAIL_INBOX_LOOKUP") and args[-4:] == ["ps", "--all", "--quiet", "inbox-worker"]:
        sys.exit(17)
    if any(arg.endswith("/docker-compose.site.yml") for arg in args) and not env.get("VERWALINO_SITE_IMAGE"):
        out("set VERWALINO_SITE_IMAGE to ghcr.io/itskeks/verwalino-site:<sha>@sha256:<digest>\n")
        sys.exit(17)
    if "migrate" in args and env.get("FAKE_PAUSE_MIGRATE"):
        state = env["FAKE_STATE"]
        open(state + "/migrating", "w").close()
        deadline = time.monotonic() + 10
        while not os.path.exists(state + "/resume"):
            if time.monotonic() >= deadline:
                sys.exit(18)
            time.sleep(0.01)
        # Exceed pipe buffers so a disconnected reader is observed before the deploy ends.
        out("fake migration output\n" * 16384)
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
if args[0] == "run" and "validate" in args:
    sys.exit(17 if "validate" in fail else 0)
if args[0] == "image":
    if "verwalino-site:" in args[-1]:
        if not env.get("FAKE_NO_DIGEST"):
            out(env.get("FAKE_SITE_DIGEST", "ghcr.io/itskeks/verwalino-site@sha256:" + "d" * 64) + "\n")
        sys.exit(0)
    if not env.get("FAKE_NO_DIGEST"):
        out("other/repo@sha256:" + "c" * 64 + "\nghcr.io/itskeks/verwalino@sha256:" + "a" * 64 + "\n")
    sys.exit(0)
if args[0] == "ps":
    if "label=com.docker.compose.service=caddy" in args and env.get("FAKE_SITE_ENABLED"):
        out("caddycontainer\n")
    elif "label=com.docker.compose.service=site" in args and env.get("FAKE_SITE_ENABLED"):
        out("sitecontainer\n")
    if "label=com.docker.compose.service=db" in args and not env.get("FAKE_NO_DB"):
        out("dbcontainer\n")
    elif "label=com.docker.compose.service=app" in args:
        out("appcontainer\n")
    sys.exit(0)
if args[0] == "volume":
    out("verwalino_documents\n" + ("other_documents\n" if env.get("FAKE_TWO_VOLUMES") else ""))
    sys.exit(0)
if args[0] == "inspect":
    if args[-1] == "caddycontainer":
        out("enabled\n" if env.get("FAKE_SITE_ENABLED") == "1" else "\n")
        sys.exit(0)
    if args[-1] == "sitecontainer":
        out(env.get("FAKE_PREVIOUS_SITE", "ghcr.io/itskeks/verwalino-site:" + "2" * 40 + "@sha256:" + "c" * 64) + "\n")
        sys.exit(0)
    out("postgres:16-alpine@sha256:" + "d" * 64 + "\n")
    sys.exit(0)
if args[0] == "exec" and "pg_dump" in args:
    if "pg_dump" in fail:
        sys.exit(17)
    sys.stdout.buffer.write(b"PGDMP fake custom dump")
    sys.exit(0)
if args[0] == "exec" and "wget" in args:
    if env.get("FAKE_HEALTH") == "never" or counter("site-health") <= int(env.get("FAKE_HEALTH_FAILS", "0")):
        sys.exit(1)
    out(env.get("FAKE_SITE_STATUS", "  HTTP/1.1 200 OK\n"))
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
    """A fake server root and tools on PATH, with a controllable UTC clock."""

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
        (self.base / "state/HEAD").write_text(OLD_COMMIT)
        binary = self.base / "bin"
        binary.mkdir()
        for tool in ("docker", "git", "curl", "sleep", "date"):
            (binary / tool).write_text(FAKE_TOOL)
            (binary / tool).chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("VERWALINO_", "FAKE_", "COMPOSE_", "SSH_"))}
        self.env.update(PATH=f"{binary}:{os.environ['PATH']}", VERWALINO_SERVER_ROOT=str(self.srv),
                        VERWALINO_CONFIG=str(self.config), FAKE_LOG=str(self.log),
                        FAKE_STATE=str(self.base / "state"), FAKE_ROOT=str(ROOT),
                        FAKE_DATE_EPOCH="1793553343")  # 2026-11-01T17:15:43Z; override per call to advance

    def install_config(self, image=OLD_DIGEST):
        """Create the installer config the way a maintainer does."""
        result = subprocess.run(["bash", str(INSTALLER), "init", "--https", "verwaltung.example.org", "--port", TEST_PORT, "--image", image],
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
        # Execute only argument validation, never the bootstrap or any host commands.
        validation, boundary, _ = BOOTSTRAP.read_text().partition("\ncommand -v apt-get")
        self.assertTrue(boundary, "bootstrap validation boundary must be explicit")
        return subprocess.run(["bash", "-c", validation + "\nexit 99\n", BOOTSTRAP.name, *args],
                              capture_output=True, text=True, timeout=15)

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
        self.assertFalse((self.srv / "backups/.lock").exists())

    def test_backup_directories_and_files_are_owner_only(self):
        result = self.run_script(BACKUP)
        self.assertEqual(result.returncode, 0, result.stderr)
        [target] = (self.srv / "backups").iterdir()
        for path in (self.srv / "backups", target, *target.iterdir()):
            with self.subTest(path=path.name):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

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

    def test_same_second_backups_survive_retention_with_and_without_labels(self):
        original = self.config.read_bytes()
        for label in ("", "pre-v1.2.3"):
            with self.subTest(label=label):
                backups = self.srv / "backups"
                shutil.rmtree(backups)
                backups.mkdir()
                for number in range(12):
                    self.config.write_bytes(original + f"# backup {number}\n".encode())
                    result = self.run_script(BACKUP, label)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    suffix = f".{number:06d}" if number else ""
                    name = f"20261101T171543Z{suffix}" + (f"-{label}" if label else "")
                    self.assertIn(name, self.backup_dirs(), "the newest backup must survive pruning")
                    self.assertEqual((backups / name / "config.env").read_bytes(), self.config.read_bytes())
                expected = ([f"20261101T171543Z.{number:06d}-{label}" for number in range(2, 12)]
                            if label else ["20261101T171543Z.000011"])
                self.assertEqual(self.backup_dirs(), expected)

    def test_backup_collision_preserves_existing_directories_files_and_symlinks(self):
        backups = self.srv / "backups"
        base = backups / "20261101T171543Z-pre-v1.2.3"
        base.mkdir()
        (base / "keep").write_bytes(b"existing backup")
        link = backups / "20261101T171543Z.000001-pre-v1.2.3"
        link.symlink_to(backups / "missing")
        file = backups / "20261101T171543Z.000002-pre-v1.2.3"
        file.write_bytes(b"existing file")
        result = self.run_script(BACKUP, "pre-v1.2.3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((base / "keep").read_bytes(), b"existing backup")
        self.assertTrue(link.is_symlink())
        self.assertEqual(file.read_bytes(), b"existing file")
        self.assertTrue((backups / "20261101T171543Z.000003-pre-v1.2.3/db.dump").is_file())

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


class DeployTests(ServerCase):
    TAG = "v1.2.3"

    def setUp(self):
        super().setUp()
        self.install_config()
        with self.config.open("a") as config:
            config.write("HV_SMTP_HOST=smtp.example.org\nHV_SMTP_FROM=mail@example.org\n")
        self.before = self.config.read_bytes()

    def deploy(self, *args, **env):
        return self.run_script(DEPLOY, *(args or (self.TAG,)), **env)

    def log_files(self):
        return sorted((self.srv / "logs").glob("deploy-*.log"))

    def assert_untouched(self):
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(list((self.srv / "backups").iterdir()), [])
        self.assertEqual((self.base / "state/HEAD").read_text(), OLD_COMMIT)

    def hosted_app_config(self, mode="selfhost"):
        path = self.srv / "config/.env.hosted-app"
        path.write_text((f"HV_DEPLOYMENT_MODE={mode}\n" if mode is not None else "") +
                        "HV_PUBLIC_HOST=verwaltung.example.org\n"
                        "HV_ALLOWED_HOSTS=verwaltung.example.org,localhost,127.0.0.1\n")
        path.chmod(0o600)
        return path

    def test_release_without_app_opt_in_keeps_every_compose_vector(self):
        result = self.deploy(HV_DEPLOYMENT_MODE="hosted", HV_PUBLIC_HOST="attacker.example")
        self.assertEqual(result.returncode, 0, result.stdout)
        prefix = ["compose", "--project-name", "verwalino", "--env-file", str(self.config),
                  "-f", str(ROOT / "infra/docker-compose.yml"), "--profile", "ops", "--profile", "https"]
        vectors = [c["args"] for c in self.calls("docker") if c["args"][0] == "compose"]
        self.assertEqual(vectors, [prefix + tail for tail in [
            ["ps", "--status", "running", "--quiet", "app"], ["pull", "app"],
            ["up", "-d", "--wait", "--wait-timeout", "120", "db"],
            ["ps", "--all", "--quiet", "inbox-worker"], ["stop", "app"],
            ["run", "--rm", "--no-deps", "-T", "migrate"],
            ["exec", "-T", "db", "psql", "--no-psqlrc", "-U", "app_migrator", "-d", "hausverwaltung",
             "--set", "ON_ERROR_STOP=1", "-Atc", "SELECT count(*) FROM public.organization"],
            ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "app"],
            ["up", "-d", "--no-deps", "caddy"],
        ]])

    def test_app_opt_in_applies_private_settings_to_every_app_start(self):
        for mode in ("selfhost", "hosted", None):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                path = self.hosted_app_config(mode)
                before = path.read_bytes()
                result = self.deploy(HV_DEPLOYMENT_MODE="attacker", HV_PUBLIC_HOST="attacker.example",
                                     HV_ALLOWED_HOSTS="*", VERWALINO_IMAGE="attacker:latest")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = [c for c in self.calls("docker") if c["args"][0] == "compose"]
                checks = [c for c in calls if c["args"][-2:] == ["config", "-q"]]
                self.assertEqual(len(checks), 1)
                self.assertEqual(checks[0]["image"], OLD_DIGEST if mode == "selfhost" else NEW_DIGEST)
                self.assertLess(self.first(lambda a: a[-2:] == ["config", "-q"], "docker"),
                                self.first(lambda a: a[0] == "pull", "docker"))
                starts = [c for c in calls if "up" in c["args"] and c["args"][-1] == "app"]
                self.assertEqual(len(starts), 1)
                for call in starts:
                    self.assertIn(str(ROOT / "infra/docker-compose.hosted-app.yml"), call["args"])
                    self.assertEqual(call["mode"], mode or "selfhost")
                    self.assertEqual(call["image"], NEW_DIGEST)
                    self.assertEqual(call["public_host"], "verwaltung.example.org")
                    self.assertEqual(call["allowed_hosts"], "verwaltung.example.org,localhost,127.0.0.1")
                migration = next(c for c in calls if "migrate" in c["args"])
                self.assertIn(str(ROOT / "infra/docker-compose.hosted-app.yml"), migration["args"])
                self.assertLess(calls.index(migration), calls.index(starts[0]))
                self.assertEqual(path.read_bytes(), before)
                self.assertNotIn("HV_DEPLOYMENT_MODE", self.config.read_text())

    def test_same_tag_redeploy_in_the_same_second_preserves_each_backup(self):
        self.hosted_app_config("hosted")
        backups = self.srv / "backups"
        snapshots = {}
        # The overridden clock stays fixed across every date call and deploy.
        for suffix in ("", ".000001", ".000002"):
            result = self.deploy(FAKE_DATE_EPOCH="1793553344")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            name = f"20261101T171544Z{suffix}-pre-{self.TAG}"
            self.assertTrue((backups / name).is_dir())
            snapshots[name] = {p.name: p.read_bytes() for p in (backups / name).iterdir()}
            self.assertEqual(sorted(p.name for p in backups.iterdir()), sorted(snapshots))
            for previous, files in snapshots.items():
                self.assertEqual({p.name: p.read_bytes() for p in (backups / previous).iterdir()}, files)
        self.assertEqual(snapshots[f"20261101T171544Z-pre-{self.TAG}"]["config.env"], self.before)

    def test_app_start_failure_releases_config_lock_without_falling_back(self):
        self.hosted_app_config("hosted")
        result = self.deploy(FAKE_FAIL_COMPOSE="migrate")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.config.parent / (self.config.name + ".lock")).exists())
        self.assertFalse(any("up" in c["args"] and c["args"][-1] == "app" for c in self.calls("docker")))

    def test_app_opt_in_trims_private_smtp_settings_like_the_installer(self):
        self.hosted_app_config("hosted")
        self.config.write_text(self.config.read_text().replace("=smtp.example.org", "=  smtp.example.org  ")
                               .replace("=mail@example.org", "=  mail@example.org  "))
        result = self.deploy(HV_SMTP_HOST="attacker.example.org", HV_SMTP_FROM="attacker@example.org")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        start = next(c for c in self.calls("docker") if "up" in c["args"] and c["args"][-1] == "app")
        self.assertEqual(start["smtp_host"], "smtp.example.org")
        self.assertEqual(start["smtp_from"], "mail@example.org")

    def test_hosted_mode_requires_a_private_smtp_host_before_any_side_effect(self):
        self.hosted_app_config("hosted")
        (self.srv / "logs").chmod(0o755)
        configured = self.before.decode()
        for host in (None, "", " \t "):
            with self.subTest(host=host):
                self.config.write_text("\n".join(
                    line for line in configured.splitlines() if not line.startswith("HV_SMTP_")
                ) + "\n" + (f"HV_SMTP_HOST={host}\n" if host is not None else ""))
                self.before = self.config.read_bytes()
                result = self.deploy(HV_SMTP_HOST="caller.example.org")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("hosted mode requires HV_SMTP_HOST in the private config", result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.log_files(), [])
                self.assertEqual(stat.S_IMODE((self.srv / "logs").stat().st_mode), 0o755)
                self.assertFalse(Path(str(self.config) + ".lock").exists())
                self.assert_untouched()

    def test_app_opt_in_requires_the_loopback_healthcheck_host_before_side_effects(self):
        path = self.hosted_app_config()
        path.write_text(path.read_text().replace(",localhost,127.0.0.1", ""))
        result = self.deploy()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("127.0.0.1", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.log_files(), [])
        self.assert_untouched()

    def test_failed_inbox_discovery_stops_before_migration_and_releases_the_lock(self):
        self.hosted_app_config()
        result = self.deploy(FAKE_FAIL_INBOX_LOOKUP="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("migrate" in c["args"] for c in self.calls("docker")))
        self.assertFalse((self.config.parent / (self.config.name + ".lock")).exists())

    def test_app_overlay_config_failure_restores_checkout_before_pull_backup_or_pin(self):
        self.hosted_app_config()
        result = self.deploy(FAKE_FAIL_COMPOSE="config")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("hosted app Compose", result.stdout)
        calls = [c["args"] for c in self.calls("docker") if c["args"][0] == "compose"]
        check = next(a for a in calls if a[-2:] == ["config", "-q"])
        self.assertIn(str(ROOT / "infra/docker-compose.hosted-app.yml"), check)
        self.assertFalse(any("up" in a or "stop" in a or "migrate" in a for a in calls))
        self.assertFalse(any(c["args"][0] == "pull" for c in self.calls("docker")))
        self.assertFalse((self.config.parent / (self.config.name + ".lock")).exists())
        self.assert_untouched()

    def test_app_overlay_missing_from_tag_restores_checkout_before_pull_backup_or_pin(self):
        self.hosted_app_config()
        # An isolated checkout represents an older tag without the overlay. Never
        # remove runtime files from the real worktree to simulate an old release.
        checkout = self.base / "old-release"
        (checkout / "scripts").mkdir(parents=True)
        (checkout / "infra").mkdir()
        for name in ("deploy-server.sh", "self-host.sh", "server-backup.sh"):
            shutil.copy2(ROOT / "scripts" / name, checkout / "scripts" / name)
        result = self.run_script(checkout / "scripts/deploy-server.sh", self.TAG)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("docker-compose.hosted-app.yml", result.stdout)
        checkouts = [c["args"][-1] for c in self.calls("git") if "checkout" in c["args"]]
        self.assertEqual(checkouts, [f"refs/tags/{self.TAG}", OLD_COMMIT])
        self.assertFalse(any(c["args"][0] == "pull" for c in self.calls("docker")))
        self.assertFalse(any(c["args"][0] == "compose" and
                             any(op in c["args"] for op in ("up", "stop", "run"))
                             for c in self.calls("docker")))
        self.assert_untouched()

    def test_https_public_host_must_match_the_routing_domain_before_side_effects(self):
        for mode in ("selfhost", "hosted"):
            with self.subTest(mode=mode):
                path = self.hosted_app_config(mode)
                path.write_text(path.read_text().replace("verwaltung.example.org", "other.example.net"))
                result = self.deploy()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("HV_PUBLIC_HOST must match VERWALINO_DOMAIN", result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.log_files(), [])
                self.assertEqual(stat.S_IMODE((self.srv / "logs").stat().st_mode), 0o755)
                self.assert_untouched()

    def test_app_hosts_merge_installer_defaults_and_private_entries_without_duplicates(self):
        original = self.config.read_text()
        for mode in ("https", "local"):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                config = original
                if mode == "local":
                    config = config.replace("VERWALINO_MODE=https", "VERWALINO_MODE=local")
                    config = config.replace("VERWALINO_DOMAIN=verwaltung.example.org", "VERWALINO_DOMAIN=")
                self.config.write_text(config)
                path = self.hosted_app_config()
                path.write_text(path.read_text().replace("localhost,127.0.0.1",
                                                        "127.0.0.1,extra.example.org,extra.example.org,127.0.0.1"))
                private_before = path.read_bytes()
                result = self.deploy(VERWALINO_ALLOWED_HOSTS="attacker.example")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                defaults = (["verwaltung.example.org"] if mode == "https" else []) + ["localhost", "127.0.0.1"]
                expected = list(dict.fromkeys(defaults + ["verwaltung.example.org", "extra.example.org"]))
                calls = [c for c in self.calls("docker") if c["args"][0] == "compose" and
                         str(ROOT / "infra/docker-compose.hosted-app.yml") in c["args"]]
                self.assertTrue(calls)
                for call in calls:
                    self.assertEqual(call["allowed_hosts"].split(","), expected)
                    self.assertEqual(call["installer_allowed_hosts"], call["allowed_hosts"])
                self.assertEqual(path.read_bytes(), private_before)

    def test_local_mode_uses_the_public_host_for_mail_without_a_routing_domain(self):
        self.config.write_text(self.config.read_text().replace("VERWALINO_MODE=https", "VERWALINO_MODE=local")
                               .replace("VERWALINO_DOMAIN=verwaltung.example.org", "VERWALINO_DOMAIN="))
        for mode in ("selfhost", "hosted"):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                path = self.hosted_app_config(mode)
                path.write_text(path.read_text().replace("verwaltung.example.org", "mail.example.net"))
                result = self.deploy()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                start = next(c for c in self.calls("docker") if "up" in c["args"] and c["args"][-1] == "app")
                self.assertEqual(start["public_host"], "mail.example.net")
                self.assertEqual(start["allowed_hosts"], "localhost,127.0.0.1,mail.example.net")
                self.assertEqual(start["installer_allowed_hosts"], start["allowed_hosts"])
                self.assertNotIn("https", start["args"])

    def test_invalid_allowed_host_entries_refuse_before_any_side_effect(self):
        path = self.hosted_app_config()
        valid = path.read_text()
        for host in (".", "-", "-.", "..", "-app.example.org", "app-.example.org", "app..example.org",
                     "app.example.org.", "a" * 64 + ".example.org", "123", "999.0.0.1"):
            with self.subTest(host=host):
                path.write_text(valid.replace(",localhost,", f",{host},localhost,"))
                result = self.deploy()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("HV_ALLOWED_HOSTS", result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.log_files(), [])
                self.assertEqual(stat.S_IMODE((self.srv / "logs").stat().st_mode), 0o755)
                self.assert_untouched()

    def test_allowed_hosts_accept_valid_dns_labels_ipv4_and_localhost(self):
        path = self.hosted_app_config()
        hosts = "verwaltung.example.org,localhost,127.0.0.1,192.0.2.10,app-2.example.org," + "a" * 63 + ".example.org"
        path.write_text("HV_PUBLIC_HOST=verwaltung.example.org\n" + f"HV_ALLOWED_HOSTS={hosts}\n")
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        start = next(c for c in self.calls("docker") if "up" in c["args"] and c["args"][-1] == "app")
        self.assertEqual(start["allowed_hosts"], hosts)

    def test_app_opt_in_keeps_proxy_and_site_in_the_start_chain(self):
        self.hosted_app_config("hosted")
        (self.srv / "config/Caddyfile.hosted").write_text((ROOT / "infra/Caddyfile.hosted").read_text())
        result = self.deploy(FAKE_SITE_ENABLED="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        starts = [c for c in self.calls("docker") if c["args"][0] == "compose" and "up" in c["args"]]
        for call in starts:
            for name in ("hosted-app", "hosted-proxy", "site"):
                self.assertIn(str(ROOT / f"infra/docker-compose.{name}.yml"), call["args"])
        self.assertEqual(len([c for c in starts if c["args"][-1] == "caddy"]), 1)

    def test_invalid_app_settings_refuse_before_any_side_effect(self):
        valid = self.hosted_app_config().read_text()
        cases = [
            (valid.replace("selfhost", mode), "HV_DEPLOYMENT_MODE")
            for mode in ("", "HOSTED", "production", " hosted ", "$(touch marker)")
        ] + [
            (valid.replace("HV_PUBLIC_HOST=verwaltung.example.org", f"HV_PUBLIC_HOST={host}"), "HV_PUBLIC_HOST")
            for host in ("", "App.example.org", "https://app.example.org", "app.example.org:443",
                         "user@app.example.org", "-app.example.org", "app..example.org", "app.example.org.",
                         "a" * 64 + ".example.org", "127.0.0.1", "localhost", "$(touch marker)")
        ] + [
            (valid.replace("HV_ALLOWED_HOSTS=verwaltung.example.org,localhost,127.0.0.1", hosts), "HV_ALLOWED_HOSTS")
            for hosts in ("", "HV_ALLOWED_HOSTS=", "HV_ALLOWED_HOSTS=other.example.org",
                          "HV_ALLOWED_HOSTS=evilverwaltung.example.org", "HV_ALLOWED_HOSTS=*",
                          "HV_ALLOWED_HOSTS=,verwaltung.example.org,127.0.0.1",
                          "HV_ALLOWED_HOSTS=verwaltung.example.org,127.0.0.1,",
                          "HV_ALLOWED_HOSTS=verwaltung.example.org,,127.0.0.1")
        ] + [(valid + "HV_DEPLOYMENT_MODE=hosted\n", "Duplicate"),
             (valid + "UNEXPECTED=value\n", "Unknown"), (valid + "invalid\n", "Invalid")]
        for contents, message in cases:
            with self.subTest(contents=contents):
                self.hosted_app_config().write_text(contents)
                result = self.deploy()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.log_files(), [])
                self.assertEqual(stat.S_IMODE((self.srv / "logs").stat().st_mode), 0o755)
                self.assertFalse((self.base / "marker").exists())
                self.assert_untouched()

    def test_app_config_must_be_private_and_not_a_symlink(self):
        path = self.hosted_app_config()
        path.chmod(0o644)
        result = self.deploy()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("0600", result.stderr)
        path.rename(path.with_suffix(".saved"))
        path.symlink_to(path.with_suffix(".saved"))
        result = self.deploy()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("regular file", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_exactly_one_strict_release_tag_is_accepted(self):
        for args in [(), ("",), (self.TAG, "extra"), ("v1; id",), ("v1.2.3;id",), ("v1.2.3 v1.2.4",), ("v1.2",),
                     ("latest",), ("1.2.3",), ("v1.2.3-",), ("v1.2.3\nfoo",), ("../v1.2.3",), ("-v1.2.3",),
                     ("v1.2.3-rc 1",), ("v1.2.3$(id)",), ("v1.2.3`id`",), ("V1.2.3",),
                     ("v1.2.3-rc.1",), ("v01.2.3",), ("v1.02.3",), ("v1.2.03",), ("v1.2.3-a..b",)]:
            with self.subTest(args=args):
                # The tag comes from the arguments only, never from SSH_ORIGINAL_COMMAND.
                result = self.run_script(DEPLOY, *args, SSH_ORIGINAL_COMMAND=self.TAG)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage: deploy-server.sh vX.Y.Z", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.log_files(), [])
        self.assert_untouched()

    def test_stable_tags_without_leading_zeroes_run(self):
        for tag in (self.TAG, "v10.20.30", "v0.0.0"):
            with self.subTest(tag=tag):
                result = self.deploy(tag)
                self.assertEqual(result.returncode, 0, result.stdout)
                pulls = [c["args"] for c in self.calls("docker") if c["args"][0] == "pull"]
                self.assertIn(["pull", f"ghcr.io/itskeks/verwalino:{tag}"], pulls)

    def test_app_release_restores_the_operator_enabled_caddy_extension(self):
        (self.srv / "config/Caddyfile.hosted").write_text((ROOT / "infra/Caddyfile.hosted").read_text())
        result = self.deploy(FAKE_SITE_ENABLED="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        compose = [c["args"] for c in self.calls("docker") if c["args"][0] == "compose"]
        restored = [a for a in compose if str(ROOT / "infra/docker-compose.site.yml") in a]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0][-4:], ["up", "-d", "--no-deps", "caddy"])
        self.assertIn(str(ROOT / "infra/docker-compose.hosted-proxy.yml"), restored[0])
        hosted = [a for a in compose if "up" in a and a[-1] == "caddy" and
                  any(str(ROOT / f"infra/docker-compose.{name}.yml") in a for name in ("hosted-proxy", "site"))]
        self.assertEqual(len(hosted), 1)
        restored_call = next(c for c in self.calls("docker") if c["args"] == restored[0])
        self.assertEqual(restored_call["site_image"], f"{SITE_REPO}:{PREVIOUS_SITE_SHA}@sha256:" + "c" * 64)
        self.assertLess(compose.index(next(a for a in compose if "migrate" in a)), compose.index(restored[0]))

    def test_enabled_extension_without_a_site_image_refuses_app_release_before_changes(self):
        (self.srv / "config/Caddyfile.hosted").write_text("import /etc/caddy/Caddyfile.site\n")
        for enabled in ("1", "wrong-network", ""):
            with self.subTest(enabled=enabled):
                result = self.deploy(FAKE_SITE_ENABLED=enabled, FAKE_PREVIOUS_SITE="")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("cannot recover the existing site image", result.stdout)
        self.assertFalse(any(c["args"][0] in ("pull", "compose") for c in self.calls("docker")))
        self.assertFalse(any("checkout" in c["args"] for c in self.calls("git")))
        self.assert_untouched()

    def disconnected_deploy(self, *, hangup=False, **env):
        state = self.base / "state"
        process = subprocess.Popen(["bash", str(DEPLOY), self.TAG],
                                   env=dict(self.env, FAKE_PAUSE_MIGRATE="1", **env), cwd=self.base,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not (state / "migrating").exists():
                self.assertIsNone(process.poll(), "deploy exited before reaching migration")
                self.assertLess(time.monotonic(), deadline, "deploy did not reach migration")
                time.sleep(0.01)
            process.stdout.close()
            process.stdout = None
            if hangup:
                os.killpg(process.pid, signal.SIGHUP)
            (state / "resume").touch()
            _, stderr = process.communicate(timeout=15)
            return process.returncode, stderr.decode()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()

    def test_closed_stdout_during_migration_keeps_the_final_status_and_releases_the_lock(self):
        rc, stderr = self.disconnected_deploy()
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())
        self.assertEqual(rc, 0, stderr)
        [log] = self.log_files()
        self.assertTrue(log.read_text().endswith(f"deploy {self.TAG} OK: {NEW_DIGEST}\n"))

    def test_hangup_during_failed_migration_keeps_the_final_status_and_releases_the_lock(self):
        rc, stderr = self.disconnected_deploy(hangup=True, FAKE_FAIL_COMPOSE="migrate")
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())
        self.assertEqual(rc, 17, stderr)
        [log] = self.log_files()
        self.assertRegex(log.read_text().splitlines()[-1], r'^deploy: FAILED at step "install .*\(exit 17\)\. Log: ')

    def test_failed_deploy_logs_are_private(self):
        result = self.deploy(FAKE_HEALTH="never")
        self.assertEqual(result.returncode, 1)
        [log] = self.log_files()
        for path, mode in ((log.parent, 0o750), (log, 0o640)):
            with self.subTest(path=path.name):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_every_git_call_trusts_the_checkout_explicitly(self):
        self.assertEqual(self.deploy().returncode, 0)
        calls = self.calls("git")
        self.assertGreaterEqual(len(calls), 6)
        for call in calls:
            self.assertEqual(call["args"][:4], ["-c", f"safe.directory={ROOT}", "-C", str(ROOT)], call)

    def test_release_is_fetched_pulled_backed_up_pinned_migrated_and_checked_in_order(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        git = [c["args"][4:] for c in self.calls("git")]
        self.assertIn(["fetch", "--tags", "origin"], git)
        self.assertIn(["checkout", "--quiet", "--detach", f"refs/tags/{self.TAG}"], git)
        self.assertEqual((self.base / "state/HEAD").read_text(), NEW_COMMIT)
        self.assertLess(git.index(["fetch", "--tags", "origin"]), git.index(["checkout", "--quiet", "--detach", f"refs/tags/{self.TAG}"]))

        sequence = [(c["tool"], c["args"]) for c in self.calls() if c["tool"] in ("git", "docker", "curl")]
        def at(tool, *needles):
            for index, (name, args) in enumerate(sequence):
                if name == tool and all(n in args for n in needles):
                    return index
            self.fail(f"missing {tool} {needles}")
        checkout = at("git", "checkout")
        pull = at("docker", "pull", f"ghcr.io/itskeks/verwalino:{self.TAG}")
        dump = at("docker", "pg_dump")
        documents = at("docker", "run", "tar")
        installer_pull = at("docker", "compose", "pull", "app")
        stop_app = at("docker", "compose", "stop", "app")
        migrate = at("docker", "compose", "run", "migrate")
        up_app = at("docker", "compose", "up", "app")
        health = at("curl")
        self.assertLess(checkout, pull)
        self.assertLess(pull, dump)
        self.assertLess(dump, documents)
        self.assertLess(documents, installer_pull)
        self.assertLess(installer_pull, stop_app)
        self.assertLess(stop_app, migrate)
        self.assertLess(migrate, up_app)
        self.assertLess(up_app, health)
        self.assertEqual(self.calls("curl")[0]["args"][-1], f"http://127.0.0.1:{TEST_PORT}/api/health/ready")

        # The new digest is pinned after the backup copied the old config; nothing else changed.
        values = self.config_values()
        self.assertEqual(values["VERWALINO_IMAGE"], NEW_DIGEST)
        self.assertEqual({k: v for k, v in values.items() if k != "VERWALINO_IMAGE"},
                         {k: v for k, v in (line.split("=", 1) for line in self.before.decode().splitlines()
                                            if line and not line.startswith("#")) if k != "VERWALINO_IMAGE"})
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        [backup] = (self.srv / "backups").iterdir()
        self.assertTrue(backup.name.endswith(f"-pre-{self.TAG}"))
        self.assertEqual((backup / "config.env").read_bytes(), self.before)
        for call in self.calls("docker"):
            if call["args"][0] == "compose":
                self.assertEqual(call["args"][1:3], ["--project-name", "verwalino"])

        [log] = self.log_files()
        self.assertIn(f"deploy {self.TAG} OK: {NEW_DIGEST}", log.read_text())
        self.assertEqual(log.read_text(), result.stdout)
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())
        self.assertFalse((self.srv / "config/.env.selfhost.lock").exists())

    def test_missing_tag_or_tag_outside_main_or_dirty_checkout_stops_before_any_change(self):
        for env, message in [({"FAKE_MISSING_TAG": "1"}, "does not exist"), ({"FAKE_NOT_ON_MAIN": "1"}, "not on main"),
                             ({"FAKE_DIRTY": " M scripts/self-host.sh\n"}, "local changes")]:
            with self.subTest(env=env):
                result = self.deploy(**env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stdout)
                self.assertFalse(any(c["args"][:1] in (["pull"], ["compose"]) for c in self.calls("docker")))
                self.assertFalse(any("checkout" in c["args"] for c in self.calls("git")))
                self.assert_untouched()
                self.assertFalse((self.srv / "logs/.deploy.lock").exists())

    def test_failed_pull_or_missing_digest_stops_before_the_backup(self):
        for env in [{"FAKE_FAIL": "pull"}, {"FAKE_NO_DIGEST": "1"}]:
            with self.subTest(env=env):
                result = self.deploy(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("pg_dump" in c["args"] for c in self.calls("docker")))
                self.assert_untouched()

    def test_failed_backup_stops_before_config_change_and_installer(self):
        result = self.deploy(FAKE_FAIL="pg_dump")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FAILED at step "backup"', result.stdout)
        self.assertFalse(any(c["args"][0] == "compose" for c in self.calls("docker")))
        self.assert_untouched()

    def test_failed_migration_says_there_is_no_rollback_and_skips_the_health_check(self):
        result = self.deploy(FAKE_FAIL_COMPOSE="migrate")
        self.assertEqual(result.returncode, 17)
        self.assertIn('FAILED at step "install (pull, migrate, restart)"', result.stdout)
        self.assertIn("no automatic rollback", result.stdout)
        self.assertIn(f"image {OLD_DIGEST}", result.stdout)
        self.assertIn("#76", result.stdout)
        self.assertEqual(self.calls("curl"), [])
        self.assertEqual((self.base / "state/HEAD").read_text(), NEW_COMMIT)
        self.assertFalse(any(c["args"][:2] == ["compose", "--project-name"] and "up" in c["args"] and c["args"][-1] == "app"
                             for c in self.calls("docker")))
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())
        [log] = self.log_files()
        self.assertIn("no automatic rollback", log.read_text())

    def test_health_check_polls_every_three_seconds_until_ready(self):
        result = self.deploy(FAKE_HEALTH_FAILS="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("curl")), 3)
        self.assertEqual([c["args"] for c in self.calls("sleep")], [["3"], ["3"]])
        self.assertIn("app ready after 3 check(s)", result.stdout)

    def test_unready_app_prints_100_log_lines_and_fails(self):
        result = self.deploy(FAKE_HEALTH="never")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls("curl")), 24)
        self.assertEqual(len(self.calls("sleep")), 23, "at most about 120 s in total")
        self.assertIn(["logs", "--tail", "100", "appcontainer"], [c["args"] for c in self.calls("docker")])
        self.assertIn("FAKE-APP-LOG line 3", result.stdout)
        self.assertIn('FAILED at step "health check"', result.stdout)
        self.assertIn("no automatic rollback", result.stdout)
        self.assertIn("FAKE-APP-LOG line 3", self.log_files()[0].read_text())

    def test_one_time_setup_token_never_reaches_the_output_or_the_log(self):
        result = self.deploy(FAKE_ORGANIZATION_COUNT="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Setup token: [hidden by deploy-server.sh]", result.stdout)
        for text in (result.stdout, result.stderr, self.log_files()[0].read_text()):
            self.assertIsNone(re.search(r"Setup token: [a-f0-9]{64}", text))

    def test_enabled_hosted_proxy_is_validated_and_reapplied_after_the_installer(self):
        (self.srv / "config/Caddyfile.hosted").write_text("import /etc/caddy/Caddyfile.production\nimport /etc/caddy/Caddyfile.staging\n")
        # The site exists, but Caddy has not yet joined its network on first enablement.
        result = self.deploy(FAKE_SITE_ENABLED="wrong-network")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [call["args"] for call in self.calls("docker")]
        self.assertEqual(calls[0], ["network", "inspect", "verwalino-staging_proxy"])
        validation = next(i for i, args in enumerate(calls) if "validate" in args)
        self.assertLess(validation, next(i for i, args in enumerate(calls) if args[0] == "pull"))
        sequence = self.calls()
        checkout = next(i for i, call in enumerate(sequence) if call["tool"] == "git" and "checkout" in call["args"])
        validate = next(i for i, call in enumerate(sequence) if call["tool"] == "docker" and "validate" in call["args"])
        self.assertLess(checkout, validate)
        self.assertEqual(calls[validation][:12], [
            "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges", "--env",
        ])
        migration = next(i for i, args in enumerate(calls) if "migrate" in args)
        self.assertLess(validation, migration)
        overlay = next(i for i, args in enumerate(calls) if str(ROOT / "infra/docker-compose.hosted-proxy.yml") in args)
        self.assertLess(validation, overlay)
        self.assertIn("none", calls[validation])
        self.assertIn("--read-only", calls[validation])
        for name in ("production", "staging", "site"):
            self.assertTrue(any(f"dst=/etc/caddy/Caddyfile.{name},readonly" in arg for arg in calls[validation]))
        self.assertIn(str(ROOT / "infra/docker-compose.site.yml"), calls[overlay])
        self.assertIn("--no-deps", calls[overlay])
        self.assertEqual(calls[overlay][-1], "caddy")
        restored_call = next(call for call in self.calls("docker") if call["args"] == calls[overlay])
        self.assertEqual(restored_call["site_image"], f"{SITE_REPO}:{PREVIOUS_SITE_SHA}@sha256:" + "c" * 64)
        self.assertTrue(any("app" == args[-1] and "up" in args for args in calls[:overlay]))

    def test_missing_staging_proxy_network_refuses_hosted_release_before_side_effects(self):
        (self.srv / "config/Caddyfile.hosted").write_text("import /etc/caddy/Caddyfile.production\n")
        result = self.deploy(FAKE_MISSING_STAGING_NETWORK="1", FAKE_SITE_ENABLED="1")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("verwalino-staging_proxy", result.stdout)
        self.assertIn("docker network create --subnet", result.stdout)
        self.assertEqual([call["args"] for call in self.calls("docker")],
                         [["network", "inspect", "verwalino-staging_proxy"]])
        self.assertEqual(self.calls("git"), [])
        self.assertEqual(list((self.srv / "backups").iterdir()), [])
        self.assert_untouched()
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())

    def test_release_without_hosted_wrapper_does_not_require_the_staging_network(self):
        result = self.deploy(FAKE_MISSING_STAGING_NETWORK="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(call["args"][:1] == ["network"] for call in self.calls("docker")))

    def test_running_deploy_or_unusable_config_is_refused_without_touching_anything(self):
        (self.srv / "logs/.deploy.lock").mkdir()
        result = self.deploy()
        self.assertEqual(result.returncode, 1)
        self.assertIn("another deploy is running", result.stdout)
        self.assertTrue((self.srv / "logs/.deploy.lock").exists(), "a foreign lock must stay")
        (self.srv / "logs/.deploy.lock").rmdir()

        target = self.base / "elsewhere"
        target.write_bytes(self.before)
        self.config.unlink()
        self.config.symlink_to(target)
        result = self.deploy()
        self.assertEqual(result.returncode, 1)
        self.assertIn("must be a regular file", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_invalid_hosted_caddyfile_aborts_before_backup_pinning_or_installer(self):
        (self.srv / "config/Caddyfile.hosted").write_text("invalid config\n")
        result = self.deploy(FAKE_FAIL="validate", FAKE_SITE_ENABLED="1")
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assert_untouched()
        self.assertFalse(any(call["args"][0] == "compose" for call in self.calls("docker")))
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())


class StagingDeployTests(ServerCase):
    SHA = "0123456789abcdef" * 2 + "01234567"

    def setUp(self):
        super().setUp()
        self.install_config()
        self.production = self.config.read_bytes()
        self.staging = self.srv / "config/.env.staging"
        self.staging.write_text(f"VERWALINO_IMAGE={OLD_DIGEST}\nHV_RELEASE_ID=old-staging-sha\n" + "".join(
            f"{key}={'e' * 64}\n" for key in
            ("SESSION_SECRET", "MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD")))
        self.staging.chmod(0o600)
        self.env["FAKE_REVISION"] = self.SHA

    def deploy(self, ref=None, **env):
        return self.run_script(DEPLOY, "staging", ref or self.SHA, **env)

    def test_staging_ignores_invalid_production_app_settings(self):
        app_config = self.srv / "config/.env.hosted-app"
        app_config.write_text("HV_DEPLOYMENT_MODE=invalid\nUNKNOWN=value\n")
        app_config.chmod(0o644)
        before = app_config.read_bytes()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(app_config.read_bytes(), before)
        self.assertEqual(self.config.read_bytes(), self.production)
        compose = [c["args"] for c in self.calls("docker") if c["args"][0] == "compose"]
        self.assertTrue(compose)
        for args in compose:
            self.assertEqual(args[1:5], ["--project-name", "verwalino-staging", "--env-file", str(self.staging)])
            self.assertNotIn(str(ROOT / "infra/docker-compose.hosted-app.yml"), args)
        self.assertIn(f"staging {self.SHA} OK", result.stdout)

    def test_real_staging_export_is_readable_and_traversable_by_postgres_under_umask_027(self):
        result = self.deploy(FAKE_REAL_ARCHIVE="1", REAL_GIT=shutil.which("git"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        exported = self.srv / "staging/infra/db/prod-init.sh"
        self.assertEqual(stat.S_IMODE(exported.stat().st_mode), 0o755)
        self.assertTrue(exported.parent.stat().st_mode & stat.S_IXOTH)

    def test_staging_pins_main_image_and_runs_only_its_project_without_checkout_or_backup(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("previous staging sha: old-staging-sha", result.stdout)
        self.assertIn(["pull", f"ghcr.io/itskeks/verwalino:sha-{self.SHA}"],
                      [call["args"] for call in self.calls("docker")])
        self.assertEqual(self.config.read_bytes(), self.production)
        self.assertEqual((self.base / "state/HEAD").read_text(), OLD_COMMIT)
        self.assertFalse(any("checkout" in call["args"] for call in self.calls("git")))
        self.assertEqual(list((self.srv / "backups").iterdir()), [])
        self.assertIn(f"VERWALINO_IMAGE={NEW_DIGEST}", self.staging.read_text())
        self.assertIn(f"HV_RELEASE_ID={self.SHA}", self.staging.read_text())
        self.assertEqual(stat.S_IMODE(self.staging.stat().st_mode), 0o600)
        calls = [call["args"] for call in self.calls("docker") if call["args"][0] == "compose"]
        for args in calls:
            self.assertEqual(args[1:5], ["--project-name", "verwalino-staging", "--env-file", str(self.staging)])
            self.assertIn(str(self.srv / "staging/infra/docker-compose.staging.yml"), args)
        for call in self.calls("docker"):
            if call["args"][0] == "compose":
                self.assertEqual(call["image"], NEW_DIGEST)
                self.assertEqual(call["release_id"], self.SHA)
        def at(verb):
            return next(i for i, args in enumerate(calls) if verb in args)
        self.assertLess(at("stop"), at("migrate"))
        self.assertEqual(calls[at("stop")][-1], "staging-app")
        self.assertLess(at("migrate"), next(i for i, args in enumerate(calls) if "--force-recreate" in args))
        self.assertLess(at("migrate"), at("exec"))

    def test_release_tag_is_resolved_to_its_main_sha_image(self):
        result = self.deploy("v1.2.3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(["pull", f"ghcr.io/itskeks/verwalino:sha-{self.SHA}"],
                      [call["args"] for call in self.calls("docker")])

    def test_staging_validates_exported_proxy_before_pulling_or_starting_anything(self):
        (self.srv / "config/Caddyfile.hosted").write_text((ROOT / "infra/Caddyfile.hosted").read_text())
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls("docker")
        validation = calls[0]["args"]
        self.assertIn("validate", validation)
        self.assertIn("NET_BIND_SERVICE", validation)
        self.assertIn("VERWALINO_DOMAIN=verwaltung.example.org", validation)
        for name in ("Caddyfile", "Caddyfile.staging", "Caddyfile.site"):
            self.assertTrue(any(f"src={self.srv}/staging/infra/{name}," in arg for arg in validation))
        self.assertEqual(self.config.read_bytes(), self.production)

    def test_invalid_staging_proxy_stops_before_pull_migration_or_config_change(self):
        (self.srv / "config/Caddyfile.hosted").write_text("invalid config\n")
        before = self.staging.read_bytes()
        result = self.deploy(FAKE_FAIL="validate")
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertEqual(len(self.calls("docker")), 1)
        self.assertIn("validate", self.calls("docker")[0]["args"])
        self.assertEqual(self.staging.read_bytes(), before)
        self.assertEqual(self.config.read_bytes(), self.production)
        self.assertEqual(list((self.srv / "backups").iterdir()), [])

    def test_copied_production_secrets_are_refused_before_any_docker_call(self):
        original = self.staging.read_text()
        production = self.config_values()
        for key in ("SESSION_SECRET", "MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD"):
            with self.subTest(key=key):
                self.staging.write_text(re.sub(rf"^{key}=.*$", f"{key}={production[key]}", original, flags=re.M))
                result = self.deploy()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(key, result.stdout)
                self.assertIn("production", result.stdout)
                self.assertNotIn(production[key], result.stdout)
                self.assertEqual(self.calls("docker"), [])
        self.staging.write_bytes(self.production)
        result = self.deploy()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.calls("docker"), [])

    def test_database_password_cannot_reuse_a_different_production_role_password(self):
        self.staging.write_text(re.sub(r"^MIGRATOR_PASSWORD=.*$",
                                      f"MIGRATOR_PASSWORD={self.config_values()['APP_RW_PASSWORD']}",
                                      self.staging.read_text(), flags=re.M))
        result = self.deploy()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.calls("docker"), [])

    def test_invalid_staging_commands_are_usage_errors_before_any_call(self):
        for args in [("staging",), ("staging", "main"), ("staging", "a" * 7),
                     ("staging", "a" * 40, "extra"), ("staging", "v1.2.3;id"),
                     ("staging", "sha-" + "a" * 40), ("staging", "../main"),
                     ("staging", "A" * 40), ("staging", "g" * 40), ("staging", "a" * 41),
                     ("staging", self.SHA + "\n"), ("staging", "v01.2.3"),
                     ("staging", "v1.2.3-rc.1"), ("staging", ""), ("STAGING", self.SHA)]:
            with self.subTest(args=args):
                self.assertEqual(self.run_script(DEPLOY, *args).returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(list((self.srv / "logs").iterdir()), [])
        self.assertEqual(self.config.read_bytes(), self.production)

    def test_foreign_commit_pull_and_digest_failure_cannot_run_migrations(self):
        for env in ({"FAKE_NOT_ON_MAIN": "1"}, {"FAKE_MISSING_TAG": "1"},
                    {"FAKE_FAIL": "pull"}, {"FAKE_NO_DIGEST": "1"}):
            with self.subTest(env=env):
                before = self.staging.read_bytes()
                result = self.deploy(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.staging.read_bytes(), before)
                self.assertFalse(any("migrate" in call["args"] for call in self.calls("docker")))
                self.assertEqual(self.config.read_bytes(), self.production)

    def test_failed_migration_does_not_start_the_app_or_report_success(self):
        result = self.deploy(FAKE_FAIL_COMPOSE="migrate")
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertNotIn("--force-recreate", str(self.calls("docker")))
        self.assertIn("rollback: staging old-staging-sha", result.stdout)
        self.assertNotIn("restore the pre-deploy backup", result.stdout)
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())


class SiteDeployTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.install_config()
        self.before = self.config.read_bytes()
        (self.srv / "config/Caddyfile.hosted").write_text("import /etc/caddy/Caddyfile.site\n")
        self.env["FAKE_SITE_ENABLED"] = "1"
        self.log.unlink(missing_ok=True)

    def deploy(self, **env):
        return self.run_script(DEPLOY, "site", SITE_SHA, **env)

    def assert_app_untouched(self):
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(list((self.srv / "backups").iterdir()), [])
        self.assertEqual(self.calls("git"), [])
        self.assertEqual(self.calls("curl"), [])
        self.assertEqual((self.base / "state/HEAD").read_text(), OLD_COMMIT)

    def test_site_pull_resolves_digest_recreates_only_site_and_checks_http_200(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls("docker")
        args = [c["args"] for c in calls]
        pull = args.index(["pull", f"{SITE_REPO}:{SITE_SHA}"])
        inspect = next(i for i, a in enumerate(args) if a[:2] == ["image", "inspect"])
        up = next(i for i, a in enumerate(args) if a[0] == "compose" and "up" in a)
        health = next(i for i, a in enumerate(args) if a[:2] == ["exec", "caddycontainer"])
        self.assertLess(pull, inspect)
        self.assertLess(inspect, up)
        self.assertLess(up, health)
        compose = args[up]
        self.assertEqual(compose[1:3], ["--project-name", "verwalino"])
        self.assertIn(str(ROOT / "infra/docker-compose.site.yml"), compose)
        self.assertIn(str(ROOT / "infra/docker-compose.yml"), compose)
        self.assertEqual(compose[-7:], ["up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "site"])
        self.assertEqual(calls[up]["site_image"], f"{SITE_REPO}:{SITE_SHA}@sha256:" + "d" * 64)
        self.assertIn("http://site:8080/", args[health])
        self.assertIn("-S", args[health])
        self.assertIn(f"previous site sha: {PREVIOUS_SITE_SHA}", result.stdout)
        self.assertIn(f"site {PREVIOUS_SITE_SHA}", result.stdout)
        self.assert_app_untouched()
        self.assertFalse((self.srv / "logs/.deploy.lock").exists())

    def test_other_command_shapes_fail_before_any_side_effect(self):
        for args in [("site",), ("site", "main"), ("site", "a" * 39), ("site", "g" * 40),
                     ("site", "A" * 40), ("site", SITE_SHA, "extra"), ("site " + SITE_SHA,),
                     ("site", SITE_SHA + ";id"), ("site", SITE_SHA + "\n"),
                     ("site", "$(id)"), ("site", "--help"), ("app", SITE_SHA),
                     ("site", "v1.2.3"), ("site", "a" * 41), ("site", ""), ("SITE", SITE_SHA)]:
            with self.subTest(args=args):
                result = self.run_script(DEPLOY, *args)
                self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(list((self.srv / "logs").iterdir()), [])
        self.assert_app_untouched()

    def test_disabled_site_is_refused_before_pull(self):
        for enabled in ("", "wrong-network"):
            with self.subTest(enabled=enabled):
                result = self.deploy(FAKE_SITE_ENABLED=enabled)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("site extension is not enabled", result.stdout)
        self.assertFalse(any(c["args"][0] in ("pull", "compose") for c in self.calls("docker")))
        self.assert_app_untouched()

    def test_wrong_repository_missing_or_malformed_digest_never_recreates(self):
        for value in ("", "other/repo@sha256:" + "d" * 64, SITE_REPO + "@sha256:bad", SITE_REPO + "@sha256:" + "G" * 64):
            with self.subTest(digest=value):
                result = self.deploy(FAKE_SITE_DIGEST=value)
                self.assertEqual(result.returncode, 1, result.stdout)
        self.assertFalse(any(c["args"][0] == "compose" for c in self.calls("docker")))
        self.assert_app_untouched()

    def test_failed_pull_and_recreate_fail_without_touching_app(self):
        for env in ({"FAKE_FAIL": "pull"}, {"FAKE_FAIL_COMPOSE": "up"}):
            with self.subTest(env=env):
                result = self.deploy(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"site {PREVIOUS_SITE_SHA}", result.stdout)
                self.assertFalse((self.srv / "logs/.deploy.lock").exists())
        self.assert_app_untouched()

    def test_health_retries_and_non_200_or_unreachable_site_fails(self):
        result = self.deploy(FAKE_HEALTH_FAILS="2")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.calls("sleep")), 2)
        for env in ({"FAKE_HEALTH": "never"}, {"FAKE_SITE_STATUS": "  HTTP/1.1 301 Moved Permanently\n"}):
            with self.subTest(env=env):
                result = self.deploy(**env)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('FAILED at step "site health check"', result.stdout)
                self.assertIn(f"site {PREVIOUS_SITE_SHA}", result.stdout)
        self.assert_app_untouched()

    def test_previous_main_tag_is_reported_as_unavailable_for_sha_rollback(self):
        result = self.deploy(FAKE_PREVIOUS_SITE=f"{SITE_REPO}:main")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("previous site sha: unknown", result.stdout)
        self.assertNotIn("rollback: site main", result.stdout)


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class HostedAppLayoutTests(unittest.TestCase):
    def test_overlay_only_forwards_the_three_private_app_settings(self):
        overlay = yaml.safe_load((ROOT / "infra/docker-compose.hosted-app.yml").read_text())
        self.assertEqual(overlay, {"services": {"app": {"environment": {
            "HV_DEPLOYMENT_MODE": "${HV_DEPLOYMENT_MODE:-selfhost}",
            "HV_PUBLIC_HOST": "${HV_PUBLIC_HOST:?set the public hostname in the private hosted app config}",
            "HV_ALLOWED_HOSTS": "${HV_ALLOWED_HOSTS:?include the public hostname in the private hosted app config}",
        }}}})
        base = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())
        for key in ("HV_DEPLOYMENT_MODE", "HV_PUBLIC_HOST"):
            self.assertNotIn(key, base["services"]["app"]["environment"])


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / ".github/workflows/deploy.yaml"
        self.text = path.read_text()
        self.doc = yaml.safe_load(self.text)
        self.job = self.doc["jobs"]["deploy"]
        self.steps = {step["name"]: step for step in self.job["steps"]}

    def test_runs_after_the_successful_release_image_workflow_or_by_hand(self):
        triggers = self.doc.get("on", self.doc.get(True))  # YAML 1.1 reads a bare "on" as true
        release = yaml.safe_load((ROOT / ".github/workflows/release-image.yaml").read_text())
        self.assertEqual(triggers["workflow_run"], {"workflows": [release["name"]], "types": ["completed"]})
        self.assertEqual(set(triggers), {"workflow_run", "workflow_dispatch"})
        tag = triggers["workflow_dispatch"]["inputs"]["tag"]
        self.assertTrue(tag["required"])
        self.assertEqual(tag["type"], "string")
        condition = " ".join(self.job["if"].split())
        for expected in ("github.event.workflow_run.conclusion == 'success'", "github.event.workflow_run.event == 'push'",
                         "github.event.workflow_run.head_repository.full_name == github.repository"):
            self.assertIn(expected, condition)

    def test_token_is_read_only_and_deploys_never_overlap(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read"})
        self.assertNotIn("permissions", self.job)
        self.assertEqual(list(self.doc["jobs"]), ["deploy"])
        self.assertTrue(self.doc["concurrency"]["group"])
        self.assertIs(self.doc["concurrency"]["cancel-in-progress"], False)

    def test_only_the_four_deploy_secrets_are_used_and_no_action_runs(self):
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", self.text))),
                         ["DEPLOY_HOST", "DEPLOY_KNOWN_HOSTS", "DEPLOY_SSH_KEY", "DEPLOY_USER"])
        self.assertNotIn("uses:", self.text, "the job needs no checkout and no third-party action")

    def test_untrusted_values_reach_scripts_only_through_the_environment(self):
        for step in self.job["steps"]:
            self.assertNotIn("${{", step.get("run", ""), step["name"])
        env = self.steps["Validate release tag"]["env"]
        self.assertEqual(env["RUN_TAG"], "${{ github.event.workflow_run.head_branch }}")
        self.assertIn(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", self.steps["Validate release tag"]["run"])

    def test_ssh_sends_only_the_tag_with_a_private_key_file_and_pinned_host_key(self):
        script = self.steps["Deploy over SSH"]["run"]
        self.assertLess(script.index("umask 077"), script.index('> "$HOME/.ssh/deploy_key"'))
        self.assertIn('chmod 600 "$HOME/.ssh/deploy_key"', script)
        self.assertIn('printf \'%s\\n\' "$DEPLOY_KNOWN_HOSTS" > "$HOME/.ssh/known_hosts"', script)
        self.assertIn("StrictHostKeyChecking=yes", script)
        self.assertIn("BatchMode=yes", script)
        ssh = script[script.index("ssh -T"):]
        self.assertTrue(ssh.rstrip().endswith('"$DEPLOY_USER@$DEPLOY_HOST" "$TAG"'), ssh)
        self.assertEqual(self.steps["Deploy over SSH"]["env"]["TAG"], "${{ steps.release.outputs.tag }}")
        self.assertEqual(self.steps["Remove the key"]["if"], "always()")


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class StagingLayoutTests(unittest.TestCase):
    def test_hosted_wrapper_imports_all_sites_with_separate_application_networks(self):
        wrapper = (ROOT / "infra/Caddyfile.hosted").read_text()
        imports = [line for line in wrapper.splitlines() if line.startswith("import ")]
        self.assertEqual(imports, [f"import /etc/caddy/Caddyfile.{name}" for name in ("production", "staging", "site")])
        proxy = yaml.safe_load((ROOT / "infra/docker-compose.hosted-proxy.yml").read_text())
        site = yaml.safe_load((ROOT / "infra/docker-compose.site.yml").read_text())
        self.assertEqual(set(proxy["services"]), {"caddy"})
        for name in ("production", "staging"):
            self.assertTrue(any(f":/etc/caddy/Caddyfile.{name}:ro" in mount for mount in proxy["services"]["caddy"]["volumes"]))
        self.assertTrue(any(mount["target"] == "/etc/caddy/Caddyfile.site" and mount["read_only"]
                            for mount in site["services"]["caddy"]["volumes"]))
        self.assertEqual(site["services"]["site"]["networks"], ["site"])
        self.assertEqual(site["services"]["caddy"]["networks"], ["proxy", "site"])
        self.assertTrue(site["networks"]["site"]["internal"])

    def test_only_the_app_joins_the_shared_proxy_and_no_ports_are_published(self):
        compose = yaml.safe_load((ROOT / "infra/docker-compose.staging.yml").read_text())
        self.assertEqual(compose["name"], "verwalino-staging")
        self.assertEqual(set(compose["volumes"]), {"pgdata_staging", "documents_staging"})
        self.assertEqual(compose["networks"]["proxy"], {"external": True, "name": "verwalino-staging_proxy"})
        self.assertTrue(compose["networks"]["backend"]["internal"])
        for name, service in compose["services"].items():
            self.assertNotIn("ports", service, name)
            self.assertEqual("proxy" in service["networks"], name == "staging-app")
        self.assertNotIn("app", compose["services"], "the shared proxy must never receive a second app DNS alias")
        env = compose["services"]["staging-app"]["environment"]
        self.assertEqual(env["HV_DEPLOYMENT_MODE"], "hosted")
        self.assertEqual(env["HV_PUBLIC_HOST"], "staging.verwalino.de")
        self.assertIn("${CANARY_EMAIL:?", env["HV_MAIL_ALLOWLIST"])

    def test_main_push_builds_before_ssh_and_docs_do_not_trigger_it(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-staging.yaml").read_text())
        image = yaml.safe_load((ROOT / ".github/workflows/build-main-image.yaml").read_text())
        trigger = image.get("on", image.get(True))["push"]
        self.assertEqual(trigger["branches"], ["main"])
        self.assertTrue({"backend/**", "frontend/**", "infra/**", "Dockerfile"} <= set(trigger["paths"]))
        self.assertNotIn("website/**", trigger["paths"])
        self.assertNotIn("docs/**", trigger["paths"])
        self.assertEqual(workflow.get("on", workflow.get(True)), {
            "workflow_run": {"workflows": [image["name"]], "types": ["completed"], "branches": ["main"]},
        })
        self.assertEqual(set(workflow["jobs"]), {"deploy"})
        deploy = workflow["jobs"]["deploy"]
        for required in ("github.event.workflow_run.conclusion == 'success'",
                         "github.event.workflow_run.event == 'push'",
                         "github.event.workflow_run.head_repository.full_name == github.repository"):
            self.assertIn(required, deploy["if"])
        self.assertEqual(deploy["steps"][0]["env"]["SHA"], "${{ github.event.workflow_run.head_sha }}")
        self.assertEqual(deploy["concurrency"], {"group": "deploy-hosted-staging", "cancel-in-progress": False})
        script = deploy["steps"][0]["run"]
        for required in ('"staging $SHA"', "StrictHostKeyChecking=yes", "BatchMode=yes", "umask 077"):
            self.assertIn(required, script)
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        release = yaml.safe_load((ROOT / ".github/workflows/release-image.yaml").read_text())
        self.assertEqual(set(release["jobs"]), {"release", "installer"})
        self.assertNotIn("workflow_call", release.get("on", release.get(True)))
        main = image["jobs"]["main"]
        build = next(step for step in main["steps"] if "docker/build-push-action@" in step.get("uses", ""))
        self.assertIn(":sha-${{ github.sha }}", build["with"]["tags"])
        self.assertEqual(build["with"]["tags"].strip(), "${{ env.IMAGE }}:sha-${{ github.sha }}")
        self.assertEqual(main["permissions"], {"contents": "read", "packages": "write", "id-token": "write"})
        self.assertIn('cosign sign --yes "$IMAGE@$DIGEST"', "\n".join(step.get("run", "") for step in main["steps"]))
        self.assertFalse(any("action-gh-release" in step.get("uses", "") for step in main["steps"]))

    def test_release_and_main_image_builds_have_separate_concurrency_groups(self):
        release = yaml.safe_load((ROOT / ".github/workflows/release-image.yaml").read_text())
        main = yaml.safe_load((ROOT / ".github/workflows/build-main-image.yaml").read_text())
        self.assertEqual(release["concurrency"], {
            "group": "release-image-tags", "cancel-in-progress": False,
        })
        self.assertNotEqual(release["concurrency"]["group"], main["concurrency"]["group"])

    def test_reusable_workflow_callers_grant_every_permission_requested_by_callee_jobs(self):
        ranks = {"none": 0, "read": 1, "write": 2}
        for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
            workflow = yaml.safe_load(path.read_text())
            for name, caller in workflow["jobs"].items():
                uses = caller.get("uses", "")
                if not uses.startswith("./.github/workflows/"):
                    continue
                callee = yaml.safe_load((ROOT / uses[2:]).read_text())
                granted = caller.get("permissions", workflow.get("permissions", {}))
                for job in callee["jobs"].values():
                    for scope, level in job.get("permissions", callee.get("permissions", {})).items():
                        with self.subTest(workflow=path.name, caller=name, scope=scope):
                            self.assertGreaterEqual(ranks[granted.get(scope, "none")], ranks[level])

    def test_ci_validates_the_full_hosted_caddy_wrapper_with_the_required_capability(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/tests-on-pr.yaml").read_text())
        steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
        validation = next(step["run"] for step in steps if "caddy validate" in step.get("run", ""))
        for required in ("--network none", "--read-only", "--cap-drop ALL", "--cap-add NET_BIND_SERVICE",
                         "--security-opt no-new-privileges", "VERWALINO_DOMAIN=validation.invalid"):
            self.assertIn(required, validation)
        for source, target in (("Caddyfile.hosted", "Caddyfile"), ("Caddyfile", "Caddyfile.production"),
                               ("Caddyfile.staging", "Caddyfile.staging"), ("Caddyfile.site", "Caddyfile.site")):
            self.assertIn(f"/infra/{source},dst=/etc/caddy/{target},readonly", validation)

    def test_robots_response_has_a_real_disallow_line(self):
        caddy = (ROOT / "infra/Caddyfile.staging").read_text()
        # Caddy takes arguments after the closing marker, never after the opener.
        self.assertRegex(caddy, r"respond <<ROBOTS\n[ \t]*User-agent: \*\n[ \t]*Disallow: /\n[ \t]*ROBOTS 200\n")

    def test_compose_network_declarations_keep_staging_off_productions_trusted_proxy(self):
        def config(file):
            return yaml.safe_load((ROOT / "infra" / file).read_text())

        staging = config("docker-compose.staging.yml")
        hosted = config("docker-compose.yml")
        caddy_networks = set(hosted["services"]["caddy"]["networks"])
        for overlay in ("docker-compose.hosted-proxy.yml", "docker-compose.site.yml"):
            extra = config(overlay)
            caddy_networks.update(extra["services"]["caddy"]["networks"])
            hosted["networks"].update(extra["networks"])
        staging_networks = {staging["networks"][key]["name"]
                            for key in staging["services"]["staging-app"]["networks"] if key == "proxy"}
        self.assertNotIn("verwalino_proxy", staging_networks)
        self.assertIn("verwalino-staging_proxy", staging_networks)
        caddy_networks = {hosted["networks"][key].get("name", f"verwalino_{key}") for key in caddy_networks}
        self.assertEqual(caddy_networks, {"verwalino_proxy", "verwalino-staging_proxy", "verwalino_site"})
        app_networks = {hosted["networks"][key].get("name", f"verwalino_{key}")
                        for key in hosted["services"]["app"]["networks"]}
        self.assertTrue(staging_networks.isdisjoint(app_networks))


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class WebsiteWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/website.yml").read_text()
        self.doc = yaml.safe_load(self.text)

    def test_pull_requests_only_check_and_main_pushes_publish_then_deploy(self):
        triggers = self.doc.get("on", self.doc.get(True))
        self.assertEqual(set(triggers), {"pull_request", "push"})
        self.assertEqual(triggers["push"]["branches"], ["main"])
        for trigger in triggers.values():
            self.assertEqual(trigger["paths"], ["website/**", ".github/workflows/website.yml"])
        jobs = self.doc["jobs"]
        self.assertEqual(jobs["build-push"]["needs"], "build")
        self.assertEqual(jobs["deploy"]["needs"], "build-push")
        for name in ("build-push", "deploy"):
            self.assertEqual(jobs[name]["if"], "github.event_name == 'push' && github.ref == 'refs/heads/main'")
        self.assertEqual([s["run"] for s in jobs["build"]["steps"] if "run" in s],
                         ["npm ci", "npm run check:i18n", "npm run lint", "npm run build", "npm run check:links"])

    def test_images_have_only_sha_tags_attestations_and_keyless_signing(self):
        job = self.doc["jobs"]["build-push"]
        self.assertEqual(job["permissions"], {"contents": "read", "packages": "write", "id-token": "write"})
        steps = job["steps"]
        build = next(s["with"] for s in steps if s.get("id") == "build")
        self.assertEqual(build["context"], "website")
        self.assertEqual(build["platforms"], "linux/amd64,linux/arm64")
        self.assertTrue(build["push"])
        self.assertEqual(build["tags"].splitlines(), ["${{ env.IMAGE }}:${{ github.sha }}"])
        self.assertEqual(build["provenance"], "mode=max")
        self.assertTrue(build["sbom"])
        self.assertIn('cosign sign --yes "$IMAGE@$DIGEST"', self.text)

    def test_deploy_uses_existing_secrets_forced_command_and_own_concurrency_group(self):
        job = self.doc["jobs"]["deploy"]
        self.assertEqual(self.doc["permissions"], {"contents": "read"})
        self.assertEqual(job["concurrency"], {"group": "deploy-hosted-site", "cancel-in-progress": False})
        self.assertEqual(self.doc["concurrency"]["cancel-in-progress"], "${{ github.event_name == 'pull_request' }}")
        self.assertNotIn("environment", job)
        self.assertNotIn("permissions", job)
        step, cleanup = job["steps"]
        self.assertEqual(set(step["env"]), {"SHA", "DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_SSH_KEY", "DEPLOY_KNOWN_HOSTS"})
        for entry in job["steps"]:
            self.assertNotIn("${{", entry.get("run", ""))
            self.assertNotIn("uses", entry)
        self.assertIn('"$DEPLOY_USER@$DEPLOY_HOST" "site $SHA"', step["run"])
        self.assertIn("StrictHostKeyChecking=yes", step["run"])
        self.assertIn("BatchMode=yes", step["run"])
        self.assertEqual(cleanup["if"], "always()")


if __name__ == "__main__":
    unittest.main()
