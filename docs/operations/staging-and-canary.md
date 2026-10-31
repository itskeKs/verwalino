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
| Shared proxy network | `verwalino_proxy` | only service `staging-app` joins |
| Public host | `app.verwalino.de` | `staging.verwalino.de` |

Use [the staging Compose file](../../infra/docker-compose.staging.yml) alone.
There is no second Caddy and no published staging port. Database, migration and
bootstrap containers join only its private backend network. The app uses hosted
mode, secure host-only cookies, the existing proxy's trusted subnet, bounded
resources and independent database credentials and session secret. AI/extraction
workers are disabled. Both databases are named `hausverwaltung` **inside separate
containers**; this is not the legacy Compose project of that name.

The staging service is deliberately named `staging-app`: Compose also registers
service names in shared-network DNS. It must never register production's `app`
name on the proxy network.

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
   VERWALINO_PROXY_SUBNET=<existing-verwalino-proxy-subnet>
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
4. Copy `infra/Caddyfile.hosted` to `/srv/verwalino/config/Caddyfile.hosted`, readable
   by the operator. Its presence enables the hosted wrapper on production deploys.
   It imports the unchanged production site and the staging snippet. After the
   installer finishes, deployment validates the wrapper in an isolated read-only
   Caddy container, then applies [the proxy overlay](../../infra/docker-compose.hosted-proxy.yml)
   to **only production Caddy**. Complete this production deploy before checking
   public staging access. Keep the wrapper in place: production deploys reapply
   the extension; the portable installer alone does not.
5. Run staging through the existing forced-command key, or rerun **Actions → Deploy
   staging** for the merged main commit. After success, create a staging admin:

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

[Deploy staging](../../.github/workflows/deploy-staging.yaml) runs on main pushes
affecting backend, frontend, infrastructure, Docker build inputs or deploy
workflow/script. Website-only and docs-only changes do not deploy. Its `image` job
calls [Release image](../../.github/workflows/release-image.yaml), whose main job
publishes both platforms as `:main` and `:sha-<full-sha>` without creating a release.
SSH runs only after publishing succeeds. Production still deploys stable tags.

The forced command accepts exactly:

```text
deploy-server.sh vX.Y.Z
deploy-server.sh staging <40-lowercase-hex-sha|vX.Y.Z>
```

Invalid grammar exits 2 before any external call. The existing `DEPLOY_*` secrets,
key, user and pinned host key are reused. Deploy jobs share a concurrency group;
the server also takes the existing common deploy lock. Running deploys are not
cancelled.

Staging resolves a fetched revision reachable from main, pulls its **`:sha-<sha>`
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

A stable tag is also accepted, but its commit needs a published `:sha-<sha>` image
and staging runtime files. Docs-only commits may have no main image. Redeploy an
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

Every run performs: login → create an unused rental property → read the fixed
tenant's ledger with an explicit scope → delete **only its newly created property**.
The fixed fixture is unchanged. Cleanup also runs on ledger failure; cleanup
failure fails the canary. A lost create response can leave a synthetic
`Canary Prüfung …` property: inspect and remove that unused row manually without
changing retention rules.

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
| `ALERT_EMAIL` | Owner's alert recipient |
| `ALERT_SMTP_URL` | Workflow-only SMTP connection: `smtp+starttls://USER:PASSWORD@HOST:587` or `smtps://USER:PASSWORD@HOST:465`; URL-encode credentials |
| `ALERT_SMTP_FROM` | SMTP-authorized alert sender |

Staging also needs the independent private env-file values above. Workflow alerts
use their own secrets, never server SMTP config or the staging allow-list. The
pinned [mail action](https://github.com/dawidd6/action-send-mail/tree/a9703ed89296f78cab37cd53cb47a0fcfc1ec1cd)
requires TLS for the documented URL schemes.

[Hosted canary](../../.github/workflows/canary.yaml) runs at `*/15 * * * *` with a
five-minute job timeout and a two-minute probe limit leaving time for alerts.
On the **first failed run**, independent failure steps send German e-mail to the
owner and call the [existing reporter](../../scripts/scenario_issues.py). Issues
have `scenario`/`canary` labels and fingerprint `canary::<step>::<error kind>`.
Repeats comment on the existing open issue for that fingerprint; closed issues
reopen as regressions. Different steps/error kinds are distinct issues. Neither
alert path depends on the other succeeding.

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
