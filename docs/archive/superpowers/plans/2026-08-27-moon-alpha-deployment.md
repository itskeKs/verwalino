# Moon Alpha Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Deploy the verified hardened image to moon.fogu.io and make it safely reachable at https://verwaltung.karat.immo.

**Architecture:** Source is transferred into a new versioned release directory and built on the x86_64 host. A stable Compose project owns only this app's containers and volumes, while labels connect the app to the existing external Traefik network. Secrets live in one mode-0600 shared environment file and never enter the repository or image.

**Tech Stack:** SSH, rsync, Docker Engine/Compose, Traefik Docker provider, PostgreSQL, Let's Encrypt, curl/OpenSSL.

---

## Operational boundaries

- The user explicitly authorized SSH and deployment to chris@moon.fogu.io.
- Do not use sudo unless a read-only preflight proves it is unavoidable. The
  user is already in the docker group and the deployment root is under
  /home/chris.
- Do not stop, restart, reconfigure, prune, or inspect secret environments of
  foreign containers.
- Do not change the central Traefik Compose project. Only attach the new app
  container to the existing external network named traefik.
- Never print DB passwords, session secret, Basic Auth verifier, or application
  admin password in command output.
- Do not remove pgdata or documents volumes, including on rollback.
- No backups are created by explicit user decision; only demo data is allowed.

### Task 1: Final local and remote preflight

- [ ] **Step 1: Verify the exact local deliverable**

Run:

~~~bash
git status --short --branch
git diff --check
cd backend && uv run pytest -q
cd ../frontend && npm ci && npm run lint && npm test && npm run build
cd ..
docker compose -f docker-compose.prod.yml config --quiet
docker image inspect hausverwaltung:hardening-test >/dev/null
~~~

Expected: every gate passes and the status still contains the protected user
work plus reviewed hardening changes. Do not deploy an unreviewed diff.

- [ ] **Step 2: Reverify DNS and TLS starting state**

Run:

~~~bash
dig @1.1.1.1 +short A verwaltung.karat.immo
dig @1.1.1.1 +short AAAA verwaltung.karat.immo
printf '' | openssl s_client \
  -connect verwaltung.karat.immo:443 \
  -servername verwaltung.karat.immo 2>/dev/null \
  | openssl x509 -noout -subject -issuer
~~~

Expected before deployment: A is 62.171.188.19, AAAA is empty, and the current
certificate may still be TRAEFIK DEFAULT CERT.

- [ ] **Step 3: Capture a non-secret remote baseline**

Run:

~~~bash
ssh -o BatchMode=yes chris@moon.fogu.io \
  'docker ps --format "{{.Names}}|{{.Image}}|{{.Ports}}|{{.Status}}";
   docker network inspect traefik --format "{{.Name}} {{.Driver}}";
   ss -lnt;
   df -h /;
   docker compose version'
~~~

Expected: existing Traefik owns 80/443, network traefik exists, and enough disk
remains. Save only names/ports/status for the post-deploy comparison.

- [ ] **Step 4: Check for a naming collision**

Run:

~~~bash
ssh chris@moon.fogu.io \
  'docker ps -a --format "{{.Names}}" | grep -E "^hausverwaltung-alpha-" || true;
   test ! -e /home/chris/apps/hausverwaltung-alpha ||
     find /home/chris/apps/hausverwaltung-alpha -maxdepth 2 -type f -name .env -prune -o -type f -print'
~~~

Expected for first deployment: no stack containers. If a deployment directory
exists, list only non-secret paths and stop for an explicit update/rollback
assessment.

### Task 2: Create a versioned release without secrets

- [ ] **Step 1: Derive and validate a release ID**

In one controlled local shell session, run:

~~~bash
HV_RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
if ! git diff --quiet || ! git diff --cached --quiet \
  || test -n "$(git ls-files --others --exclude-standard)"; then
  HV_RELEASE_ID="$HV_RELEASE_ID-dirty"
fi
case "$HV_RELEASE_ID" in
  (*[!A-Za-z0-9._-]*) exit 1 ;;
esac
export HV_RELEASE_ID
~~~

Record the concrete value in the deployment handoff. All following commands
run in the same controlled shell session or receive that exact validated value.

- [ ] **Step 2: Create only scoped remote directories**

Run with the resolved release ID:

