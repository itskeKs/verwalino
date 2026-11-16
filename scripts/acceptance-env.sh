#!/usr/bin/env bash
# One acceptance instance of a given commit, for the per-PR runs of a blind tester.
#
#   scripts/acceptance-env.sh start <commit>
#   scripts/acceptance-env.sh create-organization <name> <admin-email>
#   scripts/acceptance-env.sh erase-organization <name>
#   scripts/acceptance-env.sh stop
#
# `start` takes both locks, unpacks the commit with git archive into a scratch directory under /tmp,
# installs and builds it there, brings the shared test database (settings from scripts/test-env.sh) to the
# commit's migration head and runs the app detached on 127.0.0.1:18000 in self-hosted mode. The test
# database must already be running (`make check-testdb` says how to start it); this script starts none.
#
# The acceptance lock directory carries `info` (commit and time, as in the test database lock) and
# `instance` (scratch path and PID, start time and command line of the app). `stop` signals the app only
# when the live process still matches that record, and removes only what `start` created.
set -euo pipefail

# Fixed locations; the contract test rewrites exactly these four lines to run in a sandbox.
ACCEPTANCE_LOCK=/tmp/hausverwaltung-acceptance.lock
TESTDB_LOCK=/tmp/verwalino-testdb.lock
SCRATCH_PREFIX=/tmp/verwalino-acceptance
PORT=18000

HOST=127.0.0.1
HEALTH_WAIT_SECONDS=90
STOP_WAIT_SECONDS=10
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SHA=""
STARTING=0
IDENTITY_STARTED=""
IDENTITY_COMMAND=""
HOLD_ACCEPTANCE=0
HOLD_TESTDB=0
SCR=""
APP_PID=""
APP_STARTED=""
APP_COMMAND=""
INSTANCE_SCRATCH=""
INSTANCE_PID=""
INSTANCE_STARTED=""
INSTANCE_COMMAND=""

usage() {
  cat <<EOF
usage: scripts/acceptance-env.sh start <commit>
       scripts/acceptance-env.sh create-organization <name> <admin-email>
       scripts/acceptance-env.sh erase-organization <name>
       scripts/acceptance-env.sh stop
EOF
}

usage_error() { usage >&2; exit 2; }
die() { echo "acceptance-env: $*" >&2; exit 1; }

# ---- locks ------------------------------------------------------------------

lock_field() { # lock-directory field-number
  [ -f "$1/info" ] || return 0
  awk -v n="$2" 'NR == 1 { print $n }' "$1/info"
}

take_lock() { # lock-directory info-line
  if ! mkdir "$1" 2>/dev/null; then
    [ -e "$1" ] || die "cannot create $1"
    local holder
    holder="$(head -n 1 "$1/info" 2>/dev/null || true)"
    die "$1 is held${holder:+ ($holder)}; nothing was started"
  fi
  printf '%s\n' "$2" > "$1/info"
}

release_lock() { # lock-directory
  rm -f "$1/info" "$1/instance"
  rmdir "$1" 2>/dev/null || echo "acceptance-env: could not remove $1" >&2
}

# ---- processes --------------------------------------------------------------

ps_field() { # field pid
  { ps -o "$1=" -p "$2" 2>/dev/null | sed -e 's/^ *//' -e 's/ *$//'; } || true
}

process_alive() { # pid
  local state
  state="$(ps -o stat= -p "$1" 2>/dev/null)" || return 1
  case "$state" in *Z*) return 1 ;; esac
}

# The record of what `start` launched: same start time and same command line, not just the same number.
process_is_ours() { # pid started command
  [ "$(ps_field lstart "$1")" = "$2" ] && [ "$(ps_field command "$1")" = "$3" ]
}

wait_until_gone() { # pid seconds
  local waited=0
  while process_alive "$1" && [ "$waited" -lt $(($2 * 5)) ]; do
    sleep 0.2
    waited=$((waited + 1))
  done
  ! process_alive "$1"
}

# 0: gone (or never ran); 1: the PID is another process, nothing was signalled; 2: would not stop.
terminate_app() { # pid started command
  [ -n "$1" ] || return 0
  process_alive "$1" || return 0
  process_is_ours "$1" "$2" "$3" || return 1
  # The app was started as the leader of its own session, so its group is its own.
  kill -TERM -- "-$1" 2>/dev/null || kill -TERM "$1" 2>/dev/null || true
  wait_until_gone "$1" "$STOP_WAIT_SECONDS" && return 0
  process_is_ours "$1" "$2" "$3" || return 1
  kill -KILL -- "-$1" 2>/dev/null || kill -KILL "$1" 2>/dev/null || true
  wait_until_gone "$1" "$STOP_WAIT_SECONDS" || return 2
}

