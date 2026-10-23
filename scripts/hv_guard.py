#!/usr/bin/env python3
"""PreToolUse guard for the hausverwaltung workspace (user scope, ~/.coddy/hooks.json).

Blocks what an agent must never do there, whatever the permission mode:
- touch the production stack (containers, volumes, port 8000, docker-compose.prod.yml);
- use Docker outside the test DB or narrowly reviewed portable operations;
- edit a migration that is already committed;
- edit this guard or the user hooks file.
Any other workspace passes untouched. Exit 0 with a deny JSON blocks the call.
"""
import json
import hashlib
import ipaddress
import os
import re
import shlex
import stat
import subprocess
import sys

REPO = "/Users/keks/Developer/hausverwaltung"
TEST_COMPOSE = "infra/docker-compose.declaration-test.yml"
GUARD_PATHS = (os.path.expanduser("~/.coddy/hooks"), os.path.expanduser("~/.coddy/hooks.json"))
PROD_NAMES = ("hausverwaltung-db-1", "hausverwaltung-app-1", "hausverwaltung_pgdata",
              "hausverwaltung_documents", "docker-compose.prod.yml")
DOCKER_DESTRUCTIVE = re.compile(
    r"\bdocker(?:-compose)?\b[^|;&]*\b(rm|stop|kill|restart|down|prune|volume\s+rm|"
    r"container\s+rm|system\s+prune|rename|update)\b")
# Reviewed at feat/portable-docker f28bbca; a changed stack needs a fresh review.
PORTABLE_FILES = {
    "infra/docker-compose.yml": "3ed4ad60cce94fc1cedc181217bac3b659d3b5892d6af64e1ed54cf94d0d7fc1",
    "infra/Caddyfile": "6dd9a73b44d736d04f9fbaf17c292e515f21ced9d1bb753a92981ca0dbf61d49",
}


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "hausverwaltung guard: " + reason}}))
    sys.exit(0)


def git_read(root, *args):
    # Caller Git environment must not redirect the repository lookup.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", root, *args], capture_output=True, env=env)


def worktrees():
    result = git_read(REPO, "worktree", "list", "--porcelain", "-z")
    paths = [os.path.realpath(REPO)]
    if result.returncode == 0:
        paths += [os.path.realpath(os.fsdecode(line[9:]))
                  for line in result.stdout.split(b"\0") if line.startswith(b"worktree ")]
    return sorted(set(paths), key=len, reverse=True)


def workspace_root(path):
    path = os.path.realpath(path)
    return next((root for root in worktrees()
                 if path == root or path.startswith(root + os.sep)), None)


def in_repo(path):
    return bool(path and workspace_root(path))


def committed_migration(path, cwd):
    p = path if os.path.isabs(path) else os.path.join(cwd, path)
    p = os.path.realpath(p)
    root = workspace_root(p)
    if not root or not p.startswith(root + "/backend/migrations/versions/"):
        return False
    rel = os.path.relpath(p, root)
    r = git_read(root, "ls-files", "--error-unmatch", "--", rel)
    return r.returncode == 0


def guard_path(path, cwd):
    path = os.path.expanduser(path)
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    p2 = os.path.realpath(os.path.expanduser(path))
    return any(x == g or x.startswith(g + os.sep) for x in (p, p2) for g in GUARD_PATHS)


def simple_argv(cmd):
    # Deliberately no shell grammar, expansion, redirection, chaining or wrappers.
    if re.search(r"[\n\r\x00$`;&|<>()\\{}*?\[\]~^#]", cmd):
        return None
    try:
        return shlex.split(cmd, comments=False)
    except ValueError:
        return None


