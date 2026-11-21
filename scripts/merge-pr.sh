#!/usr/bin/env bash
# Merge a reviewed pull request under the merge lock: the maintainer step "Merge under the lock" as one command.
#
#   scripts/merge-pr.sh <PR> --reviewed <sha> --note-file <file>
#
# <sha> is the full id of the commit the review covered; <file> holds the merge comment (reviewed SHA, gates with
# results, user check outcome, follow-up issues) and is posted as it is. The caller holds the maintainer lock
# (MAINTAINER_LOCK, a directory whose `info` names a PR number and a time); this script neither takes nor releases
# it, and refuses without it. It takes the merge lock (MERGE_LOCK) for its run and releases only what it took, also on
# failure and on interrupt. In order:
#
#   1. take the merge lock, waiting while another merge holds it (at most LOCK_WAIT_SECONDS);
#   2. check that the PR is open and based on main, that its head is the reviewed commit (or that commit plus merges
#      of main, which an earlier run of this script left behind) and that no other open PR is based on its branch;
#   3. merge origin/main into the PR branch in a temporary detached worktree, stop on a conflict, and push the PR
#      branch when that produced a commit; that is the only push, and never to main;
#   4. wait for CI on that commit: every check passed or skipped, one bounded wait (CI_WAIT_SECONDS);
#   5. post the note file as a comment on the PR;
#   6. merge with a merge commit (never squash, never an admin override) pinned to that commit, and delete the branch;
#   7. tick the acceptance boxes of the issues the PR closes, and close those that are still open;
#   8. remove the issue worktree and its local branch when the tree is clean and no process uses it;
#   9. release the merge lock.
#
# A refusal is decided by structured state (gh --json fields, exit codes, ancestry), never by message text. Nothing is
# rolled back after the merge: what could not be finished is reported and makes the exit status 1. A lock that is
# still held when the wait ends, even one left behind by a crash, is reported with its `info` and left alone.
set -euo pipefail

# Fixed locations and limits; the contract test rewrites exactly these eight lines to run in a sandbox.
MAINTAINER_LOCK=/tmp/verwalino-maintainer.lock
MERGE_LOCK=/tmp/hausverwaltung-merge.lock
SCRATCH_PREFIX=/tmp/verwalino-merge-pr
# 150 minutes plus five: the longest another session may hold the merge lock, and a margin.
LOCK_WAIT_SECONDS=9300
LOCK_POLL_SECONDS=60
# 45 minutes: the whole suite runs in about 25, and the lock may not be held for longer than CI, the gates and the merge.
CI_WAIT_SECONDS=2700
CI_POLL_SECONDS=30
# The pause before the one retry of the fetch that verifies the merge, when another session's fetch holds a ref lock.
FETCH_RETRY_SECONDS=2

REPO=itskeKs/verwalino
REMOTE=origin
MAIN=main
ISSUE_WORKTREE=verwalino-issue-
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

PR=""
PR_STATE=""
PR_BASE=""
REVIEWED=""
NOTE=""
BRANCH=""
HEAD_SHA=""
TARGET_SHA=""
CLOSING=""
MERGE_COMMIT=""
HOLD_LOCK=0
LOCK_INFO=""
SCRATCH=""
NAP_PID=""
LEFT=0

usage() {
  echo "usage: scripts/merge-pr.sh <PR> --reviewed <sha> --note-file <file>"
}

