#!/bin/sh
# Disposable CI/local-only smoke test. It refuses a populated `traefik`
# network, so it can never be run against the live Moon proxy network.
set -eu

: "${HV_VERIFY_COMPOSE_FILE:?set HV_VERIFY_COMPOSE_FILE to a Compose file}"
: "${APP_IMAGE:?set APP_IMAGE to the immutable image digest under test}"

case "$APP_IMAGE" in
  *@sha256:*) ;;
  *)
    echo "verification requires APP_IMAGE to be an immutable digest reference" >&2
    exit 2
    ;;
esac

declaration_import_is_enabled() {
  case "${HV_DECLARATION_IMPORT_ENABLED:-true}" in
    false|False|FALSE|0|off|OFF|no|NO) return 1 ;;
    *) return 0 ;;
  esac
}

is_provider_key_value() {
  case "$1" in
    ''|*[!A-Za-z0-9._-]*) return 1 ;;
    *) return 0 ;;
  esac
}

if declaration_import_is_enabled && ! is_provider_key_value "${HV_OPENAI_API_KEY:-}"; then
  echo "declaration import is enabled but no OpenAI provider key is configured" >&2
  exit 2
fi

# Deliberately not configurable: a caller must never be able to point cleanup
# at a known/live Compose project.
HV_VERIFY_PROJECT_NAME="hausverwaltung-verify-$(date +%s)-$$"
HV_VERIFY_RELEASE_ID="verification-$HV_VERIFY_PROJECT_NAME"
started=0

compose() {
  HV_RELEASE_ID="$HV_VERIFY_RELEASE_ID" docker compose \
    --project-name "$HV_VERIFY_PROJECT_NAME" -f "$HV_VERIFY_COMPOSE_FILE" "$@"
}

network_container_ids() {
  docker network inspect --format '{{range $id, $_ := .Containers}}{{println $id}}{{end}}' traefik
}

assert_owned_resources() {
  for container_id in $(docker ps -aq --filter "label=com.docker.compose.project=$HV_VERIFY_PROJECT_NAME"); do
    project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$container_id")
    service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")
    [ "$project" = "$HV_VERIFY_PROJECT_NAME" ] || return 1
    case "$service" in db|app|worker) ;; *) return 1 ;; esac
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

expected_image_id=$(docker image inspect --format '{{.Id}}' "$APP_IMAGE")
[ -n "$expected_image_id" ] || {
  echo "could not capture the immutable image ID under test" >&2
  exit 1
}

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

started=1
compose up -d db
wait_for_health db
wait_for_database_role
compose --profile ops run --rm --no-deps migrate
migration_state=$(compose --profile ops run --rm --no-deps migrate alembic current)
[ "$migration_state" = '0028 (head)' ] || {
  echo "migration state is not exactly 0028 (head)" >&2
  exit 1
}
compose up -d app
wait_for_health app
compose up -d worker
wait_for_health worker

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
  [ "$runtime_image_id" = "$expected_image_id" ] || {
    echo "runtime $runtime_service was not started from the immutable image under test" >&2
    exit 1
  }
  runtime_release_label=$(docker inspect --format '{{index .Config.Labels "com.karat.hausverwaltung.release"}}' "$runtime_id")
  [ "$runtime_release_label" = "$HV_VERIFY_RELEASE_ID" ] || {
    echo "runtime $runtime_service does not carry the expected verification release label" >&2
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
  compose exec -T "$runtime_service" python -c "
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
}
assert_runtime_db_role app
assert_runtime_db_role worker

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