def safe_git(argv, cwd):
    if not argv or argv[0] != "git":
        return False
    if any(key.startswith("GIT_") for key in os.environ):
        return False
    args = argv[1:]
    if args[:1] == ["-C"] and len(args) >= 3:
        cwd = os.path.realpath(os.path.join(cwd, args[1]))
        if not in_repo(cwd):
            return False
        args = args[2:]
    options = {
        "diff": {"--cached", "--staged", "--stat", "--numstat", "--shortstat", "--summary", "--check",
                 "--name-only", "--name-status", "--no-color", "--color=never", "--no-ext-diff",
                 "--no-textconv", "--exit-code", "--quiet", "-w", "--ignore-space-at-eol"},
        "add": {"-A", "--all", "-u", "--update", "-n", "--dry-run", "-v", "--verbose", "-N", "--intent-to-add"},
        "status": {"--short", "-s", "--porcelain", "--porcelain=v1", "--porcelain=v2", "-b", "--branch", "-uno"},
        "ls-files": {"--stage", "-s", "--cached", "--modified", "--deleted", "--others", "--exclude-standard"},
    }
    if not args or args[0] not in options:
        return False
    verb, tail = args[0], args[1:]
    for arg in tail:
        if arg == "--":
            break
        if arg.startswith("-") and arg not in options[verb]:
            return False
    if verb == "diff":
        if any(k in os.environ for k in ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_CONFIG_COUNT",
                                         "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")):
            return False
        configured = git_read(cwd, "config", "--get-regexp", r"^diff\.(external|.*\.(command|textconv))$")
        if configured.returncode not in (0, 1) or configured.stdout.strip():
            return False
    return True


def absolute(path, cwd):
    return os.path.realpath(os.path.join(cwd, path))


def reviewed_root(path, cwd, relative):
    raw = os.path.join(cwd, path)
    resolved = absolute(path, cwd)
    root = workspace_root(resolved)
    if not root or os.path.islink(raw) or resolved != os.path.join(root, relative):
        return None
    try:
        with open(resolved, "rb") as source:
            if hashlib.sha256(source.read()).hexdigest() == PORTABLE_FILES[relative]:
                return root
    except OSError:
        pass
    return None


def private_config(path, cwd, root):
    raw = os.path.join(cwd, path)
    if os.path.islink(raw) or absolute(path, cwd) != os.path.join(root, ".env.selfhost"):
        return False
    try:
        info = os.stat(raw)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 8192:
            return False
        with open(raw) as source:
            lines = source.read().splitlines()
        config = {}
        for line in lines:
            if not line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key in config:
                return False
            config[key] = value
        secrets = {"MIGRATOR_PASSWORD", "APP_RW_PASSWORD", "APP_WORKER_PASSWORD", "APP_REVIEW_PASSWORD", "SESSION_SECRET"}
        if set(config) != secrets | {"VERWALINO_MODE", "VERWALINO_DOMAIN", "VERWALINO_PORT", "VERWALINO_IMAGE", "VERWALINO_PROXY_SUBNET"}:
            return False
        if any(not re.fullmatch(r"[a-f0-9]{64}", config[key]) for key in secrets):
            return False
        port = config["VERWALINO_PORT"]
        if not re.fullmatch(r"[1-9][0-9]{3,4}", port) or not 1024 <= int(port) <= 65535 or int(port) in (8000, 5432, 55432):
            return False
        image = config["VERWALINO_IMAGE"]
        if image != "verwalino:local" and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[a-f0-9]{64}", image):
            return False
        subnet = ipaddress.IPv4Network(config["VERWALINO_PROXY_SUBNET"], strict=True)
        private = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        if not 24 <= subnet.prefixlen <= 28 or not any(subnet.subnet_of(ipaddress.IPv4Network(net)) for net in private):
            return False
        mode, domain = config["VERWALINO_MODE"], config["VERWALINO_DOMAIN"]
        if mode == "local":
            if domain:
                return False
        elif mode == "https":
            if len(domain) > 253 or "." not in domain or not re.search(r"[a-z]", domain.rsplit(".", 1)[-1]):
                return False
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")):
                return False
        else:
            return False
        config.update(VERWALINO_ALLOWED_HOSTS=(domain + "," if mode == "https" else "") + "localhost,127.0.0.1",
                      VERWALINO_COOKIE_SECURE="true" if mode == "https" else "false",
                      VERWALINO_COOKIE_NAME="__Host-hv_session" if mode == "https" else "hv_session")
        if all(key not in os.environ or os.environ[key] == value for key, value in config.items()):
            return config
        return False
    except (OSError, ValueError, UnicodeError):
        return False


