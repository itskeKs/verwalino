#!/bin/sh
# Host-side, initial-alpha deployment gate. It is deliberately invoked only
# after a reviewed release archive has been extracted on Moon. The first stdin
# line is the admin password; interactive use reads it with terminal echo
# disabled. It is neither persisted nor echoed.
set -eu

: "${DEPLOY_ROOT:?set DEPLOY_ROOT}"
: "${RELEASE_ID:?set RELEASE_ID}"

case "$RELEASE_ID" in
  ''|*[!A-Za-z0-9._-]*)
    echo "RELEASE_ID must contain only letters, digits, dot, underscore, or dash" >&2
    exit 2
    ;;
esac

root=$DEPLOY_ROOT
release="$root/releases/$RELEASE_ID"
if [ "${APP_IMAGE+x}" = x ]; then
  echo "manual deployment builds the reviewed release locally; APP_IMAGE overrides are not supported" >&2
  exit 2
fi
image="hausverwaltung-alpha:$RELEASE_ID"
image_id=
env_file="$root/shared/.env"
sbom_file="$root/shared/$RELEASE_ID.sbom.cdx.json"
db_init_source="$release/infra/db/prod-init.sh"
db_init_file="$root/shared/prod-init.sh"
db_init_tmp=
base_url="https://verwaltung.karat.immo"
app_start_attempted=0
worker_start_attempted=0
previous_app_id=
previous_worker_id=
previous_app_image_id=
previous_worker_image_id=
previous_release=
previous_release_id=
previous_release_path=
new_app_id=
new_worker_id=
db_before_id=
db_after_id=
promoted=0
cookie_jar=
public_headers=
http_redirect_headers=
login_headers=
session_cookie_headers=
traversal_body=
oversize_file=
previous_release_tmp=

read_secret() {
  prompt=$1
  secret=
  if [ -t 0 ]; then
    printf '%s: ' "$prompt" >&2
    stty -echo
    if ! IFS= read -r secret; then
      stty echo
      printf '\n' >&2
      return 1
    fi
    stty echo
    printf '\n' >&2
  elif ! IFS= read -r secret; then
    return 1
  fi
  [ -n "$secret" ] || return 1
  printf '%s' "$secret"
}

dotenv_setting_is_explicit_false() {
  setting_name=$1
  awk -v name="$setting_name" '
    /^[[:space:]]*#/ { next }
    {
      line = $0
      sub(/^[[:space:]]*export[[:space:]]+/, "", line)
      if (index(line, name "=") != 1) next
      value = substr(line, length(name) + 2)
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      sub(/[[:space:]]+#.*/, "", value)
      quote = sprintf("%c", 34)
      single_quote = sprintf("%c", 39)
      if (length(value) >= 2 && ((substr(value, 1, 1) == quote && substr(value, length(value), 1) == quote) || (substr(value, 1, 1) == single_quote && substr(value, length(value), 1) == single_quote))) {
        value = substr(value, 2, length(value) - 2)
      }
      explicit_false = (value == "false" || value == "False" || value == "FALSE" || value == "0" || value == "off" || value == "OFF" || value == "no" || value == "NO")
    }
    END { exit explicit_false ? 0 : 1 }
  ' "$env_file"
}

dotenv_setting_is_provider_key() {
  setting_name=$1
  awk -v name="$setting_name" '
    /^[[:space:]]*#/ { next }
    {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      sub(/^export[[:space:]]+/, "", line)
      if (index(line, name "=") != 1) next
      value = substr(line, length(name) + 2)
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      valid = (value ~ /^[A-Za-z0-9._-]+$/)
    }
    END { exit valid ? 0 : 1 }
  ' "$env_file"
}

declaration_import_is_enabled() {
  if [ "${HV_DECLARATION_IMPORT_ENABLED+x}" = x ]; then
    case "$HV_DECLARATION_IMPORT_ENABLED" in
      false|False|FALSE|0|off|OFF|no|NO) return 1 ;;
      *) return 0 ;;
    esac
  fi
  dotenv_setting_is_explicit_false HV_DECLARATION_IMPORT_ENABLED && return 1
  return 0
}

