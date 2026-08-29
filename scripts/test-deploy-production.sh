#!/bin/sh
# Focused regression probe for deploy-production.sh cleanup safety.  It uses a
# self-hosted fake Docker CLI to reproduce a compose `up app` failure after a
# new app container was partially created.
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
  [ "${1:-}" = -Tf ] && shift
  /bin/mv -f "$1" "$2"
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
    run)
      printf '%s\n' '{}' > "$FAKE_SHARED/test.sbom.cdx.json"
      exit 0
      ;;
    inspect)
      case "$*" in
        *'{{.State.Health.Status}}'*'db-id'*) printf '%s\n' healthy ;;
        *'{{.State.Health.Status}}'*'new-app'*) printf '%s\n' healthy ;;
        *'{{.Config.Image}}'*'new-app'*) printf '%s\n' 'hausverwaltung-alpha:test' ;;
        *'{{.Id}}'*'new-app'*) printf '%s\n' new-app ;;
        *'{{.Config.Image}}'*'old-app'*) printf '%s\n' 'hausverwaltung-alpha:previous' ;;
        *'{{.Id}}'*'old-app'*) printf '%s\n' old-app ;;
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
        *' up -d db '*) exit 0 ;;
        *' ps -q db '*) printf '%s\n' db-id; exit 0 ;;
        *' ps -q app '*)
          if [ -f "$FAKE_APP_CREATED" ]; then printf '%s\n' new-app; else printf '%s\n' old-app; fi
          exit 0
          ;;
        *' run '*' alembic current '*) printf '%s\n' '0023 (head)'; exit 0 ;;
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
        *' exec '*) exit 0 ;;
        *' up -d app '*)
          : > "$FAKE_APP_CREATED"
          [ "${FAKE_APP_UP_FAIL:-0}" = 1 ] && exit 99
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

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
deploy_script="$script_dir/deploy-production.sh"
assert_gateway_basic_auth_removed
assert_admin_password_handling
assert_verifier_label_assertions
admin_password='Alpha"$\;{}[]Pass12'
tmpdir=$(mktemp -d)
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT HUP INT TERM

mkdir -p "$tmpdir/bin" "$tmpdir/root/releases/test" "$tmpdir/root/shared"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/docker"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/stat"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/curl"
ln -s "$script_dir/$(basename "$0")" "$tmpdir/bin/mv"
: > "$tmpdir/root/shared/.env"
chmod 600 "$tmpdir/root/shared/.env"
: > "$tmpdir/root/releases/test/docker-compose.prod.yml"
: > "$tmpdir/docker.log"

if printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/docker.log" \
  FAKE_SHARED="$tmpdir/root/shared" \
  FAKE_APP_CREATED="$tmpdir/app-created" \
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

second_root="$tmpdir/second-root"
mkdir -p "$second_root/releases/test" "$second_root/shared"
: > "$second_root/shared/.env"
chmod 600 "$second_root/shared/.env"
: > "$second_root/releases/test/docker-compose.prod.yml"
: > "$tmpdir/second-docker.log"
printf '%s\n' "$admin_password" | \
  PATH="$tmpdir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmpdir/second-docker.log" \
  FAKE_SHARED="$second_root/shared" \
  FAKE_APP_CREATED="$tmpdir/second-app-created" \
  FAKE_ADMIN_PASSWORD="$admin_password" \
  DEPLOY_ROOT="$second_root" RELEASE_ID=test sh "$deploy_script"
[ "$(readlink "$second_root/current")" = releases/test ] || fail 'successful public-gate probe did not promote the release'

printf '%s\n' 'deploy-production cleanup regression probe passed'