# A launch that was stopped before its identity was recorded is recognised by the scratch path in its command line.
claim_by_scratch_path() { # pid scratch: sets IDENTITY_STARTED and IDENTITY_COMMAND
  IDENTITY_STARTED="" IDENTITY_COMMAND=""
  process_alive "$1" || return 0
  local command
  command="$(ps_field command "$1")"
  case "$command" in
    *"$2/venv/bin/uvicorn"*) IDENTITY_STARTED="$(ps_field lstart "$1")" IDENTITY_COMMAND="$command" ;;
  esac
}

port_open() { (exec 3<>"/dev/tcp/$HOST/$PORT") 2>/dev/null; }

# ---- instance record --------------------------------------------------------

record() { printf '%s=%s\n' "$1" "$2" >> "$ACCEPTANCE_LOCK/instance"; }

valid_scratch() { # path
  case "$1" in "$SCRATCH_PREFIX".??????) ;; *) return 1 ;; esac
  [ -d "$1" ] && [ ! -L "$1" ] && [ -f "$1/.acceptance-env" ]
}

load_instance() {
  [ -d "$ACCEPTANCE_LOCK" ] || die "no acceptance instance is running; start one first"
  [ "$(lock_field "$ACCEPTANCE_LOCK" 3)" = acceptance-env ] \
    || die "$ACCEPTANCE_LOCK is not held by this script; not touching it"
  INSTANCE_SCRATCH="" INSTANCE_PID="" INSTANCE_STARTED="" INSTANCE_COMMAND=""
  local line
  # A start that was killed before it recorded anything leaves the lock without an `instance` file.
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      scratch=*) INSTANCE_SCRATCH="${line#scratch=}" ;;
      pid=*) INSTANCE_PID="${line#pid=}" ;;
      started=*) INSTANCE_STARTED="${line#started=}" ;;
      command=*) INSTANCE_COMMAND="${line#command=}" ;;
    esac
  done < <(cat "$ACCEPTANCE_LOCK/instance" 2>/dev/null || true)
  if [ -n "$INSTANCE_SCRATCH" ]; then
    valid_scratch "$INSTANCE_SCRATCH" || die "the recorded scratch directory $INSTANCE_SCRATCH is not one this script created"
  fi
}

require_running_instance() {
  load_instance
  [ -n "$INSTANCE_SCRATCH" ] && [ -n "$INSTANCE_PID" ] || die "the acceptance instance is not fully started"
  if ! process_alive "$INSTANCE_PID" || ! process_is_ours "$INSTANCE_PID" "$INSTANCE_STARTED" "$INSTANCE_COMMAND"; then
    die "the app of this acceptance instance is not running; run stop and start again"
  fi
  SCR="$INSTANCE_SCRATCH"
}

# ---- environment for the commit's own tools ---------------------------------

# NAME=value ... -- command ...: the test database settings of scripts/test-env.sh and nothing else the
# caller happens to have exported (no HV_* of another stack, no production value).
clean_env() {
  local assignments=()
  while [ "$1" != -- ]; do assignments+=("$1"); shift; done
  shift
  # shellcheck disable=SC2016 # $0 and $@ belong to the inner shell
  env -i PATH="$PATH" HOME="${HOME:-/}" \
    bash -c '. "$0"; exec env "$@"' "$ROOT/scripts/test-env.sh" "${assignments[@]}" "$@"
}

die_with_log() { # message: the tail of the start log shows why
  tail -n 25 "$SCR/start.log" >&2
  die "$*"
}

logged() { # command ...: output goes to the start log, shown only when the step fails
  "$@" >> "$SCR/start.log" 2>&1 || die_with_log "step failed: $*"
}

# ---- start ------------------------------------------------------------------

on_exit() {
  local status=$?
  trap - EXIT
  if [ "$STARTING" = 1 ]; then
    abort_start || true
  fi
  exit "$status"
}

abort_start() {
  set +e
  if [ -n "$APP_PID" ] && [ -z "$APP_STARTED" ]; then
    claim_by_scratch_path "$APP_PID" "$SCR"
    APP_STARTED="$IDENTITY_STARTED" APP_COMMAND="$IDENTITY_COMMAND"
  fi
  terminate_app "$APP_PID" "$APP_STARTED" "$APP_COMMAND"
  if [ -n "$SCR" ] && valid_scratch "$SCR"; then rm -rf "$SCR"; fi
  if [ "$HOLD_TESTDB" = 1 ]; then release_lock "$TESTDB_LOCK"; fi
  if [ "$HOLD_ACCEPTANCE" = 1 ]; then release_lock "$ACCEPTANCE_LOCK"; fi
}

