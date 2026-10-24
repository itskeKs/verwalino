#!/bin/sh
# Focused regression probe for deploy-production.sh cleanup safety. It uses a
# self-hosted fake Docker CLI to reproduce failed and successful app/worker
# starts without ever touching a real Docker daemon.
set -eu

# Never inherit real developer provider/smoke credentials into the fake CLI.
unset HV_OPENAI_API_KEY HV_SMOKE_ADMIN_PASSWORD

if [ "$(basename "$0")" = sleep ]; then exit 0; fi

if [ "$(basename "$0")" = stat ]; then
  case "${2:-}" in
    '%a') printf '%s\n' 600 ;;
    '%g') printf '%s\n' 20 ;;
    *) printf '%s\n' 600 ;;
  esac
  exit 0
fi

if [ "$(basename "$0")" = mv ]; then
  case "${1:-}" in -Tf|-f) shift ;; esac
  case "${2:-}" in
    */current)
      [ -z "${FAKE_DOCKER_LOG:-}" ] || printf '%s\n' promoted >> "$FAKE_DOCKER_LOG"
      ;;
  esac
  python3 - "$1" "$2" <<'REPLACE'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
REPLACE
  exit 0
fi

if [ "$(basename "$0")" = flock ]; then
  exit 0
fi

if [ "$(basename "$0")" = readlink ]; then
  if [ "${1:-}" = -f ]; then
    printf '%s\n' "$FAKE_READLINK_F"
  else
    /usr/bin/readlink "$1"
  fi
  exit 0
fi

if [ "$(basename "$0")" = curl ]; then
  headers_file=
  output_file=
  write_out=0
  cookie_jar=
  has_cookie=0
  reads_login_payload=0
  login_payload=
  url=
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dump-header) headers_file=$2; shift 2 ;;
      --output) output_file=$2; shift 2 ;;
      --write-out) write_out=1; shift 2 ;;
      --cookie-jar) cookie_jar=$2; shift 2 ;;
      --connect-timeout|--max-time|--max-redirs|--proto|--tlsv1.2|--cookie|--header|-F)
        [ "$1" != --cookie ] || has_cookie=1
        case "$1" in --tlsv1.2) shift ;; *) shift 2 ;; esac
        ;;
      --data-binary)
        [ "$2" != @- ] || reads_login_payload=1
        shift 2
        ;;
      --silent|--show-error|--fail|--path-as-is) shift ;;
      *) url=$1; shift ;;
    esac
  done
  status=200
  response_headers=
  body=
  [ "$reads_login_payload" -eq 0 ] || login_payload=$(cat)
  case "$url" in
    http://verwaltung.karat.immo/api/health/live)
      status=308
      response_headers='Location: https://verwaltung.karat.immo/api/health/live'
      ;;
    https://verwaltung.karat.immo/api/health/live)
      status=200
      ;;
    https://verwaltung.karat.immo/api/health/ready)
      response_headers='Strict-Transport-Security: max-age=1
Content-Security-Policy: default-src '\''self'\''
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer'
      ;;
    https://verwaltung.karat.immo/api/auth/login)
      printf '%s' "$login_payload" | FAKE_ADMIN_PASSWORD="$FAKE_ADMIN_PASSWORD" python3 -c '
import json
import os
import sys
payload = json.load(sys.stdin)
assert payload == {
    "email": "admin@verwaltung.karat.immo",
    "password": os.environ["FAKE_ADMIN_PASSWORD"],
}
'
      response_headers='Set-Cookie: unrelated=value; Domain=example.invalid
Set-Cookie: __Host-hv_session=value; Secure; HttpOnly; SameSite=Lax; Path=/'
      : > "$cookie_jar"
      ;;
    https://verwaltung.karat.immo/api/auth/session)
      if [ "$has_cookie" -eq 1 ]; then
        body='{"role":"OWNER_ADMIN"}'
      else
        status=401
      fi
      ;;
    https://verwaltung.karat.immo/api/organizations/me) body='{"name":"Karat Hausverwaltung Alpha"}' ;;
    *'%2e%2e'*|*'%5c..'*) body='<!doctype html><html>safe spa</html>' ;;
    https://verwaltung.karat.immo/api/invoice-documents/upload) status=413 ;;
  esac
  [ -z "$headers_file" ] || printf '%s\n' "$response_headers" > "$headers_file"
  if [ -n "$output_file" ] && [ "$output_file" != /dev/null ]; then
    printf '%s\n' "$body" > "$output_file"
  elif [ -z "$output_file" ]; then
    printf '%s' "$body"
  fi
  [ "$write_out" -eq 0 ] || printf '%s' "$status"
  exit 0
fi

