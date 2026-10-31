"""Hosted-server script contracts; Docker, git, curl and sleep are faked, nothing leaves the temp dir.

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

# One script serves as docker, git, curl and sleep; it logs every call and fails on demand.
FAKE_TOOL = r'''#!/usr/bin/env python3
import json, os, sys, tarfile, time
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
        out("0123456789abcdef\n")
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
    sys.exit(0)

# docker
if args[0] == "compose":
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
        (self.base / "state/HEAD").write_text(OLD_COMMIT)
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
        self.before = self.config.read_bytes()

    def deploy(self, *args, **env):
        return self.run_script(DEPLOY, *(args or (self.TAG,)), **env)

    def log_files(self):
        return sorted((self.srv / "logs").glob("deploy-*.log"))

    def assert_untouched(self):
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(list((self.srv / "backups").iterdir()), [])
        self.assertEqual((self.base / "state/HEAD").read_text(), OLD_COMMIT)

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


if __name__ == "__main__":
    unittest.main()
