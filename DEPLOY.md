# Alpha deployment: `verwaltung.karat.immo`

This alpha runs on Moon behind the existing Traefik Docker network and its
`letsencrypt` resolver. Do not add Caddy, publish an app/database port, use
sudo, or modify an unrelated container or network. `chris` owns the deployment
directory:

```
/home/chris/apps/hausverwaltung-alpha/
├── releases/<release-id>/    # reviewed source release + locally built image
├── shared/.env               # host-only secrets, mode 0600
├── shared/<release-id>.sbom.cdx.json  # CycloneDX SBOM, mode 0600
└── current -> releases/...   # changed only after all deployment gates pass
```

This is an alpha with **no backups** by explicit decision. PostgreSQL and
uploaded-document loss is permanent; do not use it for live operations or
retention-sensitive data.

## Deployment boundary and accepted head

Documentation only: these procedures require a separately authorized operator
window; editing this runbook does not authorize SSH, secret access or deployment.
The accepted production gate is **0122**. The local test head **0123** is not a
production approval; do not change the helpers' exact-head gate implicitly.

Phase 2 is verified locally on the disposable test database through migration
**0131**. This does **not** authorize promotion of migrations 0123–0131, change
the accepted 0122 production gate, or authorize a production schema operation.
Retained attachments and financial provenance can deliberately prevent a
lossy downgrade; do not delete originals or disable guards to force rollback.
Any later promotion requires its own reviewed compatibility and backup plan.

T43 adds a staffel generator using the existing append-only rent history; no
migration or new environment setting is required. Local validation does not
change any production gate or authorize deployment. The UI is a calculation aid,
not execution or legal approval of a rent agreement.

`HV_DUNNING_BASE_INTEREST_RATE` is an optional decimal basis-rate default for
new dunning notices, not an automatically maintained official rate. An explicit
request can override or disable it; the notice freezes its rate, reference date,
day counts and interest amounts. Existing issued notices are not recalculated
when configuration changes. The UI permits a deliberate rate entry when no
default is configured. This documentation does not change any runtime setting.

## Fresh installation: one-time preparation on Moon

Run as `chris` without sudo. The host must have Docker, Compose, `curl`,
`openssl`, `flock`, and the existing external `traefik` network. This check must print
exactly `172.21.0.0/16`; otherwise stop rather than changing proxy trust
settings blindly:

```sh
docker network inspect traefik --format '{{(index .IPAM.Config 0).Subnet}}'
```

Before the host commands below, transfer only the public placeholder template
from the reviewed commit. The release archive deliberately excludes `.env.*`,
including `.env.example`; therefore it cannot supply that template implicitly.
From the operator's reviewed checkout (not a developer's populated environment):

```sh
set -eu
reviewed_commit=REPLACE_WITH_REVIEWED_COMMIT
template=$(mktemp "${TMPDIR:-/tmp}/hausverwaltung-template.XXXXXX")
trap 'rm -f "$template"' EXIT HUP INT TERM
git show "$reviewed_commit:.env.example" > "$template"
ssh chris@moon.fogu.io 'set -eu; root=/home/chris/apps/hausverwaltung-alpha; install -d -m 700 "$root" "$root/releases" "$root/shared"; test ! -e "$root/shared/environment.example"; umask 077; cat > "$root/shared/environment.example"' < "$template"
rm -f "$template"
trap - EXIT HUP INT TERM
```

`environment.example` contains only reviewed public placeholders. Never transfer
an actual `.env`, `.env.local` or `shared/.env`; do not print their contents.
For a fresh installation only, create the host-only environment file; stop if it
already exists rather than overwriting existing credentials:

```sh
root=/home/chris/apps/hausverwaltung-alpha
install -d -m 700 "$root" "$root/releases" "$root/shared"
test ! -e "$root/shared/.env"
install -m 600 "$root/shared/environment.example" "$root/shared/.env"
```

Generate the five database/session values without entering a cleartext secret
in shell history. The values exist only in the current shell and the mode-0600
environment file; unset them immediately afterwards. This rewrites the blank
placeholders instead of appending duplicate dotenv keys.