if [ "$(basename "$0")" = docker ]; then
  printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
  case "${1:-}" in
    ps) [ "${FAKE_FAILURE:-}" != competing ] || printf '%s\n' unknown-runtime; printf '%s\n' db-id old-app old-worker; [ "${FAKE_NO_PREVIOUS_INVOICE:-0}" = 1 ] || printf '%s\n' old-invoice; exit 0 ;;
    network)
      printf '%s\n' '172.21.0.0/16'
      exit 0
      ;;
    build)
      exit 0
      ;;
    image)
      case "$*" in
        *'inspect'*'{{.Id}}'*) printf '%s\n' new-image-id ;;
        *) printf '%s\n' unexpected-image-command >&2; exit 97 ;;
      esac
      exit 0
      ;;
    run)
      case " $* " in
        *' --entrypoint alembic '*) printf '%s\n' "${FAKE_PREVIOUS_HEAD:-0122} (head)"; exit 0 ;;
        *' archive-documents '*) [ "${FAKE_FAILURE:-}" != documents ] || exit 91; printf 'synthetic-documents'; exit 0 ;;
        *' verify-documents '*) cat >/dev/null; [ "${FAKE_FAILURE:-}" != archive-check ]; exit $? ;;
      esac
      printf '%s\n' '{}' > "$FAKE_SHARED/test.sbom.cdx.json"
      exit 0
      ;;
    exec)
      case " $* " in
        *' pg_dump '*) [ "${FAKE_FAILURE:-}" != backup ] || exit 91; printf 'synthetic-dump'; exit 0 ;;
        *' pg_restore --list '*) cat >/dev/null; [ "${FAKE_FAILURE:-}" != dump-check ]; exit $? ;;
      esac
      exit 97
      ;;
    inspect)
      case "$*" in
        *'{{.State.Running}}'*) printf '%s\n' false ;;
        *'com.docker.compose.project'*) printf '%s\n' hausverwaltung-alpha ;;
        *'com.docker.compose.service'*) case "$*" in *invoice*) printf '%s\n' invoice-worker ;; *worker*) printf '%s\n' worker ;; *) printf '%s\n' app ;; esac ;;
        *'com.karat.hausverwaltung.release'*'old-'*) printf '%s\n' old ;;
        *'{{.State.Health.Status}}'*'new-invoice'*) [ "${FAKE_FAILURE:-}" != invoice-health ] || { printf '%s\n' unhealthy; exit 0; }; printf '%s\n' healthy ;;
        *'{{.Id}}'*'invoice'*) printf '%s\n' "${4:-}" ;;
        *'{{.Image}}'*'new-invoice'*) printf '%s\n' new-image-id ;;
        *'{{.Image}}'*'old-invoice'*) printf '%s\n' old-image-id ;;
        *'{{.Image}}'*'restored-invoice'*) printf '%s\n' old-image-id ;;
        *'com.karat.hausverwaltung.release'*'new-invoice'*) printf '%s\n' test ;;
        *'com.karat.hausverwaltung.release'*'restored-'*) printf '%s\n' old ;;
        *'{{.State.Health.Status}}'*'db-id'*) printf '%s\n' healthy ;;
        *'{{.State.Health.Status}}'*'new-app'*) if [ "${FAKE_FAILURE:-}" = app-health ]; then printf '%s\n' unhealthy; else printf '%s\n' healthy; fi ;;
        *'{{.State.Health.Status}}'*'new-worker'*) if [ "${FAKE_FAILURE:-}" = worker-health ]; then printf '%s\n' unhealthy; else printf '%s\n' healthy; fi ;;
        *'{{.State.Health.Status}}'*'restored-app'*) printf '%s\n' healthy ;;
        *'{{.State.Health.Status}}'*'restored-worker'*) printf '%s\n' healthy ;;
        *'{{.Config.Image}}'*'new-app'*) printf '%s\n' 'hausverwaltung-alpha:test' ;;
        *'{{.Config.Image}}'*'new-worker'*) printf '%s\n' 'hausverwaltung-alpha:test' ;;
        *'{{.Id}}'*'new-app'*) printf '%s\n' new-app ;;
        *'{{.Id}}'*'new-worker'*) printf '%s\n' new-worker ;;
        *'{{.Config.Image}}'*'old-app'*) printf '%s\n' 'hausverwaltung-alpha:previous' ;;
        *'{{.Config.Image}}'*'old-worker'*) printf '%s\n' 'hausverwaltung-alpha:previous' ;;
        *'{{.Id}}'*'old-app'*) printf '%s\n' old-app ;;
        *'{{.Id}}'*'old-worker'*) printf '%s\n' old-worker ;;
        *'{{.Id}}'*'restored-app'*) printf '%s\n' restored-app ;;
        *'{{.Id}}'*'restored-worker'*) printf '%s\n' restored-worker ;;
        *'{{.Image}}'*'new-app'*) printf '%s\n' new-image-id ;;
        *'{{.Image}}'*'new-worker'*) printf '%s\n' new-image-id ;;
        *'{{.Image}}'*'old-app'*) printf '%s\n' old-image-id ;;
        *'{{.Image}}'*'old-worker'*) printf '%s\n' old-image-id ;;
        *'{{.Image}}'*'restored-app'*) printf '%s\n' old-image-id ;;
        *'{{.Image}}'*'restored-worker'*) printf '%s\n' old-image-id ;;
        *'com.karat.hausverwaltung.release'*'new-app'*) printf '%s\n' "${FAKE_NEW_LABEL:-test}" ;;
        *'com.karat.hausverwaltung.release'*'new-worker'*) printf '%s\n' test ;;
        *'{{json .HostConfig.PortBindings}}'*) printf '%s\n' '{}' ;;
        *'{{range .Config.Env}}'*) exit 0 ;;
        *) printf '%s\n' healthy ;;
      esac
      exit 0
      ;;
    stop)
      case "$*" in *new-worker*) [ "${FAKE_FAILURE:-}" != cleanup-stop-fail ] || exit 90 ;; esac
      printf '%s\n' "stopped:${4:-${2:-}}" >> "$FAKE_DOCKER_LOG"
      exit 0
      ;;
    compose)
      case " $* " in
        *' up -d --no-recreate db '*) exit 0 ;;
        *' ps -q invoice-worker '*)
          if [ -f "$FAKE_INVOICE_RESTORED" ]; then printf '%s\n' restored-invoice
          elif [ -f "$FAKE_INVOICE_CREATED" ]; then printf '%s\n' new-invoice
          elif [ "${FAKE_NO_PREVIOUS_INVOICE:-0}" != 1 ]; then printf '%s\n' old-invoice
          fi; exit 0 ;;
        *' up -d --no-deps invoice-worker '*)
          case "$*" in *'releases/old/'*) : > "$FAKE_INVOICE_RESTORED"; exit 0 ;; esac
          : > "$FAKE_INVOICE_CREATED"
          [ "${FAKE_FAILURE:-}" != invoice-start ]; exit $? ;;
        *' ps -q db '*) printf '%s\n' db-id; exit 0 ;;
        *' ps -q app '*)
          if [ -f "${FAKE_APP_RESTORED:-}" ]; then printf '%s\n' restored-app
          elif [ -f "$FAKE_APP_CREATED" ]; then printf '%s\n' new-app
          else printf '%s\n' old-app
          fi
          exit 0
          ;;
        *' ps -q worker '*)
          if [ "${FAKE_FAILURE:-}" = cleanup-discovery-fail ] && [ -f "$FAKE_WORKER_CREATED" ]; then exit 94; fi
          if [ -f "${FAKE_WORKER_RESTORED:-}" ]; then printf '%s\n' restored-worker
          elif [ -f "$FAKE_WORKER_CREATED" ]; then printf '%s\n' new-worker
          else printf '%s\n' old-worker
          fi
          exit 0
          ;;
        *' run --rm --no-deps --entrypoint python worker -m app.workers.declaration_provider_smoke '*)
          : > "$FAKE_PROVIDER_SMOKE_CALLED"
          [ "${FAKE_PROVIDER_SMOKE_FAIL:-0}" = 1 ] && exit 99
          exit 0
          ;;
        *' run '*'app.deployment preflight '*)
          [ "${FAKE_FAILURE:-}" != roles ] || exit 92
          [ "${FAKE_FAILURE:-}" != release-head ] || exit 92
          printf '%s\n' "${FAKE_SCHEMA:-0122}"; exit 0 ;;
        *' run '*'app.deployment current '*)
          if [ -f "$FAKE_SHARED/schema-changed" ]; then printf '%s\n' 0139; elif [ -f "$FAKE_MIGRATED" ]; then printf '%s\n' "${FAKE_TARGET:-0139}"; else printf '%s\n' "${FAKE_SCHEMA:-0122}"; fi; exit 0 ;;
        *' run '*'app.deployment quiescent '*)
          [ "${FAKE_FAILURE:-}" != busy ] || exit 93
          if [ "${FAKE_FAILURE:-}" = quiescent-changed ] && [ -f "$FAKE_SHARED/backups/test-from-0122/documents.tar.gz.partial" ]; then
            : > "$FAKE_SHARED/schema-changed"; printf '%s\n' 0139
          else printf '%s\n' "$FAKE_SCHEMA"; fi; exit 0 ;;
        *' run '*'app.deployment runtime '*) [ "${FAKE_FAILURE:-}" != credentials ]; exit $? ;;
        *' run --rm --no-deps migrate '*)
          : > "$FAKE_MIGRATED"; [ "${FAKE_FAILURE:-}" != migrate ]; exit $? ;;
        *' run '*' alembic current '*) printf '%s\n' '0122 (head)'; exit 0 ;;
        *' run '*) exit 0 ;;
        *' exec -T app python -c '*'import json'*)
          python3 -c '