is_provider_key_value() {
  case "$1" in
    ''|*[!A-Za-z0-9._-]*) return 1 ;;
    *) return 0 ;;
  esac
}

require_declaration_provider_key() {
  declaration_import_is_enabled || return 0
  if [ "${HV_OPENAI_API_KEY+x}" = x ]; then
    is_provider_key_value "$HV_OPENAI_API_KEY" && return 0
  elif dotenv_setting_is_provider_key HV_OPENAI_API_KEY; then
    return 0
  fi
  echo "declaration import is enabled but no OpenAI provider key is configured" >&2
  return 1
}

cleanup() {
  status=$?
  if [ "$promoted" -ne 1 ] && [ "$app_start_attempted" -eq 1 ]; then
    # If an interrupt lands immediately after the atomic current rename, the
    # durable pointer wins: never leave that new current pointing at a stopped
    # app. Otherwise this is still the unpromoted new app and is safe to stop.
    if ! { [ -L "$root/current" ] && [ "$(readlink "$root/current")" = "releases/$RELEASE_ID" ]; }; then
      # Stop each exact-release runtime container independently. A failed
      # `compose up` may have created one service but not the other, and the
      # previous release containers must remain untouched.
      [ "$worker_start_attempted" -eq 0 ] || stop_new_release_container worker "$new_worker_id" "$previous_worker_id" || true
      stop_new_release_container app "$new_app_id" "$previous_app_id" || true
      if ! restore_previous_runtime; then
        echo "failed to restore the previous runtime after an unpromoted release failure" >&2
        status=1
      fi
    fi
  fi
  [ -z "$cookie_jar" ] || rm -f "$cookie_jar"
  [ -z "$public_headers" ] || rm -f "$public_headers"
  [ -z "$http_redirect_headers" ] || rm -f "$http_redirect_headers"
  [ -z "$login_headers" ] || rm -f "$login_headers"
  [ -z "$session_cookie_headers" ] || rm -f "$session_cookie_headers"
  [ -z "$traversal_body" ] || rm -f "$traversal_body"
  [ -z "$oversize_file" ] || rm -f "$oversize_file"
  [ -z "$previous_release_tmp" ] || rm -f "$previous_release_tmp"
  exit "$status"
}
trap cleanup EXIT
trap 'stty echo 2>/dev/null || true; exit 1' HUP INT TERM

install -d -m 700 "$root" "$root/releases" "$root/shared"
command -v flock >/dev/null 2>&1 || {
  echo "deployment requires flock for exclusive host locking" >&2
  exit 2
}
umask 077
: > "$root/shared/deploy.lock"
chmod 600 "$root/shared/deploy.lock"
exec 9>"$root/shared/deploy.lock"
flock -n 9 || {
  echo "another deployment is already running for this host" >&2
  exit 1
}
test -d "$release"
test -f "$env_file"
test "$(stat -c '%a' "$env_file")" = 600
require_declaration_provider_key
test "$(docker network inspect traefik --format '{{(index .IPAM.Config 0).Subnet}}')" = 172.21.0.0/16

admin_password=$(read_secret "Admin password") || {
  echo "missing admin password" >&2
  exit 2
}
test "${#admin_password}" -ge 12
test "${#admin_password}" -le 128

# The exact, host-built image is scanned before it can create or touch a
# database container. The SBOM lives in the 0700 shared directory at mode 0600.
docker build --pull --tag "$image" "$release"
umask 077
: > "$sbom_file"
chmod 600 "$sbom_file"
docker run --rm --user "$(id -u):$(stat -c '%g' /var/run/docker.sock)" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$root/shared:/output" \
  aquasec/trivy:0.69.0@sha256:33f816d414b9d582d25bb737ffa4a632ae34e222f7ec1b50252cef0ce2266006 \
  image --cache-dir /tmp/trivy-cache --scanners vuln --format cyclonedx \
  --output "/output/$RELEASE_ID.sbom.cdx.json" "$image"