usage_error() { # message
  [ $# -eq 0 ] || echo "merge-pr: $1" >&2
  usage >&2
  exit 2
}

die() { echo "merge-pr: $*" >&2; exit 1; }

# After the merge nothing is undone: what did not finish is named, and the exit status says so.
left() { echo "merge-pr: $*" >&2; LEFT=1; }

# ---- structured reads -------------------------------------------------------

pr_view() { gh pr view "$PR" -R "$REPO" --json "$1"; }

json_field() { # dotted-path; JSON on stdin; prints the scalar there, empty when it is absent
  python3 -I -c '
import json, sys
value = json.load(sys.stdin)
for key in sys.argv[1].split("."):
    value = value.get(key) if isinstance(value, dict) else None
print("" if value is None else value)
' "$1"
}

pr_facts() { # JSON of `gh pr view` on stdin; the numbers of the issues of this repository that the PR closes
  python3 -I -c '
import json, sys
sys.stdin.reconfigure(encoding="utf-8")
repo = sys.argv[1].lower()
data = json.load(sys.stdin)
closing = []
for ref in data.get("closingIssuesReferences") or []:
    source = ref.get("repository") or {}
    if ("%s/%s" % ((source.get("owner") or {}).get("login"), source.get("name"))).lower() == repo:
        closing.append(str(ref["number"]))
print("state=%s" % data.get("state"))
print("base=%s" % data.get("baseRefName"))
print("branch=%s" % data.get("headRefName"))
print("head=%s" % data.get("headRefOid"))
print("closing=%s" % " ".join(closing))
' "$REPO"
}

ci_snapshot() { # JSON of `gh pr view` on stdin: state, the commit the checks belong to, and one verdict for them
  python3 -I -c '
import json, sys
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
data = json.load(sys.stdin)
checks = data.get("statusCheckRollup") or []
pending, failed = [], []
for check in checks:
    if check.get("__typename") == "StatusContext":
        name, outcome = check.get("context"), check.get("state")
        if outcome == "SUCCESS":
            continue
        (pending if outcome in ("PENDING", "EXPECTED") else failed).append("%s: %s" % (name, outcome))
    elif check.get("status") != "COMPLETED":
        pending.append(check.get("name"))
    elif check.get("conclusion") not in ("SUCCESS", "SKIPPED"):
        failed.append("%s: %s" % (check.get("name"), check.get("conclusion")))
print("state=%s" % data.get("state"))
print("head=%s" % data.get("headRefOid"))
if failed:
    print("ci=failed " + ", ".join(failed))
elif pending:
    print("ci=pending %d of %d checks still running" % (len(pending), len(checks)))
elif not checks:
    print("ci=none no checks reported yet")
else:
    print("ci=green %d checks" % len(checks))
'
}

tick_acceptance() { # JSON of `gh issue view` on stdin: "changed" and the body with its acceptance boxes ticked, or "unchanged"
  python3 -I -c '
import json, re, sys
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
body = json.load(sys.stdin).get("body") or ""
section, changed, lines = False, False, []
for line in body.splitlines(True):
    text = line.rstrip("\r\n")
    heading = re.match(r"(#{1,2})\s+(.*?)\s*$", text)
    if heading:
        section = heading.group(1) == "##" and heading.group(2) == "Acceptance"
    elif section:
        ticked = re.sub(r"^(\s*[-*]\s+\[) (\])", r"\1x\2", text, count=1)
        if ticked != text:
            line, changed = ticked + line[len(text):], True
    lines.append(line)
sys.stdout.write("changed\n" + "".join(lines) if changed else "unchanged\n")
'
}

# ---- locks ------------------------------------------------------------------

require_maintainer_lock() {
  local info=""
  if [ -d "$MAINTAINER_LOCK" ] && [ ! -L "$MAINTAINER_LOCK" ]; then
    info="$(head -n 1 "$MAINTAINER_LOCK/info" 2>/dev/null || true)"
  fi
  case "$info" in
    [0-9]*" "?*) ;;
    *) die "the maintainer lock $MAINTAINER_LOCK is not held (a directory whose info names a PR number and a time); this script runs inside the maintainer session that took it; nothing was done" ;;
  esac
}

# A sleep that a signal does not have to wait for, and that never outlives the script.
nap() { # seconds
  sleep "$1" &
  NAP_PID=$!
  wait "$NAP_PID" 2>/dev/null || true
  NAP_PID=""
}

