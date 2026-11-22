"""Review-worktree contracts: stdlib only, fake gh/uv/lsof on PATH, git on local repositories, no network.

Every run happens in a sandbox: a bare "origin", a clone standing in for the main checkout and, beside that
clone, the review worktrees. The real checkout, its siblings and the real gh are never reached.

Run from the repository root: python3 -B scripts/test-review-worktree.py
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/review-worktree.sh"
BASH = shutil.which("bash")
REPO = "itskeKs/verwalino"
BRANCH = "feat/thing"
UV_ARGS = ["sync", "--locked", "--extra", "dev", "--extra", "ops"]

# One source for every fake binary; the file name selects the tool. Each call is logged with its arguments and
# working directory, so the tests can say exactly what the script asked for.
FAKE_TOOL = r'''#!/usr/bin/env python3
import json, os, pathlib, re, sys
STATE = pathlib.Path("@STATE@")
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]

with open(STATE / "calls.jsonl", "a") as handle:
    handle.write(json.dumps({"tool": name, "args": args, "cwd": os.getcwd()}) + "\n")

def read(file, default=""):
    path = STATE / file
    return path.read_text() if path.exists() else default

if name == "gh":
    # pr view <n> -R <repo> --json <fields> --template <template>
    if args[:2] != ["pr", "view"] or args[3:4] != ["-R"] or "--json" not in args or "--template" not in args:
        sys.exit(97)
    pull_request = json.loads(read("pull-requests.json", "{}")).get(args[2])
    if pull_request is None or (STATE / "gh_fail").exists():
        print("fake gh: no pull request found", file=sys.stderr)
        sys.exit(1)
    template = args[args.index("--template") + 1]
    def render(value):
        return ("true" if value else "false") if isinstance(value, bool) else value
    sys.stdout.write(re.sub(r"\{\{\.(\w+)\}\}", lambda match: render(pull_request[match.group(1)]), template))
elif name == "uv":
    if (STATE / "uv_fail").exists():
        print("fake uv: resolution failed", file=sys.stderr)
        sys.exit(1)
    environment = pathlib.Path(".venv/bin")
    environment.mkdir(parents=True)
    (environment / "python").write_text("")
elif name == "lsof":
    # Only the form the script is meant to use: -w -F pc +D <directory>
    if args[:3] != ["-w", "-F", "pc"] or args[3:4] != ["+D"] or len(args) != 5:
        sys.exit(97)
    if (STATE / "lsof_exit").exists():
        sys.exit(int(read("lsof_exit")))
    target = os.path.realpath(args[4])
    found = [
        process for process in json.loads(read("processes.json", "[]"))
        if (os.path.realpath(process["dir"]) + os.sep).startswith(target + os.sep)
    ]
    sys.stdout.write("".join("p%s\nc%s\nfcwd\n" % (process["pid"], process["command"]) for process in found))
    # macOS lsof exits 1 even when +D found something; Linux lsof exits 0 then. The script must not depend on it.
    sys.exit(int(read("lsof_found_exit", "1")) if found else 1)
else:
    sys.exit(96)
'''


class Sandbox(unittest.TestCase):
    """A bare origin, a main checkout cloned from it, fake tools, and a way to run the script."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="verwalino-review-worktree-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.state = self.base / "state"
        self.state.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for tool in ("gh", "uv", "lsof"):
            path = self.bin / tool
            path.write_text(FAKE_TOOL.replace("@STATE@", str(self.state)))
            path.chmod(0o755)
        self.origin = self.base / "origin.git"
        self.checkout = self.base / "checkout"
        self.make_repositories()
        self.set_pull_requests({"42": {"state": "OPEN", "headRefName": BRANCH, "isCrossRepository": False}})

    # ---- fixtures ----------------------------------------------------------

    def env(self):
        return {
            **os.environ,
            # The fakes come first: the real gh, uv and lsof can never be reached.
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.base),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def git(self, repository, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            env=self.env(), text=True, capture_output=True, check=check, timeout=60,
        )

    def make_repositories(self):
        self.git(self.base, "init", "-q", "--bare", "-b", "main", str(self.origin))
        seed = self.base / "seed"
        seed.mkdir()
        self.git(seed, "init", "-q", "-b", "main")
        self.git(seed, "remote", "add", "origin", str(self.origin))
        for name, text in (
            (".gitignore", ".venv/\n"),
            ("README.md", "readme\n"),
            ("backend/pyproject.toml", "[project]\nname = 'fixture'\n"),
            ("scripts/test-env.sh", "export VERWALINO_TEST_DB=1\n"),
        ):
            (seed / name).parent.mkdir(parents=True, exist_ok=True)
            (seed / name).write_text(text)
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-q", "-m", "main")
        self.git(seed, "push", "-q", "origin", "main")
        self.git(seed, "checkout", "-q", "-b", BRANCH)
        (seed / "feature.txt").write_text("first\n")
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-q", "-m", "feature")
        self.git(seed, "push", "-q", "origin", BRANCH)
        self.seed = seed
        self.git(self.base, "clone", "-q", str(self.origin), str(self.checkout))
        self.install_script(self.checkout)

    def install_script(self, checkout):
        target = checkout / "scripts/review-worktree.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        if SCRIPT.exists():
            shutil.copyfile(SCRIPT, target)
        self.script = target

    def push_new_commit(self):
        """The PR branch moves on origin; the main checkout's remote-tracking ref is stale until a fetch."""
        (self.seed / "feature.txt").write_text("second\n")
        self.git(self.seed, "commit", "-q", "-am", "second")
        self.git(self.seed, "push", "-q", "origin", BRANCH)
        return self.git(self.seed, "rev-parse", "HEAD").stdout.strip()

    def set_pull_requests(self, pull_requests):
        (self.state / "pull-requests.json").write_text(json.dumps(pull_requests))

    def set_processes(self, processes):
        (self.state / "processes.json").write_text(json.dumps(processes))

    def flag(self, name, value=""):
        (self.state / name).write_text(value)

    # ---- running and observing ---------------------------------------------

    def run_script(self, *args, script=None, cwd=None):
        return subprocess.run(
            [BASH, str(script or self.script), *args],
            cwd=cwd or self.checkout, env=self.env(), text=True, capture_output=True, timeout=120,
        )

    def calls(self, tool=None):
        path = self.state / "calls.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        calls = [json.loads(line) for line in lines]
        return [call for call in calls if tool in (None, call["tool"])]

    def review_dir(self, pr=42):
        return self.base / f"verwalino-review-{pr}"

    def registered_worktrees(self):
        listing = self.git(self.checkout, "worktree", "list", "--porcelain").stdout.splitlines()
        return [line.split(" ", 1)[1] for line in listing if line.startswith("worktree ")]

    def create(self, pr="42", **kwargs):
        return self.run_script("create", pr, **kwargs)

    def remove(self, pr="42", **kwargs):
        return self.run_script("remove", pr, **kwargs)

    def create_ok(self, pr="42"):
        result = self.create(pr)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def assertNothingChanged(self, pr="42"):
        self.assertFalse(self.review_dir(pr).exists())
        self.assertEqual(self.registered_worktrees(), [str(self.checkout)])