import json
import sys
print(json.dumps({
    "email": "admin@verwaltung.karat.immo",
    "password": sys.stdin.read(),
}, separators=(",", ":")))
'
          exit 0
          ;;
        *' exec -T db sh -ec '*) printf '%s\n' app_rw; exit 0 ;;
        *' exec -T app python -c '*'import urllib.request'*) [ "${FAKE_FAILURE:-}" != api-ready ]; exit $? ;;
        *' exec '*) exit 0 ;;
        *' up -d --no-deps app '*)
          case "$*" in
            *'releases/old/docker-compose.prod.yml'*) : > "$FAKE_APP_RESTORED"; exit 0 ;;
          esac
          : > "$FAKE_APP_CREATED"
          [ "${FAKE_APP_UP_FAIL:-0}" = 1 ] && exit 99
          exit 0
          ;;
        *' up -d --no-deps worker '*)
          case "$*" in
            *'releases/old/docker-compose.prod.yml'*) : > "$FAKE_WORKER_RESTORED"; exit 0 ;;
          esac
          : > "$FAKE_WORKER_CREATED"
          [ "${FAKE_WORKER_UP_FAIL:-0}" = 1 ] && exit 99
          exit 0
          ;;
        *' stop app '*) printf '%s\n' compose-stop-app >> "$FAKE_DOCKER_LOG"; exit 0 ;;
      esac
      printf '%s\n' "unexpected-compose:$*" >&2
      exit 98
      ;;
  esac
  printf '%s\n' "unexpected-docker:$*" >&2
  exit 97
