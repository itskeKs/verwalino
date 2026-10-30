#!/usr/bin/env bash
# checks.sh - the project's commit-time gate. Invoked by .githooks/pre-commit
# (--staged); also runnable by hand (--all). It is meant to take seconds: by
# default it only lints what the commit touches and checks the docs links when
# Markdown is staged. The express test run belongs before the push (`make test`),
# the full run in CI (.github/workflows/tests-on-pr.yaml).
#
# Usage:
#   scripts/checks.sh --staged [backend] [frontend] [docs]   scopes derived from the git index
#   scripts/checks.sh --all    [backend] [frontend] [docs]   whole tree; no scope word = all three
#
# Scopes (--staged: selected when matching files are staged):
#   backend    backend/**/*.py, backend/pyproject.toml   -> ruff check (make lint-backend)
#   frontend   anything under frontend/                   -> eslint on the staged TS/JS files
#   docs       any *.md, scripts/check_docs.py            -> python3 scripts/check_docs.py
#
# Knobs (env vars):
#   VERWALINO_HOOK_SKIP=1          bypass the whole gate (prints a warning)
#   VERWALINO_HOOK_LINT=0|1        (default 1) ruff / eslint
#   VERWALINO_HOOK_DOCS=0|1        (default 1) Markdown link check
#   VERWALINO_HOOK_TESTS=off|fast|full   (default off) additionally run tests:
#       off    no tests (the quick default)
#       fast   --staged: frontend typecheck + vitest related to the staged frontend
#                        files, backend pytest of the staged backend/tests/*.py
#                        (needs the test DB on 127.0.0.1:55432)
#              --all:    frontend typecheck + whole vitest suite
#       full   make test: whole backend suite + vitest + typecheck (minutes, needs the test DB)
#
# Exit code: 0 = everything requested passed (or was skipped), non-zero = a failure.
# Bash 3.2 compatible (macOS default).
set -o pipefail

log() { printf 'checks: %s\n' "$*" >&2; }

if [ "${VERWALINO_HOOK_SKIP:-0}" = "1" ]; then
  log "VERWALINO_HOOK_SKIP=1 - gate bypassed, nothing run."
  exit 0
fi

if ! root=$(git rev-parse --show-toplevel 2>/dev/null); then
  root=$(cd "$(dirname "$0")/.." && pwd)
fi
cd "$root" || { log "cannot cd to repo root '$root'"; exit 1; }

mode=""
want_backend=0
want_frontend=0
want_docs=0
for arg in "$@"; do
  case "$arg" in
    --staged) mode=staged ;;
    --all) mode=all ;;
    backend) want_backend=1 ;;
    frontend) want_frontend=1 ;;
    docs) want_docs=1 ;;
    -h | --help) sed -n '2,/^# Bash 3.2/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) log "unknown argument '$arg' (see --help)"; exit 2 ;;
  esac
done
if [ -z "$mode" ]; then
  log "pass --staged or --all (see --help)"
  exit 2
fi

lint="${VERWALINO_HOOK_LINT:-1}"
docs="${VERWALINO_HOOK_DOCS:-1}"
tests="${VERWALINO_HOOK_TESTS:-off}"
case "$tests" in
  off | fast | full) ;;
  *) log "unknown VERWALINO_HOOK_TESTS='$tests' (want off|fast|full)"; exit 2 ;;
esac

# ---- what is in scope -------------------------------------------------------
frontend_lintable=() # staged TS/JS files under frontend/ (paths relative to frontend/)
frontend_sources=()  # staged files under frontend/src (relative to frontend/), for vitest related
backend_tests=()     # staged backend/tests/**/*.py test modules (relative to backend/)

