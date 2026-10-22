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

T44 adds index-rent preview/adoption over the same history, without a migration,
external VPI service or new credentials. Entered receipt dates are operator data,
not delivery evidence; the calculation aid neither generates nor sends notices.
The production gate and authorization boundary remain unchanged.

T45 provides separate heating/hot-water pools and explicit tenant/WEG-owner
context. T46 adds an opt-in internal BK calculation using unit-meter boundaries
or explicit manual slice quantities; external statements remain the default.
No new environment setting or migration is required (Alembic head remains 0131).
Saved draft intent, actual meter provenance and the PDF calculation are retained
in the existing snapshot path; finalized originals are never recomputed.
Missing/ambiguous readings fail closed rather than selecting an estimate.
This local validation does not change the production gate or authorize deployment.

`HV_DUNNING_BASE_INTEREST_RATE` is an optional decimal basis-rate default for
new dunning notices, not an automatically maintained official rate. An explicit
request can override or disable it; the notice freezes its rate, reference date,
day counts and interest amounts. Existing issued notices are not recalculated
when configuration changes. The UI permits a deliberate rate entry when no
default is configured. This documentation does not change any runtime setting.

## Optional document SMTP (T47; no deployment authorization)

Outbound mail is disabled unless `HV_SMTP_HOST` and `HV_SMTP_FROM` are supplied.
Set `HV_SMTP_PORT` explicitly for the chosen service (default 587) and
`HV_SMTP_SECURITY=starttls` or `tls`; certificate verification remains enabled.
Authentication requires both `HV_SMTP_USERNAME` and secret `HV_SMTP_PASSWORD`.
`HV_SMTP_TIMEOUT_SECONDS` defaults to 15 (1–60). No real credentials, sender domain
or delivery authority are assumed by the application or this document. Keep
credentials in operator-managed secrets, not the repository.

For a **local synthetic test only**, run:

```sh
HV_DOCUMENT_STORAGE_ROOT="$(mktemp -d /tmp/hv-t47-documents.XXXXXX)" \
HV_SMTP_HOST=127.0.0.1 HV_SMTP_PORT=11025 \
HV_SMTP_FROM=verwaltung@example.invalid HV_SMTP_SECURITY=plain \
scripts/dev-local.sh
```

Then run `node scripts/acceptance/t47_mail_browser.cjs` in a second terminal with
the local Playwright package available. That script owns a loopback-only SMTP
receiver, accepts only `tenant@example.invalid`, never forwards mail, and closes
SMTP/browser resources in `finally`. Stop the launcher afterward. Unencrypted
SMTP is refused outside loopback or with credentials.

SMTP and database commits are not atomic: the application commits an append-only
prepared attempt and exact PDF storage reference **before** contacting SMTP, then
records acceptance/failure/unknown separately. A crash may leave `prepared` even
if SMTP accepted DATA. Never automatically resend unknown/open attempts. Check the
operator's SMTP logs before deliberately creating a new attempt; Message-ID and
request key aid investigation but do not guarantee recipient-side deduplication.
SMTP acceptance is not delivery or legal receipt. Retained PDFs require the same
storage retention as other originals; do not delete them. No migration or change
to the production gate is introduced.

## Saved letter originals (T48; local head 0132 only)

Migration **0132** adds tenant-protected `letter_template` and immutable
`letter_document` rows. It refuses downgrade while either table is populated;
never erase templates/originals or disable guards to force rollback. The
transactional migration test exercises real down/up operations with existing
rows and compares old data, RLS/ACLs, policies, triggers and function definitions.
The existing document storage must retain issued PDFs; downloads read those
bytes with SHA-256 validation, not a rerendered template. No new credentials or
environment settings. For synthetic UI acceptance use `scripts/dev-local.sh`
with a fresh `/tmp` `HV_DOCUMENT_STORAGE_ROOT`, then
`node scripts/acceptance/t48_letters_browser.cjs`; stop the launcher afterward.
No email is sent by that script. This does not change the accepted **0122**
production gate or authorize promoting **0123–0132**, deploying or operating on
production data.

## DATEV file export (T49)