def docker_environment_safe():
    return not any(key.startswith(("DOCKER_", "COMPOSE_")) for key in os.environ)


def compose_allowed(argv, cwd):
    if not argv or argv[:2] != ["docker", "compose"] or not docker_environment_safe():
        return False
    args, selected = argv[2:], {}
    aliases = {"-p": "project", "--project-name": "project", "-f": "file", "--file": "file",
               "--env-file": "env", "--profile": "profile"}
    while args and args[0].startswith("-"):
        option, equal, value = args[0].partition("=")
        key = aliases.get(option)
        if not key or (equal and not option.startswith("--")):
            return False
        if not equal:
            if len(args) < 2 or args[1].startswith("-"):
                return False
            value, args = args[1], args[2:]
        else:
            args = args[1:]
        if not value or (key in selected and key != "profile"):
            return False
        selected.setdefault(key, []).append(value)
    if "file" not in selected or not args:
        return False
    file = selected["file"][0]
    test_root = workspace_root(absolute(file, cwd))
    if test_root and absolute(file, cwd) == os.path.join(test_root, TEST_COMPOSE):
        overrides = set(aliases) | {"--project-directory", "--all-resources", "--compatibility", "--dry-run",
                                    "--ansi", "--parallel", "--progress", "--volumes"}
        return set(selected) == {"file"} and not any(
            a.partition("=")[0] in overrides or a.startswith(("-f", "-p")) or
            (args[0] in ("down", "rm") and a.startswith("-") and not a.startswith("--") and "v" in a)
            for a in args[1:])
    root = reviewed_root(file, cwd, "infra/docker-compose.yml")
    if not root or set(selected) != {"project", "env", "file", "profile"} or selected["project"] != ["verwalino"]:
        return False
    profiles = selected["profile"]
    config = private_config(selected["env"][0], cwd, root)
    if not config or sorted(profiles) != (["https", "ops"] if config["VERWALINO_MODE"] == "https" else ["ops"]):
        return False
    fixed = [
        ["build", "app"], ["pull", "app"], ["up", "-d", "--wait", "--wait-timeout", "120", "db"],
        ["stop", "app"], ["run", "--rm", "--no-deps", "-T", "migrate"],
        ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "app"], ["stop"], ["ps"],
    ]
    if "https" in profiles:
        fixed.append(["up", "-d", "--no-deps", "caddy"])
    if args in fixed:
        return True
    if args[:1] == ["logs"]:
        tail = args[1:]
        if tail[:1] == ["--tail"] and len(tail) >= 2 and re.fullmatch(r"[0-9]{1,4}", tail[1]):
            tail = tail[2:]
        return not tail or (len(tail) == 1 and tail[0] in ("app", "db", "migrate", "bootstrap", "caddy"))
    if args[:5] != ["run", "--rm", "--no-deps", "-T", "bootstrap"]:
        return False
    tail, admin = args[5:], {}
    while tail:
        option = tail[0]
        if option in admin or option not in ("--org", "--email", "--name", "--password-stdin"):
            return False
        if option == "--password-stdin":
            admin[option], tail = True, tail[1:]
        else:
            if len(tail) < 2 or not tail[1] or tail[1].startswith("-"):
                return False
            admin[option], tail = tail[1], tail[2:]
    return {"--org", "--email", "--password-stdin"} <= set(admin)


