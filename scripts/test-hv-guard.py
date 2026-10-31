"""Guard decisions only: no Docker, HTTP, stack installation or database access.

python3 scripts/test-hv-guard.py --guard ~/.coddy/hooks/hv_guard.py
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument("--guard", default=str(Path(__file__).with_name("hv_guard.py")))
options, remaining = parser.parse_known_args()
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("hv_guard_under_test", options.guard)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SOURCE = Path(__file__).resolve().parents[1]
GUARD_HOOK = os.path.expanduser("~/.coddy/hooks/hv_guard.py")
GUARD_CONFIG = os.path.expanduser("~/.coddy/hooks.json")
PORTABLE = SOURCE / ".coddy/worktrees/feat-portable-docker"
if not (PORTABLE / "scripts/self-host.sh").exists():
    PORTABLE = SOURCE
REAL_RUN = subprocess.run


class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.repo = Path(cls.temp.name).resolve() / "repo"
        cls.repo.mkdir()
        cls.git("init", "-q")
        cls.git("config", "user.name", "Guard tests")
        cls.git("config", "user.email", "guard@example.invalid")
        (cls.repo / "backend/migrations/versions").mkdir(parents=True)
        (cls.repo / "backend/migrations/versions/0001_test.py").write_text("# committed\n")
        for name in ("infra/docker-compose.yml", "infra/Caddyfile"):
            dest = cls.repo / name
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes((PORTABLE / name).read_bytes())
        (cls.repo / "infra/docker-compose.declaration-test.yml").write_text("services: {}\n")
        cls.git("add", ".")
        cls.git("commit", "-qm", "Guard fixture")
        cls.worktree = cls.repo / ".coddy/worktrees/portable"
        cls.worktree.parent.mkdir(parents=True)
        cls.git("worktree", "add", "-qb", "fixture", str(cls.worktree))

    @classmethod
    def git(cls, *args):
        return REAL_RUN(["git", "-C", str(cls.repo), *args], check=True,
                        text=True, capture_output=True)

    def setUp(self):
        self.patchers = [patch.object(guard, "REPO", str(self.repo)),
                         patch.dict(os.environ, {}, clear=True),
                         patch.object(guard.subprocess, "run", side_effect=self.git_only)]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        self.config_text = "\n".join([
            "VERWALINO_MODE=local", "VERWALINO_DOMAIN=", "VERWALINO_PORT=18080",
            "VERWALINO_IMAGE=verwalino:local", "VERWALINO_PROXY_SUBNET=172.30.97.0/28",
            *[f"{key}=" + "a" * 64 for key in ("MIGRATOR_PASSWORD", "APP_RW_PASSWORD",
                "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD", "SESSION_SECRET")], ""])
        for root in (self.repo, self.worktree):
            config = root / ".env.selfhost"
            config.write_text(self.config_text)
            config.chmod(0o600)

    @staticmethod
    def git_only(args, **kwargs):
        if not isinstance(args, list) or args[0] != "git":
            raise AssertionError(f"Tests must never execute external commands: {args!r}")
        return REAL_RUN(args, **kwargs)

    def decision(self, command, cwd=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                guard.check_command(command, str(cwd or self.repo))
            except SystemExit as exc:
                self.assertEqual(exc.code, 0)
        return json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] if out.getvalue() else "allow"

    def allow(self, command, cwd=None):
        self.assertEqual(self.decision(command, cwd), "allow", command)

    def block(self, command, cwd=None):
        self.assertEqual(self.decision(command, cwd), "deny", command)

    def compose(self, tail, root=None, extra=""):
        root = root or self.repo
        return (f"docker compose --project-name verwalino --env-file {root}/.env.selfhost "
                f"-f {root}/infra/docker-compose.yml --profile ops {extra} {tail}")

    def caddy(self, root=None):
        root = root or self.repo
        return ("docker run --rm --network none --read-only --cap-drop ALL --cap-add NET_BIND_SERVICE "
                "--security-opt no-new-privileges:true --env VERWALINO_DOMAIN=validation.invalid "
                f"--mount type=bind,src={root}/infra/Caddyfile,dst=/etc/caddy/Caddyfile,readonly "
                "caddy:2.10.2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile")

    def test_git_read_and_staging(self):
        for command in ("git diff -- docker-compose.prod.yml", "git diff --cached -- infra/docker-compose.yml",
                        "git diff --stat -- docker-compose.prod.yml", "git add -- infra/docker-compose.yml",
                        "git add -- docker-compose.prod.yml", "git status --short", "git ls-files -- infra/docker-compose.yml"):
            with self.subTest(command=command):
                self.allow(command)

    def test_git_has_no_shell_or_external_program_exception(self):
        for command in ("git diff --ext-diff -- docker-compose.prod.yml",
                        "git diff --textconv -- docker-compose.prod.yml",
                        "git -c diff.external=evil diff -- docker-compose.prod.yml",
                        "GIT_EXTERNAL_DIFF=evil git diff -- docker-compose.prod.yml",
                        "git diff --output=docker-compose.prod.yml",
                        "git diff * docker-compose.prod.yml", "git diff ? docker-compose.prod.yml",
                        "git diff [a-z]* docker-compose.prod.yml", "git -C . diff --ext-diff -- README.md",
                        "git diff ^README.md docker-compose.prod.yml",
                        "git status --short > docker-compose.prod.yml",
                        "git diff -- docker-compose.prod.yml; touch marker",
                        "git diff -- docker-compose.prod.yml && touch marker",
                        "git diff -- docker-compose.prod.yml | cat",
                        "git add -- infra/docker-compose.yml $(touch marker)",
                        "git add -- infra/docker-compose.yml `touch marker`",
                        "git status --short\ndocker stop hausverwaltung-app-1",
                        f"git diff -- infra/docker-compose.yml > {GUARD_HOOK}"):
            with self.subTest(command=command):
                self.block(command)

    def test_git_external_diff_environment_and_configuration(self):
        with patch.dict(os.environ, {"GIT_EXTERNAL_DIFF": "evil"}):
            self.block("git diff -- docker-compose.prod.yml")
        self.git("config", "diff.external", "evil")
        try:
            self.block("git diff -- docker-compose.prod.yml")
        finally:
            self.git("config", "--unset", "diff.external")

    def test_installer_operations_in_checkout_and_linked_worktree(self):
        tails = ("build app", "pull app", "up -d --wait --wait-timeout 120 db", "stop app",
                 "run --rm --no-deps -T migrate", "up -d --no-deps --wait --wait-timeout 120 app",
                 "ps", "logs --tail 100 app", "stop")
        for root in (self.repo, self.worktree):
            for tail in tails:
                with self.subTest(root=root.name, tail=tail):
                    self.allow(self.compose(tail, root), root)
            self.allow(self.compose('run --rm --no-deps -T bootstrap --org "Example Company" '
                                    '--email admin@example.org --name Administrator --password-stdin', root), root)
            config = root / ".env.selfhost"
            config.write_text(self.config_text.replace("MODE=local", "MODE=https").replace(
                "VERWALINO_DOMAIN=\n", "VERWALINO_DOMAIN=verwaltung.example.org\n"))
            self.allow(self.compose("up -d --no-deps caddy", root, "--profile https"), root)

    def test_relative_and_equals_options(self):
        self.allow("docker compose -p verwalino --env-file=.env.selfhost --file=infra/docker-compose.yml --profile=ops ps")
        self.allow("docker compose --project-name=verwalino --env-file ../.env.selfhost -f docker-compose.yml --profile ops ps", self.repo / "infra")

    def test_portable_rejects_overrides_and_unreviewed_paths(self):
        good = self.compose("ps")
        bad = [good.replace("--project-name verwalino", "--project-name hausverwaltung"),
               good.replace("--project-name verwalino", ""), good.replace("--env-file", "--env-file /tmp/.env.foreign --env-file"),
               good.replace("--project-name verwalino", "--project-name verwalino -p hausverwaltung"),
               good.replace("--profile ops", "--profile ops --profile ops"),
               good.replace("--profile ops", "--profile arbitrary"),
               good.replace("--profile ops", "--profile ops --project-directory /tmp"),
               good.replace("-f ", "-f /tmp/override.yml -f "),
               good.replace("-f ", "--env-file .env.selfhost -f "),
               good.replace("docker compose", "docker --context alpha compose"),
               good.replace("docker compose", "docker -H unix:///var/run/docker.sock compose"),
               good.replace("docker compose", "env COMPOSE_FILE=evil docker compose"),
               good.replace("docker compose", "DOCKER_HOST=ssh://alpha docker compose"),
               good + " --project-name hausverwaltung", good + " ; docker ps", good + " > docker-compose.prod.yml",
               good.replace("infra/docker-compose.yml", "infra/docker-compose.prod.yml"),
               good.replace(str(self.repo / ".env.selfhost"), str(self.worktree / ".env.selfhost")),
               good.replace(str(self.repo / "infra/docker-compose.yml"), "/tmp/infra/docker-compose.yml")]
        for command in bad:
            with self.subTest(command=command):
                self.block(command)

    def test_portable_rejects_dangerous_operations(self):
        for tail in ("down", "down -v", "down --volumes", "rm -v", "prune", "up -d", "up -d db app",
                     "run --rm app sh", "run --rm --no-deps -T migrate sh", "run --privileged migrate",
                     "run --rm --no-deps -T -v /:/host migrate", "run --rm --no-deps -T --entrypoint sh migrate",
                     "run --rm --no-deps -T bootstrap --org X --email a@b.org --password secret",
                     "exec app sh", "restart app", "stop hausverwaltung-app-1", "logs alpha"):
            with self.subTest(tail=tail):
                self.block(self.compose(tail))

    def test_portable_checks_reviewed_bytes_and_private_configuration(self):
        compose = self.repo / "infra/docker-compose.yml"
        original = compose.read_bytes()
        compose.write_bytes(original + b"\n# unreviewed\n")
        try:
            self.block(self.compose("ps"))
        finally:
            compose.write_bytes(original)
        config = self.repo / ".env.selfhost"
        for text in (self.config_text + "COMPOSE_PROJECT_NAME=hausverwaltung\n",
                     self.config_text + "VERWALINO_PORT=18080\n",
                     self.config_text.replace("18080", "8000"),
                     self.config_text.replace("verwalino:local", "arbitrary:latest")):
            config.write_text(text)
            self.block(self.compose("ps"))
        config.write_text(self.config_text)
        config.chmod(0o644)
        self.block(self.compose("ps"))
        config.chmod(0o600)
        config.unlink()
        config.symlink_to(self.worktree / ".env.selfhost")
        try:
            self.block(self.compose("ps"))
        finally:
            config.unlink()
        config.write_text(self.config_text)
        config.chmod(0o600)
        for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "VERWALINO_PORT"):
            with patch.dict(os.environ, {key: "override"}):
                self.block(self.compose("ps"))

    def test_caddy_isolated_validation(self):
        self.allow(self.caddy())
        self.allow(self.caddy(self.worktree), self.worktree)
        self.allow(self.caddy().replace(
            f"--mount type=bind,src={self.repo}/infra/Caddyfile,dst=/etc/caddy/Caddyfile,readonly",
            "-v infra/Caddyfile:/etc/caddy/Caddyfile:ro"))

    def test_caddy_rejects_missing_constraints_mounts_and_commands(self):
        good = self.caddy()
        for fragment in ("--network none ", "--read-only ", "--cap-drop ALL ", "--cap-add NET_BIND_SERVICE ",
                         "--security-opt no-new-privileges:true "):
            self.block(good.replace(fragment, ""))
        for extra in ("--privileged", "--cap-add SYS_ADMIN", "-p 18080:80", "-v /:/host:ro",
                      "-v /var/run/docker.sock:/var/run/docker.sock:ro", "--network host",
                      "--env-file .env.selfhost", "--entrypoint sh", "--pid host", "--user root"):
            with self.subTest(extra=extra):
                self.block(good.replace("caddy:2.10.2-alpine", extra + " caddy:2.10.2-alpine"))
        for a, b in ((",readonly", ""), ("infra/Caddyfile", "docker-compose.prod.yml"),
                     ("--cap-add NET_BIND_SERVICE", "--cap-add SYS_ADMIN"),
                     ("caddy validate", "sh -c id"), ("caddy:2.10.2-alpine", "arbitrary:latest"),
                     ("--network none", "--network none --network host")):
            self.block(good.replace(a, b))
        self.block(good + " && docker ps")
        caddy = self.repo / "infra/Caddyfile"
        original = caddy.read_bytes()
        caddy.write_bytes(original + b"\n")
        try:
            self.block(good)
        finally:
            caddy.write_bytes(original)

    def test_production_and_destructive_regressions(self):
        for command in ("docker stop hausverwaltung-db-1", "docker exec hausverwaltung-app-1 sh",
                        "docker volume inspect hausverwaltung_pgdata", "docker volume rm hausverwaltung_documents",
                        "docker compose -f docker-compose.prod.yml up -d", "docker volume rm anything",
                        "docker system prune -af",
                        "curl http://localhost:8000/api/health", "curl http://127.0.0.1:8000",
                        "curl http://[::1]:8000", "curl http://0.0.0.0:8000",
                        "sed -i x backend/migrations/versions/0001_test.py",
                        "git restore -- backend/migrations/versions/0001_test.py",
                        f"cp other {GUARD_HOOK}",
                        f"echo x > {GUARD_CONFIG}"):
            with self.subTest(command=command):
                self.block(command)

    def test_arbitrary_container_starts_and_docker_environment_overrides(self):
        for command in ("docker run --rm alpine sh", "docker start arbitrary",
                        "docker --context alpha run --privileged alpine sh",
                        "docker --host unix:///var/run/docker.sock run alpine sh",
                        "docker container run alpine sh", "docker container start arbitrary"):
            self.block(command)

    def test_existing_test_database_and_unrelated_commands(self):
        self.allow("docker compose -f infra/docker-compose.declaration-test.yml up -d")
        self.block("docker compose -f infra/docker-compose.declaration-test.yml down -v")
        self.allow("docker ps")
        self.allow("curl http://127.0.0.1:18000/api/health")
        self.allow("python3 scripts/test-hv-guard.py")

    def test_test_database_rejects_trailing_global_and_volume_overrides(self):
        for tail in ("up -d -f /tmp/other.yml", "up -d --file=/tmp/other.yml", "up -d -p alpha",
                     "up -d -palpha", "up -d --project-directory=/tmp", "up -d --env-file=/tmp/.env",
                     "up -d --profile evil", "down --volumes=true", "down -vt0"):
            self.block("docker compose -f infra/docker-compose.declaration-test.yml " + tail)

    def test_edit_migration_and_self_protection_via_hook_payload(self):
        for tool in ("edit", "write", "apply_patch"):
            for root in (self.repo, self.worktree):
                for path in (str(root / "backend/migrations/versions/0001_test.py"),
                             GUARD_HOOK, GUARD_CONFIG):
                    with self.subTest(tool=tool, root=root.name, path=path):
                        payload = {"cwd": str(root), "tool_name": tool, "tool_input": {"path": path}}
                        out = io.StringIO()
                        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(out):
                            with self.assertRaises(SystemExit):
                                guard.main()
                        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_unrelated_workspaces_stay_untouched(self):
        payload = {"cwd": self.temp.name, "tool_name": "run_command",
                   "tool_input": {"command": "docker stop hausverwaltung-app-1"}}
        out = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(out):
            guard.main()
        self.assertEqual(out.getvalue(), "")


class HookProtocolTests(unittest.TestCase):
    def test_real_hook_process_and_json_protocol(self):
        interpreter = "/usr/local/bin/python3" if Path("/usr/local/bin/python3").exists() else sys.executable
        cases = [
            (SOURCE, "run_command", {"command": "git diff -- docker-compose.prod.yml"}, "allow"),
            (SOURCE, "run_command", {"command": "git add -- infra/docker-compose.yml"}, "allow"),
            (SOURCE, "run_command", {"command": "git status --short"}, "allow"),
            (SOURCE, "run_command", {"command": "docker stop hausverwaltung-db-1"}, "deny"),
            (SOURCE, "run_command", {"command": "docker volume rm hausverwaltung_documents"}, "deny"),
            (SOURCE, "run_command", {"command": "curl http://localhost:8000/api/health"}, "deny"),
            (SOURCE, "write", {"path": GUARD_HOOK}, "deny"),
            (SOURCE, "edit", {"path": GUARD_CONFIG}, "deny"),
            (SOURCE, "edit", {"path": str(SOURCE / "backend/migrations/versions/0001_core_tables.py")}, "deny"),
            (PORTABLE, "run_command", {"command": "git diff -- infra/docker-compose.yml"}, "allow"),
            (Path(tempfile.gettempdir()), "run_command", {"command": "docker stop hausverwaltung-app-1"}, "allow"),
        ]
        env = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "DOCKER_", "COMPOSE_"))}
        for cwd, tool, args, expected in cases:
            with self.subTest(cwd=cwd.name, tool=tool, args=args):
                result = REAL_RUN([interpreter, "-I", "-B", str(Path(options.guard).resolve())],
                                  input=json.dumps({"cwd": str(cwd), "tool_name": tool, "tool_input": args}),
                                  capture_output=True, text=True, check=True, env=env, timeout=15)
                self.assertEqual(result.stderr, "")
                decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout else "allow"
                self.assertEqual(decision, expected, result.stdout)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *remaining])
