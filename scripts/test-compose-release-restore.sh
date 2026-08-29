#!/bin/sh
# Disposable Docker Compose regression for fixed-project replacement semantics.
# It creates an isolated project, never joins Traefik, and removes only its own
# exact project resources on exit.
set -eu

workspace=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmpdir=$(mktemp -d "$workspace/.compose-release-restore.XXXXXX")
project="hausverwaltung-release-restore-$(date +%s)-$$"
old_compose="$tmpdir/old-compose.yml"
new_compose="$tmpdir/new-compose.yml"
init_script="$tmpdir/shared/prod-init.sh"
postgres_image='postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685'
old_app_image='alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc'
new_app_image='alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d'
started=0

compose_release() {
  compose_file=$1
  app_image=$2
  release_id=$3
  shift 3
  APP_IMAGE="$app_image" HV_RELEASE_ID="$release_id" \
    docker compose --project-name "$project" -f "$compose_file" "$@"
}

cleanup() {
  status=$?
  if [ "$started" -eq 1 ]; then
    compose_release "$new_compose" "$new_app_image" new \
      down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$tmpdir"
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$tmpdir/shared"
printf '%s\n' \
  '#!/bin/sh' \
  'set -eu' \
  'psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE ROLE app_rw LOGIN PASSWORD '\''integration-app-password'\'';"' \
  > "$init_script"
chmod 600 "$init_script"

write_compose() {
  target=$1
  printf '%s\n' \
    'services:' \
    '  db:' \
    "    image: $postgres_image" \
    '    environment:' \
    '      POSTGRES_DB: hausverwaltung' \
    '      POSTGRES_USER: app_migrator' \
    '      POSTGRES_PASSWORD: integration-only-password' \
    '      APP_RW_PASSWORD: integration-app-password' \
    '    volumes:' \
    "      - $init_script:/docker-entrypoint-initdb.d/10-app-rw.sh:ro" \
    '    healthcheck:' \
    '      test: ["CMD-SHELL", "pg_isready -U app_migrator -d hausverwaltung"]' \
    '      interval: 1s' \
    '      timeout: 1s' \
    '      retries: 30' \
    '  app:' \
    '    image: ${APP_IMAGE:?set APP_IMAGE to an immutable image reference}' \
    '    command: ["sh", "-c", "sleep 300"]' \
    '    labels:' \
    '      com.karat.hausverwaltung.release: ${HV_RELEASE_ID:?set the release identifier}' \
    > "$target"
}

write_compose "$old_compose"
write_compose "$new_compose"

wait_for_health() {
  container_id=$1
  attempt=1
  while [ "$attempt" -le 30 ]; do
    if [ "$(docker inspect --format '{{.State.Health.Status}}' "$container_id")" = healthy ]; then
      return 0
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  return 1
}

wait_for_database_role() {
  compose_file=$1
  attempt=1
  while [ "$attempt" -le 30 ]; do
    if compose_release "$compose_file" "$old_app_image" old exec -T db sh -ec \
      'PGPASSWORD="$APP_RW_PASSWORD" psql -U app_rw -d hausverwaltung -Atqc "SELECT current_user"' | \
      grep -Fxq app_rw; then
      return 0
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  return 1
}

compose_release "$old_compose" "$old_app_image" old up -d
started=1
old_db_id=$(compose_release "$old_compose" "$old_app_image" old ps -q db)
old_app_id=$(compose_release "$old_compose" "$old_app_image" old ps -q app)
[ -n "$old_db_id" ] && [ -n "$old_app_id" ]
wait_for_health "$old_db_id"
wait_for_database_role "$old_compose"
old_app_image_id=$(docker inspect --format '{{.Image}}' "$old_app_id")

# The production helper must protect the shared database from replacement.
compose_release "$new_compose" "$new_app_image" new up -d --no-recreate db
new_db_id=$(compose_release "$new_compose" "$new_app_image" new ps -q db)
[ "$new_db_id" = "$old_db_id" ]

# Compose replaces fixed-project services when their release configuration
# changes. Simulate a later deployment gate failure and restore the old app.
compose_release "$new_compose" "$new_app_image" new up -d --no-deps app
new_app_id=$(compose_release "$new_compose" "$new_app_image" new ps -q app)
[ "$new_app_id" != "$old_app_id" ]
new_label=$(docker inspect --format '{{index .Config.Labels "com.karat.hausverwaltung.release"}}' "$new_app_id")
[ "$new_label" = new ]
new_app_image_id=$(docker inspect --format '{{.Image}}' "$new_app_id")
[ -n "$new_app_image_id" ]
docker stop "$new_app_id" >/dev/null

compose_release "$old_compose" "$old_app_image_id" old up -d --no-deps app
restored_app_id=$(compose_release "$old_compose" "$old_app_image_id" old ps -q app)
[ -n "$restored_app_id" ]
[ "$(docker inspect --format '{{.Image}}' "$restored_app_id")" = "$old_app_image_id" ]
[ "$(docker inspect --format '{{.Config.Image}}' "$restored_app_id")" = "$old_app_image_id" ]
[ "$(docker inspect --format '{{index .Config.Labels "com.karat.hausverwaltung.release"}}' "$restored_app_id")" = old ]
[ "$(compose_release "$old_compose" "$old_app_image_id" old ps -q db)" = "$old_db_id" ]

printf '%s\n' 'real Compose release-restore regression passed'
