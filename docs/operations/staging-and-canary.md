# Hosted staging and canary

Staging runs on the same Contabo server as production. The shared machine, disk,
Docker daemon and Caddy are an accepted shared failure domain. Staging contains
**synthetic data only**: never restore a production backup into it. The generic
self-host installer and its release bundle remain unchanged.

## Layout

| Component | Production | Staging |
| --- | --- | --- |
| Compose project | `verwalino` | `verwalino-staging` |
| Private configuration | `/srv/verwalino/config/.env.selfhost` | `/srv/verwalino/config/.env.staging` |
| Runtime files | `/srv/verwalino/app/infra/` at a release tag | `/srv/verwalino/staging/infra/`, exported from the selected main commit |
| Database volume | `verwalino_pgdata` | `verwalino-staging_pgdata_staging` |
| Document volume | `verwalino_documents` | `verwalino-staging_documents_staging` |
| Backend network | `verwalino_backend` | `verwalino-staging_backend` |
| Proxy network | `verwalino_proxy` | dedicated `verwalino-staging_proxy` |
| Public host | `app.verwalino.de` | `staging.verwalino.de` |

Use [the staging Compose file](../../infra/docker-compose.staging.yml) alone.
There is no second Caddy and no published staging port. Database, migration and
bootstrap containers join only its private backend network. The app uses hosted
mode, secure host-only cookies, its dedicated proxy's trusted subnet, bounded
resources and independent database credentials and session secret. AI/extraction
features are disabled, and the staging Compose file has no worker service.
Both databases are named `hausverwaltung` **inside separate
containers**; this is not the legacy Compose project of that name.

The staging service is deliberately named `staging-app`: Compose also registers
service names in network DNS. Only staging's app and hosted Caddy join
`verwalino-staging_proxy`; staging cannot reach production's `app:8000` or supply
headers from production's trusted proxy subnet.

The [public website](website-hosting.md) runs in the production project on its
own internal `verwalino_site` network. Only Caddy and `site` join it. One hosted
Caddy wrapper imports production, staging and site; both hosted overlays must
be applied together so Caddy retains all snippets and all three networks.
The staging import is unconditional: enable website and staging routes only
once `staging.verwalino.de` resolves to this server, even when enabling the website first.

The [Caddy snippet](../../infra/Caddyfile.staging) adds `X-Robots-Tag: noindex,
nofollow` to responses and serves `/robots.txt` with `Disallow: /`. Application
login remains required; robots controls discourage indexing, not access.

## First enablement by the maintainer

1. Set DNS **A record `staging.verwalino.de` to the server's IPv4 address**. Add
   AAAA only if that address reaches this server too. Existing Caddy owns ports
   80/443 and provisions the certificate.
2. Deploy a reviewed production release containing the new deploy grammar and
   hosted proxy files. The forced command lives in the release checkout; an older
   release cannot accept `staging`. The first main run can fail until setup is
   complete; rerun that workflow after enablement.
3. Create `/srv/verwalino/config/.env.staging` as a regular file, mode **0600**, in
   the existing private config directory. Generate independent random 64-character
   lowercase hex values for all database passwords and the session secret. Do not
   copy production credentials or data. Contents:

   ```dotenv
   VERWALINO_IMAGE=ghcr.io/itskeks/verwalino@sha256:<initial-main-image-digest>
   HV_RELEASE_ID=none
   MIGRATOR_PASSWORD=<independent-random-secret>
   APP_RW_PASSWORD=<independent-random-secret>
   APP_WORKER_PASSWORD=<independent-random-secret>
   APP_REVIEW_PASSWORD=<independent-random-secret>
   SESSION_SECRET=<independent-random-secret>
   VERWALINO_PROXY_SUBNET=<dedicated-staging-proxy-subnet>
   CANARY_EMAIL=<only-permitted-recipient>
   HV_SMTP_HOST=<smtp-host>
   HV_SMTP_PORT=587
   HV_SMTP_SECURITY=starttls
   HV_SMTP_FROM=<staging-sender>
   HV_SMTP_USERNAME=<smtp-user>
   HV_SMTP_PASSWORD=<smtp-password>
   ```

   Use Compose env-file quoting for SMTP credentials containing special characters.
   The deploy script never sources this file as shell code.
   Before any Docker call it compares the session secret and all four database
   passwords against the production config (`VERWALINO_CONFIG`, normally
   `/srv/verwalino/config/.env.selfhost`); a reused secret exits 2 without printing it.
   Use a **separate SMTP account for staging**, with its own credentials and sender.
   Before applying either hosted overlay, create the external network once:

   ```bash
   docker network create --subnet '<dedicated-staging-proxy-subnet>' verwalino-staging_proxy
   ```

   Choose an unused private IPv4 subnet that does not overlap production, the
   host or other Docker networks, and use exactly that subnet in `.env.staging`.
   Both Compose projects reference this external network; neither creates it.