take_merge_lock() {
  local start=$SECONDS holder shown=""
  until mkdir "$MERGE_LOCK" 2>/dev/null; do
    [ -d "$MERGE_LOCK" ] || die "cannot create $MERGE_LOCK; nothing was done"
    holder="$(head -n 1 "$MERGE_LOCK/info" 2>/dev/null || true)"
    if [ $((SECONDS - start)) -ge "$LOCK_WAIT_SECONDS" ]; then
      die "$MERGE_LOCK is still held after ${LOCK_WAIT_SECONDS}s (${holder:-no info}); nothing was done"
    fi
    if [ "$holder" != "$shown" ]; then
      echo "Waiting for $MERGE_LOCK (held: ${holder:-no info}) ..."
      shown="$holder"
    fi
    nap "$LOCK_POLL_SECONDS"
  done
  HOLD_LOCK=1
  LOCK_INFO="$PR $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s\n' "$LOCK_INFO" > "$MERGE_LOCK/info"
}

# Only what this run took, and only while it still carries this run's `info`.
release_merge_lock() {
  [ "$HOLD_LOCK" = 1 ] || return 0
  HOLD_LOCK=0
  if [ "$(head -n 1 "$MERGE_LOCK/info" 2>/dev/null || true)" != "$LOCK_INFO" ]; then
    echo "merge-pr: $MERGE_LOCK no longer carries this run's info; left in place" >&2
    return 1
  fi
  rm -f "$MERGE_LOCK/info"
  rmdir "$MERGE_LOCK" 2>/dev/null || { echo "merge-pr: could not remove $MERGE_LOCK" >&2; return 1; }
}

# ---- the temporary worktree -------------------------------------------------

valid_scratch() { # path: a directory of the scratch name, not a link, carrying the marker this script put there
  case "$1" in "$SCRATCH_PREFIX".??????) ;; *) return 1 ;; esac
  [ -d "$1" ] && [ ! -L "$1" ] && [ -f "$1/.merge-pr" ]
}

# The worktree goes through git's own manager, the marker and the directory last; nothing else is removed.
discard_scratch() {
  [ -n "$SCRATCH" ] || return 0
  local scratch="$SCRATCH"
  SCRATCH=""
  if ! valid_scratch "$scratch"; then
    echo "merge-pr: $scratch is not a scratch directory this script can show to be its own (name, directory, no link, marker); left in place" >&2
    return 1
  fi
  if [ -e "$scratch/tree" ]; then
    git -C "$scratch/tree" merge --abort >/dev/null 2>&1 || true
    git -C "$ROOT" worktree remove --force "$scratch/tree" >/dev/null 2>&1 || true
  fi
  if [ -e "$scratch/tree" ]; then
    echo "merge-pr: the temporary worktree $scratch/tree could not be removed; left in place" >&2
    return 1
  fi
  rm -f "$scratch/.merge-pr"
  rmdir "$scratch" 2>/dev/null || { echo "merge-pr: could not remove $scratch" >&2; return 1; }
}

on_exit() {
  local status=$?
  trap - EXIT
  if [ -n "$NAP_PID" ]; then kill "$NAP_PID" 2>/dev/null || true; fi
  discard_scratch || { [ "$status" -ne 0 ] || status=1; }
  release_merge_lock || { [ "$status" -ne 0 ] || status=1; }
  exit "$status"
}

# ---- step 2: the PR ----------------------------------------------------------

# 0 only when the first commit is certainly contained in the second; an unknown commit counts as not contained.
is_ancestor() { # ancestor descendant
  git -C "$ROOT" merge-base --is-ancestor "$1" "$2" 2>/dev/null
}

read_pr() {
  local facts key value
  facts="$(pr_view state,baseRefName,headRefName,headRefOid,closingIssuesReferences | pr_facts)" \
    || die "cannot read PR #$PR with gh; nothing was merged"
  while IFS='=' read -r key value; do
    case "$key" in
      state) PR_STATE="$value" ;;
      base) PR_BASE="$value" ;;
      branch) BRANCH="$value" ;;
      head) HEAD_SHA="$value" ;;
      closing) CLOSING="$value" ;;
    esac
  done <<< "$facts"
}

