#!/usr/bin/env bash
# Lint staged Python/TypeScript code and always check tracked Markdown links.
# Bash 3.2 compatible (macOS default).
set -o pipefail
cd "$(dirname "$0")/.." || exit 1

status=0
backend_changed=0
set --
while IFS= read -r -d '' file; do
  case "$file" in
    backend/*.py) backend_changed=1 ;;
    frontend/*.ts | frontend/*.tsx) set -- "$@" "${file#frontend/}" ;;
  esac
done < <(git diff --cached --name-only -z --diff-filter=ACMR)

if [ "$backend_changed" -eq 1 ]; then
  (cd backend && uv run --locked --extra dev ruff check .) || status=1
fi
if [ "$#" -gt 0 ]; then
  if [ -d frontend/node_modules ]; then
    (cd frontend && npx --no-install eslint "$@") || status=1
  else
    printf 'checks: frontend/node_modules missing - skipping eslint.\n' >&2
  fi
fi

python3 scripts/check_docs.py || status=1

if [ "$status" -eq 0 ]; then
  printf 'checks: PASS\n' >&2
else
  printf 'checks: FAIL\n' >&2
fi
exit "$status"