No DATEV credentials, network transmission, environment defaults or schema changes
are introduced by T49 (implementation head **0132**; T50 adds **0133** below). Configure SKR03/SKR04,
adviser/client identifiers, fiscal year, account length and every bank/payment/
category counterpart explicitly in the UI for each export. Only reviewed gross
accounts **without tax automation** are supported; no automatic VAT split/BU keys.
UTF-8 with BOM follows the official DATEV manual-import/EXTF specification; the
legacy KrStaPv route does not support Unicode. Target-importer acceptance was
not exercised and is not claimed. Files are full current-state projections, not
incremental/deduplicated imports; repeated or overlapping imports require manual
reconciliation. Never treat file download as successful DATEV processing.
For synthetic Q1 acceptance use the existing local launcher with fresh `/tmp`
storage and `node scripts/acceptance/t49_datev_browser.cjs`, then stop it. No
production access, promotion, deployment or change to the **0122** gate is implied.

## Offline SEPA rent file (T50; local head 0133)

Migration **0133** adds source-protected `sepa_creditor` and `sepa_mandate` with
FORCE RLS, financial/ACL input locks and immutable consent identity. Downgrade
refuses populated SEPA tables; never delete consent/history to force rollback.
The actual rollback-transaction test checks 0133→0132→0133 against existing rows,
RLS/ACLs, policies, triggers/functions and an unchanged issued letter PDF.
This does not authorize promoting **0123–0133**, deployment or changing the
accepted **0122** production gate.

No bank credentials, transmission process or payment booking is introduced.
Configure actual creditor ID, bank agreement and externally retained signed tenant
mandates explicitly; do not infer consent from an IBAN. Source identities and
bank changes require renewed checks. Download is not execution and must not
advance FRST/RCUR or actual collection history. Repeated bank uploads can duplicate
collections. Confirm bank acceptance/profile and actual prenotification externally.
The runtime ships the unchanged EPC CORE pain.008.001.08 XSD and uses `lxml` locally;
no schema/network lookup or bank connection is performed by export.

Synthetic acceptance only: start `scripts/dev-local.sh` with fresh `/tmp`
`HV_DOCUMENT_STORAGE_ROOT`, then from repository root run
`HV_PYTHON="$PWD/backend/.venv/bin/python" node scripts/acceptance/t50_sepa_browser.cjs`.
The script uses only public synthetic IBAN/creditor vectors, no real consent/funds,
validates two actual browser downloads with original ISO and EPC schemas, and
never submits a file. It permits only the launcher's local Vite HMR WebSocket;
other sockets/external targets are blocked. Stop the launcher afterward.

## CSV master-data import (T51; local head 0134)

Revision **0134** adds immutable, actor/org-scoped `master_import_batch` metadata
with FORCE RLS and SELECT/INSERT-only application grants. Populated downgrade
refuses removal; never erase import history to force rollback. The actual
0134→0133→0134 rollback-transaction test compares existing data, relation rights,
policies, triggers/functions and an unchanged issued letter PDF. This is not
permission to promote **0123–0134**, deploy or change the accepted **0122** gate.

No new credentials or storage service. CSV inputs are UTF-8, semicolon-delimited,
limited to 256 KiB/500 rows with a bounded JSON envelope. Preview never publishes
source grants or writes records. Application is an atomic live-writer operation;
explicit audiences use the existing publication lock and canonical source checks.
Only result identities/counts, not raw CSV/contact fields, are retained per batch.
For synthetic UI/download acceptance use `scripts/dev-local.sh` with fresh `/tmp`
storage, run `node scripts/acceptance/t51_master_import_browser.cjs`, then stop it.

## Monthly dashboard rent KPIs (T52)

No migration, credentials or background job. The read-only dashboard endpoint uses
the existing complete property/source/contact and MIET accounting-scope admission,
financial-input lock and booked-payment computation context. A selected scope must
cover the relevant complete lease history; incomplete coverage is not clipped.
Synthetic UI acceptance uses the isolated local launcher and
`node scripts/acceptance/t52_dashboard_kpis_browser.cjs`; stop it afterward.
This does not authorize any production promotion or change the accepted 0122 gate.

## Selected dunning batches (T53; local head 0135)

Revision **0135**, after unchanged 0134, retains immutable actor/org/request-scoped
`dunning_batch` result identities. FORCE RLS and SELECT/INSERT-only grants prevent
rewriting or deleting completed batches; a populated downgrade refuses removal.
The protected 0135→0134→0135 test compares all existing rows, relation rights,
policies, triggers/functions and an issued letter PDF inside an outer rollback.
Do not erase history to force rollback. This does **not** authorize deployment or
promotion of **0123–0135**, or change the accepted **0122** production gate.