# The head is the reviewed commit, or that commit plus merges of main and nothing else.
check_head() {
  local extra
  [ "$HEAD_SHA" = "$REVIEWED" ] && return 0
  git -C "$ROOT" cat-file -e "$REVIEWED^{commit}" 2>/dev/null \
    || die "the reviewed commit $REVIEWED is not known in $ROOT (the PR head is $HEAD_SHA); nothing was merged"
  is_ancestor "$REVIEWED" "$HEAD_SHA" \
    || die "the head $HEAD_SHA of PR #$PR does not contain the reviewed commit $REVIEWED; nothing was merged"
  extra="$(git -C "$ROOT" rev-list --no-merges "$REVIEWED..$HEAD_SHA" "^$REMOTE/$MAIN")" \
    || die "cannot list the commits after $REVIEWED; nothing was merged"
  [ -z "$extra" ] \
    || die "the head $HEAD_SHA of PR #$PR has commits after the reviewed commit $REVIEWED that are not merges of $MAIN; nothing was merged"
}

require_no_dependents() {
  local listing numbers
  listing="$(gh pr list -R "$REPO" --state open --base "$BRANCH" --json number --limit 100)" \
    || die "cannot list the PRs based on $BRANCH; nothing was merged"
  numbers="$(printf '%s' "$listing" | python3 -I -c 'import json, sys; print(" ".join("#%d" % pr["number"] for pr in json.load(sys.stdin)))')"
  [ -z "$numbers" ] \
    || die "$numbers is based on $BRANCH, and deleting that branch at the merge would close it; retarget it first (gh pr edit <number> -R $REPO --base $MAIN); nothing was merged"
}

check_pr() {
  local tip
  read_pr
  [ "$PR_STATE" = OPEN ] || die "PR #$PR is $PR_STATE, not open; nothing was merged"
  [ "$PR_BASE" = "$MAIN" ] || die "PR #$PR is based on $PR_BASE, not $MAIN; merge its base first and retarget it onto $MAIN; nothing was merged"
  [ -n "$BRANCH" ] && [ "$BRANCH" != "$MAIN" ] || die "PR #$PR has no head branch other than $MAIN; nothing was merged"
  git -C "$ROOT" fetch --quiet "$REMOTE" || die "cannot fetch $REMOTE; nothing was merged"
  tip="$(git -C "$ROOT" rev-parse --verify --quiet "refs/remotes/$REMOTE/$BRANCH^{commit}")" \
    || die "$REMOTE/$BRANCH does not exist after the fetch; nothing was merged"
  [ "$tip" = "$HEAD_SHA" ] \
    || die "$REMOTE/$BRANCH is at $tip, but GitHub reports $HEAD_SHA as the head of PR #$PR; try again in a moment; nothing was merged"
  check_head
  require_no_dependents
}

# ---- step 3: main into the branch ---------------------------------------------