unpack_commit() { git -C "$ROOT" archive "$SHA" backend frontend | tar -x -C "$SCR/src"; }
# The ops extra carries psycopg2, which Alembic and the bootstrap command need.
install_backend() { (cd "$SCR/src/backend" && UV_PROJECT_ENVIRONMENT="$SCR/venv" uv sync --frozen --extra ops); }
install_frontend() { (cd "$SCR/src/frontend" && npm ci --prefer-offline --no-audit --no-fund); }
# `npm run build` is `tsc -b && vite build`; the type check belongs to make lint, the bundle to this step.
build_frontend() { (cd "$SCR/src/frontend" && npx --no-install vite build --outDir "$SCR/dist" --emptyOutDir); }

backend_tool() { # NAME=value ... -- command ...: run in the unpacked backend directory
  (cd "$SCR/src/backend" && clean_env PYTHONDONTWRITEBYTECODE=1 "$@")
}

# The revisions recorded in the test database, one per line; nothing for a database without alembic_version.
# Read directly: `alembic current` fails for a revision the commit does not know, which is the case to detect.
database_revisions() {
  backend_tool -- "$SCR/venv/bin/python" -c '
import os
import psycopg2
import psycopg2.errors

connection = psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"])
try:
    with connection.cursor() as cursor:
        try:
            cursor.execute("SELECT version_num FROM alembic_version")
        except psycopg2.errors.UndefinedTable:
            raise SystemExit(0)
        for (revision,) in cursor.fetchall():
            print(revision)
finally:
    connection.close()
'
}

prepare_database() {
  local head current after
  if ! head="$(backend_tool -- "$SCR/venv/bin/alembic" heads 2>>"$SCR/start.log" | awk 'NF { print $1 }')"; then
    die_with_log "cannot read the migration head of the commit"
  fi
  [ -n "$head" ] && [ "$(printf '%s\n' "$head" | wc -l)" -eq 1 ] || die "the commit does not have exactly one migration head"
  if ! current="$(database_revisions 2>>"$SCR/start.log")"; then
    die_with_log "cannot read the revision of the test database"
  fi
  [ "$(printf '%s\n' "$current" | grep -c .)" -le 1 ] || die "the test database reports several revisions"
  if [ "$current" = "$head" ]; then
    echo "Database: at migration head $head."
    return 0
  fi
  # A revision the commit does not know belongs to a newer chain: ahead of this commit, not ours to downgrade.
  if [ -n "$current" ] && ! backend_tool -- "$SCR/venv/bin/alembic" show "$current" >/dev/null 2>&1; then
    die "the test database is at revision $current, which this commit does not know (it is ahead of $head); refusing to touch it"
  fi
  logged backend_tool -- "$SCR/venv/bin/alembic" upgrade head
  if ! after="$(database_revisions 2>>"$SCR/start.log")" || [ "$after" != "$head" ]; then
    die "the test database is at ${after:-no revision} after the upgrade, not at $head"
  fi
  echo "Database: migrated from ${current:-an empty schema} to $head."
}