fi

fail() {
  printf '%s\n' "FAIL: $*" >&2
  [ -z "${tmpdir:-}" ] || sed -n '1,160p' "$tmpdir/docker.log" >&2
  exit 1
}

assert_gateway_basic_auth_removed() {
  operational_files="docker-compose.prod.yml scripts/deploy-production.sh .env.example .gitlab-ci.yml DEPLOY.md"
  if grep -Ein 'basicauth|basic auth|BASIC_AUTH_USERS|gateway_netrc|--netrc-file|HV_SMOKE_GATEWAY_PASSWORD|Gateway password|gateway_verifier|httpd:2[.]4-alpine' $operational_files; then
    fail 'operational deployment files still contain gateway Basic-Auth state or CI secret wiring'
  fi
  if grep -Ein 'BASIC_AUTH_USERS|bcrypt verifier' scripts/verify-production.sh; then
    fail 'runtime verifier still requires or validates a gateway Basic-Auth secret'
  fi
}

assert_admin_password_handling() {
  if grep -Fq 'URL-safe password' scripts/deploy-production.sh; then
    fail 'deploy script still rejects application-valid special-character admin passwords'
  fi
  if grep -Fq 'printf '{'"'"'"email":"admin@verwaltung.karat.immo","password":"%s"'"'"'}'' scripts/deploy-production.sh; then
    fail 'deploy script still interpolates the admin password into raw JSON'
  fi
  grep -Fq 'test "${#admin_password}" -le 128' scripts/deploy-production.sh || \
    fail 'deploy script does not enforce the 128-character admin-password maximum'
  grep -Fq 'compose exec -T app python -c' scripts/deploy-production.sh || \
    fail 'deploy script does not encode the login JSON inside the exact app container'
}

assert_verifier_label_assertions() {
  grep -Fq '{{range $key, $_ := .Config.Labels}}{{println $key}}{{end}}' scripts/verify-production.sh || \
    fail 'runtime verifier does not enumerate every app-container label key'
  grep -Fq "grep -Eqi '\\.basicauth\\.'" scripts/verify-production.sh || \
    fail 'runtime verifier does not reject case-insensitive Basic-Auth label keys'
  for expected in \
    'traefik.http.middlewares.hausverwaltung-alpha-buffer.buffering.maxRequestBodyBytes 31457280' \
    'traefik.http.middlewares.hausverwaltung-alpha-buffer.buffering.memRequestBodyBytes 1048576' \
    'traefik.http.middlewares.hausverwaltung-alpha-login-rate.ratelimit.average 5' \
    'traefik.http.middlewares.hausverwaltung-alpha-login-rate.ratelimit.period 1m' \
    'traefik.http.middlewares.hausverwaltung-alpha-login-rate.ratelimit.burst 5'; do
    grep -Fq "assert_label_value $expected" scripts/verify-production.sh || \
      fail "runtime verifier does not assert exact label value: $expected"
  done
}

assert_declaration_worker_runtime() {
  for file in scripts/deploy-production.sh scripts/verify-production.sh; do
    grep -Fq 'app.deployment' "$file" || fail "$file does not validate the release migration graph"
    grep -Fq 'wait_for_health worker' "$file" || fail "$file does not wait for the declaration worker healthcheck"
    grep -Fq 'runtime_service in app worker' "$file" || fail "$file does not validate both runtime services"
  done
  grep -Fq 'stop_new_release_container worker' scripts/deploy-production.sh || \
    fail 'deploy cleanup does not stop an exact-release worker after an unpromoted failure'
  grep -Fq 'declaration import is enabled but no OpenAI provider key is configured' scripts/deploy-production.sh || \
    fail 'deploy does not fail closed when an enabled declaration import has no provider key'
  grep -Fq 'declaration import is enabled but no OpenAI provider key is configured' scripts/verify-production.sh || \
    fail 'runtime verifier does not fail closed when an enabled declaration import has no provider key'
}