No new credentials or sending service. Preview/apply reuse live source/contact and
MIET preparation rights, ACL → financial → request locks, and one HTTP transaction.
Source changes require a fresh preview; uncertain responses retry the same request,
not a new batch. Only selected drafts are created, never issued or sent. Synthetic
UI acceptance uses the isolated local launcher and
`node scripts/acceptance/t53_dunning_batch_browser.cjs`; stop it afterward.

## Previous-year plan proposals (T54; no new migration)

Local head remains **0135**; this feature does not authorize any production
promotion or change the accepted **0122** gate. Existing draft JSON retains
`draft_inputs` and retained source identifiers/digest in `previous_actuals`;
normal computation/finalization preserves that provenance and the existing
coverage checks. No OCR worker, bank operation, mail or automatic finalization.

Prior-year READ and destination-year PREPARE are checked independently before a
local explicit context switch; complete source disclosure precedes enumeration.
Current confirmed signed gross costs use inclusive service-period day proration,
not the invoice-date year used by the separate accounting report. Reserve proposals
remain separate. Existing sources and finalized originals are untouched; no schema
or new environment credential is required. Repeat the synthetic UI flow with the
isolated launcher and `node scripts/acceptance/t54_previous_plan_browser.cjs`, then
stop app/browser. The manual invoice path does not require the intentionally open
T10 OCR worker. Unknown create outcomes require checking the plan list, not retrying
creation automatically.

## Editable master metadata (T55; local head 0136)

Revision **0136** adds bank/vendor `is_active` and nullable property construction
 year/Decimal total area. Existing accounts/vendors remain usable; no balance or
invented building facts are added. Own additive SQL triggers serialize source
changes, prevent rewriting used IBAN/BIC and reject new inactive references.
Exact historical Storno pairs remain possible; old RLS policies/functions stay
unchanged. Populated lifecycle/building facts block downgrade rather than vanish.
The actual rollback-protected 0136→0135→0136 test compares old-column data,
relation rights, policies, triggers/functions and an issued PDF. Deferred
constraints are executed, not disabled. No new credentials or external operation.

The synthetic UI-only acceptance uses `scripts/dev-local.sh` with isolated `/tmp`
storage and `node scripts/acceptance/t55_master_data_browser.cjs`; stop app/browser
afterward. This does **not** authorize production deployment/promotion of
**0123–0136** or change the accepted **0122** gate.

## User administration and session invalidation (T57; local head 0139)

Revision **0138** adds a nonnegative session generation, its own invalidation/ACL
triggers and narrow versioned auth wrappers; existing auth functions and policies
are unchanged. Password hash changes and deactivation invalidate existing cookies;
reactivation cannot restore them. Legacy cookies mean generation zero, not a
wildcard. Admin API writes revalidate under an exclusive tenant ACL lock before
row locks. Audit events contain no passwords, hashes or session generations.

Downgrade refuses positive generations rather than resurrecting revoked sessions.
The real rollback-only 0138→0137→0138 test compares old rows/security objects and
issued PDF bytes. Only the DDL-owning migrator can prepare the new counter for a
rollback-only rehearsal; app_rw cannot alter it. Disposable-test cleanup is
explicitly database/role-guarded, not a production downgrade bypass.

Revision **0139** grants only `app_validate_session_versioned(uuid,uuid,integer)`
execution to the isolated `app_review` role, not either password lookup. The real
0139→0138→0139 round trip retains all data, RLS/policies/triggers, function
definitions and other ACLs, including issued PDF bytes; the normal Alembic CLI
round trip also passed. Authenticated generation is carried unchanged through
short knowledge-query transactions and prior-year plan admission.

This is **not** authorization to promote **0123–0139** to production or change the
accepted production **0122** gate. No deployment was performed.

## Unused master deletion (T56; local head 0137)

Revision **0137** adds only an authorized read-only reference inventory and an
internal exact-UUID JSON matcher. No table data, old RLS policy or DELETE grant
is changed. Actual deletion remains invoker/RLS-bound, requires a returned row
and executes deferred constraints before HTTP 204. Physical references including
CASCADE edges, immutable receipts, source evidence, audit and snapshot references
prevent deletion. The real rollback-protected 0137→0136→0137 test compares all
existing rows/security objects and an issued letter's exact PDF bytes.

Run `uv run pytest tests/test_master_delete.py` on the disposable test database.
The UI acceptance uses the prescribed isolated launcher and
`node scripts/acceptance/t56_master_delete_browser.cjs`; stop app/browser afterward.
This does **not** authorize production promotion of **0123–0137** or change the
accepted **0122** gate.

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
