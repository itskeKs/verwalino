"""Hosted-server script contracts; Docker, git, curl and sleep are faked, nothing leaves the temp dir.

Run in backend/ with uv run python ../scripts/test-server-deploy.py.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/server-bootstrap.sh"
SCRIPTS = ("server-bootstrap.sh", "server-backup.sh", "deploy-server.sh")


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


if __name__ == "__main__":
    unittest.main()
