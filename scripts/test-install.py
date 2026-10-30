"""Release installer contracts: stdlib only, fake downloads/Docker, no network."""

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
SCRIPT = ROOT / "install.sh"
TAG = "v1.2.3"
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/itskeks/verwalino@" + DIGEST
ASSETS = {
    "install.sh": "install.sh",
    "self-host.sh": "scripts/self-host.sh",
    "docker-compose.yml": "infra/docker-compose.yml",
    "Caddyfile": "infra/Caddyfile",
    "prod-init.sh": "infra/db/prod-init.sh",
    "env.example": "env.example",
    "IMAGE_DIGEST": "IMAGE_DIGEST",
}

FAKE_TOOL = r'''
import json, os, pathlib, shutil, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_LOG"], "a") as log:
    log.write(json.dumps([name, args]) + "\n")
if name in ("curl", "wget"):
    if os.environ.get("NO_NETWORK"):
        sys.exit("Unexpected download")
    url = args[-1]
    if url == "https://api.github.com/repos/itskeKs/verwalino/releases/latest":
        content = os.environ.get("LATEST_JSON", '{"tag_name": "v1.2.3", "prerelease": false}')
        output = args[args.index("-o") + 1] if "-o" in args else (args[args.index("-O") + 1] if "-O" in args else None)
        if output:
            pathlib.Path(output).write_text(content)
        else:
            print(content)
    elif url.startswith("https://github.com/itskeKs/verwalino/releases/download/v1.2.3/"):
        output = args[args.index("-o") + 1] if "-o" in args else args[args.index("-O") + 1]
        source = pathlib.Path(os.environ["INSTALL_TEST_BUNDLE"]) / url.rsplit("/", 1)[1]
        shutil.copyfile(source, output)
    else:
        sys.exit("Unexpected URL: " + url)
elif name == "docker":
    if args == ["compose", "version", "--short"]:
        print(os.environ.get("COMPOSE_VERSION", "2.24.0"))
    elif args == ["info"]:
        sys.exit(int(os.environ.get("DAEMON_FAIL", "0")))
    elif "psql" in args:
        if any("SELECT count(*)" in arg for arg in args):
            print("0")
        else:
            sys.stdin.read()
            print("t")
elif name == "cosign":
    if os.environ.get("NO_NETWORK") and "--trusted-root" not in args:
        sys.exit("Cosign could fetch service keys without a local trusted root")
    sys.exit(int(os.environ.get("COSIGN_FAIL", "0")))
elif name == "self-host.sh":
    if args == ["help"]:
        print("init start" + (" doctor" if os.environ.get("HAS_DOCTOR") else ""))
    elif args == ["doctor"]:
        sys.exit(int(os.environ.get("DOCTOR_FAIL", "0")))
    elif args and args[0] == "init":
        config = pathlib.Path(os.environ["VERWALINO_CONFIG"])
        if not config.exists():
            config.write_text("VERWALINO_IMAGE=" + args[args.index("--image") + 1] + "\n")
            config.chmod(0o600)
        sys.exit(int(os.environ.get("INIT_FAIL", "0")))
    elif args == ["start"]:
        print("Browser setup: http://127.0.0.1:18080/setup\nSetup token: test-terminal-token")
        sys.exit(int(os.environ.get("START_FAIL", "0")))
    else:
        sys.exit("Unexpected self-host arguments")
'''


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.binary = self.base / "bin"
        self.binary.mkdir()
        # An isolated PATH prevents accidental use of real network/Docker/cosign tools.
        for name in ("bash", "cat", "chmod", "cp", "dirname", "grep", "ln", "mkdir",
                     "mktemp", "mv", "openssl", "rm", "rmdir", "sed", "shasum", "stat"):
            target = shutil.which(name)
            self.assertIsNotNone(target, name)
            (self.binary / name).symlink_to(target)
        for name in ("docker", "curl", "wget", "cosign"):
            tool = self.binary / name
            tool.write_text(f"#!{sys.executable}\n" + FAKE_TOOL)
            tool.chmod(0o755)
        self.bundle = self.base / "bundle"
        self.bundle.mkdir()
        for asset in ASSETS:
            (self.bundle / asset).write_text("release content\n")
        # The helper remains a Bash entry point while its observable calls use the shim.
        (self.bundle / "self-host.sh").write_text('exec "$INSTALL_TEST_PYTHON" "$INSTALL_TEST_HELPER" "$@"\n')
        self.helper = self.base / "self-host.sh"
        self.helper.write_text(FAKE_TOOL)
        (self.bundle / "IMAGE_DIGEST").write_text(DIGEST + "\n")
        (self.bundle / "SHA256SUMS.sigstore.json").write_text("test signature bundle\n")
        # Operator-supplied trust material, independent of the release assets.
        (self.bundle / "trusted_root.json").write_text("test trusted root\n")
        self.checksums()
        self.dest = self.base / "installation with spaces"
        self.log = self.base / "calls.jsonl"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("VERWALINO_", "INSTALL_TEST_"))}
        self.env.update(PATH=str(self.binary), INSTALL_TEST_BUNDLE=str(self.bundle),
                        INSTALL_TEST_LOG=str(self.log), INSTALL_TEST_PYTHON=sys.executable,
                        INSTALL_TEST_HELPER=str(self.helper))

    def checksums(self):
        (self.bundle / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((self.bundle / asset).read_bytes()).hexdigest()}  {asset}\n"
            for asset in ASSETS))

    def run_install(self, *args, offline=True, version=TAG, piped=False):
        options = ["--dir", str(self.dest)]
        if version is not None:
            options += ["--version", version]
        if offline:
            options += ["--offline-bundle", str(self.bundle)]
        command = ["/bin/bash", "-s", "--"] if piped else ["/bin/bash", str(SCRIPT)]
        return subprocess.run(command + options + list(args), env=self.env, cwd=self.base,
                              input=SCRIPT.read_text() if piped and SCRIPT.exists() else None,
                              text=True, capture_output=True, timeout=15, check=False)

    def calls(self, name):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [args for tool, args in map(json.loads, lines) if tool == name]

    def assert_aborted(self, result, message):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(message.lower(), result.stderr.lower())
        self.assertEqual(self.calls("self-host.sh"), [])

    def test_offline_install_passes_exact_init_and_start_arguments_without_network(self):
        self.env["NO_NETWORK"] = "1"
        result = self.run_install("--port", "19080")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("curl") + self.calls("wget"), [])
        self.assertEqual(self.calls("self-host.sh"), [
            ["help"], ["init", "--image", IMAGE, "--port", "19080"], ["start"]])
        self.assertEqual(self.calls("docker"), [["compose", "version", "--short"], ["info"]])
        self.assertIn("/setup", result.stdout)
        self.assertIn("test-terminal-token", result.stdout)
        for path in self.dest.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"test-terminal-token", path.read_bytes())
        for asset, target in ASSETS.items():
            self.assertEqual((self.dest / target).read_bytes(), (self.bundle / asset).read_bytes())

    def test_every_payload_checksum_mismatch_aborts_before_helper_execution(self):
        for asset in ASSETS:
            with self.subTest(asset=asset):
                original = (self.bundle / asset).read_bytes()
                (self.bundle / asset).write_bytes(original + b"tampered")
                self.assert_aborted(self.run_install(), "checksum")
                (self.bundle / asset).write_bytes(original)

    def test_missing_or_duplicate_checksum_entries_and_paths_fail_closed(self):
        original = (self.bundle / "SHA256SUMS").read_text()
        for content in ("", "\n".join(original.splitlines()[1:]) + "\n",
                        original + original.splitlines()[0] + "\n",
                        original.replace("  install.sh", "  ../install.sh")):
            with self.subTest(content=content[:80]):
                (self.bundle / "SHA256SUMS").write_text(content)
                self.assert_aborted(self.run_install(), "checksum")

    def test_missing_prerequisites_have_clear_messages(self):
        for names, message in ((("docker",), "docker"), (("openssl",), "openssl"),
                               (("curl", "wget"), "curl or wget"), (("shasum",), "sha256sum or shasum")):
            with self.subTest(names=names):
                for name in names:
                    (self.binary / name).rename(self.base / name)
                self.assert_aborted(self.run_install(), message)
                for name in names:
                    (self.base / name).rename(self.binary / name)

    def test_old_compose_and_unreachable_daemon_fail_before_download(self):
        for version in ("1.29.2", "2.23.3", "unknown"):
            self.env["COMPOSE_VERSION"] = version
            self.assert_aborted(self.run_install(offline=False), "compose")
        self.env["COMPOSE_VERSION"] = "v2.24.1"
        self.env["DAEMON_FAIL"] = "1"
        self.assert_aborted(self.run_install(offline=False), "daemon")
        self.assertEqual(self.calls("curl") + self.calls("wget"), [])

    def test_latest_api_tag_selects_release_assets_when_piped_to_bash(self):
        self.env["LATEST_JSON"] = '{\n "tag_name" : "v1.2.3",\n "prerelease": false\n}'
        result = self.run_install(offline=False, version=None, piped=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        urls = [args[-1] for args in self.calls("curl")]
        self.assertEqual(urls[0], "https://api.github.com/repos/itskeKs/verwalino/releases/latest")
        self.assertEqual(set(urls[1:]), {
            f"https://github.com/itskeKs/verwalino/releases/download/{TAG}/{asset}"
            for asset in (*ASSETS, "SHA256SUMS", "SHA256SUMS.sigstore.json")})

    def test_prerelease_and_invalid_tags_are_refused(self):
        for tag in ("v1.2.3-rc1", "1.2.3", "v01.2.3", "v1.2", "v1.2.3/evil"):
            with self.subTest(tag=tag):
                self.assert_aborted(self.run_install(version=tag), "stable")
        for payload in ('{"tag_name":"v1.2.3-rc1"}', '{"tag_name":"v1.2.3","prerelease":true}', '{}'):
            self.env["LATEST_JSON"] = payload
            self.assert_aborted(self.run_install(offline=False, version=None), "stable")

    def test_signature_identity_and_issuer_are_pinned_to_selected_release(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        args, = self.calls("cosign")
        self.assertEqual(args[0], "verify-blob")
        self.assertEqual(args[args.index("--certificate-identity") + 1],
                         f"https://github.com/itskeKs/verwalino/.github/workflows/release-image.yaml@refs/tags/{TAG}")
        self.assertEqual(args[args.index("--certificate-oidc-issuer") + 1],
                         "https://token.actions.githubusercontent.com")
        self.assertIn("--bundle", args)

    def test_signature_failure_aborts_and_absent_cosign_prints_manual_command(self):
        self.env["COSIGN_FAIL"] = "1"
        self.assert_aborted(self.run_install(), "signature")
        (self.binary / "cosign").unlink()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("cosign verify-blob", "--bundle", "--certificate-identity", TAG,
                     "https://token.actions.githubusercontent.com"):
            self.assertIn(text, result.stdout)

    def test_existing_configuration_is_refused_before_any_download(self):
        self.dest.mkdir()
        config = self.dest / ".env.selfhost"
        config.write_text("preserve these bytes\n")
        self.assert_aborted(self.run_install(offline=False), "--force")
        self.assertEqual(config.read_text(), "preserve these bytes\n")
        self.assertEqual(self.calls("curl") + self.calls("wget"), [])

    def test_force_retries_the_same_image_without_overwriting_configuration(self):
        self.dest.mkdir()
        config = self.dest / ".env.selfhost"
        before = f"# preserved private configuration\nVERWALINO_IMAGE={IMAGE}\n"
        config.write_text(before)
        result = self.run_install("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(), before)
        self.assertEqual(self.calls("self-host.sh"), [["help"], ["init"], ["start"]])

    def test_force_refuses_image_changes_and_reconfiguration(self):
        self.dest.mkdir()
        config = self.dest / ".env.selfhost"
        config.write_text("VERWALINO_IMAGE=ghcr.io/itskeks/verwalino@sha256:" + "b" * 64 + "\n")
        self.assert_aborted(self.run_install("--force"), "image")
        config.write_text("VERWALINO_IMAGE=" + IMAGE + "\n")
        self.assert_aborted(self.run_install("--force", "--https", "example.org"), "reconfigur")

    def test_https_and_port_are_passed_as_separate_arguments(self):
        result = self.run_install("--https", "verwaltung.example.org", "--port", "19080")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["init", "--image", IMAGE, "--https", "verwaltung.example.org", "--port", "19080"],
                      self.calls("self-host.sh"))

    def test_doctor_is_optional_and_a_failure_stops_initialization(self):
        self.env.update(HAS_DOCTOR="1", DOCTOR_FAIL="1")
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls("self-host.sh"), [["help"], ["doctor"]])

    def test_wget_and_sha256sum_fallbacks_work_without_curl_or_shasum(self):
        (self.binary / "curl").unlink()
        (self.binary / "shasum").rename(self.binary / "real-shasum")
        wrapper = self.binary / "sha256sum"
        wrapper.write_text('#!/bin/bash\nexec "' + str(self.binary / "real-shasum") + '" -a 256 "$@"\n')
        wrapper.chmod(0o755)
        result = self.run_install(offline=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls("wget"))

    def test_invalid_image_digest_is_never_used_as_shell_code(self):
        (self.bundle / "IMAGE_DIGEST").write_text("sha256:$(touch outside)\n")
        self.checksums()
        self.assert_aborted(self.run_install(), "digest")
        self.assertFalse((self.base / "outside").exists())

    def test_destination_symlinks_cannot_write_outside_install_directory(self):
        self.dest.mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        (self.dest / "infra").symlink_to(outside, target_is_directory=True)
        self.assert_aborted(self.run_install(), "symlink")
        self.assertEqual(list(outside.iterdir()), [])

    def test_caller_config_override_cannot_write_outside_install_directory(self):
        outside = self.base / ".env.outside"
        self.env["VERWALINO_CONFIG"] = str(outside)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(outside.exists())
        self.assertTrue((self.dest / ".env.selfhost").exists())

    def test_init_and_start_failures_propagate(self):
        for failure in ("INIT_FAIL", "START_FAIL"):
            with self.subTest(failure=failure):
                self.env[failure] = "1"
                result = self.run_install("--force")
                self.assertEqual(result.returncode, 1)
                self.env.pop(failure)

    def test_offline_bundle_requires_explicit_version(self):
        self.assert_aborted(self.run_install(version=None), "--version")
        self.assertEqual(self.calls("curl") + self.calls("wget"), [])

    def test_offline_cosign_requires_local_trust_material_without_network(self):
        self.env["NO_NETWORK"] = "1"
        (self.bundle / "trusted_root.json").unlink()
        self.assert_aborted(self.run_install(), "trusted_root.json")
        self.assertEqual(self.calls("cosign"), [])

    def test_real_helper_uses_only_shipped_files_and_streams_setup_token(self):
        (self.bundle / "self-host.sh").write_bytes((ROOT / "scripts/self-host.sh").read_bytes())
        (self.bundle / "docker-compose.yml").write_bytes((ROOT / "infra/docker-compose.yml").read_bytes())
        (self.bundle / "prod-init.sh").write_bytes((ROOT / "infra/db/prod-init.sh").read_bytes())
        self.checksums()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Browser setup: http://127.0.0.1:18080/setup", result.stdout)
        token = result.stdout.split("Setup token: ")[1].splitlines()[0]
        self.assertEqual(len(token), 64)
        for path in self.dest.rglob("*"):
            if path.is_file():
                self.assertNotIn(token.encode(), path.read_bytes())
        self.assertTrue(os.access(self.dest / "infra/db/prod-init.sh", os.X_OK))
        calls = self.calls("docker")[2:]
        self.assertTrue(calls)
        for args in calls:
            self.assertEqual(args[args.index("--project-name") + 1], "verwalino")
            self.assertEqual(args[args.index("-f") + 1], str(self.dest / "infra/docker-compose.yml"))
        compose = (self.dest / "infra/docker-compose.yml").read_text()
        self.assertIn("./db/prod-init.sh:", compose)
        self.assertIn(["pull", "app"], [args[-2:] for args in calls])


if __name__ == "__main__":
    unittest.main()
