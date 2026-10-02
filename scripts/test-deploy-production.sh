#!/bin/sh
# Focused regression probe for deploy-production.sh cleanup safety. It uses a
# self-hosted fake Docker CLI to reproduce failed and successful app/worker
# starts without ever touching a real Docker daemon.
set -eu

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
  /bin/mv -f "$1" "$2"
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
      printf '%s\n' '{}' > "$FAKE_SHARED/test.sbom.cdx.json"
      exit 0
      ;;
    inspect)
      case "$*" in
        *'{{.State.Health.Status}}'*'db-id'*) printf '%s\n' healthy ;;
        *'{{.State.Health.Status}}'*'new-app'*) printf '%s\n' healthy ;;
        *'{{.State.Health.Status}}'*'new-worker'*) printf '%s\n' healthy ;;
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
        *'com.karat.hausverwaltung.release'*'new-app'*) printf '%s\n' test ;;
        *'com.karat.hausverwaltung.release'*'new-worker'*) printf '%s\n' test ;;
        *'{{json .HostConfig.PortBindings}}'*) printf '%s\n' '{}' ;;
        *'{{range .Config.Env}}'*) exit 0 ;;
        *) printf '%s\n' healthy ;;
      esac
      exit 0
      ;;
    stop)
      printf '%s\n' "stopped:${2:-}" >> "$FAKE_DOCKER_LOG"
      exit 0
      ;;
    compose)
      case " $* " in
        *' up -d --no-recreate db '*) exit 0 ;;
        *' ps -q db '*) printf '%s\n' db-id; exit 0 ;;
        *' ps -q app '*)
          if [ -f "${FAKE_APP_RESTORED:-}" ]; then printf '%s\n' restored-app
          elif [ -f "$FAKE_APP_CREATED" ]; then printf '%s\n' new-app
          else printf '%s\n' old-app
          fi
          exit 0
          ;;
        *' ps -q worker '*)
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
        *' run '*' alembic current '*) printf '%s\n' '0086 (head)'; exit 0 ;;
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
    grep -Fq '0086 (head)' "$file" || fail "$file does not gate on Alembic head 0086"
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
  grep -Fq 'compose up -d --no-recreate db' scripts/deploy-production.sh || \
    fail 'deployment does not prevent PostgreSQL recreation during a release update'
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
    'compose --profile ops run --rm --no-deps migrate alembic current' \
    'compose --profile ops run --rm --no-deps -T bootstrap'; do
    grep -Fq "$expected" scripts/deploy-production.sh || \
      fail "deployment ops command may start or reconcile database dependencies: $expected"
  done
  for expected in \
    'compose --profile ops run --rm --no-deps migrate' \
    'compose --profile ops run --rm --no-deps migrate alembic current'; do
    grep -Fq "$expected" scripts/verify-production.sh || \
      fail "verification ops command may start or reconcile database dependencies: $expected"
  done
  for file in scripts/deploy-production.sh scripts/verify-production.sh; do
    grep -Fq 'wait_for_database_role' "$file" || \
      fail "$file does not wait for app_rw after PostgreSQL reports healthy"
  done
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
deploy_script="$script_dir/deploy-production.sh"
assert_gateway_basic_auth_removed
assert_admin_password_handling
assert_verifier_label_assertions
assert_declaration_worker_runtime
assert_worker_egress_isolation
assert_worker_database_isolation
assert_provider_smoke_gate
assert_release_replacement_hardening
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

mkdir -p "$tmpdir/root/releases/test/infra/db" "$tmpdir/root/shared"
printf '%s\n' 'HV_DECLARATION_IMPORT_ENABLED=false' > "$tmpdir/root/shared/.env"
chmod 600 "$tmpdir/root/shared/.env"
: > "$tmpdir/root/releases/test/docker-compose.prod.yml"
: > "$tmpdir/root/releases/test/infra/db/prod-init.sh"
: > "$tmpdir/docker.log"

if printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/docker.log" \
  FAKE_SHARED="$tmpdir/root/shared" \
  FAKE_APP_CREATED="$tmpdir/app-created" \
  FAKE_WORKER_CREATED="$tmpdir/worker-created" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  FAKE_APP_UP_FAIL=1 \
  DEPLOY_ROOT="$tmpdir/root" RELEASE_ID=test sh "$deploy_script"; then
  fail 'deployment unexpectedly succeeded despite the injected compose failure'
fi

grep -Fqx 'stopped:new-app' "$tmpdir/docker.log" || \
  fail 'the partially-created exact release container was not stopped'
if grep -Fqx 'stopped:old-app' "$tmpdir/docker.log"; then
  fail 'the healthy pre-existing app container was stopped'
fi
if grep -Fqx 'compose-stop-app' "$tmpdir/docker.log"; then
  fail 'cleanup used compose stop app instead of an exact container ID'
fi
if grep -Fqx 'stopped:new-worker' "$tmpdir/docker.log"; then
  fail 'cleanup stopped a worker which was never created'
fi