update_branch() {
  local conflicts
  TARGET_SHA="$HEAD_SHA"
  if is_ancestor "$REMOTE/$MAIN" "$HEAD_SHA"; then
    echo "$REMOTE/$MAIN is already in $BRANCH; nothing to merge."
    return 0
  fi
  SCRATCH="$(mktemp -d "$SCRATCH_PREFIX.XXXXXX")" || die "cannot create a scratch directory; nothing was merged"
  : > "$SCRATCH/.merge-pr"
  git -C "$ROOT" worktree add --quiet --detach "$SCRATCH/tree" "$HEAD_SHA" \
    || die "cannot create the temporary worktree; nothing was merged"
  if ! git -C "$SCRATCH/tree" merge --quiet --no-ff -m "Merge remote-tracking branch '$REMOTE/$MAIN' into $BRANCH" "$REMOTE/$MAIN" >/dev/null 2>&1; then
    conflicts="$(git -C "$SCRATCH/tree" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ' || true)"
    die "merging $REMOTE/$MAIN into $BRANCH failed${conflicts:+ with a conflict in: $conflicts}; resolve it in the branch and review the result; nothing was merged"
  fi
  TARGET_SHA="$(git -C "$SCRATCH/tree" rev-parse HEAD)"
  git -C "$SCRATCH/tree" push --quiet "$REMOTE" "$TARGET_SHA:refs/heads/$BRANCH" \
    || die "pushing the merge of $MAIN to $BRANCH was rejected; nothing was merged"
  echo "Merged $REMOTE/$MAIN into $BRANCH: $TARGET_SHA"
  discard_scratch || die "the temporary worktree could not be removed; the PR is not merged"
}

# ---- step 4: CI on exactly that commit ------------------------------------------

wait_for_ci() {
  local start=$SECONDS snapshot key value state head ci last="" green=""
  echo "Waiting for CI on $TARGET_SHA (at most ${CI_WAIT_SECONDS}s) ..."
  while :; do
    ci="no answer from gh"
    if snapshot="$(pr_view state,headRefOid,statusCheckRollup 2>/dev/null | ci_snapshot)"; then
      state="" head="" ci=""
      while IFS='=' read -r key value; do
        case "$key" in state) state="$value" ;; head) head="$value" ;; ci) ci="$value" ;; esac
      done <<< "$snapshot"
      [ "$state" = OPEN ] || die "PR #$PR is $state now; nothing was merged"
      if [ "$head" = "$TARGET_SHA" ]; then
        case "$ci" in
          failed*) die "CI is not green on $TARGET_SHA: ${ci#failed }; nothing was merged" ;;
          # Green must be read twice, the same checks both times, so that a workflow that registers a moment later is seen.
          green*) if [ "$ci" = "$green" ]; then echo "CI is green on $TARGET_SHA (${ci#green })."; return 0; fi
                  green="$ci" ;;
          *) green="" ;;
        esac
      elif [ "$head" = "$HEAD_SHA" ]; then
        green="" ci="GitHub does not show the pushed commit yet"
      else
        die "the head of PR #$PR moved to $head; $TARGET_SHA was the commit to merge; nothing was merged"
      fi
    fi
    if [ "$ci" != "$last" ]; then echo "  $ci"; last="$ci"; fi
    [ $((SECONDS - start)) -lt "$CI_WAIT_SECONDS" ] \
      || die "CI did not finish on $TARGET_SHA within ${CI_WAIT_SECONDS}s ($ci); nothing was merged"
    nap "$CI_POLL_SECONDS"
  done
}

# ---- steps 5 and 6: note and merge ----------------------------------------------