~~~bash
ssh chris@moon.fogu.io \
  "install -d -m 700 /home/chris/apps/hausverwaltung-alpha;
   install -d -m 700 /home/chris/apps/hausverwaltung-alpha/releases;
   install -d -m 700 /home/chris/apps/hausverwaltung-alpha/shared;
   install -d -m 700 /home/chris/apps/hausverwaltung-alpha/releases/$HV_RELEASE_ID"
~~~

Do not use a glob or recursive deletion.

- [ ] **Step 3: Transfer the reviewed tree**

Run:

~~~bash
rsync -a --compress \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='**/.env' \
  --exclude='**/.venv/' \
  --exclude='**/node_modules/' \
  --exclude='frontend/dist/' \
  --exclude='**/__pycache__/' \
  --exclude='backend/.pytest_cache/' \
  ./ "chris@moon.fogu.io:/home/chris/apps/hausverwaltung-alpha/releases/$HV_RELEASE_ID/"
~~~

Use the literal release ID. Do not use --delete. Verify remotely that no .env,
.git directory, node_modules, or local build output was transferred.

### Task 3: Generate host-only secrets and credentials

- [ ] **Step 1: Generate five independent secrets**

Generate in the controlled shell without printing:

~~~bash
HV_MIGRATOR_PASSWORD="$(openssl rand -hex 32)"
HV_APP_RW_PASSWORD="$(openssl rand -hex 32)"
HV_SESSION_SECRET="$(openssl rand -hex 48)"
HV_BASIC_PASSWORD="$(openssl rand -base64 18 | tr '+/' '-_' | tr -d '=')"
HV_ADMIN_PASSWORD="$(openssl rand -base64 24 | tr '+/' '-_' | tr -d '=')"
export HV_MIGRATOR_PASSWORD HV_APP_RW_PASSWORD HV_SESSION_SECRET
export HV_BASIC_PASSWORD HV_ADMIN_PASSWORD
~~~

These commands produce:

- 32-byte hexadecimal migrator password,
- 32-byte hexadecimal app_rw password,
- 48-byte hexadecimal session secret,
- 18-byte base64url Basic Auth password,
- 24-byte base64url application admin password.

Keep values in process memory only. Never include them in a shell command line,
repository file, terminal narration, or reusable automation.

- [ ] **Step 2: Generate a bcrypt Basic Auth verifier via stdin**

Run:

~~~bash
HV_BASIC_VERIFIER="$(
  printf '%s\n' "$HV_BASIC_PASSWORD" |
  docker run --rm -i \
    httpd:2.4-alpine@sha256:1b766f17b84026429b7cb243317b142921b24432336e798bc881c43f45ed9567 \
    htpasswd -niB alpha |
  tr -d '\r\n'
)"
HV_BASIC_VERIFIER_COMPOSE="$(
  printf '%s' "$HV_BASIC_VERIFIER" | sed 's/\$/$$/g'
)"
export HV_BASIC_VERIFIER_COMPOSE
~~~

The plaintext is fed over stdin. Every dollar sign is doubled for Compose.

Expected verifier shape:

~~~text
alpha:$$2y$$05$$...
~~~

- [ ] **Step 3: Create the shared environment file atomically**

Build the payload locally without displaying it and pipe it into an atomic
remote write:

~~~bash
{
  printf 'COMPOSE_PROJECT_NAME=hausverwaltung-alpha\n'
  printf 'APP_DOMAIN=verwaltung.karat.immo\n'
  printf 'POSTGRES_DB=hausverwaltung\n'
  printf 'APP_IMAGE=hausverwaltung-alpha:%s\n' "$HV_RELEASE_ID"
  printf 'MIGRATOR_PASSWORD=%s\n' "$HV_MIGRATOR_PASSWORD"
  printf 'APP_RW_PASSWORD=%s\n' "$HV_APP_RW_PASSWORD"
  printf 'SESSION_SECRET=%s\n' "$HV_SESSION_SECRET"
  printf 'BASIC_AUTH_USERS=%s\n' "$HV_BASIC_VERIFIER_COMPOSE"
  printf 'SESSION_COOKIE_NAME=__Host-hv_session\n'
  printf 'SESSION_COOKIE_SECURE=true\n'
  printf 'SESSION_MAX_AGE_SECONDS=7200\n'
  printf 'WEB_CONCURRENCY=2\n'
  printf 'FORWARDED_ALLOW_IPS=172.21.0.0/16\n'
} | ssh chris@moon.fogu.io '
  set -eu
  umask 077
  HV_ENV_TMP="$(mktemp /home/chris/apps/hausverwaltung-alpha/shared/.env.XXXXXX)"
  trap "rm -f -- $HV_ENV_TMP" EXIT HUP INT TERM
  dd of="$HV_ENV_TMP" status=none
  test "$(grep -c "^MIGRATOR_PASSWORD=" "$HV_ENV_TMP")" -eq 1
  test "$(grep -c "^APP_RW_PASSWORD=" "$HV_ENV_TMP")" -eq 1
  test "$(grep -c "^SESSION_SECRET=" "$HV_ENV_TMP")" -eq 1
  test "$(grep -c "^BASIC_AUTH_USERS=" "$HV_ENV_TMP")" -eq 1
  chmod 600 "$HV_ENV_TMP"
  mv -f -- "$HV_ENV_TMP" /home/chris/apps/hausverwaltung-alpha/shared/.env
  trap - EXIT HUP INT TERM