launch_app() {
  local secret
  secret="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(32))')"
  # Detached into its own session, so that closing the caller's shell or session does not take the app along.
  APP_PID="$(cd "$SCR/src/backend" && clean_env PYTHONDONTWRITEBYTECODE=1 \
      HV_DEPLOYMENT_MODE=selfhost HV_SESSION_SECRET="$secret" \
      HV_FRONTEND_DIST="$SCR/dist" HV_DOCUMENT_STORAGE_ROOT="$SCR/documents" -- \
    python3 -I -c '
import subprocess, sys
executable, log, host, port = sys.argv[1:5]
with open(log, "ab") as output:
    child = subprocess.Popen([executable, "app.main:app", "--host", host, "--port", port],
                             stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
print(child.pid)
' "$SCR/venv/bin/uvicorn" "$SCR/app.log" "$HOST" "$PORT")"
  record pid "$APP_PID"
}

wait_for_health() {
  local waited=0
  until curl -fsS --noproxy '*' --max-time 2 "http://$HOST:$PORT/api/health/live" >/dev/null 2>&1; do
    if ! process_alive "$APP_PID"; then tail -n 25 "$SCR/app.log" >&2; die "the app exited before it answered"; fi
    waited=$((waited + 1))
    if [ "$waited" -ge $((HEALTH_WAIT_SECONDS * 5)) ]; then
      tail -n 25 "$SCR/app.log" >&2
      die "no answer on /api/health/live within ${HEALTH_WAIT_SECONDS}s"
    fi
    sleep 0.2
  done
  # Recorded now, not at launch: an interpreter launcher can still exec again right after the launch.
  APP_STARTED="$(ps_field lstart "$APP_PID")"
  APP_COMMAND="$(ps_field command "$APP_PID")"
  [ -n "$APP_STARTED" ] && [ -n "$APP_COMMAND" ] || die "the app is gone after it answered"
  record started "$APP_STARTED"
  record command "$APP_COMMAND"
}

start() {
  [ $# -eq 1 ] || usage_error
  case "$1" in ""|-*) die "'$1' is not a commit" ;; esac
  local tool
  for tool in git tar uv npm npx python3 curl make; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required and was not found"
  done
  SHA="$(git -C "$ROOT" rev-parse --verify --quiet "$1^{commit}")" || die "'$1' is not a commit of $ROOT"

  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  STARTING=1
  local info
  info="$SHA $(date -u +%Y-%m-%dT%H:%M:%SZ) acceptance-env"
  take_lock "$ACCEPTANCE_LOCK" "$info"; HOLD_ACCEPTANCE=1
  take_lock "$TESTDB_LOCK" "$info"; HOLD_TESTDB=1
  ! port_open || die "port $PORT on $HOST is already in use; nothing was started"
  make -C "$ROOT" --no-print-directory check-testdb || die "the test database is not running"

  SCR="$(mktemp -d "$SCRATCH_PREFIX.XXXXXX")"
  chmod 700 "$SCR"
  : > "$SCR/.acceptance-env"
  record scratch "$SCR"
  mkdir "$SCR/src" "$SCR/documents"

  echo "Unpacking $SHA ..."
  logged unpack_commit
  echo "Installing backend and frontend dependencies ..."
  logged install_backend
  logged install_frontend
  echo "Building the frontend ..."
  logged build_frontend
  prepare_database
  echo "Starting the app ..."
  launch_app
  wait_for_health

  STARTING=0
  cat <<EOF
Acceptance instance ready.
  URL:      http://$HOST:$PORT/
  Commit:   $SHA
  PID:      $APP_PID
  Scratch:  $SCR (app log: $SCR/app.log)
  Locks:    $ACCEPTANCE_LOCK, $TESTDB_LOCK
Next: scripts/acceptance-env.sh create-organization "<name>" <admin-email>
EOF
}

# ---- create-organization ----------------------------------------------------

sign_in_works() { # email; the password comes on stdin
  python3 -I -c '
import json, sys, urllib.request
url, email = sys.argv[1:3]
request = urllib.request.Request(url, data=json.dumps({"email": email, "password": sys.stdin.read()}).encode(),
                                 headers={"Content-Type": "application/json"})
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=30) as answer:
        sys.exit(0 if answer.status == 200 else 1)
except OSError:
    sys.exit(1)
' "http://$HOST:$PORT/api/auth/login" "$1"
}

create_organization() {
  [ $# -eq 2 ] || usage_error
  local name="$1" email="$2" password
  case "$name$email" in *$'\n'*|*$'\r'*) die "name and address must be one line each" ;; esac
  [ -n "$name" ] && [ -n "$email" ] || die "organization name and administrator address must not be empty"
  require_running_instance
  password="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(18))')"
  # The installation's own bootstrap command; the password reaches it on stdin, never in a command line.
  printf '%s\n' "$password" \
    | backend_tool -- "$SCR/venv/bin/python" -m app.admin_cli --org "$name" --email "$email" --password-stdin \
    || die "the bootstrap command failed"
  # The command ends quietly for an address that already exists; only a working sign-in proves the result.
  printf '%s' "$password" | sign_in_works "$email" \
    || die "no sign-in as $email works after the bootstrap command (does the address already exist?); nothing printed"
  printf '%s\n' "$name" >> "$SCR/organizations"
  cat <<EOF
Organization created: $name
Sign-in:  http://$HOST:$PORT/
E-mail:   $email
Password: $password
EOF
}

# ---- erase-organization -----------------------------------------------------