assert_worker_database_isolation() {
  worker_block=$(sed -n '/^  worker:/,/^volumes:/p' docker-compose.prod.yml)
  app_block=$(sed -n '/^  app:/,/^  worker:/p' docker-compose.prod.yml)
  printf '%s\n' "$worker_block" | grep -Fq 'HV_DATABASE_URL_WORKER: postgresql+asyncpg://app_worker:' || fail 'worker needs its separate database role'
  if printf '%s\n' "$app_block" | grep -q 'HV_DATABASE_URL_WORKER'; then fail 'web service must not receive worker credentials'; fi
  if printf '%s\n' "$worker_block" | grep -q 'HV_DATABASE_URL:'; then fail 'worker must not fall back to web credentials'; fi
  printf '%s\n' "$app_block" | grep -Fq 'HV_DATABASE_URL_REVIEW: postgresql+asyncpg://app_review:' || fail 'review endpoints need isolated review credentials'
  if printf '%s\n' "$worker_block" | grep -q 'HV_DATABASE_URL_REVIEW'; then fail 'worker must not receive human review credentials'; fi
  for file in scripts/deploy-production.sh scripts/verify-production.sh; do
    grep -Fq 'assert_runtime_db_role review' "$file" || fail 'review role deployment check missing'
  done
}

assert_worker_egress_isolation() {
  worker_block=$(sed -n '/^  worker:/,/^volumes:/p' docker-compose.prod.yml)
  app_block=$(sed -n '/^  app:/,/^  worker:/p' docker-compose.prod.yml)
  backend_only_services=$(sed -n '/^  db:/,/^  app:/p' docker-compose.prod.yml)
  network_block=$(sed -n '/^networks:/,$p' docker-compose.prod.yml)

  printf '%s\n' "$worker_block" | grep -Fq '      egress:' || \
    fail 'declaration worker is not attached to the dedicated egress network'
  printf '%s\n' "$worker_block" | grep -Eq '^      (- traefik|traefik:)' && \
    fail 'declaration worker must not join the shared Traefik network'
  printf '%s\n' "$app_block" | grep -Eq '^      (- egress|egress:)' && \
    fail 'app must not join the declaration worker egress network'
  printf '%s\n' "$backend_only_services" | grep -Eq '^      (- egress|egress:)' && \
    fail 'database, migration, and bootstrap services must remain backend-only'
  printf '%s\n' "$network_block" | grep -Fq '  egress:' || \
    fail 'production Compose does not define the dedicated worker egress network'
  if printf '%s\n' "$network_block" | sed -n '/^  egress:/,/^  [a-z]/p' | grep -Fq 'internal: true'; then
    fail 'declaration worker egress network must permit external DNS and HTTPS'
  fi
  grep -Fq 'assert_exact_networks' scripts/verify-production.sh || \
    fail 'isolated verifier does not assert exact runtime network attachments'
  grep -Fq 'egress_network="${HV_VERIFY_PROJECT_NAME}_egress"' scripts/verify-production.sh || \
    fail 'isolated verifier does not own and clean up the worker egress network'
  if grep -Fq 'declaration_provider_smoke' scripts/verify-production.sh; then
    fail 'isolated verifier must not make an external provider call with its placeholder key'
  fi
  grep -Fq 'gw_priority: 1' docker-compose.prod.yml || \
    fail 'worker egress network does not have the deterministic default-route priority'
  grep -Fq 'gw_priority: 0' docker-compose.prod.yml || \
    fail 'worker backend network does not have the lower default-route priority'
  grep -Fq 'worker_default_gateway' scripts/verify-production.sh || \
    fail 'isolated verifier does not inspect the worker default route'
  grep -Fq 'egress_gateway' scripts/verify-production.sh || \
    fail 'isolated verifier does not compare the worker default route to the egress gateway'
  grep -Fq "| sed '/^\$/d' | sort" scripts/verify-production.sh || \
    fail 'isolated verifier does not remove Docker inspect blank lines before network comparison'
}

assert_provider_smoke_gate() {
  smoke_line=$(grep -nF 'compose run --rm --no-deps --entrypoint python worker -m app.workers.declaration_provider_smoke' scripts/deploy-production.sh | cut -d: -f1)
  worker_start_line=$(grep -nF 'compose up -d --no-deps worker' scripts/deploy-production.sh | cut -d: -f1)
  promotion_line=$(grep -nF 'ln -s "releases/$RELEASE_ID" "$root/current.new"' scripts/deploy-production.sh | cut -d: -f1)
  [ -n "$smoke_line" ] || fail 'deploy does not perform the declaration provider connectivity smoke in an isolated worker container'
  [ -n "$worker_start_line" ] || fail 'deploy does not start the declaration worker'
  [ -n "$promotion_line" ] || fail 'deploy does not contain an atomic release promotion'
  [ "$smoke_line" -lt "$worker_start_line" ] || \
    fail 'declaration provider connectivity smoke must happen before the consuming worker starts'
  [ "$worker_start_line" -lt "$promotion_line" ] || \
    fail 'declaration worker must become ready before promotion'
  if grep -Fq 'compose exec -T worker python -m app.workers.declaration_provider_smoke' scripts/deploy-production.sh; then
    fail 'provider smoke must not execute through the consuming worker service'
  fi
}