def caddy_allowed(argv, cwd):
    if not argv or argv[:2] != ["docker", "run"] or not docker_environment_safe():
        return False
    args, options = argv[2:], {}
    aliases = {"--network": "network", "--cap-drop": "cap-drop", "--security-opt": "security-opt",
               "--mount": "mount", "-v": "volume", "--volume": "volume", "-e": "env", "--env": "env"}
    while args and args[0].startswith("-"):
        option, equal, value = args[0].partition("=")
        if option in ("--rm", "--read-only") and not equal:
            key, value, args = option[2:], True, args[1:]
        else:
            key = aliases.get(option)
            if not key:
                return False
            if equal:
                args = args[1:]
            elif len(args) >= 2:
                value, args = args[1], args[2:]
            else:
                return False
        if key in options:
            return False
        options[key] = value
    expected = {"rm", "read-only", "network", "cap-drop", "security-opt", "env"}
    if set(options) not in (expected | {"mount"}, expected | {"volume"}):
        return False
    if (options["network"] != "none" or options["cap-drop"] != "ALL" or
            options["security-opt"] not in ("no-new-privileges", "no-new-privileges:true") or
            options["env"] != "VERWALINO_DOMAIN=validation.invalid"):
        return False
    if "mount" in options:
        parts = options["mount"].split(",")
        mount = {}
        for part in parts:
            key, _, value = part.partition("=")
            if key in mount:
                return False
            mount[key] = value
        if set(mount) != {"type", "src", "dst", "readonly"} or mount["type"] != "bind" or mount["dst"] != "/etc/caddy/Caddyfile" or mount["readonly"]:
            return False
        source = mount["src"]
    else:
        parts = options["volume"].split(":")
        if len(parts) != 3 or parts[1:] != ["/etc/caddy/Caddyfile", "ro"]:
            return False
        source = parts[0]
    return bool(reviewed_root(source, cwd, "infra/Caddyfile") and args == [
        "caddy:2.10.2-alpine", "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"])


def check_command(cmd, cwd):
    c = " ".join(cmd.split())
    argv = simple_argv(cmd)
    if safe_git(argv, cwd):
        return
    if re.match(r"\s*git\s+(?:(?:-C|-c)\s+\S+\s+)*(?:diff|add|status|ls-files)\b", cmd):
        deny("only simple local Git read/staging arguments are allowed; no shell syntax or external diff programs")
    if argv and argv[0] == "docker" and len(argv) > 1 and argv[1].startswith("-"):
        deny("Docker host, context and execution overrides are not supported")
    if any(n in c for n in PROD_NAMES):
        deny("the production stack (containers hausverwaltung-db-1/-app-1, their volumes, "
             "docker-compose.prod.yml) is off limits")
    if re.search(r"(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):8000\b", c):
        deny("port 8000 is the production app; use the local dev API on :18000")
    if re.search(r"\bdocker[- ]compose\b|\bdocker\s+compose\b", c):
        if not compose_allowed(argv, cwd):
            deny("only the explicit test Compose file or reviewed verwalino installer operations with .env.selfhost are allowed")
    elif re.search(r"\bdocker\s+run\b", c):
        if not caddy_allowed(argv, cwd):
            deny("docker run is limited to isolated validation of the reviewed Caddyfile")
    elif re.search(r"\bdocker\s+(?:start|create)\b|\bdocker\s+container\s+(?:start|create|run)\b", c):
        deny("arbitrary container starts are not allowed")
    elif DOCKER_DESTRUCTIVE.search(c):
        deny("destructive docker commands are not allowed; only ps, logs, inspect and exec")
    if "migrations/versions" in c and re.search(
            r"\bsed\s+-i|\bperl\s+-[a-z]*i|>|\btee\b|\bmv\b|\brm\b|\bcp\b|"
            r"\bgit\s+(checkout|restore|reset|revert)\b|\btruncate\b|\bpython3?\s+-c\b", c):
        deny("committed migrations must not be changed from the shell; add a new migration instead")
    if any(g in c for g in ("/.coddy/hooks", ".coddy/hooks.json", "hv_guard.py")) and \
            re.search(r">|\bsed\s+-i|\btee\b|\bmv\b|\brm\b|\bcp\b|\bchmod\b|\btruncate\b", c):
        deny("the guard and the hooks file must not be changed by the agent")


def main():
    data = json.load(sys.stdin)
    cwd = data.get("cwd") or ""
    if not in_repo(cwd):
        return
    tool = data.get("tool_name") or ""
    args = data.get("tool_input") or {}
    if tool == "run_command":
        check_command(str(args.get("command") or ""), cwd)
    elif tool in ("edit", "write", "apply_patch"):
        path = str(args.get("path") or "")
        if path and committed_migration(path, cwd):
            deny("this migration is already committed and must not be edited; add a new migration instead")
        if path and guard_path(path, cwd):
            deny("the guard and the hooks file must not be changed by the agent")


if __name__ == "__main__":
    main()