4. Copy `infra/Caddyfile.hosted` to `/srv/verwalino/config/Caddyfile.hosted`, readable
   by the operator. Its presence enables the hosted wrapper on production deploys.
   This deliberately does not require Caddy to be on the site network already:
   first enablement must attach Caddy to the new networks. It imports the unchanged
   production site, staging and website snippets. Before backup, image pinning or
   installer migration/restart, deployment validates the wrapper in an isolated
   read-only Caddy container. Invalid configuration aborts before those side effects.
   All capabilities are dropped, then only `NET_BIND_SERVICE` is added back:
   the stock Caddy binary carries that file capability and cannot execute without
   it in the bounding set. Networking remains disabled and `no-new-privileges` stays set.
   After the installer finishes, deployment applies [the proxy overlay](../../infra/docker-compose.hosted-proxy.yml)
   and [site overlay](../../infra/docker-compose.site.yml) together to **only production Caddy**.
   Set website DNS and enable the site through [the website runbook](website-hosting.md),
   which uses this same wrapper and the base/proxy/site compose prefix.
   Complete this production deploy before checking
   public staging access. Keep the wrapper in place: production deploys reapply
   both extensions in one Caddy recreation; the portable installer alone does not.
   After a manual installer start, reapply that three-file prefix with
   `up -d --no-deps caddy`. Never replace the wrapper with a single extension.
5. Run staging through the existing forced-command key, or rerun **Actions → Build
   main image** for the merged main commit; success triggers **Deploy staging**.
   After success, create a staging admin:

   ```bash
   # Operator terminal on the server; keep the password out of shell history.
   read -rs staging_password; printf '\n'
   printf '%s\n' "$staging_password" |
     docker compose --project-name verwalino-staging \
       --env-file /srv/verwalino/config/.env.staging \
       -f /srv/verwalino/staging/infra/docker-compose.staging.yml --profile ops \
       run --rm --no-deps -T bootstrap --org 'Staging Canary' \
       --email '<staging-canary-email>' --password-stdin
   unset staging_password
   ```

   Hosted mode has no browser setup. Add only synthetic records. Check public
   HTTPS, the robots response header and `/robots.txt` after DNS/TLS are ready.

## Main deploys and staging rollback

[Build main image](../../.github/workflows/build-main-image.yaml) runs on main pushes
affecting backend, frontend, infrastructure, Docker build inputs or deploy
workflow/script. Website-only and docs-only changes do not deploy. It publishes both
platforms as **`:sha-<full-sha>` only**, with BuildKit provenance/SBOMs and a keyless
cosign signature, without creating a release. [Deploy staging](../../.github/workflows/deploy-staging.yaml)
runs only after that workflow succeeds for a main push in this repository, using
the completed run's SHA. [Release image](../../.github/workflows/release-image.yaml)
publishes stable tags and installer bundles only; production still deploys stable tags.

The forced command accepts exactly:

```text
deploy-server.sh vX.Y.Z
deploy-server.sh site <40-lowercase-hex-sha>
deploy-server.sh staging <40-lowercase-hex-sha|vX.Y.Z>
```