class CreateTests(Sandbox):
    def test_create_makes_a_detached_worktree_on_the_fetched_head_of_the_branch(self):
        stale = self.git(self.checkout, "rev-parse", f"origin/{BRANCH}").stdout.strip()
        tip = self.push_new_commit()
        self.assertNotEqual(stale, tip)
        self.create_ok()
        review = self.review_dir()
        self.assertEqual(self.git(review, "rev-parse", "HEAD").stdout.strip(), tip)
        self.assertNotEqual(self.git(review, "symbolic-ref", "-q", "HEAD", check=False).returncode, 0, "detached")
        self.assertIn(str(review), self.registered_worktrees())
        self.assertEqual(self.git(review, "status", "--porcelain").stdout, "", "clean, .venv is ignored")
        self.assertEqual((review / "feature.txt").read_text(), "second\n")

    def test_create_asks_github_once_for_the_state_origin_and_head_branch_of_that_pr(self):
        self.create_ok()
        self.assertEqual(
            [call["args"] for call in self.calls("gh")],
            [["pr", "view", "42", "-R", REPO, "--json", "state,isCrossRepository,headRefName", "--template", "{{.state}} {{.isCrossRepository}} {{.headRefName}}"]],
        )

    def test_create_installs_the_locked_backend_extras_in_the_review_backend(self):
        self.create_ok()
        (call,) = self.calls("uv")
        self.assertEqual(call["args"], UV_ARGS)
        self.assertEqual(call["cwd"], str(self.review_dir() / "backend"))
        self.assertTrue((self.review_dir() / "backend/.venv/bin/python").exists())

    def test_create_prints_the_directory_and_the_line_that_loads_the_test_environment(self):
        result = self.create_ok()
        review = self.review_dir()
        self.assertIn(str(review), result.stdout)
        line = f"cd {review} && source scripts/test-env.sh"
        self.assertEqual(sum(text.endswith(line) for text in result.stdout.splitlines()), 1)

    def test_a_second_create_is_refused_and_leaves_the_first_worktree_alone(self):
        self.create_ok()
        review = self.review_dir()
        (review / "backend/marker").write_text("first run")
        head = self.git(review, "rev-parse", "HEAD").stdout.strip()
        uv_calls, gh_calls = len(self.calls("uv")), len(self.calls("gh"))

        result = self.create()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(review), result.stderr)
        self.assertIn("under review", result.stderr)
        self.assertEqual((review / "backend/marker").read_text(), "first run")
        self.assertEqual(self.git(review, "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual((len(self.calls("uv")), len(self.calls("gh"))), (uv_calls, gh_calls))
        self.assertEqual(self.registered_worktrees().count(str(review)), 1)

    def test_a_plain_directory_at_the_target_path_also_refuses_create(self):
        self.review_dir().mkdir()
        result = self.create()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exists", result.stderr)
        self.assertEqual(self.calls("uv"), [])

    def test_create_refuses_a_pr_that_is_not_open(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                self.set_pull_requests({"42": {"state": state, "headRefName": BRANCH, "isCrossRepository": False}})
                result = self.create()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(state, result.stderr)
                self.assertIn("not open", result.stderr)
                self.assertNothingChanged()
        self.assertEqual(self.calls("uv"), [])

    def test_create_refuses_a_pr_from_a_fork_before_any_fetch(self):
        # The fork's head branch has the name of a branch that exists on origin: without the refusal the
        # worktree would be built on that unrelated branch and the script would report success.
        self.set_pull_requests({"42": {"state": "OPEN", "headRefName": BRANCH, "isCrossRepository": True}})
        tip = self.push_new_commit()
        tracked = self.git(self.checkout, "rev-parse", f"origin/{BRANCH}").stdout.strip()
        self.assertNotEqual(tracked, tip)

        result = self.create()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PR #42", result.stderr)
        self.assertIn("from a fork", result.stderr)
        self.assertIn("no review worktree was made", result.stderr)
        self.assertNothingChanged()
        self.assertEqual(self.git(self.checkout, "rev-parse", f"origin/{BRANCH}").stdout.strip(), tracked, "no fetch ran")
        self.assertEqual(self.calls("uv"), [])

    def test_create_refuses_a_pr_github_does_not_know(self):
        result = self.create("43")
        self.assertNotEqual(result.returncode, 0)
        self.assertNothingChanged("43")
        self.assertEqual(self.calls("uv"), [])

    def test_create_refuses_when_github_cannot_be_asked(self):
        self.flag("gh_fail")
        result = self.create()
        self.assertNotEqual(result.returncode, 0)
        self.assertNothingChanged()
        self.assertEqual(self.calls("uv"), [])

    def test_create_refuses_a_branch_that_is_gone_from_origin(self):
        self.set_pull_requests({"42": {"state": "OPEN", "headRefName": "feat/deleted", "isCrossRepository": False}})
        result = self.create()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("feat/deleted", result.stderr)
        self.assertNothingChanged()
        self.assertEqual(self.calls("uv"), [])

    def test_a_failed_install_removes_the_worktree_it_just_made(self):
        self.flag("uv_fail")
        result = self.create()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fake uv: resolution failed", result.stderr)
        self.assertNothingChanged()
        (self.state / "uv_fail").unlink()
        self.create_ok()

    def test_create_from_a_linked_worktree_lands_beside_the_main_checkout(self):
        linked = self.checkout / ".claude/worktrees/session"
        self.git(self.checkout, "worktree", "add", "-q", "--detach", str(linked), "origin/main")
        self.install_script(linked)
        result = self.create(script=linked / "scripts/review-worktree.sh", cwd=linked)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.review_dir().is_dir())
        self.assertFalse((linked.parent / "verwalino-review-42").exists())
        self.assertIn(str(self.review_dir()), self.registered_worktrees())

    def test_create_takes_no_test_database_lock_and_runs_no_tests(self):
        text = SCRIPT.read_text()
        for forbidden in ("testdb.lock", "pytest", "docker", "check-testdb", "mkdir /tmp"):
            self.assertNotIn(forbidden, text)
        self.create_ok()
        self.assertEqual({call["tool"] for call in self.calls()}, {"gh", "uv"})


class RemoveTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.create_ok()
        self.review = self.review_dir()
        self.calls_before = len(self.calls())

    def assertKept(self):
        self.assertTrue(self.review.is_dir())
        self.assertIn(str(self.review), self.registered_worktrees())

    def test_remove_deletes_a_clean_worktree_and_its_registration(self):
        self.assertTrue((self.review / "backend/.venv").is_dir())
        result = self.remove()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.review), result.stdout)
        self.assertNothingChanged()

    def test_remove_works_for_a_pr_that_is_no_longer_open_and_never_asks_github(self):
        self.set_pull_requests({"42": {"state": "MERGED", "headRefName": BRANCH, "isCrossRepository": False}})
        self.flag("gh_fail")
        self.assertEqual(self.remove().returncode, 0)
        self.assertEqual([call["tool"] for call in self.calls()[self.calls_before:]], ["lsof"])

    def test_remove_asks_lsof_about_exactly_that_directory(self):
        self.remove()
        (call,) = self.calls("lsof")
        self.assertEqual(call["args"], ["-w", "-F", "pc", "+D", str(self.review)])

    def test_remove_refuses_a_modified_tracked_file_and_names_it(self):
        (self.review / "README.md").write_text("edited\n")
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("README.md", result.stderr)
        self.assertKept()
        self.assertEqual((self.review / "README.md").read_text(), "edited\n")

    def test_remove_refuses_an_untracked_file_and_names_it(self):
        (self.review / "notes.txt").write_text("keep me")
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("notes.txt", result.stderr)
        self.assertKept()
        self.assertEqual((self.review / "notes.txt").read_text(), "keep me")

    def test_remove_refuses_while_a_process_runs_in_it_and_names_the_process(self):
        self.set_processes([{"pid": 4242, "command": "pytest", "dir": str(self.review / "backend")}])
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("4242", result.stderr)
        self.assertIn("pytest", result.stderr)
        self.assertKept()

    def test_remove_refuses_a_process_whatever_exit_status_lsof_reports(self):
        self.set_processes([{"pid": 4242, "command": "pytest", "dir": str(self.review / "backend")}])
        for status in ("0", "1"):
            with self.subTest(lsof_exit=status):
                self.flag("lsof_found_exit", status)
                result = self.remove()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("pid 4242 pytest", result.stderr)
                self.assertKept()

    def test_remove_ignores_a_process_that_runs_somewhere_else(self):
        self.set_processes([
            {"pid": 7, "command": "pytest", "dir": str(self.checkout)},
            {"pid": 8, "command": "vite", "dir": str(self.base / "verwalino-review-420")},
        ])
        result = self.remove()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNothingChanged()

    def test_remove_names_every_reason_when_dirty_and_in_use(self):
        (self.review / "README.md").write_text("edited\n")
        self.set_processes([{"pid": 4242, "command": "pytest", "dir": str(self.review)}])
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("README.md", result.stderr)
        self.assertIn("4242", result.stderr)
        self.assertKept()

    def test_remove_refuses_when_it_cannot_tell_whether_a_process_uses_it(self):
        self.flag("lsof_exit", "127")
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lsof", result.stderr)
        self.assertKept()

    def test_remove_refuses_a_pr_without_a_review_worktree(self):
        result = self.remove("43")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("43", result.stderr)
        self.assertIn(str(self.review_dir(43)), result.stderr)
        self.assertEqual(self.calls("lsof"), [])

    def test_remove_leaves_a_directory_that_is_not_a_worktree_of_this_repository(self):
        stranger = self.review_dir(43)
        stranger.mkdir()
        (stranger / "file").write_text("mine")
        result = self.remove("43")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a worktree", result.stderr)
        self.assertEqual((stranger / "file").read_text(), "mine")
        self.assertEqual(self.calls("lsof"), [])

    def test_create_works_again_after_remove(self):
        self.assertEqual(self.remove().returncode, 0)
        self.create_ok()
        self.assertKept()