```sh
migrator_password=$(openssl rand -hex 32)
app_rw_password=$(openssl rand -hex 32)
app_worker_password=$(openssl rand -hex 32)
app_review_password=$(openssl rand -hex 32)
session_secret=$(openssl rand -hex 32)
tmp_env=$(mktemp "$root/shared/.env.XXXXXX")
chmod 600 "$tmp_env"
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    MIGRATOR_PASSWORD=) printf 'MIGRATOR_PASSWORD=%s\n' "$migrator_password" ;;
    APP_WORKER_PASSWORD=) printf 'APP_WORKER_PASSWORD=%s\n' "$app_worker_password" ;;
    APP_REVIEW_PASSWORD=) printf 'APP_REVIEW_PASSWORD=%s\n' "$app_review_password" ;;
    APP_RW_PASSWORD=) printf 'APP_RW_PASSWORD=%s\n' "$app_rw_password" ;;
    SESSION_SECRET=) printf 'SESSION_SECRET=%s\n' "$session_secret" ;;
    FORWARDED_ALLOW_IPS=*) printf 'FORWARDED_ALLOW_IPS=172.21.0.0/16\n' ;;
    *) printf '%s\n' "$line" ;;
  esac
done < "$root/shared/.env" > "$tmp_env"
mv -f "$tmp_env" "$root/shared/.env"
unset migrator_password app_rw_password app_worker_password app_review_password session_secret
```

Leave `APP_IMAGE=` blank in that file for the host-build flow. The helper
builds exactly `hausverwaltung-alpha:<release-id>` from the reviewed source
archive, scans it, then captures its Docker image ID. `APP_IMAGE` overrides are
rejected by the manual helper; there is no registry-pull deployment path.

Declaration imports are enabled by default and the worker therefore requires a
non-empty `HV_OPENAI_API_KEY` in this same mode-0600 host environment file.
Store it as an unquoted `A-Z`/`a-z`/`0-9`/`.`/`_`/`-` token without an inline
comment. Provision that server-side key before deploying; never add it to an
archive or command line. If this alpha must run without declaration imports, set
`HV_DECLARATION_IMPORT_ENABLED=false` explicitly. The API's deterministic
browse/search features remain available in that mode, but the deployment and
isolated verifier otherwise fail closed before starting runtime containers.

## Upgrade of an existing installation

Do not rerun the fresh-install password-generation loop or overwrite `shared/.env`.
Before migration, verify in a separately authorized maintenance window that the
existing database has the distinct `app_worker` and `app_review` logins and the
protected `APP_WORKER_PASSWORD` and `APP_REVIEW_PASSWORD` settings. Fresh-volume
init scripts do **not** run again on existing volumes. Follow the role prerequisites
below using the reviewed `infra/db/prod-init.sh`; never reset populated volumes.

Existing database-container environment may predate those keys. Merely editing
`shared/.env` does not update it, and the deploy helper deliberately refuses an
unexpected database-container replacement. Resolve missing settings/roles in that
maintenance window before starting a release; do not use `app_rw` as a workaround.
The provisioning script creates missing roles, but does not rotate passwords on
existing roles. Credential rotation is a separate operation with workers paused.

| Connection | Runtime role / setting | Boundary |
| --- | --- | --- |
| Ordinary web API | `app_rw` / `HV_DATABASE_URL` | No worker credential; no migrator connection |
| Extraction worker | `app_worker` / `HV_DATABASE_URL_WORKER` | No web URL fallback; no review credential |
| Dedicated human-review commands in API | `app_review` / `HV_DATABASE_URL_REVIEW` | Separate pool, no ordinary-role fallback |
| One-shot migration/bootstrap | Privileged ops credentials only | Never injected into app/worker runtime |

Production promotion still requires exactly `0122 (head)`. This runbook correction
does not accept local migration 0123, change privileges, or authorize deployment.

## Declaration-worker network boundary

The PostgreSQL `backend` network remains internal. Database, migration and
bootstrap services attach only there; the API attaches only to `backend` and
the shared external `traefik` network. The declaration worker alone additionally
attaches to the project-local, non-internal `egress` network. It has no Traefik
attachment and no service publishes a host port. The worker uses the explicit
Docker gateway priorities `backend: 0` and `egress: 1`, so its default route is
the egress-network gateway while database traffic remains reachable over
`backend`.

This gives the worker DNS and HTTPS egress for the fixed
`https://api.openai.com/v1` SDK origin. The extraction harness has no URL,
browser, or tool capability: provider calls are structured-output/Models API
calls only. When declaration imports are enabled, the deploy gate runs after
the replacement API is healthy but before the consuming replacement worker is
started. It uses a one-off `compose run --rm --no-deps` worker container with
an explicit Python entrypoint override, so the normal worker loop never runs.
That container makes the non-billable Models API lookup for the configured
primary and escalation model IDs with a short timeout and one bounded SDK
retry. It sends no prompt, PDF, document content, or Responses API request;
the key is read solely from the worker container environment and is never
printed. A smoke failure preserves/restores the prior runtime and prevents
promotion.

Docker's ordinary non-internal network is **not** a domain firewall. The fixed
client origin and tool-free harness prevent application-configured alternate
destinations, but host/network policy must add an egress allowlist if a strict
network-level restriction to OpenAI is required. The isolated verifier checks
the exact topology and decodes the worker's live default route from
`/proc/net/route`, comparing it with the Docker egress gateway. It
intentionally does not perform an external provider call with its placeholder
key.