chmod 600 "$sbom_file"
test -s "$sbom_file"
docker run --rm --user "$(id -u):$(stat -c '%g' /var/run/docker.sock)" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:0.69.0@sha256:33f816d414b9d582d25bb737ffa4a632ae34e222f7ec1b50252cef0ce2266006 \
  image --cache-dir /tmp/trivy-cache --scanners vuln --ignore-unfixed \
  --severity HIGH,CRITICAL --exit-code 1 "$image"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
[ -n "$image_id" ] || {
  echo "could not capture the locally built image ID" >&2
  exit 1
}
test -f "$db_init_source"
umask 077
db_init_tmp="$root/shared/.prod-init.$RELEASE_ID.$$"
install -m 600 "$db_init_source" "$db_init_tmp"
mv -f "$db_init_tmp" "$db_init_file"
db_init_tmp=

compose_release() {
  compose_release_path=$1
  compose_image=$2
  compose_release_id=$3
  shift 3
  APP_IMAGE="$compose_image" HV_DB_INIT_SCRIPT="$db_init_file" HV_RELEASE_ID="$compose_release_id" \
    docker compose --project-name hausverwaltung-alpha \
      --env-file "$env_file" -f "$compose_release_path/docker-compose.prod.yml" "$@"
}

compose() {
  compose_release "$release" "$image" "$RELEASE_ID" "$@"
}

is_new_release_container() {
  service=$1
  candidate_container_id=$2
  previous_container_id=$3
  case "$service" in app|worker) ;; *) return 1 ;; esac
  [ -n "$candidate_container_id" ] || return 1
  [ "$candidate_container_id" != "$previous_container_id" ] || return 1
  inspected_container_id=$(docker inspect --format '{{.Id}}' "$candidate_container_id" 2>/dev/null) || return 1
  [ "$inspected_container_id" = "$candidate_container_id" ] || return 1
  inspected_container_image_id=$(docker inspect --format '{{.Image}}' "$candidate_container_id" 2>/dev/null) || return 1
  [ "$inspected_container_image_id" = "$image_id" ] || return 1
  inspected_release_label=$(docker inspect --format '{{index .Config.Labels "com.karat.hausverwaltung.release"}}' "$candidate_container_id" 2>/dev/null) || return 1
  [ "$inspected_release_label" = "$RELEASE_ID" ]
}

stop_new_release_container() {
  service=$1
  candidate_container_id=$2
  previous_container_id=$3
  # `compose up` may have created a runtime container and then failed before
  # returning. Discover only that service, then require a new ID and the exact
  # release image before stopping it.
  if [ -z "$candidate_container_id" ]; then
    candidate_container_id=$(compose ps -q "$service" 2>/dev/null || true)
  fi
  is_new_release_container "$service" "$candidate_container_id" "$previous_container_id" || return 0
  docker stop "$candidate_container_id" >/dev/null 2>&1
}

wait_for_release_health() {
  health_release_path=$1
  health_image_id=$2
  health_release_id=$3
  service=$4
  attempt=1
  while [ "$attempt" -le 30 ]; do
    container_id=$(compose_release "$health_release_path" "$health_image_id" "$health_release_id" ps -q "$service")
    if [ -n "$container_id" ] && \
      [ "$(docker inspect --format '{{.State.Health.Status}}' "$container_id")" = healthy ]; then
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "timed out waiting for $service to become healthy" >&2
  compose_release "$health_release_path" "$health_image_id" "$health_release_id" ps >&2 || true
  return 1
}

wait_for_health() {
  wait_for_release_health "$release" "$image" "$RELEASE_ID" "$1"
}

