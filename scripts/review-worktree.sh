#!/usr/bin/env bash
# The review worktree of a PR: the first and the last step of a fresh review.
#
#   scripts/review-worktree.sh create <PR>
#   scripts/review-worktree.sh remove <PR>
#
# `create` asks GitHub for the PR's state and head branch, refuses a PR that is not open and a directory that
# already exists (an existing review worktree means that PR is under review), fetches the branch, adds the
# detached worktree ../verwalino-review-<PR> beside the main checkout on origin/<branch>, installs the locked
# backend environment there and prints the directory and the line that loads the test environment. A failed
# install removes the worktree again, so that a second call is not refused because of it.
#
# `remove` needs no network. It removes the worktree only when its tree is clean and no process has a file or
# its working directory in it; otherwise it names what keeps it and changes nothing.
#
# Neither command takes a lock, starts a database or runs a test.
set -euo pipefail

REPO=itskeKs/verwalino

usage() {
  cat <<'EOF'
usage: scripts/review-worktree.sh create <PR>
       scripts/review-worktree.sh remove <PR>
EOF
}

usage_error() {
  [ $# -eq 0 ] || echo "review-worktree: $1" >&2
  usage >&2
  exit 2
}
die() { echo "review-worktree: $*" >&2; exit 1; }

create() { # PR
  local pr="$1" dir="$BASE/verwalino-review-$1" listing state branch
  if [ -e "$dir" ] || [ -L "$dir" ]; then
    die "$dir exists: PR #$pr is under review there, or it is a leftover; nothing was changed"
  fi
  listing="$(gh pr view "$pr" -R "$REPO" --json state,headRefName --template '{{.state}} {{.headRefName}}')" \
    || die "cannot read PR #$pr from GitHub"
  read -r state branch <<< "$listing"
  [ "$state" = OPEN ] || die "PR #$pr is ${state:-unknown}, not open; no review worktree was made"
  [ -n "$branch" ] || die "PR #$pr has no head branch; no review worktree was made"
  git -C "$MAIN" fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch" \
    || die "cannot fetch branch $branch of PR #$pr from origin; no review worktree was made"
  git -C "$MAIN" worktree add --quiet --detach "$dir" "origin/$branch"
  if ! ( cd "$dir/backend" && uv sync --locked --extra dev --extra ops --extra ocr ); then
    if git -C "$MAIN" worktree remove "$dir"; then
      die "installing the backend environment failed; the new worktree $dir was removed again"
    fi
    die "installing the backend environment failed; remove $dir with: make review-worktree-remove PR=$pr"
  fi
  echo "review worktree for PR #$pr ($branch): $dir"
  printf 'load the test environment: cd %q && source scripts/test-env.sh\n' "$dir"
}

remove() { # PR
  local pr="$1" dir="$BASE/verwalino-review-$1" registered status processes rc=0 kept=0
  [ -e "$dir" ] || die "no review worktree for PR #$pr at $dir"
  registered="$(git -C "$MAIN" worktree list --porcelain)"
  if [ -L "$dir" ] || [ ! -d "$dir" ] || ! grep -Fxq "worktree $dir" <<< "$registered"; then
    die "$dir is not a worktree of this repository; left in place"
  fi
  status="$(git -C "$dir" status --porcelain)" || die "cannot read the state of $dir"
  if [ -n "$status" ]; then
    kept=1
    echo "review-worktree: $dir is not clean:" >&2
    awk 'NR <= 10 { print "  " $0 } END { if (NR > 10) print "  ..." }' <<< "$status" >&2
  fi
  processes="$(lsof -w -F pc +D "$dir" 2>/dev/null)" || rc=$?
  case "$rc" in
    0 | 1) ;; # lsof exits 1 when nothing is open there
    *) die "lsof failed (exit $rc); cannot tell whether a process uses $dir; left in place" ;;
  esac
  if [ -n "$processes" ]; then
    kept=1
    echo "review-worktree: processes use $dir:" >&2
    awk '
      /^p/ { pid = substr($0, 2) }
      /^c/ { line = "  pid " pid " " substr($0, 2); if (!seen[line]++) print line }' <<< "$processes" >&2
  fi
  [ "$kept" -eq 0 ] || die "$dir was not removed"
  git -C "$MAIN" worktree remove "$dir"
  echo "removed $dir"
}

[ $# -eq 2 ] || usage_error
case "$1" in
  create | remove) ;;
  *) usage_error "unknown command: $1" ;;
esac
[[ "$2" =~ ^[1-9][0-9]*$ ]] || usage_error "PR must be a positive number, e.g. make review-worktree PR=123"

# The review worktree sits beside the main checkout, also when this script runs in another worktree of it.
MAIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && cd "$(git rev-parse --git-common-dir)" && cd .. && pwd -P)"
BASE="$(dirname "$MAIN")"
"$1" "$2"