## Manual release transfer and promotion

Run the following from the reviewed worktree. The archive is deliberately
created outside the archived tree. It excludes all dotenv files and local
build/cache directories, so no deployment or developer secret crosses SSH.
Substitute a stable reviewed release id (usually the commit SHA).

```sh
worktree=$PWD
release_id=REPLACE_WITH_REVIEWED_COMMIT
archive=$(mktemp "${TMPDIR:-/tmp}/hausverwaltung-alpha.XXXXXX")
COPYFILE_DISABLE=1 tar -C "$worktree" \
  --exclude=.git --exclude=.claude --exclude='.claude/**' \
  --exclude='.DS_Store' --exclude='*/.DS_Store' \
  --exclude='._*' --exclude='*/._*' \
  --exclude='.env' --exclude='.env.*' \
  --exclude='*/.env' --exclude='*/.env.*' \
  --exclude='backend/.venv' --exclude='backend/.venv/**' \
  --exclude='.hypothesis' --exclude='.hypothesis/**' \
  --exclude='backend/.hypothesis' --exclude='backend/.hypothesis/**' \
  --exclude='backend/*.egg-info' --exclude='backend/*.egg-info/**' \
  --exclude='frontend/node_modules' --exclude='frontend/node_modules/**' \
  --exclude='frontend/dist' --exclude='frontend/dist/**' \
  --exclude='**/.pytest_cache' --exclude='**/.pytest_cache/**' \
  --exclude='**/__pycache__' --exclude='**/__pycache__/**' \
  --exclude='image.env' --exclude='sbom.cdx.json' \
  -czf "$archive" .
ssh chris@moon.fogu.io "set -eu; root=/home/chris/apps/hausverwaltung-alpha; \
  install -d -m 700 \"\$root\" \"\$root/releases\" \"\$root/shared\"; \
  test -f \"\$root/shared/.env\"; test \"\$(stat -c '%a' \"\$root/shared/.env\")\" = 600; \
  test ! -e \"\$root/releases/$release_id\"; test ! -e \"\$root/releases/$release_id.tar.gz\""
scp "$archive" "chris@moon.fogu.io:/home/chris/apps/hausverwaltung-alpha/releases/$release_id.tar.gz"
rm -f "$archive"
ssh -t chris@moon.fogu.io "set -eu; root=/home/chris/apps/hausverwaltung-alpha; \
  release=\"\$root/releases/$release_id\"; archive=\"\$root/releases/$release_id.tar.gz\"; \
  install -d -m 700 \"\$release\"; tar -xzf \"\$archive\" -C \"\$release\"; rm -f \"\$archive\"; \
  DEPLOY_ROOT=\"\$root\" RELEASE_ID='$release_id' sh \"\$release/scripts/deploy-production.sh\""
```

The helper asks for the first application-admin password with terminal echo
disabled. Save it in a password manager before continuing. It creates exactly
this first identity through the privileged,
one-shot bootstrap container:

```
organization: Karat Hausverwaltung Alpha
email:        admin@verwaltung.karat.immo
name:         Alpha Admin
```

Before starting PostgreSQL it acquires an exclusive host deployment lock,
verifies the enabled import has its provider key, builds the exact local image,
creates a mode-0600 CycloneDX SBOM in `shared/`, and runs the pinned Trivy
vulnerability HIGH/CRITICAL (`--scanners vuln --ignore-unfixed`) gate. It
installs the reviewed PostgreSQL init script at a stable shared-host path and
starts PostgreSQL with `--no-recreate`; an existing database container ID must
remain unchanged. After Docker health reports ready, it also waits until the
unprivileged `app_rw` role can connect as an additional database completion
gate.
It then uses the fixed order `db → migrate → bootstrap → app → worker`, waits
for both runtime health checks, requires exactly Alembic head `0122 (head)`,
and runs each one-shot `migrate`/`bootstrap` Compose command with `--no-deps`,
so that command cannot re-evaluate, create, or replace the already verified
PostgreSQL service. It then verifies per-release ownership labels, Docker image IDs, closed host ports,
the separated runtime credentials: `app_rw` for ordinary API access,
`app_worker` for extraction, and the API-only `app_review` pool. It polls
the public Traefik/Let's Encrypt route for at most 180 seconds and, before
atomically moving `current`, checks HTTPS/TLS,
the public unauthenticated session boundary, security headers, secure session
cookie, login/session/organization, traversal rejection, upload 413 and
readiness.

Compose replaces fixed-project app/worker containers when a new release has a
different runtime configuration. On any unpromoted failure after replacement,
the helper stops **only** newly created containers proven by the release label
and Docker image ID, then restores the previously captured app and optional
worker with the prior release Compose file and immutable image IDs. The
PostgreSQL container and named volumes are not replaced or deleted, and
`current` is not moved. Already applied schema migrations remain in place and
are never rolled back. This restoration is therefore safe only for additive,
backward-compatible schema migrations; assess that compatibility before every
deployment. `current` is changed only after the full smoke;
the previous pointer is atomically persisted first and a stale `current.new`
is removed only when it is a validated release symlink. Assess migration
compatibility before an explicit app-only rollback.

