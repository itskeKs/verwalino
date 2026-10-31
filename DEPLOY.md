# Deployment

## Deployment mode

`HV_DEPLOYMENT_MODE` accepts exactly `selfhost` (default) or `hosted`. Invalid values
stop the process at configuration initialization with the allowed values in the error.
Set it in the backend process environment; a custom container deployment must pass it
explicitly to the container. Changing it requires restarting the backend.

Both modes use the same image, authentication and organization isolation. `selfhost`
offers token-gated first-run setup on an empty database. `hosted` suppresses setup:
`GET /api/setup` reports `required: false`, every non-GET request returns the closed
setup 404, and `/setup` redirects to login. Hosted mode offers public signup with
email confirmation; quotas and legal pages remain deferred. Password recovery is available
in both modes when mail is configured. Login uses the
selected rate-limit profile described below. The
[policy table](docs/architecture.md#deployment-modes) records only decisions with
implemented readers. Opt-in error tracking is configured separately below.

The portable installer below is selfhost-only and unchanged, including `start`'s
setup-token flow. Its manifest relies on the `selfhost` default; it does not forward
`HV_DEPLOYMENT_MODE` from the caller or accept it in `.env.selfhost`. Its HTTP/HTTPS
`VERWALINO_MODE` is a separate transport choice. Use a separately managed deployment
to run hosted mode; do not use the portable installer to start a hosted service.

For the retained `docker-compose.prod.yml`, `.env.example` is the environment
template; keep the populated `.env` private with mode 0600. Set the required
`HV_PUBLIC_HOST` to the single public hostname (for example
`verwaltung.example.org`). Compose passes it as `HV_ALLOWED_HOSTS` and uses it
in the Traefik router rules. The API healthcheck requests
`http://127.0.0.1:8000/api/health/live` with that value as its `Host` header.

## Password recovery configuration

Password recovery works in both deployment modes. Configure the existing SMTP
settings and `HV_PUBLIC_HOST` as the trusted public routing hostname, using the
same validation rules as hosted signup. Links always use HTTPS and never use
the request Host.
No new configuration key is required. The public `/api/meta` boolean
`mail_available` reports only whether this configuration is usable, with no secrets
or SMTP network probe. Login hides “Passwort vergessen?” when it is false or metadata
is unavailable. Selfhost setup and login continue to work without mail.

`POST /api/auth/password-reset` accepts `{email, website}` and returns the same
202 conditional receipt for known, unknown, throttled and mail-unavailable requests.
An unavailable configuration logs one WARNING per application process; subsequent
requests remain generic. Known active, unambiguous accounts get a German link to
`/passwort-zuruecksetzen?token=…`, valid once for one hour. Unknown addresses get no
mail. Each new link consumes earlier pending links. A separate recipient limiter
allows at most three issued mails per normalized address per hour, across IPs;
unknown or unavailable recipients do not count. Both paths perform the same
Argon2 dummy hash and mail construction; SMTP
submission runs after the response. Transport failure logs a generic warning and
keeps the same receipt; SMTP acceptance does not prove delivery.

The page saves the token in tab-local sessionStorage, removes it from the URL and
POSTs only on explicit submit. Reset returns 204 without a cookie or automatic
login. The user signs in with the new password; all old sessions and outstanding
reset links are invalidated. A German confirmation email follows the committed
reset. SMTP failure cannot undo that reset. Self-service/admin password changes
and deactivation also consume pending links. Missing, expired, used and throttled
tokens return the same German 403 refusal. Body/policy errors return a German 422
before token lookup and do not count as confirmation failures; the page retains
the token for correction. Password bounds are 12–128 Unicode code points. The server
strips reset-page queries from its access log; upstream proxies must redact them
too. Request throttling uses `password_reset` (all IP requests); confirmation uses
`password_reset_confirm` (all well-formed IP requests, failures keyed by SHA-256
of the token); `password_reset_recipient` counts issued mails only. The latter
keeps partial counts until a full hour without an issued mail and locks for one
hour after the third, without extending that lock on refusals.
Tests use captured SMTP only.

Runtime roles cannot read or write `password_reset_request` directly. Issuance
and confirmation use fixed-path SECURITY DEFINER functions that refuse any
non-empty organization context. Tokens are generated in PostgreSQL: 32 random
bytes (256 bits) when pgcrypto is installed at migration time, otherwise two concatenated random
UUIDs (244 random bits). Only the SHA-256 digest is stored. Every 100th accepted
request per process schedules a separate background transaction deleting at
most 100 rows whose expiry or use time is older than seven days.

Revision 0144 downgrade refuses while **any** reset rows remain, including used
rows. For an intentional rollback, stop reset traffic/workers, connect using the
operator's migrator credential store, and explicitly invalidate/delete the reset
state before running the normal downgrade:

```sql
BEGIN;
LOCK TABLE public.password_reset_request IN ACCESS EXCLUSIVE MODE;
DELETE FROM public.password_reset_request;
COMMIT;
```

This invalidates every outstanding recovery link; users must request fresh links
after an upgrade. Normal retention cleanup does not clear recent rows for rollback.

## Hosted signup configuration

For a separately managed hosted deployment, pass `HV_DEPLOYMENT_MODE=hosted`,
`HV_PUBLIC_HOST=your-public-host.example` and the SMTP settings described
under [outbound mail](#optional-document-smtp-t47-no-deployment-authorization).
`HV_PUBLIC_HOST` is the public routing hostname, without scheme, port, credentials,
path, query or fragment; signup links always use HTTPS. It is never inferred from
request Host or forwarded headers. Keep
`HV_ALLOWED_HOSTS` aligned with the public host. The ASGI startup check refuses
hosted mode if this hostname or valid SMTP configuration is missing. Selfhost needs
neither setting for setup/login and returns unknown-route 404 for every signup
API method before body parsing or database access.

Confirmation links use `/signup/confirm?token=…`. The page asks for the chosen password and
posts `{token, password}` on explicit confirmation; there is no GET API side effect. The browser removes it from the URL before the
POST and uses a no-referrer policy. A tab-local `sessionStorage` entry keeps the
token across reloads until success or a 403; retryable errors retain it. Passwords
are never stored there. The application strips the server query scope
for this page before the default Uvicorn access logger sees it; separately managed
reverse proxies must also avoid recording confirmation query strings. Tokens
contain 244 random bits from two PostgreSQL UUIDs (pgcrypto is absent); only
SHA-256 hashes persist, and each link expires after 24 hours. Resending rotates the token and renews that lifetime,
with one send at most every ten minutes across signup and resend. Pending requests
cannot sign in. Confirmation creates the tenant and owner atomically and returns
the normal session cookie. Existing addresses receive the identical generic 202
without a new pending request or email. Password hashing/verification work is
equivalent for allowed signup paths and SMTP runs only after the HTTP response.
A live pending request keeps the first password hash and names, even after the
cooldown; only an expired request can be replaced by a new submission. Resend
rotates the token without changing credentials. A mistyped password on a live
link returns a retryable 422 and preserves the token; invalid, used, expired or
throttled confirmation returns the generic 403. Success refunds its limiter
reservation, so only failed confirmations remain counted. Runtime roles cannot
read or write `signup_request` directly; guarded SECURITY DEFINER functions issue,
read, rotate, provision and clean up requests and refuse tenant-context calls.
SMTP failure keeps the pending request,
returns the same response and permits retry after the cooldown; SMTP acceptance
is not proof of recipient delivery. No test submits to real SMTP: the test compose
has no Mailpit service, so the hosted scenario keeps the existing **FakeSMTP** capture double.
Organization names are display names; multiple hosted tenants may share a name,
and signup always creates a separate tenant. The admin CLI retains its existing
name-reuse behavior. Successful confirmation deletes the pending signup data;
the address can register again if its organization and user have been removed.
Every 100th signup per worker opportunistically deletes at most 100 requests
expired for more than seven days, in a separate best-effort transaction.

## Login lockout and proxy trust

Login failure limits come from the deployment policy, with a fixed 15-minute
window: attempts reserve both counters before password verification. Selfhost
permits 20 failures per IP and 10 per normalized email before a 15-minute
lockout; hosted permits 10 per IP and 5 per email before a 30-minute lockout.
Repeated hosted lockouts double to at most 24 hours. Correct credentials are
refused while either key is locked or all attempts in its window are reserved.
Active lockouts are not extended by more attempts. Success clears only the email
key and its escalation history, preserving IP failures. An attacker who knows an
email can deliberately lock the victim out with only 5 failures in strict mode,
with escalating hosted waits. A locked IP also counts each refused attempt
against the email key. The victim cannot clear an active lockout by logging in:
they must wait or ask an operator to unlock it as described below.

Escalation resets when the last lockout started strictly more than 24 hours ago
(hosted) or more than one 15-minute window ago (selfhost), even with continuous
traffic. Exactly at that boundary it is retained. State survives restarts. Every
100th login per worker performs one best-effort cleanup batch of at most 1000 rows
idle for over one day, in a separate transaction after the login outcome commits.
Lock/statement timeouts bound cleanup; its failure cannot undo a login outcome.
There is no new scheduled worker or configurable threshold.

All credential failures retain `401 {"detail":"Invalid credentials"}`, with no
`Retry-After` or cookie, and run one Argon2 verification, including the locked path.
A WARNING includes the HMAC key hash and a server-generated request id, never raw
email/IP. HMAC-SHA256 uses a pepper derived from the existing `HV_SESSION_SECRET`;
rotating that secret clears effective throttling state (old rows age out), as
well as invalidating existing session cookies. No new secret setting is needed.
The German login message is unchanged.

Native IPv6 addresses share a /64 throttle key; IPv4-mapped IPv6 maps to IPv4.
Unparsable client addresses (for example a trusted proxy's `unknown`) share the
constant `invalid` key and still receive the normal authentication response.

The limiter uses `request.client.host`, never an unverified forwarding header.
The existing container entrypoint uses Uvicorn's `--proxy-headers` and
`--forwarded-allow-ips="$FORWARDED_ALLOW_IPS"`. Without proxy middleware/trust the
key is the direct peer. Set `FORWARDED_ALLOW_IPS` to only the addresses/subnets
of proxies that actually front the app, never `*` on a publicly reachable app.
Uvicorn walks the forwarding chain from the trusted end and supplies the resolved
client once; the limiter must not parse it again. The portable stack already
passes `VERWALINO_PROXY_SUBNET` for its dedicated Caddy network, including local
HTTP mode. The stock Caddy proxy replaces incoming forwarding headers. The
loopback diagnostic listener remains untrusted. For direct uvicorn development,
use `--no-proxy-headers` when there is no trusted proxy. No additional
`HV_TRUSTED_PROXY_COUNT` is introduced alongside the existing address allowlist.

To unlock an email, run the existing admin CLI in the backend/container environment
as an authorized operator, with `HV_DATABASE_URL_SYNC` pointing to the **migrator**
and the same `HV_SESSION_SECRET` as the app. This command does not bootstrap an
organization and requires no admin password:

```sh
python -m app.admin_cli unlock --email user@example.org
python -m app.admin_cli unlock --ip 203.0.113.5
```

The mutually exclusive options delete only the selected login key using the
app's normalization and HMAC, and report the actual number of deleted rows.
Use any address in the affected /64 to unlock a native IPv6 key. An independently
locked IP must also be cleared or allowed to expire. No runtime unlock endpoint
is exposed.

These deletes reset only the chosen key and its escalation history. Hashes are
scope-specific; submit, resend and confirmation use `signup`, `signup_resend` and
`signup_confirm` respectively, with the
shared limiter/honeypot helpers described in
[auth hardening](docs/architecture.md#43-loginsession-approach-recommendation-decision).
Submit/resend count only their scoped IP buckets for flood protection; only failed
confirmations count toward the confirm IP/email buckets. Five resends cannot lock
an address out of confirmation. Throttled signup/resend retain the generic 202;
confirmation returns the generic 403. The hidden `website` field silently accepts
bots without effects on both signup and resend; confirmation has no honeypot.
`downgrade()` of 0142 refuses while rows exist; operators must
delete the `auth_throttle` rows first.

## Install in one command

For a **new** Linux server or macOS/Linux laptop with Bash, OpenSSL, Docker
Engine/Desktop, Compose **v2 >= 2.24**, `curl` or `wget`, and `sha256sum` or
`shasum`. Docker must already be running and accessible to your user. The
script does not use sudo or install system packages. Choose a Docker context
without an existing Verwalino installation; only one installation per context
is supported. Do not run this on an existing production host.

**Release preparation:** these commands work once the maintainer publishes the
first stable tag with installer assets and enables anonymous repository/release
and GHCR access. No published version or fresh-VM success is claimed here.

On a laptop, open Docker Desktop first, then run:

```bash
curl -fsSL https://github.com/itskeKs/verwalino/releases/latest/download/install.sh | bash -s --
```

The default directory is `./verwalino`; the app listens only on
`http://127.0.0.1:18080`. For a different directory/port, append
`--dir "$HOME/apps/verwalino" --port 19080`. PostgreSQL has no published port.

On a new server, point the domain's A/AAAA records at it (remove an unusable
AAAA record), make TCP 80/443 reachable and ensure those ports are free:

```bash
curl -fsSL https://github.com/itskeKs/verwalino/releases/latest/download/install.sh | bash -s -- --https verwaltung.example.org
```

Caddy provisions HTTPS. Open the printed HTTPS `/setup` URL once DNS and
certificate issuance are ready. Enter the separately printed one-time token,
organization, administrator details and password twice to reach the dashboard.
Keep the terminal output private: the token expires after 24 hours and is never
saved by the installer. [Browser setup and operational limits](#portable-docker-first-installation)
also apply to this path.

The script checks prerequisites, resolves the latest stable tag through the
GitHub API (without jq), and downloads that tag's assets. To pin a release,
append `--version vX.Y.Z` with a real published version; prereleases and other
tag formats are refused. It verifies **every payload file** against
`SHA256SUMS`, including `install.sh` and `IMAGE_DIGEST`, before running the
downloaded helper. If cosign is installed, it first verifies the checksum
signature against the exact release workflow identity and GitHub OIDC issuer;
failure stops installation. Without cosign it prints a manual verification
command and continues with checksum verification. Checksums detect corruption;
the signature authenticates the checksum file. The piped bootstrap itself runs
before it can check its downloaded copy; use the manual path below to verify
the script before executing it.

The verified `IMAGE_DIGEST` pins the multi-platform GHCR image without a
registry lookup. The script runs `scripts/self-host.sh init --image
ghcr.io/itskeks/verwalino@sha256:…`, forwarding `--https`/`--port`, then `start`.
If that release's helper advertises `doctor` in its help, the installer runs it
after `init` creates the config and before `start`, stopping if it fails.
All installation files and any temporary signature cache stay under `--dir`;
the script does not send telemetry. Docker pulls images and creates the existing portable stack's
volumes/networks as described below. It does not clone the source or build the
application on the host.

Init creates `.env.selfhost` with fresh secrets and mode 0600.
A non-empty `--dir` (default `./verwalino`) without `.env.selfhost` is refused
unless `--force` is given. An existing config blocks the installer before
downloading anything. To retry a failed start, rerun with **the same `--version`
and `--dir`**, plus `--force`; omit
`--https` and `--port`. The existing config is kept byte for byte, and a
different image digest is refused. This is a retry, not an upgrade mechanism.
Use the installed helper for normal operations:

```bash
cd verwalino
bash scripts/self-host.sh status
bash scripts/self-host.sh start  # Also replaces a lost, unused setup token.
bash scripts/self-host.sh stop   # Keeps all data.
```

### Release assets and manual verification

Each stable GitHub Release carries flat assets; the installer arranges them
into the directory layout the unchanged helper and Compose file require:

| Release asset | Installed path | Verification |
| --- | --- | --- |
| `install.sh` | `install.sh` | SHA256SUMS |
| `self-host.sh` | `scripts/self-host.sh` | SHA256SUMS |
| `docker-compose.yml` | `infra/docker-compose.yml` | SHA256SUMS |
| `Caddyfile` | `infra/Caddyfile` | SHA256SUMS |
| `prod-init.sh` | `infra/db/prod-init.sh` (database role bootstrap bind mount) | SHA256SUMS |
| `env.example` | `env.example` (reference only; not sourced or copied to config) | SHA256SUMS |
| `IMAGE_DIGEST` | `IMAGE_DIGEST` (one `sha256:…` index digest) | SHA256SUMS |
| `SHA256SUMS` | `SHA256SUMS` (seven flat payload names) | Keyless signature |
| `SHA256SUMS.sigstore.json` | `SHA256SUMS.sigstore.json` | Sigstore certificate, signature and transparency proof for SHA256SUMS |

On a connected machine, download and verify a chosen release before executing
its installer. Install [cosign](https://docs.sigstore.dev/cosign/system_config/installation/)
first; the blob verification follows [Sigstore's verification guide](https://docs.sigstore.dev/cosign/verifying/verify/).
`v1.2.3` below is an example, not a claim of an existing release:

```bash
tag=v1.2.3  # Replace with a published stable release that has these assets.
mkdir -p verwalino-bundle
cd verwalino-bundle
for file in install.sh self-host.sh docker-compose.yml Caddyfile prod-init.sh env.example IMAGE_DIGEST SHA256SUMS SHA256SUMS.sigstore.json; do
  curl -fsSL -o "$file" "https://github.com/itskeKs/verwalino/releases/download/$tag/$file"
done
# On macOS, replace sha256sum -c below with shasum -a 256 -c.
# The chain stops before execution if signature or checksum verification fails.
cosign verify-blob --bundle SHA256SUMS.sigstore.json \
  --certificate-identity "https://github.com/itskeKs/verwalino/.github/workflows/release-image.yaml@refs/tags/$tag" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS &&
sha256sum -c SHA256SUMS &&
bash install.sh --version "$tag" --dir ../verwalino
```

To reuse local assets, add `--offline-bundle "$PWD"` to the final command.
With cosign on PATH, offline verification also requires an independently
trusted **`trusted_root.json`** in that bundle directory. Provision Sigstore's
service keys/certificates on the connected machine through your trusted
Sigstore tooling and transfer them alongside the verified assets. This is
operator trust material, **not a release asset authenticated by its own
checksum**. The installer supplies it with cosign's `--trusted-root` so
verification does not fetch service keys. Missing trust material fails before
cosign runs. The online path lets cosign obtain its trust roots online.

`--offline-bundle DIR` with explicit `--version` uses local assets without API,
asset or registry-discovery requests from the wrapper. The subsequent `start`
still uses Compose's pull and needs registry access, including GHCR for the
app and the PostgreSQL/optional Caddy image sources. The unchanged helper does
not provide fully air-gapped image provisioning. The first
release publication, anonymous downloads/pulls, fresh-VM setup/login/restart,
and real domain/TLS run remain maintainer acceptance steps.

## Install matrix

[Install matrix](.github/workflows/install-matrix.yaml) runs at **04:00 UTC**
(after the scenario nightly), through `workflow_dispatch`, and after a pushed
`v*` tag. Tag runs build that tag's checkout independently; they do not wait for
the release workflow or test published release assets. There is no PR trigger.
Only `contents: read` is granted; no images, tags, releases or issues are published.

The default matrix uses native **Ubuntu 22.04 and 24.04 amd64** runners.
Set the repository variable `INSTALL_MATRIX_ARM64=true` to include native
`ubuntu-24.04-arm`; leaving it unset avoids allocating that runner. Public
repository standard runners are free; private repository usage may be billed
under [GitHub's runner pricing](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Debian 12 is a commented slot: a nested Docker daemon requires privileged mode,
and sharing the Ubuntu host daemon would exercise its networking and bind mounts
rather than a Debian Docker host. This matrix uses no privileged containers.

Each job builds this checkout's image natively. Because `install.sh` pins the
GHCR repository name and `start` pulls it, the disposable runner temporarily
serves **its own TLS registry** as `ghcr.io` through `/etc/hosts` and a private
Docker trust certificate. The build happens before that override (uv's base
image comes from real GHCR). No credentials or real GHCR push are involved.
The local manifest digest goes into `IMAGE_DIGEST`;
`scripts/build-release-bundle.sh` supplies the same seven payloads and checksum
manifest as the release job. Checkout bundles are deliberately unsigned and
scenario children omit cosign from PATH; release signature verification remains
covered by the existing installer contracts. This does not verify release signing,
anonymous downloads, multi-platform publication or public TLS.

`scripts/install-scenarios.sh` runs these four dependent scenarios in order:

| Scenario | Pass criteria |
| --- | --- |
| Fresh install reaches setup | One `install.sh --version v0.0.0 --offline-bundle … --dir … --port …` command succeeds; `/api/health/ready` succeeds; `/api/setup` says `required: true`; `/setup` serves HTML; setup URL and token were printed. |
| Second run is harmless | The same command refuses with the `--force` hint; retry with `--force` and no port option succeeds, every installed file is unchanged, readiness still succeeds. |
| Busy port is refused | Stop the instance and occupy its app port with a dummy listener; new init and existing start both refuse with `Port … is already in use; choose a free --port or stop its listener.`; no installation containers run. |
| Restart keeps data | Start, create an admin through the CLI with stdin password, log in, stop, remove containers without deleting named volumes, start again, then log in with the same credentials and user/organization identity and check the named documents volume. |

The restart scenario proves that administrator and organization data survive
container recreation and that the named documents volume still exists.

An existing `verwalino` container or volume blocks the run before any installation
operation. A failed scenario fails the job and skips dependent scenarios.
Cleanup always terminates the dummy listener, calls the installed helper's
`stop`, and removes the temporary installation directory. Data volumes and
stopped containers survive until the disposable VM is discarded.
Setup tokens, generated admin passwords and sessions are not put in the report.

The Actions run contains **`install-<os>-<UTC date>`** artifacts retained for
**90 days**, with `summary.md` also shown in the job summary. `scenarios.json`
matches the nightly BDD reporter's shape: `{run, scenarios}`; run metadata has
`started`, `finished`, `git_sha`, `mode` and `runner`. Every scenario has
`feature: "install"`, `scenario`, `tags`, `steps`, `outcome`, `duration_s`,
`error` and `artifacts`. Steps have `keyword`, `name`, `outcome`, `duration_s`
and `error`; errors are null or `{type, message}`. Failed scenarios attach
relative paths to helper `status` output and bounded, non-colored Compose logs.
An unsuccessful artifact capture is recorded in the corresponding file.

TODO matrix entries: **update N-1 → N** after #9 / PR #76 and **backup/restore
round trip** after #10 / PR #57. They are not executed or counted as passing.
The #15 reporter (`scripts/scenario_issues.py`, PR #89) is absent on this branch.
Once available, the intended reporting step consumes this JSON and its uploaded
artifacts with run URL/OS provenance in a separately authorized issue-writing
context. No issue-filing step is active yet.

To reproduce locally, use a **new disposable Linux VM**, with Bash 5, Python 3,
OpenSSL, curl, netcat-openbsd, Docker and Compose v2 >= 2.24. Never use the
production host or this project's test database. In the VM console, check out
the revision to test and execute the workflow's native image build and
runner-local TLS registry steps (substitute a private temporary directory for
`RUNNER_TEMP`). Those steps export the checkout image's `DIGEST`. Then run:

```bash
DIGEST="$DIGEST" bash scripts/build-release-bundle.sh ./checkout-bundle
printf '{}\n' > ./checkout-bundle/SHA256SUMS.sigstore.json
bash scripts/install-scenarios.sh --version v0.0.0 \
  --bundle ./checkout-bundle --report ./report
```

The command runs the four scenarios together against that VM's Docker daemon.
Inspect `report/summary.md` and failure artifacts, then discard the VM. Do not
reuse retained volumes as a fresh-install test. On the development machine,
run only `python3 -B scripts/test-install-scenarios.py` or `make test-installer`:
they use fake Docker/curl and need no database. The first real matrix run is
still pending; local contracts do not establish real installation success.

## Portable Docker first installation

`infra/docker-compose.yml` provides the portable stack and replaces the obsolete
development-only :5433 database starter; development and
backend tests still use **only** `infra/docker-compose.declaration-test.yml`
and `scripts/test-env.sh`. Never run the portable stack as a test database or
on the existing production Docker host. Agent execution of this stack or an
isolated Caddy validation container requires separate authorization and must
satisfy the narrow exceptions in `AGENTS.md` and the installed guard. Current
checks use simulated Docker and static contracts; no real-container or complete
installation test is claimed.

Prerequisites: Linux or macOS with Bash (including macOS Bash 3.2), OpenSSL, Docker Engine >= 24
(or Docker Desktop with that engine) and Compose >= 2.24 (including later major releases).
From a reviewed checkout:

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
`documents`, `caddy_data` and `caddy_config` volumes, no externally owned volumes
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
Init and start refuse an occupied app port with `Port … is already in use;
choose a free --port or stop its listener.` before creating configuration or
starting services. Start permits the app's own listener on a running instance.
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
Caddy configuration relies on the persistent application login limiter described
above; it does not replicate Traefik's separate alpha middleware.

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
Signup remains deferred to #19; application login lockout is enabled in both modes.

CLI bootstrap remains the alternative:

```bash
bash scripts/self-host.sh admin --org "My property management" --email admin@example.org
```

It uses the migrator role and invalidates any pending setup token in the same
transaction. Admin bootstrap prompts for the password twice without echo and
transmits it only via stdin. There is no default password.
Existing users are left unchanged by the existing admin CLI; it is not a password-reset tool.
For automation feed a newline-terminated password from a secret manager to
`admin --org ... --email ... --password-stdin`; never place it in arguments,
checked-in files, or an `echo` command in shell history.

Use `status` for container status and `stop` to stop without deleting any data:

```bash
bash scripts/self-host.sh status
bash scripts/self-host.sh stop
```

There is deliberately no destroy or automatic update command. An older image
is not a schema rollback. Version upgrades require a reviewed migration and
recovery plan.

### Portable troubleshooting: start with doctor

Run `bash scripts/self-host.sh doctor` first when installation or startup fails.
It attempts every independent check, prints one German finding and a concrete
next step per check, and ends with counts of `OK`, `WARN` and `FEHLER`.
Exit **1** means at least one blocker (`FEHLER`); exit **0** means no blocker
was detected, including runs with warnings. An unavailable dependent check is
reported explicitly; a warning is not proof that the installation works.

| Check | `FEHLER` (blocker) | `WARN` / next step |
| --- | --- | --- |
| Docker and Compose | Docker missing, daemon unreachable, engine below 24 or version unknown, Compose below 2.24 or version unknown | Install/update Docker and the Compose plugin; versions 2.24 and later, including 5.x, are supported. Check daemon access and Docker context |
| Private configuration | Missing, symlink, unsafe filename, mode other than 0600/0400, incomplete or invalid keys/settings/secrets | Restore the original `.env.selfhost`; `chmod 600` it. Existing database credentials must not be regenerated |
| Port occupancy | Foreign listener on the configured HTTP port, or on 80/443 in HTTPS mode | Install `ss`, `lsof` or `nc` when inspection is unavailable; identify conflicting services before changing their listeners |
| Free disk | Less than **2 GiB** available | Less than **10 GiB**: free space before backups/updates. Both thresholds use binary GiB (1,048,576 KiB); exactly 2 GiB warns, exactly 10 GiB passes |
| Containers (`db`, `app`, HTTPS `caddy`) | Restarting, unhealthy, dead or paused | Missing, exited, starting, missing healthcheck or status unavailable: use the printed `start` and scoped `logs` commands |
| Migration state | Database revision newer than the configured image's release target | Behind: back up, then `start` migrates. Empty/missing version table: not initialized yet, run `start`. Unknown/ambiguous/query failure or unavailable image target: inspect config/image/logs |
| First organization | — | Count zero: `bash scripts/self-host.sh admin --org NAME --email EMAIL`. An unreadable database/count also warns |
| HTTPS domain and DNS | Invalid domain or no A/AAAA records | Missing DNS tools, unknown public host address or any address mismatch: compare A/AAAA and the public destination manually; proxies, NAT/elastic IPs and `/etc/hosts` can explain a mismatch. Check for stale AAAA records |
| HTTPS local reachability | — | Cannot connect to 127.0.0.1:80/443 or `nc` unavailable: start Caddy, check port publishing and firewall |
| HTTPS certificate | — | Caddy stopped, domain certificate absent/unreadable in `/data/caddy/certificates`, OpenSSL unavailable, TLS probe fails, invalid certificate or expiry within **14 days**: inspect Caddy logs, DNS and renewal access |

Disk is measured with `df -Pk` at both the checkout and Docker's reported data
root (`docker info -f '{{.DockerRootDir}}'`). An inaccessible Docker data root
(common with Docker Desktop or a remote context) warns: inspect the Docker host
or VM disk separately. Allow additional room for complete backup/restore copies.
Port checks try `ss`, `lsof`, then `nc`. An existing listener is accepted as owned
only when the running service belongs to this checkout's Compose file and
publishes the expected port. HTTPS checks inspect ports 80/443; HTTP checks
inspect the configured `VERWALINO_PORT`.

DNS uses `getent` (A/AAAA), `dig`, or `host`, in that availability order. Public
host addresses come from `ip` or `ifconfig`; private/NAT addresses cannot prove
the public destination. The TLS probe uses SNI for `VERWALINO_DOMAIN` against
127.0.0.1:443 and bounds the handshake to eight seconds. These are local checks;
verify Internet reachability, firewall/NAT forwarding and browser trust from
another host as well.

Doctor does not repair settings, migrate, bootstrap users, build/pull images or
start/stop the stack. Database queries and Caddy certificate listing require
running services. Comparing migration revisions runs
`from app.deployment import TARGET; print(TARGET)` in a temporary container from
the configured local image, with Python as its entrypoint. The probe uses a
non-root user and a read-only filesystem, mounts no volumes, adds no capabilities
and has no network access. Missing or unreadable release-target metadata warns.
Docker diagnostics use the current
Docker context, which must refer to the same host as the checkout/port checks
for those checks to describe one machine. After following a finding's next
step, rerun doctor; use `status` and the printed `logs` commands for detail.

### Portable backup and restore

```bash
bash scripts/self-host.sh backup --out /private/backups
# Without --out, the private output directory is <checkout>/backups/.
bash scripts/self-host.sh stop
bash scripts/self-host.sh restore /private/backups/verwalino-backup-20261030T120000Z.tar
bash scripts/self-host.sh start
```

`backup` works with the app and database running. It writes one private (0600)
`verwalino-backup-YYYYMMDDTHHMMSSZ.tar`, prints its absolute path and size in
bytes, and never overwrites a same-named archive. Its three members are:

- `db.dump`: a consistent PostgreSQL custom-format snapshot (`pg_dump -Fc`),
  executed inside this installation's `db` container as `app_migrator`.
- `documents.tar.gz`: the documents volume copied **after** the database dump.
  A one-shot app-image helper overrides the web entrypoint, starts no dependent
  services, and uses the existing project-owned documents volume. Concurrent
  uploads can add later files; deletions between the dump and the documents copy
  can leave restored database rows pointing at missing files. Run backup when no
  one uploads or deletes documents. For a cold backup, stop the app first, then
  start **only db** for the dump, leaving app/migration/bootstrap stopped. This
  is not an atomic database/filesystem snapshot.
- `manifest.json`: `format` (`verwalino-selfhost`), `format_version` (1),
  `schema_head` (queried from `alembic_version` during backup), `app_image`
  (configured image reference, including a digest when selected), `created_at`
  (UTC), and `config_fingerprint`. The latter is SHA-256 over the five ordered
  `KEY=value\n` settings `VERWALINO_MODE`, `VERWALINO_DOMAIN`, `VERWALINO_PORT`,
  `VERWALINO_IMAGE`, `VERWALINO_PROXY_SUBNET`.

The helper runs as root with only `CHOWN` and `DAC_OVERRIDE` added to the app's
otherwise dropped capabilities, so it can access the private staging bind and
restore app-owned files (UID/GID 10001). It uses the app image's Python; host
Python is not required. An image without the document helpers fails with an
update instruction. If rootless Docker or user-namespace remapping prevents host
ownership changes, the helper warns; publication copies the archive privately
if hard-linking is refused. The archive contains application data, including account
and session data; protect it accordingly. Installation passwords, session signing
secret and the private configuration file are **not** included. Neither are
PostgreSQL roles/passwords, container images, Caddy state/certificates or external
services. Keep the original `.env.selfhost` separately in a private encrypted
backup; init must not regenerate credentials for an existing installation.
Retain the reviewed checkout and the selected image, especially a mutable local
build, so recovery has the required application and PostgreSQL tools.
The default `<checkout>/backups/` directory is created with mode 0700. Git ignores
`backups/`, `verwalino-backup-*.tar` and `.verwalino-backup.*`; output directly in
the checkout root is refused. New custom output directories use mode 0700;
existing directories keep their permissions.

`restore FILE [--yes]` checks the private config and acquires the installer lock,
then refuses running, restarting or paused app/migration/bootstrap containers
with the `stop` command.
It copies the input to private staging, requires all three regular archive members,
checks the manifest marker and format version, and refuses a schema newer than
the selected image's `app.deployment.TARGET`, read inside the helper container.
Unknown/ambiguous heads,
unsafe document paths, links, special files and invalid document archives are
refused before the database starts. An older numeric schema head is allowed;
`start` performs subsequent migrations. This portable restore check does not
change the narrower `SUPPORTED` set used by the read-only deployment checks
and upgrade rehearsals.

A different configuration fingerprint emits a warning. Restore then asks you to
type `restore`; `--yes` explicitly skips that prompt for automation. It starts
**only db**, checks the dump's table of contents with `pg_restore --list`, creates
`hausverwaltung_restore` with `createdb -T template0`, and imports via
`pg_restore -U app_migrator -d hausverwaltung_restore --exit-on-error
--single-transaction`. It queries that database's own `alembic_version` and
requires exactly the manifest head. It then extracts, verifies and sets ownership
of staged documents on the documents volume, before changing the old database
or document contents. Allow free space for both databases and both document copies.
An existing `hausverwaltung_restore` or `hausverwaltung_previous` is refused;
inspect it and recover the intended live database before removing any leftover.
Restore also refuses any `.restore-*` directory on the documents volume before
creating the scratch database. Verify the live data, then remove the named
directory, then retry; it may contain the only retained copy of previous documents.
Before cutover, failures remove the scratch database and staged documents and
leave old data untouched; all failures attempt to stop db and leave the app stopped.

After staging succeeds, cutover renames `hausverwaltung` to
`hausverwaltung_previous` (skipped if no live database exists) and
`hausverwaltung_restore` to `hausverwaltung`, moves
old document entries into `.restore-<id>/previous/` and staged entries into place
on the same volume, then drops the previous database and deletes the previous
documents only after the swap succeeds.
If no live database existed, there is no previous database to drop.
Failures before that drop attempt to rename the old database and move previous
documents back, retain staged documents in `.restore-<id>/documents/`, and report
the remaining database names and document directories with manual recovery SQL
and move instructions if rollback fails; keep the app stopped until recovery is verified.
If interruption happens after the previous database was dropped but before
document cleanup, the restored database and documents remain live. Verify them,
manually remove the retained `.restore-<id>` directory (including `previous/`)
from the documents volume, then run `bash scripts/self-host.sh start`.
Do not run the rollback SQL in this state. Avoid backups until the retained
directory is removed: document archives include it while it remains on the volume.
When no live database existed initially, an incomplete document swap rolls back
to that absent-live state and retains the restored database as
`hausverwaltung_restore`; a completed swap keeps the restored data live and
requires the same directory cleanup and `start` if interrupted during cleanup.
After success, db stops and the printed `start` command starts the restored installation.
Use only trusted archives: PostgreSQL restoration executes SQL from the dump.

Back up at least daily while the installation is in use and before a reviewed
upgrade or destructive maintenance. Copy completed archives to a separate,
access-controlled encrypted disk or another host; the local output directory
alone is not disaster recovery. Off-site transfer, scheduling, retention and
automatic updates are operator responsibilities. Periodically test recovery on
a separate authorized installation with the original private config. Contract
tests and a real PostgreSQL dump/archive/scratch-database restore round trip cover
format and command behavior; a complete running self-host installation restore
has not yet been accepted.
Run the opt-in rehearsal with `make test-selfhost-format`: it sets
`VERWALINO_FORMAT_TEST=1`, acquires `/tmp/verwalino-testdb.lock` itself and uses
only its own scratch databases in `infra-postgres-1` at `127.0.0.1:55432`.
Ordinary test runs skip it with a reason; it also skips when Docker is missing.

Declaration import/Q&A, SMTP and the ordinary invoice worker are disabled in
the default portable stack. The optional [inbox worker](#inbox-worker) processes
intake documents locally; it requires scanner signatures and, for scans, German
OCR. The T10 worker is configured in the local production Compose file. The
first tag publication and native arm64 image installation,
complete clean-host setup/login/restart and real domain/TLS acceptance still
need operator checks; do not treat the mock installer tests as
proof of those behaviors. Run the portable tests without Docker using
`cd backend && uv run python ../scripts/test-self-host.py`; the backend CI job
runs these before pytest. Real Caddyfile validation and the complete portable
stack smoke test require a separately authorized clean Docker host. Update
automation remains outstanding; this is not yet a general production release.

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
and `admin` commands above. `scripts/self-host.sh` refuses remote image tags;
that helper does not resolve tags or verify signatures for you. `vX.Y` is a
discovery alias, not an automatic update mechanism.

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
4. Check the second job published a GitHub Release containing `install.sh`,
   `self-host.sh`, `docker-compose.yml`, `Caddyfile`, `prod-init.sh`, `env.example`,
   `IMAGE_DIGEST`, `SHA256SUMS` and `SHA256SUMS.sigstore.json`. Compare `IMAGE_DIGEST`
   and the release notes with the image job's index digest. Verify the checksum
   signature and all seven payload checksums as above; test anonymous asset
   downloads after the repository/release is made public by the maintainer.
5. On a fresh VM, run the one-line command through browser setup, sign-in and
   stop/start persistence. Repeat with a real domain for DNS/TLS acceptance.
   Record the tag, digest, host and result in the PR; fake-Docker contracts do
   not prove this. Complete the native arm64 installation check above.
6. Copy the successful release tag and index digest into the examples in this
   section.

The **first tag with installer assets, fresh-VM run and arm64 installation
check remain acceptance steps for the maintainer**. This preparation does not
close #7 or #12. Full `make test`
remains a pre-merge gate; the local workflow/installer checks need no database.

## Diagnostics and reporting a problem

In **Benutzer**, an administrator can select **Problem melden**, read the disclosure,
and choose **Diagnosepaket herunterladen**. Only `OWNER_ADMIN` can download
`POST /api/admin/diagnostics`; `GET` on the same path supplies the configured support
link. The response is an uncached attachment. Collection writes no server-side
archive or report state; an INFO audit line `diagnostics_download` carries its request ID.
Download first, review the archive, then manually attach it to an issue or email with
steps, expected/actual behavior and the displayed request ID. Nothing is uploaded or
sent automatically. The client remembers the last valid server-error ID in memory
until reload or successful login/logout; it never adds it to the support URL.

On a portable installation, use the existing app image without starting the stack:

```sh
bash scripts/self-host.sh diagnose
bash scripts/self-host.sh diagnose --out /private/path/support
```

The default directory is `diagnostics/` beside `.env.selfhost` (or the selected private
config). Newly created directories are mode 0700; existing directories keep their mode
and must be writable. Archives are mode 0600, and the printed path names a
`verwalino-diagnostics-<UTC>.tar.gz`. Default diagnostics directories are excluded from
Git and image build contexts; keep a custom output directory private too. An existing
same-second filename is never overwritten; retry with a new timestamp. Failed collection
publishes no archive. Symlink output directories are refused.

The shell wrapper only handles arguments, private output publication, `compose ps --all
--format json`, the last 2,000 physical lines of `compose logs app`, and the one-shot
`python -m app.diagnostics` invocation inside the app image. It reads the schema head
with `compose exec db psql` as `app_migrator` and passes it via `--schema-head`;
tenant counts still use `app_rw`. It does not start services,
run migrations or invoke an upload service. Bundle collection,
filtering and serialization live in Python. Installer contracts use fake Docker; this
is not a claim of live-stack acceptance.

| Member | Contains | Omits |
| --- | --- | --- |
| `manifest.json` | Format/time, application version, release target, recognized `HV_RELEASE_ID` and SHA-256 image digest when known | Image registry/name, arbitrary release text, host/user names |
| `environment.json` | API: normalized deployment mode, application version and tenant-facing feature switches only. CLI: Python version, OS family, effective `HV_*` keys with type/length shapes and boolean switches; secret-like keys show only `<set>`/`<unset>` | API: global configuration shape. Both: configuration strings, URLs, paths and secrets |
| `containers.json` | Known Compose service identifiers, state and health; `null` for API bundles | Container names/IDs, commands, ports, mounts, labels and raw Docker output |
| `database.json` | CLI: supplied migration head and organization count. Both: numeric tenant-table counts with collection status and scope | API: installation-wide organization count and migration head (both `null`). Both: organization identifiers/names, rows, schema/driver error messages and connection details |
| `errors.log` | Last 500 WARNING/ERROR/CRITICAL records: timestamp, severity, request ID and exception class when available | All free-form messages, traceback source/paths, SQL values, bodies, cookies, names, emails, addresses and credentials |
| `README.txt` | Sharing instructions and collection limits | Installation/user data |

Every source log line passes through `app.core.scrub.scrub_text`; only structural fields
then survive. Raw messages are excluded even when a name or body has no recognizable
pattern. Logs are bounded to 8,192 characters per source line and 2,000 source lines.
UUID request IDs stay opaque; records whose other IDs are recognized as personal data
or credentials by the scrubber are omitted. Keep personal data out of request IDs and
release identifiers, since arbitrary names cannot be distinguished from opaque IDs. Recognized release IDs
are Git hex revisions, UTC `YYYYMMDDTHHMMSSZ` stamps or semantic versions; other labels
are omitted. Portable CLI bundles obtain a digest from the selected pinned image;
API deployments may supply `HV_IMAGE_DIGEST=sha256:<64 hex digits>` when known.
Local builds report an unknown digest rather than guessing.
Client-selected request IDs that survive filtering pass through the bundle verbatim;
do not put personal data in them (see [Request IDs and optional error tracking](#request-ids-and-optional-error-tracking), introduced in #84).
`errors.log` supports correlation with the user's own logs, not replay of a failure.

The API feature allowlist covers API documentation, financial computation, invoice
extraction, declaration import and declaration Q&A. Deployment mode normalizes the
existing error-tracking environment label: `selfhost` or `hosted` (including `hosted-*`);
unrecognized labels produce `null` rather than exposing an operator's custom label.

Database collection uses the runtime **app_rw** role in a read-only transaction, with
five-second statement bounds. API counts use explicit organization filters and the authenticated organization/user
RLS context. CLI counts aggregate RLS-visible rows across organizations without a user
identity; stricter source/user policies may hide rows. Counts are diagnostic observations,
not a data inventory. The organization count is collected only for the CLI.
Unavailable metadata stays null/marked unavailable. The API keeps migration head
`null`: the runtime role lacks SELECT on `alembic_version`, and API collection never
probes it or changes grants. The installer supplies the head using its existing
migrator access; the builder validates its format before inclusion.

The API buffer is shared by workers on one host through `HV_DIAGNOSTICS_DIR`
(default `/tmp/verwalino-diagnostics` inside the container), a private mode-0700
directory. This is an API process setting, not an installer config key; custom
deployments must give all web workers in ONE container the same directory; separate
containers must not share it because PID-keyed files would collide or evict each other. Each worker appends
structural records to its mode-0600 `<pid>.jsonl`, compacted to the latest 500.
Separate `<pid>.<organization-id>.jsonl` reserves keep the latest 50 per organization;
each reserve file is bounded to 50 records. Readers combine live workers' files,
retain the latest 500 overall plus the latest 50 per organization, and remove dead
workers' files. Only the authenticated organization's records enter its bundle;
organization tags remain internal, and records without a validated organization
are excluded. Disk failures can make records unavailable; no raw log fallback is used.
This transient buffer is per host; hosted multi-host aggregation is out of scope.
Container replacement clears the default temporary directory. Use the CLI for the
bounded Docker history and container states.

`HV_SUPPORT_URL` defaults to `https://github.com/itskeKs/verwalino/issues`. It accepts
an HTTP(S) URL without credentials. Set it in the API process environment; portable
Compose forwards an exported value (it is not a `.env.selfhost` key). The dialog opens
the link only when selected, without attaching a bundle or adding request data.

**Reproduction contract:** `test_seeded_request_failure_keeps_request_id_and_error_class`
forces a test-only route to raise `RuntimeError`, captures the safe 500 response ID,
builds the archive and finds that ID plus the error class in `errors.log`, with the
private message absent. `test_api_bundle_correlates_seeded_failure_without_other_organization_logs`
exercises the same path through the admin download and excludes another organization's
failure. These deterministic tests establish correlation for a seeded failure. A report
still needs the user's reproduction steps; the archive deliberately contains no data
or request body to replay. Tests also iterate every tar member for forbidden fixture
strings and exercise the installer with fake Docker.

## Hosted server

The hosted service runs on the Contabo server with Debian 13; the bootstrap also accepts Ubuntu, untested.
It uses the [portable stack](#portable-docker-first-installation)
in HTTPS mode, updated by a GitHub workflow that logs in over a second SSH key of the admin user, restricted to
the deploy script. Off-site encrypted backups and the restore drill belong to #42 and are not covered here.
Placeholders: `SERVER_IP`, `verwaltung.example.org`; the admin user is called `chris` below.

### Bootstrap

From a checkout, as root once (safe to repeat; it resets the firewall to exactly the rules below):

```bash
scp scripts/server-bootstrap.sh ~/.ssh/id_ed25519.pub root@SERVER_IP:
ssh root@SERVER_IP 'bash server-bootstrap.sh chris id_ed25519.pub verwalino'
```

`server-bootstrap.sh ADMIN_USER PUBKEY_FILE [HOSTNAME]` installs the packages (`ufw`, `fail2ban`,
`unattended-upgrades`, `cron`, `jq`, git), creates the admin user with passwordless sudo and the key(s) from
the file, writes `/etc/ssh/sshd_config.d/10-hardening.conf` (no root, no passwords, `AllowUsers ADMIN_USER`),
opens 22 (rate-limited), 80 and 443, installs Docker and Compose from Docker's repository, limits journald
(500 MB, one month) and the Docker default log driver (`local`, 3 × 10 MB), adds 4 GiB swap when the host
has none, and sets the time zone (`TIMEZONE`, default `Europe/Berlin`) and, if given, the host name. Afterwards
only `ADMIN_USER` can log in.

### Folders and ownership

Everything belongs to the admin user, who is also a member of `docker`; nothing needs a second account.

| Path | Mode | Holds |
|---|---|---|
| `/srv/verwalino/app` | 755 | checkout of this repository; the deploy script moves it to the release tag |
| `/srv/verwalino/config` | **700** | `.env.selfhost` (**0600**, the installer insists), `deploy_key` (read-only GitHub deploy key for `git fetch`) and the installer's lock directory |
| `/srv/verwalino/backups` | 700 | `<timestamp>[-label]/` backups (directories 0700, files 0600) |
| `/srv/verwalino/logs` | 750 | `deploy-<timestamp>.log` (0640), `backup.log` |
| `/srv/verwalino/data` | 755 | reserved and empty: the stack keeps its data in the Docker volumes `verwalino_pgdata` and `verwalino_documents`, under Docker's data root |

```bash
sudo install -d -o chris -g chris /srv/verwalino /srv/verwalino/{app,data}
sudo install -d -o chris -g chris -m 750 /srv/verwalino/logs
sudo install -d -o chris -g chris -m 700 /srv/verwalino/{config,backups}
ssh-keygen -t ed25519 -N '' -C verwalino-server-readonly -f /srv/verwalino/config/deploy_key   # add the .pub as a read-only GitHub deploy key
git clone -c core.sshCommand='ssh -i /srv/verwalino/config/deploy_key -o IdentitiesOnly=yes' \
  git@github.com:itskeKs/verwalino.git /srv/verwalino/app
```

The repository is public, so an HTTPS remote works without the deploy key too. Initialise and start the stack once
by hand with a [verified image digest](#published-image-verification):

```bash
cd /srv/verwalino/app; export VERWALINO_CONFIG=/srv/verwalino/config/.env.selfhost
bash scripts/self-host.sh init --https verwaltung.example.org --image 'ghcr.io/itskeks/verwalino@sha256:<64-hex-digest>'
bash scripts/self-host.sh start
```

Create the first organization as in the [first installation](#portable-docker-first-installation). The deploy
script masks the setup token, so always run the first `start` in a terminal. To pull the image, make the GHCR
package public (package settings → visibility) or run `docker login ghcr.io` once as `chris` with a `read:packages`
token.

### Deploy key

Create a key pair that is used for nothing else and put the private half into the secret `DEPLOY_SSH_KEY`. On the
server append the public half to the admin user's `authorized_keys`, restricted to the deploy script:

```bash
ssh-keygen -t ed25519 -N '' -C verwalino-ci -f ci_key && scp ci_key.pub chris@SERVER_IP:   # local
# on the server, as chris:
cat >> ~/.ssh/authorized_keys <<EOF2
command="/srv/verwalino/app/scripts/deploy-server.sh \$SSH_ORIGINAL_COMMAND",restrict $(cat ci_key.pub)
EOF2
rm ci_key.pub
```

The workflow sends only the tag as the SSH command (`ssh chris@host v1.2.3`). The user's shell expands
`$SSH_ORIGINAL_COMMAND` into arguments of the script, which accepts exactly one argument shaped like a release
tag (`vX.Y.Z`, no suffix or leading zeroes) and exits 2 on anything else. `restrict` switches off forwarding, PTYs and
agent. The user has sudo and Docker, so this key is as sensitive as the admin key: the forced command and the tag
check are its only limits. Fill `DEPLOY_KNOWN_HOSTS` with the output of `ssh-keyscan -t ed25519 "$DEPLOY_HOST"`,
where `DEPLOY_HOST` is exactly the host name or IP stored in that secret, after comparing the key with
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server. `StrictHostKeyChecking=yes` matches that name;
an IP-only entry does not match a host name in `DEPLOY_HOST`.

### Deploy flow

1. Push `vX.Y.Z` ([release checklist](#maintainer-release-checklist)). `Release image` publishes and signs it.
2. When that run succeeds, [Deploy hosted server](.github/workflows/deploy.yaml) (`workflow_run`) calls
   `ssh chris@host vX.Y.Z`. **Actions → Deploy hosted server → Run workflow** with a `tag` redeploys any
   published tag; on the server, `/srv/verwalino/app/scripts/deploy-server.sh vX.Y.Z` does the same.
3. [`scripts/deploy-server.sh`](scripts/deploy-server.sh) fetches tags and checks the tag out (it must exist and be
   on `main`), pulls `ghcr.io/itskeks/verwalino:vX.Y.Z` and resolves its digest, runs
   [`scripts/server-backup.sh`](scripts/server-backup.sh) as `pre-vX.Y.Z`, writes the digest to `VERWALINO_IMAGE`
   in the config, runs `scripts/self-host.sh start` (pull by digest, stop the app, migrate, start, wait for
   healthy) and polls `/api/health/ready` for up to 120 s. The run is logged to
   `/srv/verwalino/logs/deploy-<timestamp>.log`.
4. On failure it prints the last 100 app log lines and the previous tag, commit and image, and exits non-zero.
   There is **no automatic rollback**: the schema may already be migrated, so starting an older image is not safe
   (#9, #76). Before the digest is pinned, a failure restores the previous checkout. Fix forward with a new tag,
   or restore the pre-deploy backup.

Secrets of the workflow (repository secrets, names only): `DEPLOY_HOST`, `DEPLOY_USER` (the admin user),
`DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`. The token is read-only (`contents: read`) and the job uses no other secret.

### Backups

[`scripts/server-backup.sh`](scripts/server-backup.sh) writes `/srv/verwalino/backups/<UTC timestamp>[-label]/`:
`db.dump` (`pg_dump` custom format), `documents.tar`, `config.env` (a copy of the config, secrets in clear:
keep the directory private and encrypt before it leaves the server), `MANIFEST` and `SHA256SUMS`. It keeps the
newest unlabelled backup of each of the last 14 days and 8 weeks and the 10 newest backups with any label.
`scripts/server-backup.sh manual` competes with `pre-<tag>` backups for those 10 places. Schedule it daily:

```bash
echo '0 3 * * * chris /srv/verwalino/app/scripts/server-backup.sh >> /srv/verwalino/logs/backup.log 2>&1' |
  sudo tee /etc/cron.d/verwalino-backup
```

These copies live on the same machine as the data, so they do not cover a lost server or disk. The server scripts still use
this separate backup format; integration with the installer's portable `backup`/`restore` and a future
`update` (#76) remains pending (marked `TODO` in both). Restoring this format is manual work for the maintainer: stop the app, load
`db.dump` with `pg_restore` into an empty database and unpack `documents.tar` into the volume, then rehearse it on
a throwaway host (#42).

### Runbook

```bash
cd /srv/verwalino/app; export VERWALINO_CONFIG=/srv/verwalino/config/.env.selfhost
bash scripts/self-host.sh status                                        # containers
docker logs --tail 100 "$(docker ps -q --filter label=com.docker.compose.service=app)"   # app log
less /srv/verwalino/logs/deploy-*.log                                   # last deploys, newest name last
docker restart "$(docker ps -q --filter label=com.docker.compose.service=app)"   # restart only the app
scripts/server-backup.sh manual                                         # extra backup before risky work
docker image prune -af --filter until=720h                              # old release images; running ones stay
```

The scripts and the workflow are covered by `make test-installer` (`scripts/test-server-deploy.py`; Docker, git,
curl and sleep are faked, nothing touches a real host). After a cancelled or broken CI deploy, check for and
remove `/srv/verwalino/logs/.deploy.lock` once no deploy is running. A stale `/srv/verwalino/backups/.lock`
(a run was killed) is removed by hand once no backup is running. Never
run `docker compose down -v` or delete the volumes.

## Migration compatibility: 0029–0144

| Revisions | Effect on existing data and runtime compatibility |
| --- | --- |
| 0029–0122 | Adds financial, declaration, source custody and review data structures and guards. The 0028 runtime cannot interpret the new schema or enforce its later security rules. The 0028→0144 rehearsal preserves all old columns and rows with synthetic organization, user, document, draft/failed import and failed extraction-run data, but does not prove every live-data combination. |
| 0123 | Adds nullable replacement evidence and changes exception-completion functions/outcomes. Existing rows retain their old disposition; old clients cannot interpret new replacement outcomes. Downgrade refuses retained evidence. |
| 0124–0125 | Adds bank transactions and a nullable payment link, then enforces tenant/account coherence and immutable booked links. A 0122 database has no pre-existing links; inconsistent intermediate data is not silently repaired. |
| 0126–0127 | Adds append-only rent history and frozen special levies; replaces financial/source/report adapters. No old financial rows are rewritten. Older runtimes do not understand the new obligations/history. Their downgrades can drop populated new tables; they are not a safe recovery path. |
| 0128–0129 | Extends dunning provenance with default empty levy IDs, default zero interest and nullable snapshots. Existing amounts survive; new levy/interest provenance blocks downgrade. |
| 0130–0135 | Adds retained attachments, tasks, letters/PDF references, SEPA consent, immutable import and dunning batches. No original files are migrated. Most populated downgrades refuse; task downgrade drops its table. No blanket lossless rollback claim. |
| 0136–0137 | Adds default-active bank/vendor lifecycle and nullable property facts plus stricter reference triggers and read-only deletion-reference functions. Non-default lifecycle/property facts prevent downgrade. |
| 0138–0139 | Adds default-zero session generation and versioned auth functions, then grants only session validation to `app_review`. Positive generations block downgrade. An older runtime's unversioned auth does not enforce the new invalidations: **automatic 0122 runtime restoration on the new schema is unsafe**. |
| Parent references (accepted source 0140) | Replaces client-selected parent references with organization-bound composite foreign keys. Validates pending deferred checks and rejects existing invalid references without repair before replacing constraints. Adds a boolean-only, tenant-bound property existence check for scoped financial creation. Downgrade restores the original constraints and removes that check; an older runtime is not approved on this schema. |
| Browser setup (accepted source 0141) | Adds the private singleton setup-token table and a token-gated, serialized first-organization function. Removes direct organization INSERT from app_rw; the migrator CLI shares the creation SQL. A populated token table blocks downgrade to avoid silently losing pending/consumed state. Existing organization/user/membership rows are preserved. |
| 0142 | Adds global authentication throttle rows containing only scoped key hashes, failure windows, lockout expiry, last lockout start and escalation history, with narrow runtime DML. No existing rows or grants change. A populated table blocks downgrade to avoid losing lockouts. |
| Lease documents (accepted source 0143) | Allows retained built-in letter documents without a template row; the exact migration is preserved from #114. Downgrade refuses a null template reference. |
| 0144 | Adds private pending signup requests and token-bound SECURITY DEFINER functions; runtime roles have no direct request-table access. Successful confirmation atomically creates a separate tenant and deletes its request. Retained requests block downgrade. |
| 0144 | Adds global password reset requests with 32-byte token digests, context-free definer issuance/confirmation and bounded cleanup; runtime table access is revoked. The T57 trigger path revokes sessions and consumes links on password changes/deactivation. Tokens expire after one hour; successful reset marks all outstanding links for that user used. A populated downgrade refuses to discard retained reset state; an empty downgrade exactly removes the new table, index, trigger and functions. |

DDL uses the existing PostgreSQL/Alembic transaction; 0123 also takes financial
and ACL locks with NOWAIT table locks. Contention or unexpected data/function
definitions must abort, not trigger repair/deletion. A rollback-only synthetic
0028→0144 rehearsal compares every old table column/row and retained RLS flags;
the 0122→0144, 0139→0144 and accepted-source 0140→0144, 0141→0144 and
0142→0144 and 0143→0144 rehearsals also
compare existing ACLs, allowing only the explicit organization INSERT revocation;
it does not prove live data validity or cross-version runtime compatibility.

### Release-target checklist

When adding a migration, update these together in the same release:

1. Add a new revision; never edit a committed migration or remove its downgrade.
2. Update `TARGET`, the explicitly reviewed source revisions in `SUPPORTED`, and
   the exact linear graph check in `backend/app/deployment.py`.
3. Update the accepted head below and the compatibility table above. Review
   `scripts/self-host.sh` and its contract tests for release-specific assumptions;
   the installer migrates to the selected image's Alembic head.
4. Extend `backend/tests/test_deployment_upgrade.py` and its migration setup in
   `backend/tests/conftest.py` to rehearse each supported source revision. Retain
   document/archive coverage and representative older backup fixtures.
5. Run the installer contracts, upgrade/session-review tests, `make lint` and
   `make docs-check`; require the full `make test` gate before merging a release.

## Deployment boundary and accepted head

The reviewed code targets **0144**. `backend/app/deployment.py` retains `TARGET`,
`SUPPORTED`, `release_head()` and the read-only schema/role/runtime checks used
by the migration rehearsals. Those checks accept measured **0028 → 0144**,
**0122 → 0144**, **0139 → 0144**, **0140 → 0144**, **0141 → 0144**, **0142 → 0144**, **0143 → 0144** (accepted source), or **0144 → 0144**; unknown,
missing, multiple or intermediate revisions are rejected. These are code and
rehearsal boundaries, not a measurement of a running installation.

The portable installer and hosted workflow run the selected image's
`alembic upgrade head`; they do not invoke the narrower `SUPPORTED` preflight.
Before an upgrade, measure the installed schema, review compatibility and retain
an independently verified database/document backup plus the private config and
previous immutable image. No automatic downgrade or runtime rollback is provided.
Retained attachments and financial provenance can deliberately prevent a lossy
downgrade; never delete originals or disable guards to force it.

Production changes require a separately authorized operator window. Editing this
runbook, changing the target or passing local rehearsals does not authorize SSH,
secret access, deployment or a production restore. A later release needs its own
compatibility and recovery plan; an older image is not a schema rollback.

### Recovery across schema versions

Recovery requires a separately authorized window:

1. Stop the API and any workers. Preserve the failed database/documents and the
   pre-upgrade backup, especially uploads or writes made after the new API started.
   Prefer a reviewed forward repair when it preserves those later writes.
2. Verify the complete database/document pair and original private config. For
   manual snapshot recovery, restore the dump with
   `pg_restore --exit-on-error --single-transaction` into an operator-provided
   **empty recovery destination** with the original separated roles. Never use
   `--clean`, downgrade, reset or volume removal against the live installation.
3. Restore documents using the matching backup format described above. For a
   `documents.tar.gz` archive, the reviewed image's
   `python -m app.deployment restore-documents <empty-staging-directory>` reads
   gzip-compressed tar on stdin and refuses nonempty targets, links and traversal.
   The hosted server's `documents.tar` is uncompressed and uses its manual
   recovery procedure. Never promote failed staging output; verify referenced
   originals, byte identity, source revision and role separation in recovery.
4. Select an immutable runtime matching the recovered schema. Reconcile later
   writes and accept recovery before switching storage/runtime. Start the API
   and verify `/api/health/ready` before any workers. Never run old and new
   consumers together.

The synthetic dump/document rehearsal runs with
`make test-backend PYTEST_ARGS="-q tests/test_deployment_upgrade.py"`, which owns
the shared test DB lock. It restores only a unique synthetic schema in existing
`infra-postgres-1` at `127.0.0.1:55432` and temporary synthetic document files;
it does not prove a complete production restore.

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

## Feature operating notes

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
No new environment setting or migration is required by these features.
Saved draft intent, actual meter provenance and the PDF calculation are retained
in the existing snapshot path; finalized originals are never recomputed.
Missing/ambiguous readings fail closed rather than selecting an estimate.
This local validation does not change the production gate or authorize deployment.

### Dunning interest defaults

`HV_DUNNING_BASE_INTEREST_RATE` is an optional decimal basis-rate default for
new dunning notices, not an automatically maintained official rate. An explicit
request can override or disable it; the notice freezes its rate, reference date,
day counts and interest amounts. Existing issued notices are not recalculated
when configuration changes. The UI permits a deliberate rate entry when no
default is configured. This documentation does not change any runtime setting.

## Inbox worker

Both Compose files define `inbox-worker`, using the same app image, isolated
`app_worker` connection (`HV_DATABASE_URL_WORKER`), `restart: unless-stopped`
and worker entrypoint pattern as `invoice-worker`. `HV_WORKER_KIND=workflow_runner`
selects the existing runner; `--all-organizations` schedules only its default
intake inspection, internal delivery and logical extraction stages. It has no
provider key/egress, exposed port, web/review role or migrator credential.
Documents and ClamAV signatures are read-only; the root filesystem is read-only,
capabilities are dropped and processing uses bounded temporary storage. The
130-second stop grace period allows bounded processing to drain. The inbox
container is capped at 4 GiB; the isolated inspector has a documented 3 GiB address
space cap, accommodating the [official signature engine's memory requirements](https://docs.clamav.net/manual/Installing/Docker.html#memory-ram-requirements).
Startup and
`python -m app.workers.workflow_runner --healthcheck` fail closed when local
scanner/storage or worker-role/queue access is unavailable. Healthchecks use
`sigtool --info` to verify official archives without loading a second signature
engine alongside a job; startup additionally scans a synthetic XML probe through
the bounded inspector. Neither claims work. See the [flow and review commands](docs/operations/inbox-review-chain.md).

The image includes ClamAV, Poppler and German Tesseract. **Before enabling the
worker**, provision a host directory of official, current ClamAV signatures
(using the host's `freshclam` installation), readable by uid 10001. Maintain
updates on the host; the worker has no network updater. Set
`HV_CLAMAV_DATABASE_DIR` to that absolute directory for Compose; the default is
`./clamav` beside the Compose file. Compose refuses a missing bind source and
the worker refuses an unusable signature database. Do not put signatures in the
document volume or bypass scanning. This deployment path is documented in
`.env.example`; the container receives `HV_CLAMAV_DATABASE_DIR=/var/lib/clamav`
and `HV_DOCUMENT_STORAGE_ROOT=/data/documents`. For host development, export
`HV_CLAMAV_DATABASE_DIR` pointing to the host's signature directory before
launching `scripts/dev-local.sh` (otherwise `/var/lib/clamav` is used).

Portable installations leave the worker **off by default**, under profile
`inbox`. After the existing installer has completed migrations and app readiness,
an operator can start just the worker from the reviewed checkout. For local HTTP:

```bash
VERWALINO_COOKIE_NAME=hv_session VERWALINO_COOKIE_SECURE=false \
VERWALINO_ALLOWED_HOSTS=localhost,127.0.0.1 \
HV_CLAMAV_DATABASE_DIR=/absolute/path/to/clamav \
docker compose --env-file .env.selfhost -f infra/docker-compose.yml \
  --profile inbox up -d --no-deps inbox-worker
```

For HTTPS use `VERWALINO_COOKIE_NAME=__Host-hv_session`,
`VERWALINO_COOKIE_SECURE=true` and
`VERWALINO_ALLOWED_HOSTS=your.domain,localhost,127.0.0.1` instead. Use the same
private config, Docker context and reviewed image as the app. These commands
are operator instructions, not authorization to run the portable or production
stack in a development session. The installer deliberately clears caller
profiles; `COMPOSE_PROFILES=inbox self-host.sh start` does not enable the worker.
Do not add `HV_CLAMAV_DATABASE_DIR` to `.env.selfhost` (its key allowlist is strict).
Installer `start` stops an existing inbox worker before migration; `stop` also
stops it. The worker stays off until the operator restarts it after app readiness.
Before migrations or upgrades outside the installer, stop it with the same
Compose/config selection and `--profile inbox stop inbox-worker`.

The production definition is not profiled; provision its signatures and start
the service only as part of a separately reviewed deployment. Adding the service
to a file does not start it on an existing deployment. The local launcher starts
the invoice worker and, when its healthcheck passes, the inbox worker on the test
DB; the inbox worker uses the host ClamAV installation.
No new configuration switch or provider setting is introduced.

## Invoice worker (T10)

The `invoice-worker` service in `docker-compose.prod.yml` belongs to the owner's
local production configuration and is covered by
`tests/test_invoice_extraction_worker.py`. It adds no migration of its own.
The portable stack, including the hosted server, does not start this worker;
automatic invoice extraction is disabled there.

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

For a **local synthetic test only**, use the launcher and required OCR dependencies
documented in the [development environment setup](CONTRIBUTING.md#development-environment).

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
Set `HV_SMTP_PORT` explicitly for the chosen service (default 587).
`HV_SMTP_SECURITY` defaults to `starttls`; `tls` is also supported and certificate
verification remains enabled.
Authentication requires both `HV_SMTP_USERNAME` and secret `HV_SMTP_PASSWORD`.
`HV_SMTP_TIMEOUT_SECONDS` defaults to 15 (1–60). No real credentials, recipients,
sender domain or delivery authority are assumed by the application or this document.
Keep credentials in operator-managed secrets, not the repository.

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
No email is sent by that script. Production changes follow the
[deployment boundary](#deployment-boundary-and-accepted-head).

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
production access, promotion or deployment is implied.

## Offline SEPA rent file (T50; local head 0133)

Migration **0133** adds source-protected `sepa_creditor` and `sepa_mandate` with
FORCE RLS, financial/ACL input locks and immutable consent identity. Downgrade
refuses populated SEPA tables; never delete consent/history to force rollback.
The actual rollback-transaction test checks 0133→0132→0133 against existing rows,
RLS/ACLs, policies, triggers/functions and an unchanged issued letter PDF.
Deployment follows the [accepted head and recovery boundary](#deployment-boundary-and-accepted-head).

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
policies, triggers/functions and an unchanged issued letter PDF. Production
changes follow the [deployment boundary](#deployment-boundary-and-accepted-head).

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
Production changes follow the [deployment boundary](#deployment-boundary-and-accepted-head).

## Selected dunning batches (T53; local head 0135)

Revision **0135**, after unchanged 0134, retains immutable actor/org/request-scoped
`dunning_batch` result identities. FORCE RLS and SELECT/INSERT-only grants prevent
rewriting or deleting completed batches; a populated downgrade refuses removal.
The protected 0135→0134→0135 test compares all existing rows, relation rights,
policies, triggers/functions and an issued letter PDF inside an outer rollback.
Do not erase history to force rollback. Production changes follow the
[deployment boundary](#deployment-boundary-and-accepted-head).

No new credentials or sending service. Preview/apply reuse live source/contact and
MIET preparation rights, ACL → financial → request locks, and one HTTP transaction.
Source changes require a fresh preview; uncertain responses retry the same request,
not a new batch. Only selected drafts are created, never issued or sent. Synthetic
UI acceptance uses [the dunning batch browser feature](features/dunning_batch.feature)
via `make test-browser`, which owns and stops its isolated local app stack.

## Previous-year plan proposals (T54; no new migration)

This feature adds no migration beyond **0135**. Existing draft JSON retains
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
afterward. Production changes follow the
[deployment boundary](#deployment-boundary-and-accepted-head).

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

Production changes follow the [deployment boundary](#deployment-boundary-and-accepted-head).

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
Production changes follow the [deployment boundary](#deployment-boundary-and-accepted-head).

## Declaration-worker network boundary

The portable stack, including the hosted server, has no declaration-worker
service and disables import/Q&A. In the retained local production configuration,
PostgreSQL stays on the internal `backend` network. Migration and bootstrap
services attach only there; the API additionally attaches to the external
`traefik` proxy network. The declaration worker alone additionally attaches to
the project-local, non-internal `egress` network, without proxy attachment or a
published host port. Its explicit Docker gateway priorities are `backend: 0`
and `egress: 1`, keeping database traffic private and routing external traffic
through the egress gateway.

That worker uses the fixed `https://api.openai.com/v1` SDK origin. The extraction
harness has no URL, browser or tool capability: provider calls use structured
outputs. The optional `python -m app.workers.declaration_provider_smoke` command
checks the configured primary/escalation model IDs through the non-billable
Models API with a short timeout and one bounded SDK retry. It consumes no jobs
and sends no prompt, PDF, document content or Responses API request. Its key is
read only from the worker environment and never printed. This probe is not part
of the portable installer or hosted deploy workflow.

Docker's non-internal network is **not** a domain firewall. The fixed client
origin and tool-free harness prevent application-configured alternate destinations;
host/network policy must add an egress allowlist when a strict network-level
provider restriction is required.

### Database role separation

| Connection | Runtime role / setting | Boundary |
| --- | --- | --- |
| Ordinary web API | `app_rw` / `HV_DATABASE_URL` | No worker credential; no migrator connection |
| Declaration and invoice workers | `app_worker` / `HV_DATABASE_URL_WORKER` | No web URL fallback; no review credential |
| Dedicated human-review commands in API | `app_review` / `HV_DATABASE_URL_REVIEW` | Separate pool, no ordinary-role fallback |
| One-shot migration/bootstrap | Privileged ops credentials only | Never injected into app/worker runtime |

## Worker role prerequisite for migration 0070

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
must be accounted for in the maintenance procedure; editing a dotenv file alone
does not update an existing database container.

Only the API receives `HV_DATABASE_URL_REVIEW`; the extraction worker must never
receive it. The two mutation endpoints use a separate pool with no fallback to
HV_DATABASE_URL. Missing/unsafe review configuration returns REVIEW_RUNTIME_UNAVAILABLE.
Read-only review views keep their ordinary connection. Local development uses the
same dedicated setting, pointed at the dev-only app_review login from init.sql.

The runtime checks actual session/current role, no membership edges, no elevated
attributes or ownership, and no public-schema CREATE privilege. The retained
read-only runtime check also validates the review connection. This separation protects
against arbitrary ordinary-runtime SQL; it does not protect against compromise of
the API process or its dedicated review credential. Keep that credential server-side
and rotate it independently. Source activation remains disabled by this release.