assert_release_replacement_hardening() {
  grep -Fq 'flock -n 9' scripts/deploy-production.sh || \
    fail 'deployment does not acquire an exclusive host lock before Compose work'
  grep -Fq 'existing database container is required' scripts/deploy-production.sh || \
    fail 'upgrade must require the existing database without reconciling it'
  grep -Fq 'compose up -d --no-deps app' scripts/deploy-production.sh || \
    fail 'app startup may re-evaluate or recreate its already-verified database dependency'
  grep -Fq 'compose up -d --no-deps worker' scripts/deploy-production.sh || \
    fail 'worker startup may re-evaluate or recreate its already-verified dependencies'
  grep -Fq "'{{.Image}}'" scripts/deploy-production.sh || \
    fail 'deployment does not prove runtime ownership with immutable image IDs'
  grep -Fq 'com.karat.hausverwaltung.release' docker-compose.prod.yml || \
    fail 'runtime services do not carry a per-release ownership label'
  grep -Fq 'runtime_release_label' scripts/verify-production.sh || \
    fail 'isolated verifier does not verify per-release runtime ownership labels'
  grep -Fq 'restore_previous_runtime' scripts/deploy-production.sh || \
    fail 'failed replacement does not restore the previous runtime'
  grep -Fq 'migration_state' scripts/deploy-production.sh || \
    fail 'migration gate does not require exactly one expected head value'
  grep -Fq 'is_provider_key_value' scripts/deploy-production.sh || \
    fail 'deployment provider-key gate accepts malformed or comment-only values'
  for expected in \
    'compose --profile ops run --rm --no-deps migrate' \
    'compose --profile ops run --rm --no-deps migrate python -m app.deployment' \
    'compose --profile ops run --rm --no-deps -T bootstrap'; do
    grep -Fq "$expected" scripts/deploy-production.sh || \
      fail "deployment ops command may start or reconcile database dependencies: $expected"
  done
  for expected in \
    'compose --profile ops run --rm --no-deps migrate' \
    'compose --profile ops run --rm --no-deps migrate python -m app.deployment preflight'; do
    grep -Fq "$expected" scripts/verify-production.sh || \
      fail "verification ops command may start or reconcile database dependencies: $expected"
  done
  for file in scripts/deploy-production.sh scripts/verify-production.sh; do
    grep -Eq 'wait_for_database_role|app.deployment' "$file" || \
      fail "$file does not wait for app_rw after PostgreSQL reports healthy"
  done
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
deploy_script="$script_dir/deploy-production.sh"
admin_password='Alpha"$\;{}[]Pass12'
tmpdir=$(mktemp -d)
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT HUP INT TERM

mkdir -p "$tmpdir/bin"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/docker"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/stat"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/curl"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/mv"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/flock"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/readlink"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/sleep"

missing_key_root="$tmpdir/missing-key-root"
mkdir -p "$missing_key_root/releases/test" "$missing_key_root/shared"
: > "$missing_key_root/shared/.env"
chmod 600 "$missing_key_root/shared/.env"
: > "$missing_key_root/releases/test/docker-compose.prod.yml"
: > "$tmpdir/missing-key-docker.log"
if printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/missing-key-docker.log" \
  FAKE_SHARED="$missing_key_root/shared" \
  FAKE_APP_CREATED="$tmpdir/missing-key-app-created" \
  FAKE_WORKER_CREATED="$tmpdir/missing-key-worker-created" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  HV_OPENAI_API_KEY='   ' \
  DEPLOY_ROOT="$missing_key_root" RELEASE_ID=test sh "$deploy_script" >"$tmpdir/missing-key-output" 2>&1; then
  fail 'deployment unexpectedly accepted an enabled declaration import without a provider key'
fi
grep -Fq 'declaration import is enabled but no OpenAI provider key is configured' "$tmpdir/missing-key-output" || \
  fail 'missing provider key did not return the fail-closed deployment error'
[ ! -s "$tmpdir/missing-key-docker.log" ] || \
  fail 'missing provider key reached Docker before the deployment gate failed'

for blank_key_case in unquoted-comment quoted-comment; do
  blank_key_root="$tmpdir/$blank_key_case-root"
  mkdir -p "$blank_key_root/releases/test" "$blank_key_root/shared"
  case "$blank_key_case" in
    unquoted-comment) printf '%s\n' 'HV_OPENAI_API_KEY= # not configured' > "$blank_key_root/shared/.env" ;;
    quoted-comment) printf '%s\n' 'HV_OPENAI_API_KEY="" # not configured' > "$blank_key_root/shared/.env" ;;
  esac
  chmod 600 "$blank_key_root/shared/.env"
  : > "$blank_key_root/releases/test/docker-compose.prod.yml"
  : > "$tmpdir/$blank_key_case-docker.log"
  if printf '%s\n' "$admin_password" | \
    PATH="$tmpdir/bin:$PATH" \
    FAKE_DOCKER_LOG="$tmpdir/$blank_key_case-docker.log" \
    FAKE_SHARED="$blank_key_root/shared" \
    FAKE_APP_CREATED="$tmpdir/$blank_key_case-app-created" \
    FAKE_WORKER_CREATED="$tmpdir/$blank_key_case-worker-created" \
    FAKE_ADMIN_PASSWORD="$admin_password" \
    DEPLOY_ROOT="$blank_key_root" RELEASE_ID=test sh "$deploy_script" \
      >"$tmpdir/$blank_key_case-output" 2>&1; then
    fail "deployment unexpectedly accepted $blank_key_case as a provider key"
  fi
  grep -Fq 'declaration import is enabled but no OpenAI provider key is configured' \
    "$tmpdir/$blank_key_case-output" || \
    fail "$blank_key_case did not return the fail-closed deployment error"
  [ ! -s "$tmpdir/$blank_key_case-docker.log" ] || \
    fail "$blank_key_case reached Docker before the deployment gate failed"