worker_failure_root="$tmpdir/worker-failure-root"
mkdir -p "$worker_failure_root/releases/test/infra/db" "$worker_failure_root/shared"
printf '%s\n' 'HV_DECLARATION_IMPORT_ENABLED=false' > "$worker_failure_root/shared/.env"
chmod 600 "$worker_failure_root/shared/.env"
: > "$worker_failure_root/releases/test/docker-compose.prod.yml"
: > "$worker_failure_root/releases/test/infra/db/prod-init.sh"
: > "$tmpdir/worker-failure-docker.log"
if printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/worker-failure-docker.log" \
  FAKE_SHARED="$worker_failure_root/shared" \
  FAKE_APP_CREATED="$tmpdir/worker-failure-app-created" \
  FAKE_WORKER_CREATED="$tmpdir/worker-failure-worker-created" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  FAKE_WORKER_UP_FAIL=1 \
  DEPLOY_ROOT="$worker_failure_root" RELEASE_ID=test sh "$deploy_script"; then
  fail 'deployment unexpectedly succeeded despite the injected worker compose failure'
fi
grep -Fqx 'stopped:new-worker' "$tmpdir/worker-failure-docker.log" || \
  fail 'the partially-created exact release worker was not stopped'
grep -Fqx 'stopped:new-app' "$tmpdir/worker-failure-docker.log" || \
  fail 'the exact release app was not stopped after worker startup failed'
if grep -Eqx 'stopped:old-(app|worker)' "$tmpdir/worker-failure-docker.log"; then
  fail 'worker failure cleanup stopped a healthy pre-existing runtime container'
fi

restore_root="$tmpdir/restore-root"
mkdir -p "$restore_root/releases/test/infra/db" "$restore_root/releases/old/infra/db" "$restore_root/shared"
printf '%s\n' 'HV_DECLARATION_IMPORT_ENABLED=false' > "$restore_root/shared/.env"
chmod 600 "$restore_root/shared/.env"
: > "$restore_root/releases/test/docker-compose.prod.yml"
: > "$restore_root/releases/test/infra/db/prod-init.sh"
: > "$restore_root/releases/old/docker-compose.prod.yml"
: > "$restore_root/releases/old/infra/db/prod-init.sh"
ln -s releases/old "$restore_root/current"
: > "$tmpdir/restore-docker.log"
if printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/restore-docker.log" \
  FAKE_SHARED="$restore_root/shared" \
  FAKE_APP_CREATED="$tmpdir/restore-app-created" \
  FAKE_WORKER_CREATED="$tmpdir/restore-worker-created" \
  FAKE_APP_RESTORED="$tmpdir/restore-app-restored" \
  FAKE_WORKER_RESTORED="$tmpdir/restore-worker-restored" \
  FAKE_READLINK_F="$restore_root/releases/old" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  FAKE_WORKER_UP_FAIL=1 \
  DEPLOY_ROOT="$restore_root" RELEASE_ID=test sh "$deploy_script"; then
  fail 'deployment unexpectedly succeeded despite the injected replacement failure'
fi
grep -Fq 'releases/old/docker-compose.prod.yml up -d --no-deps app' "$tmpdir/restore-docker.log" || \
  fail 'failed replacement did not restore the previous app runtime'
grep -Fq 'releases/old/docker-compose.prod.yml up -d --no-deps worker' "$tmpdir/restore-docker.log" || \
  fail 'failed replacement did not restore the previous worker runtime'
[ -f "$tmpdir/restore-app-restored" ] || fail 'previous app was not restored after replacement failure'
[ -f "$tmpdir/restore-worker-restored" ] || fail 'previous worker was not restored after replacement failure'

provider_failure_root="$tmpdir/provider-failure-root"
mkdir -p "$provider_failure_root/releases/test/infra/db" "$provider_failure_root/shared"
printf '%s\n' 'HV_OPENAI_API_KEY=fake_provider_key' > "$provider_failure_root/shared/.env"
chmod 600 "$provider_failure_root/shared/.env"
: > "$provider_failure_root/releases/test/docker-compose.prod.yml"
: > "$provider_failure_root/releases/test/infra/db/prod-init.sh"
if printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/provider-failure-docker.log" \
  FAKE_SHARED="$provider_failure_root/shared" \
  FAKE_APP_CREATED="$tmpdir/provider-failure-app-created" \
  FAKE_WORKER_CREATED="$tmpdir/provider-failure-worker-created" \
  FAKE_PROVIDER_SMOKE_CALLED="$tmpdir/provider-smoke-called" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  FAKE_PROVIDER_SMOKE_FAIL=1 \
  DEPLOY_ROOT="$provider_failure_root" RELEASE_ID=test sh "$deploy_script"; then
  fail 'deployment unexpectedly succeeded despite the injected provider smoke failure'
fi
[ -f "$tmpdir/provider-smoke-called" ] || fail 'provider smoke was not invoked from the worker container'
[ ! -e "$provider_failure_root/current" ] || fail 'provider smoke failure promoted the release'