wait_for_database_role() {
  attempt=1
  while [ "$attempt" -le 30 ]; do
    if compose exec -T db sh -ec \
      'PGPASSWORD="$APP_RW_PASSWORD" psql -U app_rw -d hausverwaltung -Atqc "SELECT current_user"' | \
      grep -Fxq app_rw; then
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "database became healthy but app_rw did not become ready within 60 seconds" >&2
  compose ps >&2 || true
  return 1
}

restore_previous_runtime() {
  [ -n "$previous" ] || return 0
  if [ -z "$previous_release_path" ] || [ -z "$previous_app_id" ] || [ -z "$previous_app_image_id" ]; then
    echo "cannot restore previous runtime: captured app identity is incomplete" >&2
    return 1
  fi
  compose_release "$previous_release_path" "$previous_app_image_id" "$previous_release_id" up -d --no-deps app || return 1
  wait_for_release_health "$previous_release_path" "$previous_app_image_id" "$previous_release_id" app || return 1
  restored_app_id=$(compose_release "$previous_release_path" "$previous_app_image_id" "$previous_release_id" ps -q app)
  [ -n "$restored_app_id" ] || return 1
  [ "$(docker inspect --format '{{.Image}}' "$restored_app_id")" = "$previous_app_image_id" ] || return 1
  if [ -n "$previous_worker_id" ]; then
    [ -n "$previous_worker_image_id" ] || {
      echo "cannot restore previous runtime: captured worker identity is incomplete" >&2
      return 1
    }
    compose_release "$previous_release_path" "$previous_worker_image_id" "$previous_release_id" up -d --no-deps worker || return 1
    wait_for_release_health "$previous_release_path" "$previous_worker_image_id" "$previous_release_id" worker || return 1
    restored_worker_id=$(compose_release "$previous_release_path" "$previous_worker_image_id" "$previous_release_id" ps -q worker)
    [ -n "$restored_worker_id" ] || return 1
    [ "$(docker inspect --format '{{.Image}}' "$restored_worker_id")" = "$previous_worker_image_id" ] || return 1
  fi
}

