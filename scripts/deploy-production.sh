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
image="${APP_IMAGE:-hausverwaltung-alpha:$RELEASE_ID}"
env_file="$root/shared/.env"
sbom_file="$root/shared/$RELEASE_ID.sbom.cdx.json"
base_url="https://verwaltung.karat.immo"
app_start_attempted=0
app_cleanup_active=0
previous_app_id=
new_app_id=
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

cleanup() {
  status=$?
  if [ "$promoted" -ne 1 ] && [ "$app_start_attempted" -eq 1 ]; then
    # If an interrupt lands immediately after the atomic current rename, the
    # durable pointer wins: never leave that new current pointing at a stopped
    # app. Otherwise this is still the unpromoted new app and is safe to stop.
    if ! { [ -L "$root/current" ] && [ "$(readlink "$root/current")" = "releases/$RELEASE_ID" ]; }; then
      stop_new_release_app || true
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
test -d "$release"
test -f "$env_file"
test "$(stat -c '%a' "$env_file")" = 600
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

compose() {
  APP_IMAGE="$image" docker compose --project-name hausverwaltung-alpha \
    --env-file "$env_file" -f "$release/docker-compose.prod.yml" "$@"
}

is_new_release_app() {
  candidate_app_id=$1
  [ -n "$candidate_app_id" ] || return 1
  [ "$candidate_app_id" != "$previous_app_id" ] || return 1
  inspected_app_id=$(docker inspect --format '{{.Id}}' "$candidate_app_id" 2>/dev/null) || return 1
  [ "$inspected_app_id" = "$candidate_app_id" ] || return 1
  inspected_app_image=$(docker inspect --format '{{.Config.Image}}' "$candidate_app_id" 2>/dev/null) || return 1
  [ "$inspected_app_image" = "$image" ]
}

stop_new_release_app() {
  candidate_app_id=$new_app_id
  # `compose up` may have created the new app and then failed before the
  # command returned. In that case discover it, but only after comparing it
  # with the pre-existing ID and exact configured image.
  if [ -z "$candidate_app_id" ] && [ "$app_cleanup_active" -eq 0 ]; then
    candidate_app_id=$(compose ps -q app 2>/dev/null || true)
  fi
  is_new_release_app "$candidate_app_id" || return 0
  docker stop "$candidate_app_id" >/dev/null 2>&1
}

wait_for_health() {
  service=$1
  attempt=1
  while [ "$attempt" -le 30 ]; do
    container_id=$(compose ps -q "$service")
    if [ -n "$container_id" ] && [ "$(docker inspect --format '{{.State.Health.Status}}' "$container_id")" = healthy ]; then
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "timed out waiting for $service to become healthy" >&2
  compose ps >&2 || true
  return 1
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
fi
compose up -d db
wait_for_health db
compose --profile ops run --rm migrate
compose --profile ops run --rm migrate alembic current | grep -Eq '0027 \(head\)'
printf '%s\n' "$admin_password" | compose --profile ops run --rm -T bootstrap \
  python -m app.admin_cli \
  --org 'Karat Hausverwaltung Alpha' \
  --email 'admin@verwaltung.karat.immo' \
  --name 'Alpha Admin' \
  --password-stdin

# Capture the old ID before Compose can replace it. A failed `up` can still
# create a new container, so the trap only stops a discovered container whose
# immutable ID and configured image prove it belongs to this release.
previous_app_id=$(compose ps -q app)
app_start_attempted=1
compose up -d app
wait_for_health app
new_app_id=$(compose ps -q app)
is_new_release_app "$new_app_id" || {
  echo "new app container was not created from the expected release image" >&2
  exit 1
}
app_cleanup_active=1
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
db_id=$(compose ps -q db)
for container_id in "$app_id" "$db_id"; do
  [ -n "$container_id" ] || { echo "missing expected runtime container" >&2; exit 1; }
  port_bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$container_id")
  case "$port_bindings" in null|'{}') ;; *) echo "runtime container has published ports" >&2; exit 1 ;; esac
done
if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$app_id" \
  | grep -Eq '^HV_DATABASE_URL_(MIGRATOR|SYNC)='; then
  echo "runtime app unexpectedly contains a privileged database URL" >&2
  exit 1
fi
compose exec -T app python -c "
import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    engine = create_async_engine(os.environ['HV_DATABASE_URL'])
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT current_user')) == 'app_rw'
    finally:
        await engine.dispose()

asyncio.run(main())
"

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