Stable tags have no suffix or leading zeroes. Invalid grammar exits 2 before any
external call. The existing `DEPLOY_*` secrets, key, user and pinned host key are
reused. Production uses `deploy-hosted-server`, staging uses `deploy-hosted-staging`
and the website uses `deploy-hosted-site`, so queued runs cannot replace each other.
All three targets take the existing common server deploy lock; it **refuses** a
second deployment instead of waiting. Retry the refused run after the active one
finishes. Running deploys are not cancelled. Image builds use separate
`release-image-tags` and `build-main-image` groups. Tag builds remain serialized
because they also publish shared minor tags. A newer main image build may cancel
an older main build; running tag builds are not cancelled.

Staging resolves a fetched revision reachable from main, exports its runtime files
and validates the hosted wrapper with that revision's snippets before any image
pull or staging service operation. Invalid configuration leaves production Caddy
and both private env files untouched. It then pulls its **`:sha-<sha>`
image**, verifies a matching registry SHA-256 digest exists and uses that digest
for Compose. It exports only staging runtime files without moving the production
checkout or changing its config. It starts the staging database, stops its app,
migrates, recreates the app, waits for health and probes readiness inside the
container. Only after success does it atomically record the digest and SHA in
`.env.staging`. Logs print the previous successful staging SHA/image and live in
private `/srv/verwalino/logs/deploy-staging-<timestamp>.log` files.

For a staging rollback, send the previous SHA with the same restricted key and
the workflow's pinned-host-key options:

```bash
ssh -T -i <existing-deploy-key> <existing-user>@<server> 'staging <previous-full-sha>'
```

A stable tag is also accepted, but `staging vX.Y.Z` still requires its commit's
`:sha-<sha>` image from the **main build**, plus staging runtime files; the tag's
release image alone is insufficient. Docs-only commits may have no main image. Redeploy an
older image **only if compatible with the migrated schema**. There is no automatic
rollback or database downgrade; otherwise fix forward or have the operator
recreate staging from synthetic fixtures. Never delete production volumes.

## Mail boundary

Staging supplies `HV_MAIL_ALLOWLIST` from its required `CANARY_EMAIL`. The mail
service accepts **exactly that single bare mailbox**, compared exactly, before
rendering or SMTP connection. Other recipients receive a German validation error
and generate a WARNING without logging their address. Empty/malformed allow-lists
fail closed. Unset `HV_MAIL_ALLOWLIST` retains existing mail behavior elsewhere.
This is an application environment setting, not a portable installer config key.
SMTP uses STARTTLS/TLS with certificate checks. The canary itself sends no app mail.

## Production canary setup

Create a **separate production canary organization and OWNER_ADMIN user** once
with `scripts/self-host.sh admin --org 'Hosted Canary' --email EMAIL
--password-stdin`, using the production private config. Store the login only in
repository secrets `CANARY_EMAIL` and `CANARY_PASSWORD`.

Sign in as this user and prepare the synthetic fixture through the ordinary UI:

- One fixed rental property, one residential unit, one synthetic tenant and
  **exactly one lease** in the organization, covering January 2026.
- One readable **MIET accounting scope** for that property, including its unit,
  effective for all of January 2026. Grant the canary user `read` capability;
  see [accounting scope setup](accounting-scope-setup.md).
- Keep the dedicated organization's **property-source mode in PREPARATION**.
  Do not activate property-source rollout: explicit audiences/receipts can protect
  newly created records from deletion.

No real bank data, payments, tenants or recipients belong here. The script
discovers the fixed lease, unit/property and the single readable MIET scope via
the API; no resource IDs or extra secrets are needed. January 2026 is a fixed
fixture period with no annual rollover maintenance.