## Operation and rollback

The app has no host port. Traefik makes the static SPA, login page, and health
endpoints public, while applying TLS, CSP/HSTS/clickjacking/MIME/referrer
headers, a login rate limit, and a 30 MiB proxy body limit. Application data
remains protected by the application session/authentication boundary and its
database RLS controls. The app independently rejects individual files over 25
MiB. `/tmp` is a noexec/nosuid 128 MiB tmpfs: enough for three concurrent 25
MiB prevalidation spools and still below the 768 MiB app memory limit.

The ordinary API connection uses `app_rw`; the declaration worker uses only
`app_worker`. Only the API receives the separate `app_review` pool URL. Verify
this separation and confirm no migrator URL leaked into either service:

```sh
root=/home/chris/apps/hausverwaltung-alpha
release=$(readlink -f "$root/current")
image="hausverwaltung-alpha:$(basename "$release")"
APP_IMAGE="$image" HV_RELEASE_ID="$(basename "$release")" docker compose --project-name hausverwaltung-alpha \
  --env-file "$root/shared/.env" -f "$release/docker-compose.prod.yml" ps
```

There is no automatic rollback after promotion. Before promotion, the helper
automatically restores the prior runtime after a failed replacement while
leaving the migrated schema in place. After promotion, only if an earlier image
is compatible with that schema, explicitly start that app with its own release
Compose file and then move `current`; do not run downgrade or stop
PostgreSQL/volumes.

## Optional GitLab deploy

The GitLab deploy job stays disabled until `HV_ENABLE_CI_DEPLOY=true` on the
protected default branch. It requires protected/masked `SSH_PRIVATE_KEY`,
`SSH_KNOWN_HOSTS`, `DEPLOY_HOST`, `DEPLOY_USER`,
and `HV_SMOKE_ADMIN_PASSWORD`. The admin password is passed to the remote
helper only through standard input, never archive content or command
arguments. CI first requires successful image build, Trivy scan and isolated
container smoke; the remote helper repeats its host-build scan and full public
smoke before promotion.


### Worker role prerequisite for migration 0070

Provision the separate `app_worker` login before upgrading an existing database.
Fresh volumes use the reviewed `infra/db/prod-init.sh`; existing volumes do not
rerun initialization automatically. Use that idempotent role provisioning script
with the protected `APP_WORKER_PASSWORD` supplied only to the database service.
Do not reset a populated volume, copy web credentials, or grant `app_worker` to
`app_rw`. Migration refuses a missing/privileged worker role or reverse membership.
This procedure requires the separately authorized deployment window.

The worker receives only `HV_DATABASE_URL_WORKER` for its runtime connection and
verifies that the current role is `app_worker`, without superuser, BYPASSRLS or
CREATEROLE. The web service continues with `app_rw` and receives no worker URL.
A missing worker URL fails startup; there is no fallback. Both roles remain subject
to tenant/source RLS. Only the worker can claim, renew and transition extraction
leases. After migration, retain this role separation during rollback and pause
workers before credential rotation. Source enforcement itself remains disabled.


## Isolated review runtime (0073)

Provision the independent `app_review` login before migration 0073 on an existing
volume, using the protected `APP_REVIEW_PASSWORD` and the reviewed `prod-init.sh`
procedure in a separately authorized maintenance operation. Do not reset volumes
or grant app_rw/app_worker membership. The review role is NOINHERIT, owns no objects,
has no elevated flags, parent roles or delegates, and receives only explicit graph
SELECT privileges and narrow review commands from the migration. Startup of a new
volume includes it automatically. Existing database-service environment changes
must be accounted for in the maintenance procedure; the deploy helper still refuses
an unexpected replacement of the stable database container.

Only the API receives `HV_DATABASE_URL_REVIEW`; the extraction worker must never
receive it. The two mutation endpoints use a separate pool with no fallback to
HV_DATABASE_URL. Missing/unsafe review configuration returns REVIEW_RUNTIME_UNAVAILABLE.
Read-only review views keep their ordinary connection. Local development uses the
same dedicated setting, pointed at the dev-only app_review login from init.sql.

The runtime checks actual session/current role, no membership edges, no elevated
attributes or ownership, and no public-schema CREATE privilege. Both deployment
helpers test the review connection from the API container. This separation protects
against arbitrary ordinary-runtime SQL; it does not protect against compromise of
the API process or its dedicated review credential. Keep that credential server-side
and rotate it independently. Source activation remains disabled by this release.
