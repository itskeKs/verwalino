"""Merge script contracts: stdlib only; gh and lsof are faked, git runs against local repositories, no network.

Run from the repository root: python3 -B scripts/test-merge-pr.py
"""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest

from installer_test_process import install_interrupt_handlers, managed_process, run, unittest_main

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/merge-pr.sh"
BASH = shutil.which("bash")
REPO = "itskeKs/verwalino"
# The script keeps its fixed locations and limits in these constants; the sandbox rewrites exactly these lines.
CONSTANTS = ("MAINTAINER_LOCK", "MERGE_LOCK", "SCRATCH_PREFIX",
             "LOCK_WAIT_SECONDS", "LOCK_POLL_SECONDS", "CI_WAIT_SECONDS", "CI_POLL_SECONDS")
SANDBOX_LIMITS = {"LOCK_WAIT_SECONDS": "10", "LOCK_POLL_SECONDS": "0.05", "CI_WAIT_SECONDS": "20", "CI_POLL_SECONDS": "0.05"}
NOTE = "Merged as is.\n\n- Checked and fine: everything.\n\nCI green on the reviewed commit; user check none.\n"
ISSUE_BODY = """## What

- [ ] a task list item that is not an acceptance box

## Acceptance

- [ ] the first box
- [x] a box that was ticked already
- [ ] the second box, with `make test` and the German text „Rückzahlung“

## Out of scope

- [ ] a bullet in another section
"""
ISSUE_BODY_TICKED = ISSUE_BODY.replace("- [ ] the first box", "- [x] the first box").replace(
    "- [ ] the second box", "- [x] the second box")

PASSED = [
    {"__typename": "CheckRun", "name": "Backend shard 1/4", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"__typename": "CheckRun", "name": "Website", "status": "COMPLETED", "conclusion": "SKIPPED"},
    {"__typename": "StatusContext", "context": "legacy/status", "state": "SUCCESS"},
]
RUNNING = [dict(PASSED[0], status="IN_PROGRESS", conclusion=None), PASSED[1]]
FAILED = [PASSED[0], dict(PASSED[1], name="Frontend", conclusion="FAILURE")]

# Stands in for gh. The state of "GitHub" is one JSON file; the head of a PR is the live tip of its branch in the
# local bare repository, so what the script pushes is what the next call sees. The file name selects nothing:
# only the calls the script is meant to make are served, every other one fails loudly.
FAKE_GH = r'''#!/usr/bin/env python3
import json, pathlib, subprocess, sys
BASE = pathlib.Path("@BASE@")
STATE_FILE = BASE / "state" / "github.json"
ORIGIN = BASE / "origin.git"
args = sys.argv[1:]
state = json.loads(STATE_FILE.read_text())
entry = {"args": args}


def save():
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(STATE_FILE)
    with open(BASE / "state" / "calls.jsonl", "a") as handle:
        handle.write(json.dumps(entry) + "\n")


def git(*a, cwd=None):
    done = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        sys.exit("fake gh: git %s failed: %s" % (" ".join(a), done.stderr))
    return done.stdout.strip()


def fail(message, code=1):
    save()
    print(message, file=sys.stderr)
    sys.exit(code)


def value(name):
    return args[args.index(name) + 1] if name in args else None


def tip(branch):
    done = subprocess.run(["git", "--git-dir", str(ORIGIN), "rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
                          capture_output=True, text=True)
    return done.stdout.strip() or None


def clone():
    path = BASE / "gh-clone"
    if not path.exists():
        git("clone", "-q", str(ORIGIN), str(path))
    git("fetch", "-q", "origin", cwd=path)
    return path


def event(kind):
    if kind["do"] == "push":
        path = clone()
        git("checkout", "-q", "-B", kind["branch"], "origin/" + kind["branch"], cwd=path)
        (path / "late.txt").write_text("pushed during the wait\n")
        git("add", "late.txt", cwd=path)
        git("commit", "-q", "-m", "late commit", cwd=path)
        git("push", "-q", "origin", kind["branch"], cwd=path)
    elif kind["do"] == "open-dependent":
        state["prs"]["9"] = {"state": "OPEN", "base": kind["branch"], "branch": "feat/next", "closing": [], "merge_commit": None}


if value("-R") != "@REPO@":
    fail("fake gh: every call needs -R @REPO@", 99)
area, verb = args[0], args[1]
number = args[2] if len(args) > 2 and not args[2].startswith("-") else None
stdin = sys.stdin.read() if (area, verb) == ("issue", "edit") and value("--body-file") == "-" else ""
if stdin:
    entry["stdin"] = stdin

if (area, verb) == ("pr", "view"):
    pr = state["prs"][number]
    fields = value("--json").split(",")
    if "statusCheckRollup" in fields:
        state["rollup_calls"] += 1
        for planned in state.get("events", []):
            if planned["on_rollup_call"] == state["rollup_calls"]:
                event(planned)
    head = tip(pr["branch"])
    lag = state.get("lag")
    if lag and "statusCheckRollup" in fields and state["rollup_calls"] <= lag["calls"]:
        head = lag["head"]
    rollups = state["rollups"]
    available = {
        "state": pr["state"],
        "baseRefName": pr["base"],
        "headRefName": pr["branch"],
        "headRefOid": head,
        "closingIssuesReferences": [
            {"number": n, "url": "https://github.com/@REPO@/issues/%d" % n,
             "repository": {"name": "@REPO@".split("/")[1], "owner": {"login": "@REPO@".split("/")[0]}}}
            for n in pr["closing"]] + state.get("foreign_closing", []),
        "mergeCommit": {"oid": pr["merge_commit"]} if pr["merge_commit"] else None,
        "statusCheckRollup": rollups[min(state["rollup_calls"] - 1, len(rollups) - 1)] if state["rollup_calls"] else [],
    }
    print(json.dumps({field: available[field] for field in fields}))
elif (area, verb) == ("pr", "list"):
    assert value("--state") == "open" and value("--json") == "number", args
    print(json.dumps([{"number": int(n)} for n, pr in state["prs"].items()
                      if pr["state"] == "OPEN" and pr["base"] == value("--base")]))
elif (area, verb) == ("pr", "comment"):
    path = value("--body-file")
    state.setdefault("comments", []).append(pathlib.Path(path).read_text())
elif (area, verb) == ("pr", "merge"):
    for forbidden in ("--squash", "--rebase", "--admin", "--auto", "--disable-auto"):
        if forbidden in args:
            fail("fake gh: %s must never be used" % forbidden, 97)
    if "--merge" not in args or "--delete-branch" not in args:
        fail("fake gh: the merge needs --merge and --delete-branch", 97)
    pr = state["prs"][number]
    refs = subprocess.run(["git", "--git-dir", str(ORIGIN), "for-each-ref", "--format=%(refname) %(objectname)"],
                          capture_output=True, text=True).stdout
    entry["refs"] = refs
    if state.get("merge_error"):
        fail(state["merge_error"])
    if value("--match-head-commit") != tip(pr["branch"]):
        fail("Head branch was modified. Review and try the merge again.")
    path = clone()
    git("checkout", "-q", "-B", "main", "origin/main", cwd=path)
    git("merge", "--no-ff", "-q", "-m", "Merge pull request #%s from @REPO@" % number, "origin/" + pr["branch"], cwd=path)
    git("push", "-q", "origin", "main", cwd=path)
    pr["merge_commit"] = git("rev-parse", "HEAD", cwd=path)
    pr["state"] = "MERGED"
    if not state.get("keep_branch"):
        git("push", "-q", "origin", "--delete", pr["branch"], cwd=path)
    if state.get("auto_close"):
        for n in pr["closing"]:
            state["issues"][str(n)]["state"] = "CLOSED"
elif (area, verb) == ("issue", "view"):
    issue = state["issues"][number]
    print(json.dumps({field: issue[field] for field in value("--json").split(",")}))
elif (area, verb) == ("issue", "edit"):
    if value("--body-file") != "-":
        fail("fake gh: the edit must read the body from stdin", 97)
    state["issues"][number]["body"] = stdin
elif (area, verb) == ("issue", "close"):
    state["issues"][number]["state"] = "CLOSED"
else:
    fail("fake gh: unexpected call %s" % " ".join(args), 98)
save()
'''

