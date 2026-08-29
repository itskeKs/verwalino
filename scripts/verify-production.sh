#!/bin/sh
# Disposable CI/local-only smoke test. It refuses a populated `traefik`
# network, so it can never be run against the live Moon proxy network.
set -eu

: "${HV_VERIFY_COMPOSE_FILE:?set HV_VERIFY_COMPOSE_FILE to a Compose file}"

# Deliberately not configurable: a caller must never be able to point cleanup
# at a known/live Compose project.
HV_VERIFY_PROJECT_NAME="hausverwaltung-verify-$(date +%s)-$$"
started=0

compose() {
  docker compose --project-name "$HV_VERIFY_PROJECT_NAME" -f "$HV_VERIFY_COMPOSE_FILE" "$@"
}

network_container_ids() {
  docker network inspect --format '{{range $id, $_ := .Containers}}{{println $id}}{{end}}' traefik
}

assert_owned_resources() {
  for container_id in $(docker ps -aq --filter "label=com.docker.compose.project=$HV_VERIFY_PROJECT_NAME"); do
    project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$container_id")
    service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")
    [ "$project" = "$HV_VERIFY_PROJECT_NAME" ] || return 1
    case "$service" in db|app) ;; *) return 1 ;; esac
  done

  for volume in $(docker volume ls -q --filter "label=com.docker.compose.project=$HV_VERIFY_PROJECT_NAME"); do
    project=$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$volume")
    [ "$project" = "$HV_VERIFY_PROJECT_NAME" ] || return 1
    case "$volume" in
      "${HV_VERIFY_PROJECT_NAME}_pgdata"|"${HV_VERIFY_PROJECT_NAME}_documents") ;;
      *) return 1 ;;
    esac
  done

  backend_network="${HV_VERIFY_PROJECT_NAME}_backend"
  if docker network inspect "$backend_network" >/dev/null 2>&1; then
    project=$(docker network inspect --format '{{index .Labels "com.docker.compose.project"}}' "$backend_network")
    [ "$project" = "$HV_VERIFY_PROJECT_NAME" ] || return 1
  fi

  for container_id in $(network_container_ids); do
    project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$container_id")
    service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")
    [ "$project" = "$HV_VERIFY_PROJECT_NAME" ] && [ "$service" = "app" ] || return 1
  done
}

cleanup() {
  status=$?
  [ "$started" -eq 1 ] || exit "$status"
  if ! assert_owned_resources; then
    echo "refusing verification cleanup: unexpected project or Traefik attachment" >&2
    exit 1
  fi
  compose down --volumes >/dev/null 2>&1 || exit 1
  exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

if ! docker network inspect traefik >/dev/null 2>&1; then
  echo "verification requires a pre-created, isolated external Docker network named 'traefik'" >&2
  exit 1
fi
if [ -n "$(network_container_ids)" ]; then
  echo "refusing verification: 'traefik' has existing container attachments" >&2
  exit 1
fi

wait_for_health() {
  service=$1
  attempt=1
  while [ "$attempt" -le 30 ]; do
    container_id=$(compose ps -q "$service")
    if [ -n "$container_id" ] && [ "$(docker inspect --format '{{.State.Health.Status}}' "$container_id")" = "healthy" ]; then
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "timed out waiting for $service to become healthy" >&2
  compose ps >&2 || true
  return 1
}

started=1
compose up -d db
wait_for_health db
compose --profile ops run --rm migrate
compose --profile ops run --rm migrate alembic current | grep -Eq '0023 \(head\)'
compose up -d app
wait_for_health app

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

if docker inspect --format '{{range $key, $_ := .Config.Labels}}{{println $key}}{{end}}' "$app_id" | \
  grep -Eqi '\.basicauth\.'; then
  echo "runtime app unexpectedly retains a Basic Auth label" >&2
  exit 1
fi

assert_label_value() {
  label=$1
  expected_value=$2
  actual_value=$(docker inspect --format "{{with index .Config.Labels \"$label\"}}{{.}}{{end}}" "$app_id")
  if [ "$actual_value" != "$expected_value" ]; then
    echo "runtime label does not match the approved value: $label" >&2
    exit 1
  fi
}
assert_label_value traefik.http.middlewares.hausverwaltung-alpha-buffer.buffering.maxRequestBodyBytes 31457280
assert_label_value traefik.http.middlewares.hausverwaltung-alpha-buffer.buffering.memRequestBodyBytes 1048576
assert_label_value traefik.http.middlewares.hausverwaltung-alpha-login-rate.ratelimit.average 5
assert_label_value traefik.http.middlewares.hausverwaltung-alpha-login-rate.ratelimit.period 1m
assert_label_value traefik.http.middlewares.hausverwaltung-alpha-login-rate.ratelimit.burst 5

assert_router_middlewares() {
  router_label=$1
  expected_middlewares=$2
  actual_middlewares=$(docker inspect --format "{{with index .Config.Labels \"$router_label\"}}{{.}}{{end}}" "$app_id")
  if [ "$actual_middlewares" != "$expected_middlewares" ]; then
    echo "runtime router middleware chain does not match the approved value: $router_label" >&2
    exit 1
  fi
}
assert_router_middlewares traefik.http.routers.hausverwaltung-alpha.middlewares \
  hausverwaltung-alpha-security,hausverwaltung-alpha-buffer
assert_router_middlewares traefik.http.routers.hausverwaltung-alpha-login.middlewares \
  hausverwaltung-alpha-security,hausverwaltung-alpha-buffer,hausverwaltung-alpha-login-rate

compose exec -T app python -c "
import urllib.request
for endpoint in ('/api/health/live', '/api/health/ready'):
    request = urllib.request.Request(
        'http://127.0.0.1:8000' + endpoint,
        headers={'Host': 'verwaltung.karat.immo'},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise SystemExit(f'{endpoint}: {response.status}')
"

echo "isolated production smoke passed for $HV_VERIFY_PROJECT_NAME"