class ArgumentTests(Sandbox):
    def test_a_pr_must_be_a_positive_number(self):
        for command in ("create", "remove"):
            for bad in ("", "abc", "0", "-1", "007", "12x", "1 2", "../42", "4$(touch x)"):
                with self.subTest(command=command, pr=bad):
                    result = self.run_script(command, bad)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("usage:", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertNothingChanged()

    def test_the_subcommand_and_argument_count_are_checked(self):
        for args in ((), ("create",), ("remove",), ("prepare", "42"), ("create", "42", "43"), ("remove", "42", "--force")):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage:", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertNothingChanged()


class WiringTests(unittest.TestCase):
    """Makefile targets, help text, CI step and the pages that list them."""

    def make(self, *args):
        sandbox = tempfile.TemporaryDirectory(prefix="verwalino-review-worktree-make-")
        self.addCleanup(sandbox.cleanup)
        bin_dir = Path(sandbox.name) / "bin"
        bin_dir.mkdir()
        for tool in ("gh", "uv", "lsof"):  # whatever the script were to call, it is not the real tool
            (bin_dir / tool).write_text("#!/bin/sh\nexit 97\n")
            (bin_dir / tool).chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(["make", *args], cwd=ROOT, env=env, text=True, capture_output=True, timeout=60)

    def test_make_targets_call_the_script_with_the_pr(self):
        for target, command in (("review-worktree", "create"), ("review-worktree-remove", "remove")):
            with self.subTest(target=target):
                result = self.make("-n", target, "PR=42")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'scripts/review-worktree.sh {command} "42"', result.stdout)

    def test_make_targets_refuse_a_missing_pr_before_touching_anything(self):
        for target in ("review-worktree", "review-worktree-remove"):
            with self.subTest(target=target):
                result = self.make(target)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("usage:", result.stderr)

    def test_make_help_lists_both_targets(self):
        result = self.make("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for target in ("review-worktree ", "review-worktree-remove ", "test-review-worktree "):
            self.assertIn(f"  {target}", result.stdout)

    def test_the_contract_runs_in_make_test_and_in_ci(self):
        makefile = (ROOT / "Makefile").read_text()
        test_line = next(line for line in makefile.splitlines() if line.startswith("test:"))
        self.assertIn("test-review-worktree", test_line.split("##")[0].split())
        self.assertIn("python3 -B scripts/test-review-worktree.py", makefile)
        workflow = (ROOT / ".github/workflows/tests-on-pr.yaml").read_text()
        self.assertIn("run: make test-review-worktree", workflow)

    def test_agents_and_contributing_name_both_targets(self):
        agents = (ROOT / "AGENTS.md").read_text()
        contributing = (ROOT / "CONTRIBUTING.md").read_text()
        for page in (agents, contributing):
            self.assertIn("`make review-worktree PR=<n>`", page)
            self.assertIn("`make review-worktree-remove PR=<n>`", page)
        self.assertIn("make test-review-worktree", contributing)
        self.assertIn("`scripts/review-worktree.sh`", contributing)


if __name__ == "__main__":
    unittest.main()