'
~~~

On moon this sets umask 077, writes a new temporary file inside the scoped
shared directory, validates required variables exactly once, applies mode 0600,
and atomically renames it to .env.

The resulting non-secret settings are:

~~~text
COMPOSE_PROJECT_NAME=hausverwaltung-alpha
APP_DOMAIN=verwaltung.karat.immo
POSTGRES_DB=hausverwaltung
APP_IMAGE=hausverwaltung-alpha:$HV_RELEASE_ID
SESSION_COOKIE_NAME=__Host-hv_session
SESSION_COOKIE_SECURE=true
SESSION_MAX_AGE_SECONDS=7200
WEB_CONCURRENCY=2
FORWARDED_ALLOW_IPS=172.21.0.0/16
~~~

Required secret settings are MIGRATOR_PASSWORD, APP_RW_PASSWORD,
SESSION_SECRET, and BASIC_AUTH_USERS. Expand the validated HV_RELEASE_ID while
building the temporary file. Never display the file with cat or docker compose
config after real secrets are present.

### Task 4: Build, inspect, and scan on moon

- [ ] **Step 1: Build the immutable release image**

Run remotely from the versioned release:

~~~bash
ssh chris@moon.fogu.io \
  "docker build --pull \
    --tag hausverwaltung-alpha:$HV_RELEASE_ID \
    /home/chris/apps/hausverwaltung-alpha/releases/$HV_RELEASE_ID"
~~~

Expected: frontend lint/tests/build and locked backend installation succeed.

- [ ] **Step 2: Inspect runtime metadata without starting the app**

Run:

~~~bash
ssh chris@moon.fogu.io \
  "docker image inspect hausverwaltung-alpha:$HV_RELEASE_ID \
    --format '{{.Config.User}} {{json .Config.Entrypoint}}';
   docker run --rm --entrypoint python hausverwaltung-alpha:$HV_RELEASE_ID \
    -c 'import app, weasyprint; print(\"imports-ok\")'"
~~~

Expected: UID 10001, runtime-only entrypoint, imports-ok.

- [ ] **Step 3: Scan the exact image**

Run
aquasec/trivy:0.69.0@sha256:33f816d414b9d582d25bb737ffa4a632ae34e222f7ec1b50252cef0ce2266006
against hausverwaltung-alpha:$HV_RELEASE_ID with:

~~~text
--scanners vuln
--severity HIGH,CRITICAL
--ignore-unfixed
--exit-code 1
~~~

Generate a CycloneDX SBOM into the release directory with mode 0600. It contains
package metadata but no secrets. Any fixed high/critical finding blocks startup
until dependency or base image remediation.

### Task 5: Start database, migrate, app, and bootstrap

- [ ] **Step 1: Validate service names without expanding secrets**

Run:

~~~bash
docker compose \
  --project-name hausverwaltung-alpha \
  --env-file /home/chris/apps/hausverwaltung-alpha/shared/.env \
  -f "/home/chris/apps/hausverwaltung-alpha/releases/$HV_RELEASE_ID/docker-compose.prod.yml" \
  config --services
~~~

Expected exactly: db, migrate, bootstrap, app. Do not print the expanded
configuration.

- [ ] **Step 2: Start only PostgreSQL and await health**

Run the db service detached. Poll docker inspect Health.Status for at most two
minutes. On failure, inspect only db logs and stop; do not remove its volume.

- [ ] **Step 3: Apply migration 0023 once**