if [ "$mode" = "staged" ]; then
  while IFS= read -r -d '' f; do
    case "$f" in
      backend/*.py | backend/pyproject.toml) want_backend=1 ;;
    esac
    case "$f" in
      frontend/package-lock.json) want_frontend=1 ;;
      frontend/node_modules/* | frontend/dist/*) ;;
      frontend/*) want_frontend=1 ;;
    esac
    case "$f" in
      *.md | scripts/check_docs.py) want_docs=1 ;;
    esac
  done < <(git diff --cached --name-only -z --diff-filter=ACMRD)
  # Only files that still exist after the change (not deletions) can be linted/tested.
  while IFS= read -r -d '' f; do
    case "$f" in
      frontend/node_modules/* | frontend/dist/*) ;;
      frontend/*.ts | frontend/*.tsx | frontend/*.js | frontend/*.jsx | frontend/*.mjs | frontend/*.cjs)
        frontend_lintable+=("${f#frontend/}")
        case "$f" in frontend/src/*) frontend_sources+=("${f#frontend/}") ;; esac ;;
    esac
    case "$f" in
      backend/tests/*.py)
        case "$(basename "$f")" in test_*.py | *_test.py) backend_tests+=("${f#backend/}") ;; esac ;;
    esac
  done < <(git diff --cached --name-only -z --diff-filter=ACMR)
  if [ "$want_backend$want_frontend$want_docs" = "000" ]; then
    log "no backend, frontend or Markdown changes staged - skipping checks."
    exit 0
  fi
elif [ "$want_backend$want_frontend$want_docs" = "000" ]; then
  want_backend=1; want_frontend=1; want_docs=1
fi

status=0
ran=""

have_frontend_deps() {
  if [ -d frontend/node_modules ]; then return 0; fi
  log "frontend/node_modules missing - skipping frontend checks (run: make frontend-deps)."
  return 1
}

# ---- lint -------------------------------------------------------------------
if [ "$lint" = "1" ]; then
  if [ "$want_backend" = "1" ]; then
    if command -v uv >/dev/null 2>&1; then
      log "lint: ruff (make lint-backend)"
      make --no-print-directory lint-backend || status=1
      ran="$ran backend-lint"
    else
      log "uv not found - cannot lint the backend."
      status=1
    fi
  fi
  if [ "$want_frontend" = "1" ] && have_frontend_deps; then
    if [ "$mode" = "all" ]; then
      log "lint: eslint over frontend/ (make lint-frontend)"
      make --no-print-directory lint-frontend || status=1
      ran="$ran frontend-lint"
    elif [ "${#frontend_lintable[@]}" -gt 0 ]; then
      log "lint: eslint on ${#frontend_lintable[@]} staged frontend file(s)"
      (cd frontend && npx --no-install eslint "${frontend_lintable[@]}") || status=1
      ran="$ran frontend-lint"
    fi
  fi
fi

# ---- tests (opt-in) ---------------------------------------------------------
case "$tests" in
  off) ;;
  full)
    log "tests: make test"
    make --no-print-directory test || status=1
    ran="$ran tests-full"
    ;;
  fast)
    if [ "$want_frontend" = "1" ] && have_frontend_deps; then
      log "tests: frontend typecheck (make typecheck)"
      make --no-print-directory typecheck || status=1
      if [ "$mode" = "all" ]; then
        log "tests: make test-frontend"
        make --no-print-directory test-frontend || status=1
      elif [ "${#frontend_sources[@]}" -gt 0 ]; then
        log "tests: vitest related to ${#frontend_sources[@]} staged frontend file(s)"
        (cd frontend && npx --no-install vitest related --run --passWithNoTests "${frontend_sources[@]}") || status=1
      fi
      ran="$ran frontend-tests"
    fi
    if [ "$mode" = "staged" ] && [ "${#backend_tests[@]}" -gt 0 ]; then
      log "tests: pytest on ${#backend_tests[@]} staged backend test module(s)"
      make --no-print-directory test-backend PYTEST_ARGS="-q $(printf '%q ' "${backend_tests[@]}")" || status=1
      ran="$ran backend-tests"
    fi
    ;;
esac

# ---- docs -------------------------------------------------------------------
if [ "$docs" = "1" ] && [ "$want_docs" = "1" ]; then
  log "docs: python3 scripts/check_docs.py (relative links and images)"
  python3 scripts/check_docs.py || status=1
  ran="$ran docs"
fi

if [ "$status" -eq 0 ]; then
  log "PASS (mode=$mode, tests=$tests, ran:${ran:- nothing})"
else
  log "FAIL - fix the reported issues before committing (bypass once: git commit --no-verify)."
fi
exit "$status"