provider_restore_root="$tmpdir/provider-restore-root"
mkdir -p "$provider_restore_root/releases/test/infra/db" "$provider_restore_root/releases/old/infra/db" "$provider_restore_root/shared"
printf '%s\n' 'HV_OPENAI_API_KEY=fake_provider_key' > "$provider_restore_root/shared/.env"
chmod 600 "$provider_restore_root/shared/.env"
: > "$provider_restore_root/releases/test/docker-compose.prod.yml"
: > "$provider_restore_root/releases/test/infra/db/prod-init.sh"
: > "$provider_restore_root/releases/old/docker-compose.prod.yml"
: > "$provider_restore_root/releases/old/infra/db/prod-init.sh"
ln -s releases/old "$provider_restore_root/current"
: > "$tmpdir/provider-restore-docker.log"
if printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/provider-restore-docker.log" \
  FAKE_SHARED="$provider_restore_root/shared" \
  FAKE_APP_CREATED="$tmpdir/provider-restore-app-created" \
  FAKE_WORKER_CREATED="$tmpdir/provider-restore-worker-created" \
  FAKE_APP_RESTORED="$tmpdir/provider-restore-app-restored" \
  FAKE_WORKER_RESTORED="$tmpdir/provider-restore-worker-restored" \
  FAKE_PROVIDER_SMOKE_CALLED="$tmpdir/provider-restore-smoke-called" \
  FAKE_READLINK_F="$provider_restore_root/releases/old" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  FAKE_PROVIDER_SMOKE_FAIL=1 \
  DEPLOY_ROOT="$provider_restore_root" RELEASE_ID=test sh "$deploy_script"; then
  fail 'deployment unexpectedly succeeded despite the injected provider smoke failure with a prior release'
fi
[ -f "$tmpdir/provider-restore-smoke-called" ] || fail 'provider smoke was not invoked before replacement with a prior release'
[ -f "$tmpdir/provider-restore-app-restored" ] || fail 'provider smoke failure did not restore the prior app'
[ -f "$tmpdir/provider-restore-worker-restored" ] || fail 'provider smoke failure did not restore the prior worker'
grep -Fq 'releases/old/docker-compose.prod.yml up -d --no-deps app' "$tmpdir/provider-restore-docker.log" || \
  fail 'provider smoke failure did not restore the prior app command'
grep -Fq 'releases/old/docker-compose.prod.yml up -d --no-deps worker' "$tmpdir/provider-restore-docker.log" || \
  fail 'provider smoke failure did not restore the prior worker command'
if grep -Fq "releases/test/docker-compose.prod.yml up -d --no-deps worker" "$tmpdir/provider-restore-docker.log"; then
  fail 'provider smoke failure started the consuming replacement worker'
fi
[ "$(readlink "$provider_restore_root/current")" = releases/old ] || \
  fail 'provider smoke failure changed the prior current release pointer'

success_root="$tmpdir/success-root"
mkdir -p "$success_root/releases/test/infra/db" "$success_root/shared"
printf '%s\n' 'HV_OPENAI_API_KEY=fake_provider_key' > "$success_root/shared/.env"
chmod 600 "$success_root/shared/.env"
: > "$success_root/releases/test/docker-compose.prod.yml"
: > "$success_root/releases/test/infra/db/prod-init.sh"
: > "$tmpdir/success-docker.log"
printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/success-docker.log" \
  FAKE_SHARED="$success_root/shared" \
  FAKE_APP_CREATED="$tmpdir/success-app-created" \
  FAKE_WORKER_CREATED="$tmpdir/success-worker-created" \
  FAKE_PROVIDER_SMOKE_CALLED="$tmpdir/success-smoke-called" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  DEPLOY_ROOT="$success_root" RELEASE_ID=test sh "$deploy_script"
[ "$(readlink "$success_root/current")" = releases/test ] || fail 'successful public-gate probe did not promote the release'
grep -Fq 'up -d --no-deps worker' "$tmpdir/success-docker.log" || \
  fail 'successful deployment did not start the declaration worker'
success_smoke_line=$(grep -nF 'run --rm --no-deps --entrypoint python worker -m app.workers.declaration_provider_smoke' "$tmpdir/success-docker.log" | cut -d: -f1)
success_worker_line=$(grep -nF 'up -d --no-deps worker' "$tmpdir/success-docker.log" | cut -d: -f1)
success_promotion_line=$(grep -nFx promoted "$tmpdir/success-docker.log" | cut -d: -f1)
[ -n "$success_smoke_line" ] || fail 'successful deployment did not run the isolated provider smoke'
[ -n "$success_worker_line" ] || fail 'successful deployment did not start the consuming worker'
[ -n "$success_promotion_line" ] || fail 'successful deployment did not record promotion'
[ "$success_smoke_line" -lt "$success_worker_line" ] || \
  fail 'successful deployment ran the consuming worker before the provider smoke'
[ "$success_worker_line" -lt "$success_promotion_line" ] || \
  fail 'successful deployment promoted before the consuming worker started'

printf '%s\n' 'deploy-production app and worker cleanup regression probe passed'