done

# Each case uses only the fake CLI, synthetic archives and a private release root.
for scenario in wrong-source ambiguous-source release-head previous-head roles credentials competing backup-exists busy quiescent-changed backup documents dump-check archive-check migrate target app-start app-health api-ready provider-smoke worker-start worker-health invoice-start invoice-health no-invoice no-invoice-restore restore-compatible cleanup-stop-fail cleanup-discovery-fail foreign-container success success-0028; do
  case_root="$tmpdir/$scenario"
  mkdir -p "$case_root/releases/test/infra/db" "$case_root/releases/old" "$case_root/shared"
  printf '%s\n' 'HV_OPENAI_API_KEY=fake_provider_key' > "$case_root/shared/.env"
  chmod 600 "$case_root/shared/.env"
  : > "$case_root/releases/test/docker-compose.prod.yml"
  : > "$case_root/releases/test/infra/db/prod-init.sh"
  : > "$case_root/releases/old/docker-compose.prod.yml"
  ln -s releases/old "$case_root/current"
  export FAKE_DOCKER_LOG="$case_root/docker.log" FAKE_SHARED="$case_root/shared"
  export FAKE_APP_CREATED="$case_root/app-created" FAKE_WORKER_CREATED="$case_root/worker-created"
  export FAKE_APP_RESTORED="$case_root/app-restored" FAKE_WORKER_RESTORED="$case_root/worker-restored"
  export FAKE_INVOICE_CREATED="$case_root/invoice-created" FAKE_INVOICE_RESTORED="$case_root/invoice-restored"
  export FAKE_MIGRATED="$case_root/migrated" FAKE_READLINK_F="$case_root/releases/old"
  export FAKE_PROVIDER_SMOKE_CALLED="$case_root/provider-smoke" FAKE_ADMIN_PASSWORD="$admin_password"
  export FAKE_SCHEMA=0122 FAKE_TARGET=0139 FAKE_PREVIOUS_HEAD=0122 FAKE_FAILURE="$scenario"
  export FAKE_PROVIDER_SMOKE_FAIL=0
  export FAKE_NO_PREVIOUS_INVOICE=0 FAKE_APP_UP_FAIL=0 FAKE_WORKER_UP_FAIL=0 FAKE_NEW_LABEL=test
  case "$scenario" in
    wrong-source) FAKE_SCHEMA=0121 ;;
    success-0028) FAKE_SCHEMA=0028; FAKE_PREVIOUS_HEAD=0028 ;;
    ambiguous-source) FAKE_SCHEMA='0122
0139' ;;
    target) FAKE_TARGET=0138 ;;
    previous-head) FAKE_PREVIOUS_HEAD=0138 ;;
    backup-exists) mkdir -p "$case_root/shared/backups/test-from-0122"; printf preserve > "$case_root/shared/backups/test-from-0122/database.dump" ;;
    provider-smoke) FAKE_PROVIDER_SMOKE_FAIL=1 ;;
    cleanup-stop-fail|cleanup-discovery-fail) FAKE_SCHEMA=0139; FAKE_PREVIOUS_HEAD=0139; FAKE_WORKER_UP_FAIL=1 ;;
    no-invoice-restore) FAKE_SCHEMA=0139; FAKE_PREVIOUS_HEAD=0139; FAKE_WORKER_UP_FAIL=1; FAKE_NO_PREVIOUS_INVOICE=1 ;;
    app-start) FAKE_APP_UP_FAIL=1 ;;
    worker-start) FAKE_WORKER_UP_FAIL=1 ;;
    no-invoice) FAKE_NO_PREVIOUS_INVOICE=1 ;;
    restore-compatible) FAKE_SCHEMA=0139; FAKE_PREVIOUS_HEAD=0139; FAKE_WORKER_UP_FAIL=1 ;;
    foreign-container) FAKE_NEW_LABEL=foreign; FAKE_APP_UP_FAIL=1 ;;
  esac
  outcome=0
  printf '%s\n' "$admin_password" | PATH="$tmpdir/bin:$PATH" \
    DEPLOY_ROOT="$case_root" RELEASE_ID=test sh "$deploy_script" >"$case_root/output" 2>&1 || outcome=$?
  case "$scenario" in
    success|success-0028|no-invoice)
      [ "$outcome" -eq 0 ] || { cat "$case_root/output" >&2; fail "$scenario failed"; }
      [ "$(readlink "$case_root/current")" = releases/test ] || fail "$scenario did not promote"
      pair="$case_root/shared/backups/test-from-$FAKE_SCHEMA"
      [ -f "$pair/database.dump" ] || fail 'no completed database backup'
      [ -f "$pair/documents.tar.gz" ] || fail 'no completed documents backup'
      python3 - "$FAKE_DOCKER_LOG" "$pair" <<'ASSERT_ORDER'