# A process list as lsof -d cwd -Fn prints it, plus whatever a test adds.
FAKE_LSOF = r'''#!/bin/sh
printf 'p1\nfcwd\nn/\n'
if [ -f "@BASE@/state/lsof_extra" ]; then cat "@BASE@/state/lsof_extra"; fi
'''


def git_environment(home):
    """Git that knows nothing of the developer's configuration: no signing, no hooks path, no identity of theirs."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(HOME=str(home), GIT_TERMINAL_PROMPT="0", LC_ALL="C", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return env


def git_in(repo, *args, home):
    return run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
               env=git_environment(home)).stdout.strip()


# The repositories every test starts from are built once and copied: origin (bare), the clone that pushes to it as
# somebody else would (seed) and the main checkout. Branch feat/thing is one commit ahead of main.
TEMPLATE = {}


def setUpModule():
    directory = tempfile.TemporaryDirectory()
    TEMPLATE["directory"] = directory
    base = Path(directory.name).resolve()
    env = git_environment(base)
    # No hook samples and no template files: fewer files to create and copy for every test.
    run(["git", "init", "-q", "--template=", "--bare", "-b", "main", str(base / "origin.git")], check=True, env=env)
    run(["git", "clone", "-q", "--template=", str(base / "origin.git"), str(base / "seed")], check=True,
        capture_output=True, env=env)
    seed = base / "seed"

    def commit(name, text):
        (seed / name).write_text(text)
        git_in(seed, "add", name, home=base)
        git_in(seed, "commit", "-q", "-m", f"add {name}", home=base)
        return git_in(seed, "rev-parse", "HEAD", home=base)

    git_in(seed, "checkout", "-q", "-b", "main", home=base)
    commit("app.txt", "base\n")
    git_in(seed, "push", "-q", "origin", "main", home=base)
    git_in(seed, "checkout", "-q", "-b", "feat/thing", home=base)
    TEMPLATE["reviewed"] = commit("feature.txt", "feature\n")
    git_in(seed, "push", "-q", "origin", "feat/thing", home=base)
    git_in(seed, "checkout", "-q", "main", home=base)
    run(["git", "clone", "-q", "--template=", str(base / "origin.git"), str(base / "checkout")], check=True,
        capture_output=True, env=env)
    TEMPLATE["path"] = base


def tearDownModule():
    TEMPLATE["directory"].cleanup()


class MergePrCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.state_dir = self.base / "state"
        self.state_dir.mkdir()
        self.maintainer_lock = self.base / "maintainer.lock"
        self.merge_lock = self.base / "merge.lock"
        self.scratch_prefix = self.base / "scratch"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name, text in (("gh", FAKE_GH), ("lsof", FAKE_LSOF)):
            (self.bin / name).write_text(text.replace("@BASE@", str(self.base)).replace("@REPO@", REPO))
            (self.bin / name).chmod(0o755)
        self.note = self.base / "note.md"
        self.note.write_text(NOTE)
        self.build_repositories()
        self.install_script()
        self.write_state({
            "prs": {"7": {"state": "OPEN", "base": "main", "branch": "feat/thing", "closing": [12], "merge_commit": None}},
            "issues": {"12": {"state": "OPEN", "body": ISSUE_BODY}},
            "rollups": [PASSED], "rollup_calls": 0, "comments": [], "events": [],
        })
        self.hold_maintainer_lock()

    # ---- fixture ---------------------------------------------------------

    def environment(self):
        return dict(git_environment(self.base), PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")

    def git(self, repo, *args, check=True):
        result = run(["git", "-C", str(repo), *args], check=check, capture_output=True, text=True, env=self.environment())
        return result.stdout.strip() if check else result

    def commit(self, repo, name, text, branch=None):
        if branch:
            self.git(repo, "checkout", "-q", branch)
        (Path(repo) / name).write_text(text)
        self.git(repo, "add", name)
        self.git(repo, "commit", "-q", "-m", f"add {name}")
        return self.git(repo, "rev-parse", "HEAD")

    def build_repositories(self):
        shutil.copytree(TEMPLATE["path"], self.base, symlinks=True, dirs_exist_ok=True)
        origin = self.base / "origin.git"
        self.seed, self.checkout = self.base / "seed", self.base / "checkout"
        self.reviewed = TEMPLATE["reviewed"]
        for clone in (self.seed, self.checkout):
            self.git(clone, "remote", "set-url", "origin", str(origin))
        self.worktree = self.base / "verwalino-issue-12"
        self.git(self.checkout, "worktree", "add", "-q", "-b", "feat/thing", str(self.worktree), "origin/feat/thing")

    def install_script(self, **limits):
        text = SCRIPT.read_text()
        values = {"MAINTAINER_LOCK": self.maintainer_lock, "MERGE_LOCK": self.merge_lock,
                  "SCRATCH_PREFIX": self.scratch_prefix, **SANDBOX_LIMITS, **limits}
        for name in CONSTANTS:
            text, replaced = re.subn(rf"^{name}=.*$", lambda _: f"{name}={shlex.quote(str(values[name]))}", text, flags=re.M)
            self.assertEqual(replaced, 1, name)
        self.script_path = self.checkout / "scripts/merge-pr.sh"
        self.script_path.parent.mkdir(exist_ok=True)
        self.script_path.write_text(text)
        # A copy of the script is no change to the tree it sits in.
        exclude = self.checkout / ".git/info/exclude"
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text("scripts/\n")

    def hold_maintainer_lock(self, info="7 2026-11-16T12:00:00Z\n"):
        self.maintainer_lock.mkdir()
        (self.maintainer_lock / "info").write_text(info)

    def write_state(self, state):
        (self.state_dir / "github.json").write_text(json.dumps(state))

    def github(self):
        return json.loads((self.state_dir / "github.json").read_text())

    def change_github(self, **changes):
        state = self.github()
        state.update(changes)
        self.write_state(state)

    def calls(self):
        path = self.state_dir / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def kinds(self):
        return [" ".join(call["args"][:2]) for call in self.calls()]

    def calls_of(self, kind):
        return [call for call in self.calls() if " ".join(call["args"][:2]) == kind]

    def ci_reads(self):
        """Positions among all calls of the reads that carry the checks of the PR."""
        return [i for i, call in enumerate(self.calls()) if call["args"][:2] == ["pr", "view"]
                and "statusCheckRollup" in call["args"][-1]]

    def wait_for(self, condition, seconds=20):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(condition())

    # ---- origin ----------------------------------------------------------

    def origin(self, *args):
        return self.git(self.base / "origin.git", *args)

    def origin_tip(self, branch):
        return self.origin("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")

    def origin_refs(self):
        return self.origin("for-each-ref", "--format=%(refname) %(objectname)")

    def advance_main(self, name="other.txt", text="more\n"):
        sha = self.commit(self.seed, name, text, branch="main")
        self.git(self.seed, "push", "-q", "origin", "main")
        return sha

    def push_to_branch(self, name="after-review.txt"):
        sha = self.commit(self.seed, name, "pushed after the review\n", branch="feat/thing")
        self.git(self.seed, "push", "-q", "origin", "feat/thing")
        return sha

    # ---- running ---------------------------------------------------------

    def arguments(self, pr="7", reviewed=None, note=None):
        return [pr, "--reviewed", reviewed or self.reviewed, "--note-file", str(note or self.note)]

    def script(self, *args, timeout=120):
        return run([BASH, str(self.script_path), *args], cwd=self.base, env=self.environment(),
                   capture_output=True, text=True, timeout=timeout)

    def merge(self, **kwargs):
        return self.script(*self.arguments(**kwargs))

    def scratch_dirs(self):
        return sorted(self.base.glob("scratch.*"))

    def registered_worktrees(self):
        return [line.split(" ", 1)[1] for line in self.git(self.checkout, "worktree", "list", "--porcelain").splitlines()
                if line.startswith("worktree ")]

    # ---- assertions ------------------------------------------------------

    def assertMerged(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.github()["prs"]["7"]["state"], "MERGED")
        self.assertIn(self.github()["prs"]["7"]["merge_commit"], result.stdout)

    def assertRefused(self, result, *fragments, posted=False, attempted=False, state="OPEN"):
        """A refusal: exit 1 with the reason, nothing merged, no lock and no scratch directory left of the run."""
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for fragment in fragments:
            self.assertIn(fragment, result.stderr)
        self.assertEqual(self.github()["prs"]["7"]["state"], state)
        self.assertEqual(self.calls_of("issue edit") + self.calls_of("issue close"), [])
        self.assertEqual(len(self.calls_of("pr merge")), 1 if attempted else 0)
        self.assertEqual(len(self.calls_of("pr comment")), 1 if posted else 0)
        self.assertNothingLeft()

    def assertNothingLeft(self):
        self.assertFalse(self.merge_lock.exists())
        self.assertEqual(self.scratch_dirs(), [])
        self.assertEqual(self.registered_worktrees(), [str(self.checkout), str(self.worktree)])
        # The maintainer lock belongs to the caller: never taken, changed or released.
        self.assertEqual((self.maintainer_lock / "info").read_text(), "7 2026-11-16T12:00:00Z\n")


class MergeTests(MergePrCase):
    def test_merges_a_green_pr_with_the_nine_steps_in_order_and_prints_the_merge_commit(self):
        refs_before = self.origin_refs()
        main_before = self.origin_tip("main")
        result = self.merge()
        self.assertMerged(result)
        merge_commit = self.github()["prs"]["7"]["merge_commit"]
        self.assertEqual(self.origin_tip("main"), merge_commit)
        self.assertEqual(self.origin("rev-list", "--parents", "-n1", merge_commit).split()[1:], [main_before, self.reviewed])
        kinds = self.kinds()
        # The CI reads come before the note, the note before the merge, the issue is handled after the merge.
        self.assertTrue(self.ci_reads())
        self.assertLess(max(self.ci_reads()), kinds.index("pr comment"))
        self.assertLess(kinds.index("pr comment"), kinds.index("pr merge"))
        self.assertLess(kinds.index("pr merge"), kinds.index("issue edit"))
        self.assertEqual([kinds.count(k) for k in ("pr comment", "pr merge", "issue edit")], [1, 1, 1])
        self.assertEqual(self.github()["comments"], [NOTE])
        merge_call = self.calls_of("pr merge")[0]["args"]
        self.assertEqual(merge_call[merge_call.index("--match-head-commit") + 1], self.reviewed)
        self.assertEqual(merge_call.count("--merge"), 1)
        # Up to date with main: nothing was pushed, and at the merge only the refs it started with were there.
        self.assertEqual(self.calls_of("pr merge")[0]["refs"].strip(), refs_before.strip())
        # Step 7: the Acceptance boxes of the closed issue are ticked, others are not, and the issue is closed.
        self.assertEqual(self.github()["issues"]["12"], {"state": "CLOSED", "body": ISSUE_BODY_TICKED})
        # Step 8: the issue worktree and its branch are gone, the main checkout is untouched.
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.git(self.checkout, "branch", "--list", "feat/thing"), "")
        self.assertEqual(self.registered_worktrees(), [str(self.checkout)])
        # Step 9 and the rest: lock released, scratch gone, nothing left of the run.
        self.assertFalse(self.merge_lock.exists())
        self.assertEqual(self.scratch_dirs(), [])
        self.assertEqual((self.maintainer_lock / "info").read_text(), "7 2026-11-16T12:00:00Z\n")
        self.assertEqual(self.origin("branch", "--list", "feat/thing"), "")

    def test_takes_the_merge_lock_with_the_pr_and_time_for_the_run(self):
        self.change_github(rollups=[RUNNING, RUNNING, PASSED])
        with managed_process([BASH, str(self.script_path), *self.arguments()], cwd=self.base, env=self.environment(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            self.wait_for(lambda: (self.merge_lock / "info").exists() and (self.merge_lock / "info").stat().st_size > 0)
            info = (self.merge_lock / "info").read_text()
            child.communicate(timeout=60)
        self.assertRegex(info, r"^7 \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n$")
        self.assertFalse(self.merge_lock.exists())

    def test_merges_main_into_the_branch_in_a_temporary_worktree_and_pushes_that_commit_only(self):
        main_tip = self.advance_main()
        refs_before = self.origin_refs()
        result = self.merge()
        self.assertMerged(result)
        new_head = self.calls_of("pr merge")[0]["args"][self.calls_of("pr merge")[0]["args"].index("--match-head-commit") + 1]
        self.assertNotEqual(new_head, self.reviewed)
        self.assertEqual(self.origin("rev-list", "--parents", "-n1", new_head).split()[1:], [self.reviewed, main_tip])
        # Between the push and the merge only the PR branch had moved, and no other ref.
        at_merge = dict(line.split(" ") for line in self.calls_of("pr merge")[0]["refs"].strip().splitlines())
        before = dict(line.split(" ") for line in refs_before.strip().splitlines())
        self.assertEqual({ref for ref in at_merge if at_merge[ref] != before.get(ref)}, {"refs/heads/feat/thing"})
        self.assertEqual(at_merge["refs/heads/feat/thing"], new_head)
        self.assertEqual(self.scratch_dirs(), [])
        self.assertEqual(self.registered_worktrees(), [str(self.checkout)])
        # The local branch of the issue worktree was behind the pushed merge but fully contained in main: removed.
        self.assertFalse(self.worktree.exists())

    def test_waits_for_running_checks_and_needs_two_green_reads_of_the_same_checks(self):
        self.change_github(rollups=[[], RUNNING, RUNNING, PASSED, PASSED])
        result = self.merge()
        self.assertMerged(result)
        self.assertEqual(len(self.ci_reads()), 5)
        self.assertEqual(self.github()["rollup_calls"], 5)

    def test_a_green_read_that_is_followed_by_more_checks_is_not_enough(self):
        self.change_github(rollups=[PASSED, PASSED + [dict(PASSED[0], name="late workflow", status="QUEUED", conclusion=None)],
                                    PASSED + [dict(PASSED[0], name="late workflow")]])
        self.assertMerged(self.merge())
        self.assertEqual(self.github()["rollup_calls"], 4)

    def test_ignores_the_old_head_that_github_still_reports_right_after_the_push(self):
        self.advance_main()
        self.change_github(lag={"head": self.reviewed, "calls": 2}, rollups=[PASSED])
        result = self.merge()
        self.assertMerged(result)
        self.assertGreaterEqual(self.github()["rollup_calls"], 4)
        merge_call = self.calls_of("pr merge")[0]["args"]
        self.assertNotEqual(merge_call[merge_call.index("--match-head-commit") + 1], self.reviewed)

    def test_accepts_the_head_an_earlier_run_produced_by_merging_main(self):
        main_tip = self.advance_main()
        self.git(self.seed, "checkout", "-q", "feat/thing")
        self.git(self.seed, "merge", "-q", "--no-ff", "-m", "merge main", "main")
        self.git(self.seed, "push", "-q", "origin", "feat/thing")
        merged_head = self.origin_tip("feat/thing")
        result = self.merge()
        self.assertMerged(result)
        merge_call = self.calls_of("pr merge")[0]["args"]
        self.assertEqual(merge_call[merge_call.index("--match-head-commit") + 1], merged_head)
        self.assertEqual(self.git(self.base / "origin.git", "merge-base", "--is-ancestor", main_tip, merged_head,
                                  check=False).returncode, 0)

    def test_ticks_only_the_acceptance_section_and_does_not_close_an_issue_github_already_closed(self):
        self.change_github(auto_close=True)
        self.assertMerged(self.merge())
        self.assertEqual(self.github()["issues"]["12"]["body"], ISSUE_BODY_TICKED)
        self.assertEqual(self.calls_of("issue close"), [])

    def test_a_pr_that_closes_no_issue_touches_no_issue(self):
        state = self.github()
        state["prs"]["7"]["closing"] = []
        self.write_state(state)
        self.assertMerged(self.merge())
        self.assertEqual([kind for kind in self.kinds() if kind.startswith("issue")], [])

    def test_issues_of_another_repository_are_not_touched(self):
        state = self.github()
        state["prs"]["7"]["closing"] = []
        state["foreign_closing"] = [{"number": 12, "url": "https://github.com/someone/else/issues/12",
                                     "repository": {"name": "else", "owner": {"login": "someone"}}}]
        self.write_state(state)
        self.assertMerged(self.merge())
        self.assertEqual([kind for kind in self.kinds() if kind.startswith("issue")], [])
        self.assertEqual(self.github()["issues"]["12"]["state"], "OPEN")


class AfterTheMergeTests(MergePrCase):
    def test_a_branch_that_is_still_on_the_remote_is_reported_and_the_rest_is_finished(self):
        self.change_github(keep_branch=True)
        result = self.script(*self.arguments())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("still on origin", result.stderr)
        self.assertEqual(self.github()["prs"]["7"]["state"], "MERGED")
        self.assertIn(self.github()["prs"]["7"]["merge_commit"], result.stdout)
        self.assertEqual(self.github()["issues"]["12"], {"state": "CLOSED", "body": ISSUE_BODY_TICKED})
        self.assertFalse(self.merge_lock.exists())
        self.assertEqual(self.scratch_dirs(), [])

    def test_an_issue_gh_cannot_update_is_reported_after_the_merge(self):
        state = self.github()
        state["prs"]["7"]["closing"] = [12, 13]  # issue 13 does not exist: the fake fails on it
        self.write_state(state)
        result = self.script(*self.arguments())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("issue #13", result.stderr)
        self.assertEqual(self.github()["issues"]["12"], {"state": "CLOSED", "body": ISSUE_BODY_TICKED})
        self.assertEqual(self.github()["prs"]["7"]["state"], "MERGED")
        self.assertFalse(self.merge_lock.exists())


class WorktreeRemovalTests(MergePrCase):
    def assertKept(self, result, reason):
        self.assertMerged(result)
        self.assertIn(reason, result.stdout)
        self.assertTrue(self.worktree.is_dir())
        self.assertIn("feat/thing", self.git(self.checkout, "branch", "--list", "feat/thing"))
        self.assertFalse(self.merge_lock.exists())

    def test_keeps_a_worktree_with_uncommitted_files(self):
        (self.worktree / "scratch.txt").write_text("not committed\n")
        self.assertKept(self.merge(), "not clean")

    def test_keeps_a_worktree_a_process_has_its_working_directory_in(self):
        (self.state_dir / "lsof_extra").write_text(f"p4242\nfcwd\nn{self.worktree}/sub\n")
        self.assertKept(self.merge(), "process")

    def test_keeps_a_worktree_when_it_cannot_be_checked_for_processes(self):
        (self.bin / "lsof").write_text("#!/bin/sh\nexit 1\n")
        self.assertKept(self.merge(), "process")

    def test_keeps_a_local_branch_with_commits_that_never_reached_main(self):
        self.commit(self.worktree, "unpushed.txt", "only here\n")
        self.assertKept(self.merge(), "commits")

    def test_keeps_a_worktree_that_is_not_named_like_an_issue_worktree(self):
        self.git(self.checkout, "worktree", "move", str(self.worktree), str(self.base / "codex-work"))
        self.worktree = self.base / "codex-work"
        self.assertKept(self.merge(), "verwalino-issue-")

    def test_keeps_a_locked_worktree(self):
        self.git(self.checkout, "worktree", "lock", str(self.worktree))
        self.assertKept(self.merge(), "locked")

    def test_never_removes_the_main_checkout(self):
        self.git(self.worktree, "checkout", "-q", "--detach")
        self.git(self.checkout, "checkout", "-q", "feat/thing")
        self.assertMerged(self.merge())
        self.assertTrue(self.checkout.is_dir())
        self.assertIn("feat/thing", self.git(self.checkout, "branch", "--list", "feat/thing"))

    def test_never_removes_the_worktree_it_runs_from(self):
        (self.worktree / "scripts").mkdir()
        shutil.copyfile(self.script_path, self.worktree / "scripts/merge-pr.sh")
        self.script_path = self.worktree / "scripts/merge-pr.sh"
        self.assertKept(self.merge(), "runs from")

    def test_removes_the_local_branch_alone_when_no_worktree_holds_it(self):
        self.git(self.checkout, "worktree", "remove", str(self.worktree))
        self.assertMerged(self.merge())
        self.assertEqual(self.git(self.checkout, "branch", "--list", "feat/thing"), "")

    def test_never_touches_other_worktrees(self):
        other = self.base / "verwalino-issue-99"
        self.git(self.checkout, "worktree", "add", "-q", "-b", "feat/other", str(other), "origin/main")
        self.assertMerged(self.merge())
        self.assertTrue(other.is_dir())
        self.assertIn("feat/other", self.git(self.checkout, "branch", "--list", "feat/other"))


class RefusalTests(MergePrCase):
    def test_refuses_without_a_note_file(self):
        result = self.merge(note=self.base / "missing.md")
        self.assertRefused(result, "note file")
        self.assertEqual(self.calls(), [])

    def test_refuses_an_empty_note_file(self):
        for text in ("", "\n  \n\t\n"):
            with self.subTest(text=text):
                self.note.write_text(text)
                result = self.merge()
                self.assertRefused(result, "note file")
                self.assertEqual(self.calls(), [])

    def test_refuses_when_the_maintainer_lock_is_not_held(self):
        for kind in ("absent", "no info", "no pr number", "a file", "a link"):
            with self.subTest(kind=kind):
                shutil.rmtree(self.maintainer_lock, ignore_errors=True)
                self.maintainer_lock.unlink(missing_ok=True)
                if kind == "no info":
                    self.maintainer_lock.mkdir()
                elif kind == "no pr number":
                    self.hold_maintainer_lock("someone 2026-11-16T12:00:00Z\n")
                elif kind == "a file":
                    self.maintainer_lock.write_text("7 2026-11-16T12:00:00Z\n")
                elif kind == "a link":
                    (self.base / "elsewhere").mkdir(exist_ok=True)
                    (self.base / "elsewhere/info").write_text("7 2026-11-16T12:00:00Z\n")
                    self.maintainer_lock.symlink_to(self.base / "elsewhere")
                result = self.merge()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("maintainer lock", result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertFalse(self.merge_lock.exists())
                self.assertEqual(self.scratch_dirs(), [])

    def test_refuses_a_pr_that_is_not_open(self):
        for state in ("CLOSED", "MERGED"):
            with self.subTest(state=state):
                github = self.github()
                github["prs"]["7"]["state"] = state
                self.write_state(github)
                self.assertRefused(self.merge(), state, state=state)

    def test_refuses_a_pr_that_is_not_based_on_main(self):
        github = self.github()
        github["prs"]["7"]["base"] = "feat/base"
        self.write_state(github)
        self.assertRefused(self.merge(), "feat/base", "main")

    def test_refuses_a_head_that_has_a_commit_after_the_reviewed_one(self):
        self.push_to_branch()
        result = self.merge()
        self.assertRefused(result, self.reviewed, "not merges")

    def test_refuses_a_head_that_does_not_contain_the_reviewed_commit(self):
        self.git(self.seed, "checkout", "-q", "-b", "elsewhere", "main")
        elsewhere = self.commit(self.seed, "elsewhere.txt", "x\n")
        self.git(self.seed, "push", "-q", "origin", "elsewhere")
        self.assertRefused(self.merge(reviewed=elsewhere), elsewhere, "does not contain")

    def test_refuses_a_reviewed_commit_this_checkout_does_not_know(self):
        self.assertRefused(self.merge(reviewed="1" * 40), "1" * 40, "not known")

    def test_refuses_an_older_commit_as_the_reviewed_one(self):
        old = self.origin("rev-parse", "main")
        self.assertRefused(self.merge(reviewed=old), old)

    def test_refuses_when_another_open_pr_is_based_on_the_branch_and_names_it(self):
        github = self.github()
        github["prs"]["9"] = {"state": "OPEN", "base": "feat/thing", "branch": "feat/next", "closing": [], "merge_commit": None}
        self.write_state(github)
        result = self.merge()
        self.assertRefused(result, "#9", "feat/thing", "--base main")
        self.assertEqual(github["prs"]["9"]["base"], self.github()["prs"]["9"]["base"])

    def test_a_pr_opened_on_the_branch_while_the_ci_ran_is_caught_before_the_note(self):
        self.change_github(rollups=[RUNNING, PASSED], events=[{"on_rollup_call": 2, "do": "open-dependent", "branch": "feat/thing"}])
        self.assertRefused(self.merge(), "#9", "feat/thing")

    def test_refuses_on_a_conflict_and_leaves_nothing_behind(self):
        self.advance_main("feature.txt", "main's own feature.txt\n")
        refs_before = self.origin_refs()
        result = self.merge()
        self.assertRefused(result, "conflict", "feature.txt")
        self.assertEqual(self.origin_refs(), refs_before)

    def test_refuses_when_a_check_is_not_green_and_names_it(self):
        self.change_github(rollups=[FAILED])
        self.assertRefused(self.merge(), "not green", "Frontend", "FAILURE")

    def test_refuses_at_once_on_a_failed_check_while_others_still_run(self):
        self.change_github(rollups=[RUNNING + [dict(FAILED[1])]])
        self.assertRefused(self.merge(), "Frontend")
        self.assertEqual(self.github()["rollup_calls"], 1)

    def test_a_failed_status_context_is_not_green(self):
        self.change_github(rollups=[PASSED + [{"__typename": "StatusContext", "context": "ci/legacy", "state": "FAILURE"}]])
        self.assertRefused(self.merge(), "ci/legacy", "FAILURE")

    def test_neutral_cancelled_and_timed_out_are_not_green(self):
        for conclusion in ("NEUTRAL", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"):
            with self.subTest(conclusion=conclusion):
                self.change_github(rollups=[PASSED + [dict(PASSED[0], name="odd", conclusion=conclusion)]], rollup_calls=0)
                self.assertRefused(self.merge(), "odd", conclusion)

    def test_refuses_when_the_checks_do_not_finish_within_the_bound(self):
        self.install_script(CI_WAIT_SECONDS="1")
        self.change_github(rollups=[RUNNING])
        started = time.monotonic()
        self.assertRefused(self.merge(), "within 1s")
        self.assertLess(time.monotonic() - started, 30)

    def test_no_checks_at_all_is_not_green(self):
        self.install_script(CI_WAIT_SECONDS="1")
        self.change_github(rollups=[[]])
        self.assertRefused(self.merge(), "within 1s", "no checks")

    def test_refuses_when_the_head_moves_while_the_checks_run(self):
        self.change_github(rollups=[RUNNING, PASSED], events=[{"on_rollup_call": 2, "do": "push", "branch": "feat/thing"}])
        self.assertRefused(self.merge(), "moved")

    def test_refuses_when_gh_cannot_read_the_pr(self):
        github = self.github()
        github["prs"] = {}
        self.write_state(github)
        result = self.merge(pr="7")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.merge_lock.exists())

    def test_a_merge_gh_refuses_leaves_the_comment_but_no_other_trace(self):
        self.change_github(merge_error="GraphQL: Pull request is not mergeable")
        result = self.merge()
        self.assertRefused(result, "not mergeable", "not merged", posted=True, attempted=True)
        self.assertTrue(self.worktree.is_dir())
        self.assertEqual(self.github()["issues"]["12"], {"state": "OPEN", "body": ISSUE_BODY})

    def test_refuses_a_push_the_remote_rejects_and_leaves_nothing_behind(self):
        self.advance_main()
        hook = self.base / "origin.git/hooks/pre-receive"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        result = self.merge()
        self.assertRefused(result, "push")


class LockTests(MergePrCase):
    def test_waits_while_another_merge_holds_the_lock_and_proceeds_when_it_is_free(self):
        self.merge_lock.mkdir()
        (self.merge_lock / "info").write_text("99 2026-11-16T10:00:00Z\n")
        timer = threading.Timer(1.0, lambda: shutil.rmtree(self.merge_lock))
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.merge()
        timer.join()
        self.assertMerged(result)
        self.assertIn("99 2026-11-16T10:00:00Z", result.stdout)
        self.assertFalse(self.merge_lock.exists())

    def test_gives_up_after_the_bound_and_leaves_the_foreign_lock_alone(self):
        self.install_script(LOCK_WAIT_SECONDS="1")
        self.merge_lock.mkdir()
        (self.merge_lock / "info").write_text("99 2026-11-16T10:00:00Z\n")
        result = self.merge()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("99 2026-11-16T10:00:00Z", result.stderr)
        self.assertEqual((self.merge_lock / "info").read_text(), "99 2026-11-16T10:00:00Z\n")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.scratch_dirs(), [])

    def test_does_not_release_a_lock_that_no_longer_carries_its_info(self):
        self.change_github(rollups=[RUNNING, RUNNING, PASSED])
        with managed_process([BASH, str(self.script_path), *self.arguments()], cwd=self.base, env=self.environment(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            self.wait_for(lambda: (self.merge_lock / "info").exists() and (self.merge_lock / "info").stat().st_size > 0)
            (self.merge_lock / "info").write_text("55 2026-11-16T13:00:00Z\n")
            _, stderr = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 1)
        self.assertEqual((self.merge_lock / "info").read_text(), "55 2026-11-16T13:00:00Z\n")
        self.assertIn("left in place", stderr)

    def test_an_interrupt_during_the_wait_releases_the_lock_and_stops_before_the_note(self):
        self.install_script(CI_WAIT_SECONDS="60", CI_POLL_SECONDS="37.5")
        for sent, code in ((signal.SIGTERM, 143), (signal.SIGINT, 130)):
            with self.subTest(signal=sent.name):
                self.change_github(rollups=[RUNNING], rollup_calls=0)
                with managed_process([BASH, str(self.script_path), *self.arguments()], cwd=self.base,
                                     env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True) as child:
                    self.wait_for(lambda: self.github()["rollup_calls"] >= 1)
                    time.sleep(0.3)
                    child.send_signal(sent)
                    child.communicate(timeout=10)
                self.assertEqual(child.returncode, code)
                self.assertEqual(self.calls_of("pr comment") + self.calls_of("pr merge"), [])
                self.assertNothingLeft()
                self.wait_for(lambda: self.sleepers() == [], seconds=5)

    def sleepers(self):
        listing = run(["ps", "-axo", "command="], capture_output=True, text=True).stdout.splitlines()
        return [line for line in listing if line.strip() == "sleep 37.5"]


class SurfaceTests(MergePrCase):
    def assertUsageError(self, args):
        result = self.script(*args)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("usage: scripts/merge-pr.sh <PR> --reviewed <sha> --note-file <file>", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertNothingLeft()

    def test_usage_names_exactly_the_three_arguments(self):
        result = self.script("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: scripts/merge-pr.sh <PR> --reviewed <sha> --note-file <file>", result.stdout)

    def test_a_call_that_does_not_match_the_interface_is_a_usage_error(self):
        sha, note = self.reviewed, str(self.note)
        for args in ([], ["7"], ["7", "--reviewed", sha], ["7", "--note-file", note], ["x", "--reviewed", sha, "--note-file", note],
                     ["0", "--reviewed", sha, "--note-file", note], ["7", "--reviewed", sha[:10], "--note-file", note],
                     ["7", "--reviewed", "g" * 40, "--note-file", note],
                     ["7", "--reviewed", sha, "--note-file", note, "--squash"],
                     ["7", "--reviewed", sha, "--note-file", note, "--force"],
                     ["7", "--reviewed", sha, "--reviewed", sha, "--note-file", note],
                     ["7", "--reviewed", sha, "--note-file"]):
            with self.subTest(args=args):
                self.assertUsageError(args)

    def test_arguments_may_come_in_either_order_and_the_sha_may_be_upper_case(self):
        result = self.script("7", "--note-file", str(self.note), "--reviewed", self.reviewed.upper())
        self.assertMerged(result)

    def test_script_has_exactly_the_constants_the_sandbox_rewrites_and_no_forbidden_option(self):
        text = SCRIPT.read_text()
        for name in CONSTANTS:
            self.assertEqual(len(re.findall(rf"(?m)^{name}=", text)), 1, name)
        for forbidden in ("--squash", "--rebase", "--admin", "--no-verify", "--auto", "worktree prune", "rm -rf"):
            self.assertNotIn(forbidden, text)
        # The one --force is git's own manager removing the script's temporary worktree; no push is ever forced.
        self.assertEqual(re.findall(r"--force\b[^\n]*", text), ['--force "$scratch/tree" >/dev/null 2>&1 || true'])
        self.assertNotRegex(text, r"push[^\n]*(--force|--mirror|--delete| -f |\+refs)")
        # The merge is one command and always a merge commit, pinned to the commit that was checked.
        self.assertEqual(len(re.findall(r"gh pr merge ", text)), 1)
        self.assertIn('--merge --delete-branch --match-head-commit "$TARGET_SHA"', text)
        self.assertEqual(len(re.findall(r'(?m)^\s+git -C "\$SCRATCH/tree" push ', text)), 1)
        self.assertEqual(len(re.findall(r"(?m)^\s+git [^\n]*\bpush\b", text)), 1)
        self.assertRegex(text, r"(?m)^MERGE_LOCK=/tmp/hausverwaltung-merge\.lock$")
        self.assertRegex(text, r"(?m)^MAINTAINER_LOCK=/tmp/verwalino-maintainer\.lock$")
        self.assertNotIn("verwalino-merge.lock", text)

    def test_runs_under_the_bash_3_2_of_macos_when_it_is_there(self):
        old = Path("/bin/bash")
        if not old.exists() or "version 3." not in run([str(old), "--version"], capture_output=True, text=True).stdout:
            self.skipTest("/bin/bash is not bash 3.x here")
        result = run([str(old), str(self.script_path), *self.arguments()], cwd=self.base, env=self.environment(),
                     capture_output=True, text=True, timeout=120)
        self.assertMerged(result)

    def test_documents_name_the_script_and_the_lock_it_takes(self):
        agents = (ROOT / "AGENTS.md").read_text()
        contributing = (ROOT / "CONTRIBUTING.md").read_text()
        self.assertIn("`scripts/merge-pr.sh", agents)
        self.assertIn("scripts/merge-pr.sh <PR> --reviewed <sha> --note-file <file>", contributing)
        self.assertIn("/tmp/hausverwaltung-merge.lock", agents)
        self.assertNotIn("verwalino-merge.lock", agents + contributing)

    def test_contract_test_is_part_of_the_installer_target(self):
        result = run(["make", "-n", "test-installer"], cwd=ROOT, check=True, capture_output=True, text=True)
        self.assertIn("python3 -B scripts/test-merge-pr.py", result.stdout)


if __name__ == "__main__":
    install_interrupt_handlers()
    unittest_main()