erase_organization() {
  [ $# -eq 1 ] || usage_error
  local name="$1"
  [ -n "$name" ] || die "organization name must not be empty"
  require_running_instance
  grep -Fxq -- "$name" "$SCR/organizations" 2>/dev/null \
    || die "'$name' was not created by create-organization in this run; refusing to erase it"
  echo "Simulated waiting time: the seven-day waiting period of the requested deletion is not waited for;"
  echo "this one command runs with its clock eight days ahead and erases that organization only."
  # The product's own erasure run (python -m app.organization_deletion), limited to this one organization.
  backend_tool HV_DOCUMENT_STORAGE_ROOT="$SCR/documents" -- "$SCR/venv/bin/python" - "$name" <<'PY' \
    || die "the erasure did not complete for '$name'"
import asyncio
import datetime as real_datetime
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.organization_deletion as erasure
import app.services.organization_deletion_service as service
from app.core.config import settings


class Shifted(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.datetime.now(tz) + real_datetime.timedelta(days=8)


service.datetime = Shifted  # the clock of this process only


async def main(name):
    engine = create_async_engine(settings.database_url_migrator)
    try:
        async with AsyncSession(engine) as db:
            ids = (await db.execute(text(
                "SELECT o.id FROM organization o JOIN organization_deletion d ON d.organization_id = o.id "
                "WHERE o.name = :name AND d.status = 'cooling_off'"), {"name": name})).scalars().all()
    finally:
        await engine.dispose()
    if len(ids) != 1:
        print(f"Exactly one organization named '{name}' with a confirmed deletion is required; found {len(ids)}.")
        return 1
    result = await erasure.run(organization_ids=[ids[0]])
    print(f"erased: {result.completed}, failed: {len(result.failed)}, file cleanup pending: {len(result.cleanup_pending)}")
    return 0 if result.completed == 1 and not result.failed and not result.cleanup_pending else 1


sys.exit(asyncio.run(main(sys.argv[1])))
PY
  echo "Erased organization '$name': its administrator address can be used again."
}

# ---- stop -------------------------------------------------------------------

stop() {
  [ $# -eq 0 ] || usage_error
  if [ ! -d "$ACCEPTANCE_LOCK" ]; then
    echo "No acceptance instance is running."
    return 0
  fi
  load_instance
  local status=0 outcome=0 running=0
  if [ -n "$INSTANCE_PID" ] && [ -z "$INSTANCE_STARTED" ] && [ -n "$INSTANCE_SCRATCH" ]; then
    claim_by_scratch_path "$INSTANCE_PID" "$INSTANCE_SCRATCH"
    INSTANCE_STARTED="$IDENTITY_STARTED" INSTANCE_COMMAND="$IDENTITY_COMMAND"
  fi
  if [ -n "$INSTANCE_PID" ] && process_alive "$INSTANCE_PID"; then running=1; fi
  terminate_app "$INSTANCE_PID" "$INSTANCE_STARTED" "$INSTANCE_COMMAND" || outcome=$?
  case "$outcome" in
    1) die "PID $INSTANCE_PID is not the process this script started; nothing was stopped or removed" ;;
    2) die "the app (PID $INSTANCE_PID) did not stop; nothing was removed" ;;
  esac
  if [ "$running" = 1 ]; then echo "Stopped the app (PID $INSTANCE_PID)."; fi
  if [ -n "$INSTANCE_SCRATCH" ]; then
    rm -rf "$INSTANCE_SCRATCH"
    echo "Removed $INSTANCE_SCRATCH."
  fi
  if [ "$(cat "$TESTDB_LOCK/info" 2>/dev/null || true)" = "$(cat "$ACCEPTANCE_LOCK/info")" ]; then
    release_lock "$TESTDB_LOCK"
  else
    echo "acceptance-env: $TESTDB_LOCK is not the lock of this run; left in place" >&2
    status=1
  fi
  release_lock "$ACCEPTANCE_LOCK"
  if port_open; then
    echo "acceptance-env: port $PORT is still in use by another process" >&2
    status=1
  fi
  [ "$status" -eq 0 ] || exit "$status"
  echo "Done: no process, no scratch directory and no lock of this run is left; port $PORT is free."
}

# ---- main -------------------------------------------------------------------

[ $# -ge 1 ] || usage_error
subcommand="$1"
shift
case "$subcommand" in
  start) start "$@" ;;
  create-organization) create_organization "$@" ;;
  erase-organization) erase_organization "$@" ;;
  stop) stop "$@" ;;
  -h|--help) usage ;;
  *) usage_error ;;
esac