merge_pr() {
  local status=0 state facts key value
  gh pr comment "$PR" -R "$REPO" --body-file "$NOTE" >/dev/null \
    || die "the note was not posted on PR #$PR; nothing was merged"
  echo "Posted $NOTE on PR #$PR."
  gh pr merge "$PR" -R "$REPO" --merge --delete-branch --match-head-commit "$TARGET_SHA" </dev/null || status=$?
  facts="$(pr_view state,mergeCommit | python3 -I -c '
import json, sys
data = json.load(sys.stdin)
print("state=%s" % data.get("state"))
print("commit=%s" % (data.get("mergeCommit") or {}).get("oid", ""))
')" || die "cannot read PR #$PR after the merge attempt (gh exited with $status); check it by hand"
  state=""
  while IFS='=' read -r key value; do
    case "$key" in state) state="$value" ;; commit) MERGE_COMMIT="$value" ;; esac
  done <<< "$facts"
  [ "$state" = MERGED ] || die "PR #$PR was not merged (gh exited with $status; the note is posted); nothing else was done"
  [ "$status" -eq 0 ] || left "gh exited with $status after merging PR #$PR; check what it did not finish"
  echo "Merged PR #$PR: merge commit ${MERGE_COMMIT:-unknown}"
  # Another session may fetch at the same moment and hold the lock on a ref: one retry after a short pause settles it.
  git -C "$ROOT" fetch --quiet "$REMOTE" || {
    echo "Fetching $REMOTE failed; trying once more in ${FETCH_RETRY_SECONDS}s."
    nap "$FETCH_RETRY_SECONDS"
    git -C "$ROOT" fetch --quiet "$REMOTE" || left "cannot fetch $REMOTE to verify the merge commit"
  }
  if [ -z "$MERGE_COMMIT" ]; then
    left "GitHub names no merge commit for PR #$PR; check it by hand"
  elif ! is_ancestor "$MERGE_COMMIT" "$REMOTE/$MAIN"; then
    left "the merge commit $MERGE_COMMIT is not on $REMOTE/$MAIN after the fetch"
  fi
  status=0
  git -C "$ROOT" ls-remote --exit-code --heads "$REMOTE" "refs/heads/$BRANCH" >/dev/null 2>&1 || status=$?
  case "$status" in
    2) ;;
    0) left "the branch $BRANCH is still on $REMOTE; delete it by hand" ;;
    *) left "cannot tell whether $BRANCH is still on $REMOTE" ;;
  esac
}

# ---- step 7: the issues ---------------------------------------------------------

finish_issues() {
  local number json out body state
  for number in $CLOSING; do
    json="$(gh issue view "$number" -R "$REPO" --json state,body)" \
      || { left "cannot read issue #$number; its acceptance boxes are not ticked and it is not closed"; continue; }
    # printf x keeps the body's trailing newlines through the command substitution.
    out="$(printf '%s' "$json" | tick_acceptance; printf x)"
    out="${out%x}"
    case "$out" in
      changed*)
        body="${out#changed?}"
        if printf '%s' "$body" | gh issue edit "$number" -R "$REPO" --body-file - >/dev/null; then
          echo "Ticked the acceptance boxes of issue #$number."
        else
          left "cannot update issue #$number; its acceptance boxes are not ticked"
        fi ;;
      unchanged*) echo "Issue #$number has no unticked acceptance box." ;;
      *) left "cannot read the body of issue #$number; its acceptance boxes are not ticked" ;;
    esac
    # GitHub closes the issue of a `Closes #N` itself, a moment after the merge: look at the state now, not before.
    state="$(gh issue view "$number" -R "$REPO" --json state | json_field state)" \
      || { left "cannot read the state of issue #$number; it may still be open"; continue; }
    if [ "$state" = OPEN ]; then
      if gh issue close "$number" -R "$REPO" >/dev/null; then
        echo "Closed issue #$number."
      else
        left "cannot close issue #$number"
      fi
    fi
  done
}

# ---- step 8: the issue worktree -------------------------------------------------

uses_directory() { # dir: 0 when a process has its working directory in it, 1 when none, 2 when that cannot be told
  local listing line
  command -v lsof >/dev/null 2>&1 || return 2
  listing="$(lsof -d cwd -Fn 2>/dev/null || true)"
  [ -n "$listing" ] || return 2
  while IFS= read -r line; do
    case "$line" in "n$1"|"n$1/"*) return 0 ;; esac
  done <<< "$listing"
  return 1
}