import sys
log = open(sys.argv[1]).read()
steps = ['stopped:old-worker', 'stopped:old-app', 'pg_dump', 'archive-documents',
         'run --rm --no-deps migrate\n', 'up -d --no-deps app', 'http://127.0.0.1:8000/api/health/ready',
         'up -d --no-deps worker', 'up -d --no-deps invoice-worker', 'promoted']
positions = [log.index(step) for step in steps]
assert positions == sorted(positions), positions
from pathlib import Path
import stat
pair = Path(sys.argv[2])
assert stat.S_IMODE(pair.stat().st_mode) == 0o700
assert all(stat.S_IMODE((pair / name).stat().st_mode) == 0o600 for name in ['database.dump', 'documents.tar.gz'])
ASSERT_ORDER
      if [ "$scenario" = no-invoice ]; then
        ! grep -q 'stopped:old-invoice' "$FAKE_DOCKER_LOG" || fail 'stopped absent old invoice worker'
      else
        grep -q 'stopped:old-invoice' "$FAKE_DOCKER_LOG" || fail 'did not stop previous invoice worker'
      fi
      # A repeated release must not overwrite its existing backup.
      printf '%s' preserve > "$pair/database.dump"
      if printf '%s\n' "$admin_password" | PATH="$tmpdir/bin:$PATH" DEPLOY_ROOT="$case_root" RELEASE_ID=test sh "$deploy_script" >"$case_root/repeat-output" 2>&1; then fail 'repeated release accepted'; fi
      [ "$(cat "$pair/database.dump")" = preserve ] || fail 'backup overwritten'
      ;;
    *)
      [ "$outcome" -ne 0 ] || fail "$scenario unexpectedly succeeded"
      [ "$(readlink "$case_root/current")" = releases/old ] || fail "$scenario changed current"
      case "$scenario" in
        wrong-source|ambiguous-source|release-head|previous-head|roles|credentials|competing|backup-exists)
          ! grep -q 'stopped:' "$FAKE_DOCKER_LOG" || fail "$scenario stopped runtime before preflight passed" ;;
        busy|backup|documents|dump-check|archive-check)
          [ ! -f "$FAKE_MIGRATED" ] || fail "$scenario began migration"
          [ -f "$FAKE_APP_RESTORED" ] && [ -f "$FAKE_WORKER_RESTORED" ] && [ -f "$FAKE_INVOICE_RESTORED" ] || fail "$scenario did not resume compatible runtime" ;;
        quiescent-changed)
          [ ! -f "$FAKE_MIGRATED" ] && [ ! -f "$FAKE_APP_RESTORED" ] || fail 'schema drift migrated or blindly resumed old runtime'
          grep -q 'MAINTENANCE REQUIRED' "$case_root/output" || fail 'schema drift lacks recovery instruction' ;;
        no-invoice-restore)
          [ -f "$FAKE_APP_RESTORED" ] && [ -f "$FAKE_WORKER_RESTORED" ] || fail 'prior runtime without invoice worker not restored'
          [ ! -f "$FAKE_INVOICE_RESTORED" ] || fail 'restoration invented an absent invoice worker' ;;
        restore-compatible)
          [ -f "$FAKE_APP_RESTORED" ] && [ -f "$FAKE_INVOICE_RESTORED" ] || fail 'compatible runtime not restored' ;;
        foreign-container)
          ! grep -q 'stopped:new-app' "$FAKE_DOCKER_LOG" || fail 'cleanup stopped foreign container' ;;
        *)
          [ ! -f "$FAKE_APP_RESTORED" ] || fail "$scenario blindly restored incompatible old runtime"
          grep -q 'MAINTENANCE REQUIRED' "$case_root/output" || fail "$scenario lacks recovery instruction" ;;
      esac
      case "$scenario" in
        invoice-start|invoice-health)
          for id in new-invoice new-worker new-app; do grep -q "stopped:$id" "$FAKE_DOCKER_LOG" || fail "$scenario did not clean $id"; done ;;
      esac
      ;;
  esac
  ! grep -Eq 'stopped:db-id| down |volume rm|alembic downgrade|up -d.* db' "$FAKE_DOCKER_LOG" || fail 'database/volume mutation in runtime handling'
  ! grep -Fq "$admin_password" "$FAKE_DOCKER_LOG" || fail 'admin secret in CLI arguments'
done
printf '%s\n' 'deploy-production: 32 upgrade/backup/worker/recovery scenarios passed'

assert_gateway_basic_auth_removed
assert_admin_password_handling
assert_verifier_label_assertions
assert_declaration_worker_runtime
assert_worker_egress_isolation
assert_worker_database_isolation
assert_provider_smoke_gate
assert_release_replacement_hardening