Every run performs: login → check leftover count → create an unused rental property → read the fixed
tenant's ledger with an explicit scope → delete **only its newly created property**.
The fixed fixture is unchanged. Cleanup also runs on ledger failure; cleanup
failure fails the canary. A lost create response can leave a synthetic
`Canary Prüfung …` property. More than **10** properties whose names start with
`Canary Prüfung` in this organization refuses the journey before creation, reports
`leftovers: CanaryLeftovers` and uses the same issue/e-mail alert path below.
Sign in as the dedicated canary user, inspect these properties and manually delete
only unused synthetic `Canary Prüfung …` records through the ordinary UI. Keep
the fixed property/lease/scope, inspect the cleanup failure, then rerun the canary.
Do not weaken retention rules or delete records in another organization.

`python3 -B scripts/canary.py --dry-run` prints the journey without credentials,
HTTP requests or a report. A real run reads environment credentials and writes
`report/canary.json`. An operator can explicitly select staging with
`--base-url https://staging.verwalino.de` and its own fixture/login. The schedule
targets production only. Redirects are refused; reports contain steps and
structured error kinds, never credentials, cookies, response bodies or money data.

## Alerts and secrets

| Secret | Purpose |
| --- | --- |
| `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` | Existing forced-command deployment path |
| `CANARY_EMAIL`, `CANARY_PASSWORD` | Dedicated production canary login |
| `ALERT_EMAIL` | Alert recipient: `alarm@verwalino.de` (the owner's role address) |
| `ALERT_SMTP_URL` | Workflow-only SMTP connection: `smtp+starttls://USER:PASSWORD@HOST:587` or `smtps://USER:PASSWORD@HOST:465`; URL-encode credentials |
| `ALERT_SMTP_FROM` | SMTP-authorized alert sender |

Staging also needs the independent private env-file values above. Workflow alerts
use their own secrets, never server SMTP config or the staging allow-list. The
pinned [mail action](https://github.com/dawidd6/action-send-mail/tree/a9703ed89296f78cab37cd53cb47a0fcfc1ec1cd)
requires TLS for the documented URL schemes.

[Hosted canary](../../.github/workflows/canary.yaml) runs at `*/15 * * * *` with a
five-minute job timeout and a two-minute probe limit leaving time for alerts.
That is **96 runs/day**, about **2,900 runner minutes/month** at GitHub's one-minute
job billing minimum (before reruns or longer failures). The repository is public
today; a private repository would consume its Actions allowance/billable minutes.
On the **first failed run**, the [existing reporter](../../scripts/scenario_issues.py)
opens one issue with `scenario`/`canary` labels and fingerprint `canary`. Its title
and body identify the first failing step and error kind. Every later failing run
adds one comment listing its failing steps and error kinds, even when they change;
it never opens a second issue while that canary issue remains open. After the
operator closes the incident, a later failure can create a new issue.

The reporter prints `canary_issue_created=true|false` and emits it to
`GITHUB_OUTPUT` only after successful filing. German owner e-mail runs only when
that output is `true`, so a continuing outage generates one e-mail. Repeated
failures, dry runs and failed issue creation do not send e-mail; inspect Actions
if filing fails. Sending mail now depends on successful issue creation.

The intended budget is **15 + 5 = 20 minutes**, assuming scheduled runners start
on time and GitHub/SMTP are available. GitHub schedules can be delayed; SMTP
acceptance does not guarantee inbox delivery. This is not a hard wall-clock SLA.
After setting secrets, run the workflow manually and exercise a controlled failure
to verify **both actual alert channels**, then restore correct credentials.
Sanitized reports are retained for 14 days. Inspect Actions if an alert transport fails.

## Validation and operator work

`make test-installer` covers fake Docker/Git deployment contracts and the canary's
fake HTTP server. `make test-scenario-issues` covers reporter deduplication.
Targeted backend tests cover mail denial before rendering or an SMTP attempt.
Compose `config -q`, workflow YAML parsing/actionlint, lint, docs link checks and
agent-rule tests validate the checked-in configuration. Live staging deployment,
DNS/TLS provisioning and actual alert delivery remain the maintainer's work (#25).
