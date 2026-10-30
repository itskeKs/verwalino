# Deployment

## Deployment mode

`HV_DEPLOYMENT_MODE` accepts exactly `selfhost` (default) or `hosted`. Invalid values
stop the process at configuration initialization with the allowed values in the error.
Set it in the backend process environment; a custom container deployment must pass it
explicitly to the container. Changing it requires restarting the backend. The public
`GET /api/meta` reports the active mode and application version, without secrets.

Both modes use the same image, authentication and organization isolation. `selfhost`
offers token-gated first-run setup on an empty database. `hosted` suppresses setup:
`GET /api/setup` reports `required: false`, every non-GET request returns the closed
setup 404, and `/setup` redirects to login. Until signup (#19) exists, operators use
the existing admin CLI to bootstrap hosted organizations. Selecting hosted mode does
not implement signup, rate limiting, quotas, password reset or legal pages. The
[policy table](docs/architecture.md#deployment-modes) records current readers and
future consumers; error tracking's environment default is reserved for #16.

The portable installer below is selfhost-only and unchanged, including `start`'s
setup-token flow. Its manifest relies on the `selfhost` default; it does not forward
`HV_DEPLOYMENT_MODE` from the caller or accept it in `.env.selfhost`. Its HTTP/HTTPS
`VERWALINO_MODE` is a separate transport choice. Use a separately managed deployment
to run hosted mode; do not use the portable installer to start a hosted service.

## Portable Docker first installation

This path is independent of the existing alpha. `infra/docker-compose.yml`
replaces the obsolete development-only :5433 database starter; development and
backend tests still use **only** `infra/docker-compose.declaration-test.yml`
and `scripts/test-env.sh`. Never run the portable stack as a test database or
on the existing production Docker host. Agent execution of this stack or an
isolated Caddy validation container requires separate authorization and must
satisfy the narrow exceptions in `AGENTS.md` and the installed guard. Current
checks use simulated Docker and static contracts; no real-container or complete
installation test is claimed.

Prerequisites: Bash (including macOS Bash 3.2), OpenSSL, Docker Engine/Desktop
and Compose v2 >= 2.24. From a reviewed checkout:

```bash
bash scripts/self-host.sh init
bash scripts/self-host.sh start
```

No host Python, Node, uv, API key, external proxy network or SMTP account is
required. By default, `start` builds the checked-out Dockerfile (including its
frontend checks). The release workflow publishes to `ghcr.io/itskeks/verwalino`
for `linux/amd64` and `linux/arm64`; the first published release and native arm64
installation still need maintainer acceptance. Once a release is available,
[find and verify its digest](#published-image-verification) before selecting it
on first init:

```bash
bash scripts/self-host.sh init --image 'ghcr.io/itskeks/verwalino@sha256:<64-hex-digest>'
```

The installer generates five distinct 256-bit hexadecimal secrets in
`.env.selfhost`, mode 0600. Keep this file private and back it up. It is neither
sourced as shell code nor passed wholesale into containers. Init does not
rotate credentials or overwrite an existing file; a repeat with configuration
options is refused. Symlinks, unsafe permissions, unknown/duplicate/missing
keys, malformed domains and non-digest remote images fail closed.
`VERWALINO_CONFIG=/absolute/private/path/.env.selfhost` changes its location,
not the Compose project. Only `.env.*` filenames are accepted and these are
excluded recursively from Git and Docker build contexts. Do not run with shell
tracing or print the environment/config in support logs.

The Compose project is always **verwalino**, with project-owned `pgdata`,
`documents`, `caddy_data` and `caddy_config` volumes, no external alpha volumes
or networks. Only one portable installation per Docker context is supported.
Keep the checkout and file paths stable: moving bind-mounted infrastructure
files may cause Docker to recreate containers. An interrupted operation can
leave `<config>.lock`; remove that empty directory only after verifying no
installer operation is active. A partial/absent config on an existing database
requires restoring the original config, not regenerating different passwords.

Start order is build/pull -> database readiness -> stop any existing web writer
-> one-shot migration -> setup-token issuance when empty -> app readiness
(DB and document-store probe) -> optional Caddy. Migration, build, database or app failures return nonzero without a
success message. A failed migration leaves the web app stopped; no automatic
schema downgrade, restoration or container rollback is attempted. Do not use
`start` as an unreviewed version upgrade. The migration and bootstrap services
alone receive the migrator URL; app runtime gets only app_rw/app_review.
PostgreSQL has no published port and runs on an internal backend network.

Default local mode publishes only **127.0.0.1:18080**, without secure-cookie
requirements over HTTP. `init --port 19080` selects another loopback port.
Do not expose this HTTP listener publicly or tunnel it as a production endpoint.
For a new Internet-facing installation choose HTTPS at init instead:

```bash
bash scripts/self-host.sh init --https verwaltung.example.org
```

Configure A/AAAA records correctly (remove an unusable AAAA record); open TCP
80/443 and ensure no other proxy uses those ports. Caddy redirects HTTP to HTTPS
and persists certificates. The app uses secure `__Host-` session cookies,
explicit allowed hosts, security headers and a 30 MB proxy upload limit. The
loopback diagnostic listener remains published, but use the HTTPS URL to log
in. Uvicorn trusts forwarded headers only from the dedicated private proxy
subnet (default `172.30.97.0/28`). If it conflicts with a host/VPN/Docker route,
choose an unused aligned private /24 through /28 at init with
`--proxy-subnet 192.168.240.0/28`. The script reports certificate provisioning
as pending, not as a successful TLS/Internet reachability check. The stock
Caddy configuration does not replicate Traefik's alpha login-rate middleware;
public rollout requires a separate abuse-protection review.

On a database with no organization, `start` prints a `/setup` URL and a new
256-bit **Setup token** exactly once after app readiness. Open that URL, enter
the token, organization, administrator name/email and a password (12–128
characters, repeated). Successful setup signs you in and opens the dashboard.
The token is never in the URL, private config or application logs; only its
SHA-256 digest is retained in the private singleton `setup_token` table. Do
not include terminal output in support logs. It expires after 24 hours; run
`start` again to replace a lost/expired token and invalidate the previous one.
A failed start does not print a token; retry `start` to issue another one.
Once any organization exists, no token is issued, the form redirects to login,
and non-GET setup requests return the same 404 body as unknown API GET routes.
Wrong, consumed or expired tokens receive one generic German 403 response; invalid fields
receive 422. Leading/trailing spaces in the token and organization/admin names
are trimmed; control characters in these fields and the admin email are rejected. If the initial
setup-status request fails, login and signed-in pages remain available; reload
after connectivity recovers to open setup on a fresh installation.
No signup or application rate limiter is included.

CLI bootstrap remains the alternative:

```bash
bash scripts/self-host.sh admin --org "My property management" --email admin@example.org
```

It uses the migrator role and invalidates any pending setup token in the same
transaction. Admin bootstrap prompts for the password twice without echo.
Existing users are left unchanged by the existing admin CLI; it is not a password-reset tool.
For automation feed a newline-terminated password from a secret manager to
`admin --org ... --email ... --password-stdin`; never place it in arguments,
checked-in files, or an `echo` command in shell history.

Use `status` for container status and `stop` to stop without deleting any data.
There is deliberately no destroy, automatic update or backup command. Before
an upgrade, plan a consistent backup of the stopped application's database,
document volume and private config, and test restoring them together on a
separate host. An older image is not a schema rollback. This feature does not
authorize an alpha deployment, promote its schema gate, or supply a backup
and disaster-recovery guarantee.

AI declaration import/Q&A, SMTP and invoice OCR are disabled in this initial
portable stack. Manual invoice review remains supported. The T10 worker is
active on the existing alpha; portable worker integration is outside this
installer's scope. The first tag publication and native arm64 image installation,
complete clean-host setup/login/restart and real domain/TLS acceptance still
need operator checks; do not treat the mock installer tests as
proof of those behaviors. Run the portable tests without Docker using
`cd backend && uv run python ../scripts/test-self-host.py`; the backend CI job
runs these before pytest. Real Caddyfile validation and the complete portable
stack smoke test require a separately authorized clean Docker host.

### Published image verification

The [release workflow](.github/workflows/release-image.yaml) runs on a pushed
stable tag such as `v1.2.3` and publishes only `v1.2.3` and `v1.2` tags.
Tags can move; installation uses the immutable
**multi-platform index digest**. BuildKit attaches maximum-mode provenance
and SBOM attestations for both platforms. Cosign signs the enclosing index
with GitHub Actions OIDC; no stored signing key is used. There is no additional
GitHub provenance attestation: BuildKit already supplies provenance.
The static frontend and its lint/tests build on the builder's native platform
to avoid test failures under emulation; Python dependencies and runtime checks
build separately for each target architecture. Base-image digests stay pinned.

After a successful [Release image run](https://github.com/itskeKs/verwalino/actions/workflows/release-image.yaml),
copy `ghcr.io/itskeks/verwalino@sha256:...` from its job summary. Alternatively,
inspect the release tag and copy the top-level `Digest` (not a platform's digest):

```bash
image=ghcr.io/itskeks/verwalino
tag=v1.2.3  # Example; choose a successfully published release.
docker buildx imagetools inspect "$image:$tag"
digest='sha256:<64-hex-digest>'  # Replace with the index digest from that run.
```

Install [cosign](https://docs.sigstore.dev/cosign/system_config/installation/)
and verify the digest against this repository's release workflow and the
chosen tag. Both the certificate identity and issuer must match:

```bash
tag_pattern="${tag//./[.]}"
cosign verify \
  --certificate-identity-regexp "^https://github[.]com/itskeKs/verwalino/[.]github/workflows/release-image[.]yaml@refs/tags/${tag_pattern}$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "$image@$digest"
```

Proceed only after verification succeeds. Use that same digest with
`bash scripts/self-host.sh init --image "$image@$digest"`, then the `start`
and `admin` commands above. Remote tags are deliberately refused by the installer;
it does not resolve tags or verify signatures for you. `vX.Y` is a discovery
alias, not an automatic update mechanism.

On a **native 64-bit arm64** host (Apple Silicon or a 64-bit Raspberry Pi OS),
run the same signature verification, then check the selected image:

```bash
uname -m  # arm64 or aarch64; an emulated run is not native install acceptance.
docker buildx imagetools inspect "$image@$digest"  # Must include linux/arm64.
docker pull --platform linux/arm64 "$image@$digest"
docker image inspect "$image@$digest" --format '{{.Os}}/{{.Architecture}}'
```

Expect `linux/arm64`. On a clean host, initialize with that digest, start,
create the admin, open the local URL and sign in. Check
`curl --fail http://127.0.0.1:18080/api/health/ready`, then `stop`, `start` and
sign in again to confirm persistence. Record the host, release digest and result
in the PR. A local cross-platform build alone does not prove installation.

### Maintainer release checklist

1. Choose `X.Y.Z`, bump the application versions in `backend/pyproject.toml`
   and `frontend/package.json` with their lockfiles, and review/merge the release
   changes after `make test` and `make lint` pass.
2. Create and push the reviewed `vX.Y.Z` tag. The `Release image` workflow builds,
   publishes and signs both platforms. Only stable `vX.Y.Z` tags are accepted.
   The workflow refuses any tag whose commit is not on `main`.
   For a manual rehearsal/retry after the workflow exists on the default branch,
   set `tag` to that existing release tag and run
   `gh workflow run release-image.yaml -R itskeKs/verwalino --ref "$tag" -f tag="$tag"`.
   The workflow ref and input must match. **This publishes too** and updates
   the `vX.Y.Z` and `vX.Y` tags for the selected version.
   Release runs are serialised; a tag whose run was skipped while another release
   was pending must be re-dispatched.
3. Check the workflow succeeded, make the GHCR package public for anonymous
   pulls if necessary, and inspect both platforms, provenance/SBOMs and the
   signature at the summary's digest. Verify anonymously using the commands above.
4. Copy the successful release tag and index digest into the examples in this
   section and [README.md](README.md#self-hosting-verwalino-portable-docker-first-installation).
   Complete the native arm64 installation check above.

The **first tag run and the arm64 installation check remain acceptance steps
for the maintainer**. This preparation does not close #7. Full `make test`
remains a pre-merge gate; the local workflow/installer checks need no database.

## Request IDs and optional error tracking

Request correlation is always enabled, including when error tracking is off.
Every HTTP response carries `X-Request-ID`. An incoming value is accepted only
when it contains 8–128 ASCII letters, digits, dots, underscores or hyphens;
otherwise the app generates a UUID. Do not put personal data into request IDs.
Generic exceptions return `{"detail":"Internal Server Error","request_id":"…"}`;
HTTP 5xx exceptions keep their existing detail and headers and add `request_id`.
HTTP 4xx bodies are unchanged. The existing frontend error messages show the
header ID on server failures. No request IDs are added to URLs or stored in the DB.
Explicit status responses such as readiness `503 {"status":"unavailable"}`
retain their bodies, carry the header and do not generate exception events.
Raised HTTP 503 exceptions (for example, disabled features) also retain their
detail and headers, add the request ID and produce a WARNING without a tracker
event. Only unhandled exceptions and raised HTTP 500 exceptions are reported
and logged at ERROR; other raised HTTP 5xx exceptions produce a WARNING.

Log lines include `request_id=<id>` across the API, services and database layer.
Worker processing uses the durable job/import ID in that same field; startup,
idle and other records outside a request/job use `-`. On an authorized portable
installation, an operator can find a reported ID without starting the stack:

```sh
docker compose --project-name verwalino --env-file .env.selfhost \
  -f infra/docker-compose.yml logs --no-color app | rg --fixed-strings 'request_id=REPORTED_ID'
```

Error reporting is **off by default**, including self-host. These optional keys
are read from the API process environment (or its `.env`):

| Key | Default | Meaning |
| --- | --- | --- |
| `HV_ERROR_TRACKING_DSN` | empty | Empty disables SDK initialization and tracker transport. A nonempty Sentry-compatible project DSN opts in. Keep it in operator-managed secrets. |
| `HV_ERROR_TRACKING_ENVIRONMENT` | `selfhost` | Deployment label, e.g. `hosted-production`; never a person's or organization's name. |

The portable installer keeps tracking disabled and does not accept/forward these
keys in `.env.selfhost`. The existing production Compose configuration also needs
separately reviewed environment wiring before opting in; adding keys to the
host's dotenv file alone does not forward them into a container.

For the hosted offer, use **self-hosted GlitchTip** as the Sentry-compatible
tracker. Provision its project, access controls, retention and TLS separately,
then supply its DSN to the API process. No external SaaS account is required by
the application. Events carry `request_id` and, after session validation,
`organization_id` (UUID only). Filter tracker events by the `request_id` tag
using the reported ID to find the event matching a response or log line.
Release is `HV_RELEASE_ID` from the **process environment**, otherwise app version
`0.1.0`. The production Compose label alone does not set that environment variable;
image labels/digests are not discovered at runtime. Portable Compose forwards an
exported `HV_RELEASE_ID`; it is not an accepted `.env.selfhost` setting.
The SDK uses FastAPI/Starlette integrations, explicit `data_collection` controls,
at most ten breadcrumbs and no performance tracing. Error-event probability is
fixed at `1.0` in `core/error_tracking.py`: every eligible error is sent while
tracking is enabled. User identity, cookies,
bodies, query parameters, SQL values, frame locals and source context are not
collected. The machine hostname (`server_name`) is omitted. Duplicate exception
reports are suppressed; a failed response stream is reported once with its ID
and remains an incomplete response.
See the [Sentry Python configuration options](https://docs.sentry.io/platforms/python/configuration/options/)
for the SDK settings.

Before sending, a shared pure scrubber removes request bodies, cookies, user
objects, stack locals and source snippets. Request headers are dropped except
`content-type`, `content-length`, `accept`, `user-agent` and `x-request-id`
(case-insensitive); their retained values are scrubbed too. Filtering reaches
tags, `extra`, contexts and breadcrumb messages/data at every level. Postgres `DETAIL:`, `HINT:`,
`Failing row contains (...)` and `Key (...)=(...)` lines are removed while the
constraint name and SQLSTATE in the error summary remain.
It masks email addresses, compact/space/hyphen-grouped IBANs (either case), phone
shapes (international prefix, leading zero or separators), recognizable German
street addresses (`straße`, `strasse`, `str.` plus house number), surname-first
names, names with particles and accented capitals, and structured owner/key
values. Generic capitalized multiword names are masked only in error contexts;
HTTP reason phrases, ISO dates and digit runs without a phone shape are preserved.
Bearer/signed session tokens and labeled passwords/secrets are masked as well.
Query-string values are redacted, including URLs in exception messages and
breadcrumbs. Masks use `[redacted:email]`,
`[redacted:iban]`, `[redacted:phone]`, `[redacted:address]`, `[redacted:name]`,
`[redacted:token]`, `[redacted:password]`, `[redacted:header]` and `[redacted:query]`.
The same text scrubber filters formatted messages and tracebacks **only at
ERROR and above** (the 500/unhandled-error paths). `uvicorn.access` receives
correlation IDs without text scrubbing, regardless of level. INFO/WARNING log text
is unchanged; copies sent as tracker breadcrumbs are scrubbed as error context.
Each scrubbed string is limited to 8,192 input characters. Tracebacks retain the
last complete lines within that limit so the final error line survives. An
overlong final line, and other text, retain the first 8,192 characters. Omitted
text is marked with `[truncated]`. Patterns use bounded tokens and
linear scanning. SQLAlchemy hides bound parameters. Correlation IDs are
preserved, and `weasyprint`/`fontTools` emit only WARNING and above.

Authenticated names/email and the organization's person directory are not
available in the request context and are not fetched for reporting. Name masking
is pattern-based: in error contexts it can mask ordinary capitalized phrases
and miss unusual or unlabelled single-word names. Other unrecognized free text may still be personal data;
keep exception messages structural and never intentionally log documents or
request bodies.

## Existing alpha: `verwaltung.karat.immo`

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

The former explicit **no backups** decision is superseded: a verified, paired
database/document backup is mandatory before schema changes. The authorized
2026-10-24 upgrade created and verified the private pair at
`shared/backups/f0d2c411d52a6e2c927ea2d346d0a5070cf53ec5-from-0028/`
(`database.dump` and `documents.tar.gz`, both 0600; directory 0700). A future
schema change needs its own authorized window and backup. Preserve both live
volumes and the previous release.

## Deployment boundary and accepted head

These procedures require an authorized operator window; editing this runbook
does not itself authorize SSH, secret access or deployment. Before the
2026-10-24 upgrade, the alpha database and running image were at **0028**; the
former **0122** assumption was not a live measurement. The authorized release
`f0d2c411d52a6e2c927ea2d346d0a5070cf53ec5` migrated the database to
**0139** and is now the `current` release. The API, declaration worker, invoice
worker and existing database were healthy after promotion; public HTTPS
`/api/health/ready` returned 200 with verified TLS. The prepared code now targets
**0141**; this does not change the recorded live release or authorize deployment.
The release gate accepts only measured **0028 → 0141**, **0122 → 0141**,
**0139 → 0141**, **0140 → 0141** (accepted source), or **0141 → 0141**. Unknown,
missing, multiple or intermediate revisions fail before stopping writers or
migrating. Historical feature notes below describe earlier boundaries; their
statements about an unchanged helper or an inactive alpha are superseded by
this verified deployment.

The earlier phase-2 work was verified locally on the disposable test database
through migration **0131**. Its former 0122 gate and authorization statements
were superseded by the separately authorized 2026-10-24 alpha upgrade above.
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

## Invoice worker (T10; alpha active since 2026-10-24)

The `invoice-worker` service is defined in the existing production Compose file
and checked in `tests/test_invoice_extraction_worker.py`. The separately
authorized 2026-10-24 alpha upgrade started it on the same immutable image as
the API and declaration worker; its container passed health and release identity
checks. The worker adds no migration of its own. The deployment helper includes
its start, health/identity checks, cleanup and compatible runtime restoration.

The service uses the same immutable app image and existing `documents` volume,
mounted **read-only**. It receives only `HV_DATABASE_URL_WORKER` (`app_worker`),
local storage/worker settings and `HV_INVOICE_EXTRACTION_ENABLED` (default true,
shared with the API). It has only the internal `backend` network: no published
ports, Traefik, provider egress/key, web/review connection or migration credential.
Non-root/read-only execution, dropped capabilities, bounded tmpfs/memory/processes
and `no-new-privileges` remain enforced. Init plus a 130-second stop grace period
allow the bounded extractor to drain on SIGTERM; a canceled job retains its lease
for the existing fenced recovery path. Original bytes are never rewritten.

`python -m app.workers.invoice_extraction --healthcheck` checks the isolated worker
role, queue access, readable document storage, temporary storage and German local
OCR without consuming jobs or calling a provider. Startup fails closed if the
check fails. Normal processing discovers organization IDs each polling round;
source/principal RLS checks still apply to every claim and completion. A failure
in one organization is logged without source content and does not stop the others.
`--organization UUID` may be repeated to restrict processing; `--once` returns
nonzero on an iteration failure. Retry/lease budgets and human review are unchanged.

For a **local synthetic test only**, install the README's dev/OCR dependencies,
Poppler and Tesseract with `deu`, then run `scripts/dev-local.sh`. The launcher
pins all database connections to the disposable `:55432` database, uses a fresh
`/tmp/hv-dev-documents.*` directory by default (or an explicitly supplied `/tmp`
`HV_DOCUMENT_STORAGE_ROOT`), probes the worker and starts worker/API/UI. Ctrl+C
stops its children, including on partial startup failure; it does not delete
stored originals or stop PostgreSQL. Do not run resetting backend tests while
this UI acceptance is active.

In another terminal run `node scripts/acceptance/t10_invoice_worker_browser.cjs`
with `HV_PLAYWRIGHT_PACKAGE`/`HV_CHROMIUM_EXECUTABLE` pointing to installed tools
when necessary. It logs in as the synthetic local test admin, uploads a generated
PDF through the UI, waits for real worker extraction and checks **119.00 EUR /
2025-06-01**, unchanged source identity and no cost adoption/release. It blocks
external requests and non-login/upload writes. `HV_T10_INVOICE_PDF` may point to a
synthetic image-only PDF with those exact values to exercise OCR instead. No
extraction override, API/SQL fixture write or saved report is used by the check.

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
UI acceptance uses [the dunning batch browser feature](features/dunning_batch.feature)
via `make test-browser`, which owns and stops its isolated local app stack.

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
stop app/browser. The manual invoice path remains independent of automatic
invoice extraction; the local launcher now also starts the T10 worker. Unknown
create outcomes require checking the plan list, not retrying creation automatically.

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

## User administration and session invalidation (T57; revisions 0138–0139)

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
Before migration, verify in the authorized maintenance window that the
existing database has the distinct `app_worker` and `app_review` logins and the
protected `APP_WORKER_PASSWORD` and `APP_REVIEW_PASSWORD` settings. Fresh-volume
init scripts do **not** run again on existing volumes. Follow the role prerequisites
below with the stdin-only procedure; never reset populated volumes.
The pre-upgrade read-only check found only `app_migrator` and `app_rw`, and
neither new password key was present in `shared/.env`. Both new roles and
protected random credentials were provisioned before the 2026-10-24 upgrade.
For another existing installation, provision them before invoking the release
helper. Retain those credentials with the paired backup for recovery. Do not
rotate the existing `app_rw` or `app_migrator` passwords.

Existing database-container environment may predate those keys. Merely editing
`shared/.env` does not update it, and the deploy helper deliberately refuses an
database-container replacement: it requires the existing healthy DB and never
starts or reconciles that service. Runtime credential probes use the new release
settings, independently of old DB-container environment. Resolve missing roles
or credentials before release; do not use `app_rw` as a worker workaround.
The provisioning procedure creates missing roles, but does not rotate passwords on
existing roles. It was used as `chris` on Moon before the 0028 upgrade and can
be rerun only when the role/credential prerequisite needs repair. The existing
database container stays running. Run this in a private shell without tracing;
no password is passed in a process argument or printed:

```sh
(
set -eu
umask 077
root=/home/chris/apps/hausverwaltung-alpha
env_file="$root/shared/.env"
test -f "$env_file" && test ! -L "$env_file"
test "$(stat -c '%a' "$env_file")" = 600
exec 9>"$root/shared/deploy.lock"
flock -n 9
worker_count=$(grep -c '^APP_WORKER_PASSWORD=' "$env_file" || true)
review_count=$(grep -c '^APP_REVIEW_PASSWORD=' "$env_file" || true)
test "$worker_count" = "$review_count"
case "$worker_count" in
  0)
    worker=$(openssl rand -hex 32)
    review=$(openssl rand -hex 32)
    test "${#worker}" -eq 64 && test "${#review}" -eq 64 && test "$worker" != "$review"
    case "$worker$review" in *[!0-9a-f]*) exit 1 ;; esac
    tmp=$(mktemp "$env_file.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    chmod 600 "$tmp"
    cat "$env_file" > "$tmp"
    printf '\nAPP_WORKER_PASSWORD=%s\nAPP_REVIEW_PASSWORD=%s\n' "$worker" "$review" >> "$tmp"
    mv -f "$tmp" "$env_file"
    tmp=
    ;;
  1)
    worker=$(sed -n 's/^APP_WORKER_PASSWORD=//p' "$env_file")
    review=$(sed -n 's/^APP_REVIEW_PASSWORD=//p' "$env_file")
    ;;
  *) exit 1 ;;
esac
test "${#worker}" -eq 64 && test "${#review}" -eq 64 && test "$worker" != "$review"
case "$worker$review" in *[!0-9a-f]*) exit 1 ;; esac
db_id=$(docker ps --no-trunc -q --filter label=com.docker.compose.project=hausverwaltung-alpha --filter label=com.docker.compose.service=db)
test -n "$db_id" && test "$(printf '%s\n' "$db_id" | wc -l)" -eq 1
docker exec -i "$db_id" psql -Xqv ON_ERROR_STOP=1 -U app_migrator -d hausverwaltung <<SQL
BEGIN;
SELECT pg_catalog.format('CREATE ROLE app_worker LOGIN PASSWORD %L INHERIT NOBYPASSRLS NOCREATEDB NOCREATEROLE', '$worker')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_worker')
\gexec
SELECT pg_catalog.format('CREATE ROLE app_review LOGIN PASSWORD %L NOINHERIT NOBYPASSRLS NOCREATEDB NOCREATEROLE', '$review')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_review')
\gexec
GRANT app_rw TO app_worker;
GRANT CONNECT ON DATABASE hausverwaltung TO app_worker,app_review;
COMMIT;
SQL
unset worker review
)
```

If provisioning stops partway through, rerun it with the same protected keys;
never generate replacements for a partly provisioned database. The release
helper's disposable credential probes must pass before stopping writers.
Credential rotation is a separate operation with workers paused.

| Connection | Runtime role / setting | Boundary |
| --- | --- | --- |
| Ordinary web API | `app_rw` / `HV_DATABASE_URL` | No worker credential; no migrator connection |
| Declaration and invoice workers | `app_worker` / `HV_DATABASE_URL_WORKER` | No web URL fallback; no review credential |
| Dedicated human-review commands in API | `app_review` / `HV_DATABASE_URL_REVIEW` | Separate pool, no ordinary-role fallback |
| One-shot migration/bootstrap | Privileged ops credentials only | Never injected into app/worker runtime |

Before downtime, the new image checks its single head **0141**, the exact linear
0029–0141 chain, the measured single DB revision and the isolated login/role
membership, ownership and schema/ACL prerequisites. Disposable non-consuming
probes test each actual API/review/worker credential. The captured previous image
must have exactly the measured DB head and every previous runtime must have the
same immutable image plus matching project/service/release labels. Unexpected
running project containers fail closed. No roles or privileges are repaired.

The helper requires an existing `current` release and API container. Fresh
installation is a separate procedure; this helper no longer initializes an empty
DB. Each source revision is accepted only when actually measured.

### Migration compatibility: 0029–0141

| Revisions | Effect on existing data and runtime compatibility |
| --- | --- |
| 0029–0122 | Adds financial, declaration, source custody and review data structures and guards. The 0028 runtime cannot interpret the new schema or enforce its later security rules. The 0028→0141 rehearsal preserves all old columns and rows with synthetic organization, user, document, draft/failed import and failed extraction-run data, but does not prove every live-data combination. |
| 0123 | Adds nullable replacement evidence and changes exception-completion functions/outcomes. Existing rows retain their old disposition; old clients cannot interpret new replacement outcomes. Downgrade refuses retained evidence. |
| 0124–0125 | Adds bank transactions and a nullable payment link, then enforces tenant/account coherence and immutable booked links. A 0122 database has no pre-existing links; inconsistent intermediate data is not silently repaired. |
| 0126–0127 | Adds append-only rent history and frozen special levies; replaces financial/source/report adapters. No old financial rows are rewritten. Older runtimes do not understand the new obligations/history. Their downgrades can drop populated new tables; they are not a safe recovery path. |
| 0128–0129 | Extends dunning provenance with default empty levy IDs, default zero interest and nullable snapshots. Existing amounts survive; new levy/interest provenance blocks downgrade. |
| 0130–0135 | Adds retained attachments, tasks, letters/PDF references, SEPA consent, immutable import and dunning batches. No original files are migrated. Most populated downgrades refuse; task downgrade drops its table. No blanket lossless rollback claim. |
| 0136–0137 | Adds default-active bank/vendor lifecycle and nullable property facts plus stricter reference triggers and read-only deletion-reference functions. Non-default lifecycle/property facts prevent downgrade. |
| 0138–0139 | Adds default-zero session generation and versioned auth functions, then grants only session validation to `app_review`. Positive generations block downgrade. An older runtime's unversioned auth does not enforce the new invalidations: **automatic 0122 runtime restoration on the new schema is unsafe**. |
| Parent references (accepted source 0140) | Replaces client-selected parent references with organization-bound composite foreign keys. Validates pending deferred checks and rejects existing invalid references without repair before replacing constraints. Adds a boolean-only, tenant-bound property existence check for scoped financial creation. Downgrade restores the original constraints and removes that check; an older runtime is not approved on this schema. |
| 0141 | Adds the private singleton setup-token table and a token-gated, serialized first-organization function. Removes direct organization INSERT from app_rw; the migrator CLI shares the creation SQL. A populated token table blocks downgrade to avoid silently losing pending/consumed state. Existing organization/user/membership rows are preserved. |

DDL uses the existing PostgreSQL/Alembic transaction; 0123 also takes financial
and ACL locks with NOWAIT table locks. Contention or unexpected data/function
definitions must abort, not trigger repair/deletion. A rollback-only synthetic
0028→0141 rehearsal compares every old table column/row and retained RLS flags;
the 0122→0141, 0139→0141 and accepted-source 0140→0141 rehearsals also
compare existing ACLs, allowing only the explicit organization INSERT revocation;
it does not prove live data validity or cross-version runtime compatibility.

### Paired backup and recovery

During the authorized maintenance window, the helper reserves a new private
`shared/backups/<release-id>-from-<measured-revision>/` directory (0700; files
0600). It stops captured declaration/invoice workers with a 130-second drain,
then the API, and verifies they stopped. An absent old invoice worker is allowed.
Leased work uses the existing fencing/recovery rules after restart. PostgreSQL
stays running; all database clients other than the check itself must be gone.
The operator must prevent other writers/restarts throughout this window and retain
the previous source release, immutable image and protected credentials for recovery.

It creates `database.dump` with PostgreSQL's custom-format `pg_dump` and
`documents.tar.gz` from the stopped API's document mount, inherited read-only by
a network-disabled container using the new immutable image. It checks the dump
catalogue and reads every archive member including gzip integrity; links and
unsafe paths are refused. It rechecks DB identity and quiescence before migration.
Failed `.partial` files remain private and unusable. Both final files must exist
and verify to form a recovery pair; never use a partial pair. Existing directories
are never reused or overwritten. No credentials enter backup command arguments
or error logs. These host-local backups do not provide off-host disaster recovery;
retention/off-host storage needs an operator decision before relying on it.

Before migration, a failure can restart the captured API, then only the workers
that previously existed, after rechecking the unchanged schema. On 0141→0141,
the helper skips migration and may similarly restore the prior 0141 runtime.
Once an older-to-0141 migration is attempted, any failure leaves `current` unchanged
and stops only provably owned new runtime containers. It reports
`MAINTENANCE REQUIRED` and keeps old writers stopped, even if the migration may
have rolled back. Unknown ownership or failed stops also prohibit automatic
restoration. PostgreSQL, both live volumes and the backup remain intact.

Recovery in a **separately authorized** window:

1. Keep API and both workers stopped. Preserve the failed DB/documents as well
   as the pre-upgrade pair, especially any uploads/writes after new API startup.
   Prefer a reviewed forward repair when it preserves these later writes.
2. For snapshot recovery, verify both complete backup files and restore the dump
   with `pg_restore --exit-on-error --single-transaction` into an operator-provided
   **empty recovery destination** with the original separated roles. Never run
   `--clean`, downgrade, reset or volume removal against the live installation.
3. Restore documents with the reviewed image's
   `python -m app.deployment restore-documents <empty-staging-directory>` reading
   `documents.tar.gz` on stdin. It refuses a nonempty target, links and traversal;
   failed staging output must not be promoted. Check referenced originals and
   byte identity, the recorded source revision and role separation in recovery.
4. Select the immutable runtime whose head matches that recovered schema. Only
   after recovery acceptance and reconciliation of later writes may the operator
   switch storage/runtime/current. Start API and verify `/api/health/ready` before
   the previously present workers. Never start old and new consumers together.

Local synthetic restoration is reproducible with
`source scripts/test-env.sh; cd backend; uv run pytest tests/test_deployment_upgrade.py`.
It uses only a unique synthetic schema inside existing `infra-postgres-1` on
127.0.0.1:55432 and temporary synthetic document files, not another database or
production data. This task does not perform a production restore rehearsal.

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
printed. A smoke failure applies the compatibility/recovery policy above and prevents
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

The helper acquires the exclusive host deployment lock, verifies the provider
setting, builds/scans the exact image and writes its protected SBOM. It then
executes the existing-installation preflight and paired backup above. All ops
and runtime starts use `--no-deps`; there is no DB start/recreation. After the
exact target revision check, the order is `bootstrap → app → API readiness →
provider connectivity probe → worker → invoice-worker`. Both workers must pass
health, immutable image and release identity checks before acceptance.

It verifies closed host ports and isolated API/review/worker database roles,
then the existing public TLS/security-header/session/login/traversal/upload-limit
smoke. `current` changes atomically only after every gate succeeds; the previous
pointer is persisted first. On failure the recovery policy above applies, with
no automatic downgrade or volume deletion. The disposable CI verifier checks
0141 and the invoice worker too; its execution remains separately authorized.

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

There is no automatic rollback after promotion. Before promotion, automatic
runtime restoration requires the unchanged schema, matching previous image head,
and successful cleanup of all owned replacement consumers. A cross-schema attempt
requires maintenance/recovery as described above; never blindly start the 0122
image on 0141. The same compatibility requirement applies to a manual rollback.

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