remove_issue_worktree() {
  local primary="" path="" branch="" locked=0 holder="" holder_locked=0 line tip used=0
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) path="${line#worktree }"; branch=""; locked=0
                    if [ -z "$primary" ]; then primary="$path"; fi ;;
      "branch refs/heads/"*) branch="${line#branch refs/heads/}" ;;
      locked*) locked=1 ;;
      "") if [ "$branch" = "$BRANCH" ]; then holder="$path"; holder_locked="$locked"; fi ;;
    esac
  done < <(git -C "$ROOT" worktree list --porcelain 2>/dev/null || true; echo)

  if ! git -C "$ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    echo "No local branch $BRANCH; nothing to remove."
    return 0
  fi
  tip="$(git -C "$ROOT" rev-parse --verify "refs/heads/$BRANCH")"
  if ! is_ancestor "$tip" "$REMOTE/$MAIN"; then
    echo "Kept the local branch $BRANCH${holder:+ and its worktree $holder}: it has commits that are not on $MAIN."
    return 0
  fi
  if [ -n "$holder" ]; then
    if [ "$holder" = "$primary" ] || [ "$holder" = "$ROOT" ]; then
      echo "Kept $holder and $BRANCH: the main checkout, or the one this script runs from."
      return 0
    fi
    case "${holder##*/}" in
      "$ISSUE_WORKTREE"[0-9]*) ;;
      *) echo "Kept $holder and $BRANCH: the worktree is not named $ISSUE_WORKTREE<N>, so it is not an issue worktree."
         return 0 ;;
    esac
    if [ "$holder_locked" = 1 ]; then
      echo "Kept $holder and $BRANCH: the worktree is locked."
      return 0
    fi
    uses_directory "$holder" || used=$? # 0 stays when a process uses it
    case "$used" in
      0) echo "Kept $holder and $BRANCH: a process has its working directory in the worktree."; return 0 ;;
      2) echo "Kept $holder and $BRANCH: cannot tell whether a process uses the worktree."; return 0 ;;
    esac
    if ! git -C "$ROOT" worktree remove "$holder" >/dev/null 2>&1; then
      echo "Kept $holder and $BRANCH: the tree is not clean."
      return 0
    fi
    echo "Removed the worktree $holder."
  fi
  if git -C "$ROOT" branch -D "$BRANCH" >/dev/null 2>&1; then
    echo "Removed the local branch $BRANCH."
  else
    left "could not remove the local branch $BRANCH"
  fi
}

# ---- main -----------------------------------------------------------------------

parse_arguments() {
  [ $# -ge 1 ] || usage_error
  case "$1" in -h|--help) usage; exit 0 ;; esac
  PR="$1"
  shift
  case "$PR" in ''|0*|*[!0-9]*) usage_error "'$PR' is not a PR number" ;; esac
  while [ $# -gt 0 ]; do
    case "$1" in
      --reviewed) [ $# -ge 2 ] && [ -z "$REVIEWED" ] || usage_error; REVIEWED="$2"; shift 2 ;;
      --note-file) [ $# -ge 2 ] && [ -z "$NOTE" ] || usage_error; NOTE="$2"; shift 2 ;;
      *) usage_error "unknown argument '$1'" ;;
    esac
  done
  [ -n "$REVIEWED" ] && [ -n "$NOTE" ] || usage_error
  REVIEWED="$(printf '%s' "$REVIEWED" | tr 'A-F' 'a-f')"
  case "$REVIEWED" in *[!0-9a-f]*) usage_error "--reviewed needs the full commit id" ;; esac
  [ "${#REVIEWED}" -eq 40 ] || usage_error "--reviewed needs the full 40-character commit id (gh pr view $PR --json headRefOid)"
}

main() {
  local tool
  parse_arguments "$@"
  for tool in git gh python3; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required and was not found"
  done
  [ -f "$NOTE" ] && [ -r "$NOTE" ] || die "the note file $NOTE does not exist; nothing was done"
  grep -q '[^[:space:]]' "$NOTE" || die "the note file $NOTE is empty; nothing was done"
  require_maintainer_lock

  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  take_merge_lock
  check_pr
  update_branch
  wait_for_ci
  require_no_dependents
  merge_pr
  finish_issues
  remove_issue_worktree
  if release_merge_lock; then echo "Released $MERGE_LOCK."; else LEFT=1; fi
  [ "$LEFT" -eq 0 ] || exit 1
}

main "$@"