Run the ops-profile migrate service with --rm. Expected exit code 0. Then query
the database from the migrate container to prove Alembic version 0023 and
app_rw existence without printing password values.

- [ ] **Step 4: Start the app and await readiness**

Start only app detached. Poll its Docker health for at most two minutes.
Inspect:

~~~bash
docker inspect hausverwaltung-alpha-app-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | cut -d= -f1 \
  | sort
~~~

Expected: HV_DATABASE_URL exists; HV_DATABASE_URL_MIGRATOR and
HV_DATABASE_URL_SYNC do not exist. Only variable names are printed.

- [ ] **Step 5: Bootstrap the Alpha admin through stdin**

Use:

~~~text
organization: Karat Hausverwaltung Alpha
email: admin@verwaltung.karat.immo
display name: Alpha Admin
~~~

Run the bootstrap profile with --password-stdin and feed the generated admin
password over stdin. Expected: organization and OWNER_ADMIN are created. A
second dry/idempotency call must refuse duplication without creating another
user.

### Task 6: Public TLS and authentication smoke

- [ ] **Step 1: Wait for a trusted certificate**

Poll HTTPS with normal certificate verification for at most three minutes.
Expected certificate SAN includes verwaltung.karat.immo, issuer is a trusted
Let's Encrypt chain, and the self-signed Traefik default certificate is gone.

- [ ] **Step 2: Verify unauthenticated boundary**

Run:

~~~bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  https://verwaltung.karat.immo/
~~~

Expected: 401 with WWW-Authenticate. HTTP redirects permanently to HTTPS.

- [ ] **Step 3: Verify headers with Basic Auth**

Feed Basic Auth credentials without logging them. Expected response includes:

- Strict-Transport-Security,
- Content-Security-Policy,
- X-Content-Type-Options: nosniff,
- X-Frame-Options: DENY,
- Referrer-Policy: no-referrer.

- [ ] **Step 4: Verify app login and current session**

Use a mode-0600 temporary cookie jar created by mktemp. Send Basic Auth plus a
JSON login body generated over stdin, then call:

~~~text
GET /api/auth/session
GET /api/organizations/me
GET /api/health/ready
~~~

Expected: login 200, Secure/HttpOnly/SameSite cookie, session role OWNER_ADMIN,
organization Karat Hausverwaltung Alpha, readiness 200. Remove the cookie jar
with a trap.

- [ ] **Step 5: Verify traversal and upload edge behavior publicly**

With curl --path-as-is and Basic Auth, request parent traversal variants.
Expected: no file outside the SPA dist is returned. Send a multipart file over
25 MiB; expected 413 at Traefik or app. Do not upload sensitive local files.

### Task 7: Promote release or roll back safely

- [ ] **Step 1: Compare foreign-container baseline**

Run the same names/images/ports/status snapshot as Task 1. Expected: every
foreign container remains present with unchanged port mappings/status; only
hausverwaltung-alpha containers are new.

- [ ] **Step 2: Promote only after all smoke tests pass**

Atomically update:

~~~text
/home/chris/apps/hausverwaltung-alpha/current
~~~

to the successful release directory. Record release ID, image ID, Alembic head,
and test results. Do not commit or push local work.

- [ ] **Step 3: Failure behavior**

If first-deploy smoke fails:

- stop only the app container,
- retain db and both named volumes,
- retain logs and release directory for diagnosis,
- do not downgrade the database automatically,
- do not expose a broken router publicly longer than diagnosis requires.

For a later deployment, restore the previous immutable image only after
confirming migration compatibility. Never run docker image prune or compose
down --volumes on the shared host.

### Task 8: Cleanup and credential handoff

- [ ] **Step 1: Remove only temporary artifacts**

Remove local/remote cookie jars, temporary environment staging files, exited
helper containers, and disposable verification projects. Keep the active image,
current release, prior rollback image, SBOM, pgdata, and documents.

- [ ] **Step 2: Final verification**

Run one final HTTPS login/session/readiness smoke and docker ps comparison.
Expected: healthy app/db, trusted TLS, Basic Auth enabled, no public app/db
ports, no migrator variables in app, foreign services untouched.

- [ ] **Step 3: Hand back credentials once**

Return the Basic Auth username/password and application admin email/password to
the user once in the final response. Do not return DB or session secrets. Also
report URL, release ID, image ID, migrations, tests/scans, no-backup risk, and
any remaining host-level risks.