validate_release_target() {
  link_target=$1
  case "$link_target" in
    releases/*) ;;
    *) return 1 ;;
  esac
  resolved_target=$(readlink -f "$root/$link_target") || return 1
  case "$resolved_target" in
    "$root"/releases/*) [ -d "$resolved_target" ] ;;
    *) return 1 ;;
  esac
}

previous=
if [ -e "$root/current" ] || [ -L "$root/current" ]; then
  [ -L "$root/current" ] || {
    echo "refusing non-symlink current pointer" >&2
    exit 1
  }
  previous=$(readlink "$root/current")
  validate_release_target "$previous" || {
    echo "refusing current pointer outside releases" >&2
    exit 1
  }
  previous_release_id=${previous#releases/}
  previous_release_path=$(readlink -f "$root/$previous")
  previous_app_id=$(compose ps -q app)
  [ -n "$previous_app_id" ] || {
    echo "current release exists but its app container could not be captured for restoration" >&2
    exit 1
  }
  [ "$(docker inspect --format '{{.Id}}' "$previous_app_id")" = "$previous_app_id" ] || {
    echo "current release app identity could not be validated for restoration" >&2
    exit 1
  }
  previous_app_image_id=$(docker inspect --format '{{.Image}}' "$previous_app_id")
  [ -n "$previous_app_image_id" ] || {
    echo "current release app has no immutable image ID" >&2
    exit 1
  }
  previous_worker_id=$(compose ps -q worker)
  if [ -n "$previous_worker_id" ]; then
    [ "$(docker inspect --format '{{.Id}}' "$previous_worker_id")" = "$previous_worker_id" ] || {
      echo "current release worker identity could not be validated for restoration" >&2
      exit 1
    }
    previous_worker_image_id=$(docker inspect --format '{{.Image}}' "$previous_worker_id")
    [ -n "$previous_worker_image_id" ] || {
      echo "current release worker has no immutable image ID" >&2
      exit 1
    }
  fi
fi
db_before_id=$(compose ps -q db)
compose up -d --no-recreate db
wait_for_health db
wait_for_database_role
db_after_id=$(compose ps -q db)
[ -n "$db_after_id" ] || { echo "database container is missing after startup" >&2; exit 1; }
if [ -n "$db_before_id" ] && [ "$db_before_id" != "$db_after_id" ]; then
  echo "refusing deployment: Compose replaced the existing database container" >&2
  exit 1
fi
compose --profile ops run --rm --no-deps migrate
migration_state=$(compose --profile ops run --rm --no-deps migrate alembic current)
[ "$migration_state" = '0094 (head)' ] || {
  echo "migration state is not exactly 0094 (head)" >&2
  exit 1
}
printf '%s\n' "$admin_password" | compose --profile ops run --rm --no-deps -T bootstrap \
  python -m app.admin_cli \
  --org 'Karat Hausverwaltung Alpha' \
  --email 'admin@verwaltung.karat.immo' \
  --name 'Alpha Admin' \
  --password-stdin

# The previous runtime was captured before Compose can replace fixed-project
# services. A failed `up` can still create a new container, so the trap only
# stops a discovered container proven to belong to this exact release.
app_start_attempted=1
compose up -d --no-deps app
wait_for_health app
new_app_id=$(compose ps -q app)
is_new_release_container app "$new_app_id" "$previous_app_id" || {
  echo "new app container was not created from the expected release image" >&2
  exit 1
}
# This disposable container overrides the worker entrypoint, so it never runs
# the consuming worker loop. It makes only Models API GET requests to the
# SDK's fixed OpenAI origin; no document, prompt, response, or tool request is
# sent. The existing worker stays alive until the checked replacement starts.
if declaration_import_is_enabled; then
  compose run --rm --no-deps --entrypoint python worker -m app.workers.declaration_provider_smoke
fi
worker_start_attempted=1
compose up -d --no-deps worker
wait_for_health worker
new_worker_id=$(compose ps -q worker)
is_new_release_container worker "$new_worker_id" "$previous_worker_id" || {
  echo "new worker container was not created from the expected release image" >&2
  exit 1
}
compose exec -T app python -c "
import urllib.request
request = urllib.request.Request(
    'http://127.0.0.1:8000/api/health/ready',
    headers={'Host': 'verwaltung.karat.immo'},
)
with urllib.request.urlopen(request, timeout=10) as response:
    assert response.status == 200
"

app_id=$(compose ps -q app)
worker_id=$(compose ps -q worker)
db_id=$(compose ps -q db)
for container_id in "$app_id" "$worker_id" "$db_id"; do
  [ -n "$container_id" ] || { echo "missing expected runtime container" >&2; exit 1; }
  port_bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$container_id")
  case "$port_bindings" in null|'{}') ;; *) echo "runtime container has published ports" >&2; exit 1 ;; esac
done
for runtime_service in app worker; do
  case "$runtime_service" in
    app) runtime_id=$app_id ;;
    worker) runtime_id=$worker_id ;;
  esac
  runtime_image_id=$(docker inspect --format '{{.Image}}' "$runtime_id")
  [ "$runtime_image_id" = "$image_id" ] || {
    echo "runtime $runtime_service was not started from the expected release image" >&2
    exit 1
  }
  if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$runtime_id" \
    | grep -Eq '^HV_DATABASE_URL_(MIGRATOR|SYNC)='; then
    echo "runtime $runtime_service unexpectedly contains a privileged database URL" >&2
    exit 1
  fi
done
assert_runtime_db_role() {
  runtime_service=$1
  case "$runtime_service" in
    app) runtime_url_key=HV_DATABASE_URL; runtime_role=app_rw ;;
    worker) runtime_url_key=HV_DATABASE_URL_WORKER; runtime_role=app_worker ;;
    review) runtime_url_key=HV_DATABASE_URL_REVIEW; runtime_role=app_review; runtime_service=app ;;
  esac
  compose exec -T "$runtime_service" python -c "
import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.db import REVIEW_ROLE_CHECK

async def main():
    engine = create_async_engine(os.environ['$runtime_url_key'])
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT current_user')) == '$runtime_role'
            if '$runtime_role' == 'app_review':
                assert await connection.scalar(text(REVIEW_ROLE_CHECK)), 'Unsafe review role topology'
    finally:
        await engine.dispose()

asyncio.run(main())
"
}
assert_runtime_db_role app
assert_runtime_db_role worker
assert_runtime_db_role review

cookie_jar=$(mktemp)
public_headers=$(mktemp)
http_redirect_headers=$(mktemp)
login_headers=$(mktemp)
session_cookie_headers=$(mktemp)
traversal_body=$(mktemp)
oversize_file=$(mktemp)
chmod 600 "$cookie_jar" "$public_headers" "$http_redirect_headers" "$login_headers" "$session_cookie_headers" "$traversal_body" "$oversize_file"

wait_for_public_app() {
  attempt=1
  while [ "$attempt" -le 36 ]; do
    public_status=$(curl --silent --show-error --output /dev/null \
      --connect-timeout 1 --max-time 2 --proto '=https' --tlsv1.2 \
      --write-out '%{http_code}' "$base_url/api/health/live" || true)
    if [ "$public_status" = 200 ]; then
      return 0
    fi
    sleep 3
    attempt=$((attempt + 1))
  done
  echo "public Traefik/Let's Encrypt route did not become app-ready within 180 seconds" >&2
  compose ps >&2 || true
  docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$app_id" >&2 || true
  return 1
}

wait_for_public_app
http_redirect_status=$(curl --silent --show-error --output /dev/null --dump-header "$http_redirect_headers" \
  --connect-timeout 3 --max-time 20 --max-redirs 0 --proto '=http' \
  --write-out '%{http_code}' 'http://verwaltung.karat.immo/api/health/live' || true)
case "$http_redirect_status" in
  301|308) ;;
  *) echo "HTTP did not permanently redirect to HTTPS" >&2; exit 1 ;;
esac
grep -Eqi '^Location:[[:space:]]*https://verwaltung[.]karat[.]immo/api/health/live[[:space:]]*$' "$http_redirect_headers" || {
  echo "HTTP redirect target was not the exact HTTPS health URL" >&2
  exit 1
}
curl --fail --silent --show-error --max-time 20 --proto '=https' --tlsv1.2 \
  --dump-header "$public_headers" \
  "$base_url/api/health/ready" >/dev/null
for header_pattern in \
  '^Strict-Transport-Security:' \
  '^Content-Security-Policy:' \
  '^X-Content-Type-Options:[[:space:]]*nosniff' \
  '^X-Frame-Options:[[:space:]]*DENY' \
  '^Referrer-Policy:[[:space:]]*no-referrer'; do
  grep -Eqi "$header_pattern" "$public_headers" || {
    echo "missing required public security header" >&2
    exit 1
  }
done
unauthenticated_session_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --max-time 20 --proto '=https' --tlsv1.2 "$base_url/api/auth/session" || true)
test "$unauthenticated_session_status" = 401 || {
  echo "unauthenticated public session endpoint did not return 401" >&2
  exit 1
}
printf '%s' "$admin_password" | compose exec -T app python -c "
import json
import sys

print(json.dumps({
    'email': 'admin@verwaltung.karat.immo',
    'password': sys.stdin.read(),
}, separators=(',', ':')))
" | curl --fail --silent --show-error --max-time 20 --proto '=https' --tlsv1.2 \
    --cookie "$cookie_jar" --cookie-jar "$cookie_jar" \
    --dump-header "$login_headers" --header 'Content-Type: application/json' \
    --data-binary @- "$base_url/api/auth/login" >/dev/null
session_cookie_count=$(grep -Eic '^Set-Cookie:[[:space:]]*__Host-hv_session=' "$login_headers" || true)
test "$session_cookie_count" -eq 1 || {
  echo "expected exactly one session Set-Cookie header" >&2
  exit 1
}
grep -Ei '^Set-Cookie:[[:space:]]*__Host-hv_session=' "$login_headers" > "$session_cookie_headers"
grep -Eqi ';[[:space:]]*Secure([;[:space:]]|$)' "$session_cookie_headers"
grep -Eqi ';[[:space:]]*HttpOnly([;[:space:]]|$)' "$session_cookie_headers"
grep -Eqi ';[[:space:]]*SameSite=Lax([;[:space:]]|$)' "$session_cookie_headers"
grep -Eqi ';[[:space:]]*Path=/([;[:space:]]|$)' "$session_cookie_headers"
if grep -Eqi ';[[:space:]]*Domain=' "$session_cookie_headers"; then
  echo "session cookie must not set a Domain attribute" >&2
  exit 1
fi
curl --fail --silent --show-error --max-time 20 --proto '=https' --tlsv1.2 \
  --cookie "$cookie_jar" "$base_url/api/auth/session" | \
  grep -Eq '"role":"OWNER_ADMIN"'
curl --fail --silent --show-error --max-time 20 --proto '=https' --tlsv1.2 \
  --cookie "$cookie_jar" "$base_url/api/organizations/me" | \
  grep -Fq '"name":"Karat Hausverwaltung Alpha"'
curl --fail --silent --show-error --max-time 20 --proto '=https' --tlsv1.2 \
  "$base_url/api/health/ready" >/dev/null

assert_safe_traversal() {
  traversal_path=$1
  traversal_status=$(curl --silent --show-error --output "$traversal_body" --write-out '%{http_code}' \
    --max-time 20 --path-as-is --proto '=https' --tlsv1.2 \
    "$base_url/$traversal_path" || true)
  case "$traversal_status" in
    400|404) ;;
    # The SPA fallback is acceptable only when it is demonstrably not file
    # content. This exercises both API routing and the non-API static resolver.
    200) grep -Eqi '<!doctype html|<html' "$traversal_body" ;;
    *) echo "unexpected traversal response status" >&2; return 1 ;;
  esac
  if grep -Eqi 'root:|/etc/passwd' "$traversal_body"; then
    echo "traversal response leaked filesystem content" >&2
    return 1
  fi
}
assert_safe_traversal 'api/%2e%2e/%2e%2e/etc/passwd'
assert_safe_traversal '%5c..%5c..%5cetc%5cpasswd'
dd if=/dev/zero of="$oversize_file" bs=1048576 count=26 status=none
oversize_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --max-time 60 --proto '=https' --tlsv1.2 \
  --cookie "$cookie_jar" -F "file=@$oversize_file;type=application/pdf" \
  "$base_url/api/invoice-documents/upload" || true)
test "$oversize_status" = 413

if [ -e "$root/current.new" ] || [ -L "$root/current.new" ]; then
  [ -L "$root/current.new" ] || { echo "refusing non-symlink stale current.new" >&2; exit 1; }
  stale_target=$(readlink "$root/current.new")
  validate_release_target "$stale_target" || {
    echo "refusing stale current.new outside releases" >&2
    exit 1
  }
  unlink "$root/current.new"
fi
if [ -n "$previous" ]; then
  previous_release_tmp="$root/shared/.previous-release.$RELEASE_ID.$$"
  umask 077
  printf '%s\n' "$previous" > "$previous_release_tmp"
  chmod 600 "$previous_release_tmp"
  mv -f "$previous_release_tmp" "$root/shared/previous-release"
  previous_release_tmp=
fi
ln -s "releases/$RELEASE_ID" "$root/current.new"
mv -Tf "$root/current.new" "$root/current"
promoted=1
echo "promoted $RELEASE_ID"
