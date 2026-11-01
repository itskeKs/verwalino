# Website hosting

The website deployment configured for issue #24 uses the existing hosted server and forced-command key.
This runbook describes operator setup after merge; it does not authorize a rollout. Legal review remains
with #26. Registration stays closed. See [DEPLOY.md](../../DEPLOY.md#website-container) for the workflow
and accepted SSH command grammar, and [website/README.md](../../website/README.md) for the static build.

The independent [production app mode and mail opt-in](../../DEPLOY.md#production-app-mode-and-mail-opt-in)
uses private `config/.env.hosted-app` and `infra/docker-compose.hosted-app.yml` on release deploys.
It forwards the owner's mode, public hostname and allowed hosts, preserving this proxy/site
overlay chain. It does not change the website or open signup unless the owner selects `hosted`.
After that opt-in, use the managed release deploy path for app restarts; the portable installer
alone starts the stock selfhost app. Follow the linked owner acceptance steps for mail and signup.

## Image and isolation

`website/Dockerfile` runs `npm ci`, i18n, lint, build and link checks under digest-pinned Node 22.
Only `dist/` reaches the runtime. We use Caddy 2.10.2 from its pinned official Alpine image because it
already implements file serving, directory redirects and localized error pages. Its static binary is copied
onto pinned Alpine 3.22, avoiding the upstream runtime's curl/libcap packages and a duplicate binary layer.
BusyBox already supplies `wget` for the image healthcheck; no probe package is installed. The upstream
low-port file capability is removed before copying, so the binary runs with all capabilities dropped.
The runtime contains neither Node/npm nor project sources. The local amd64 image was about 76 MB
(uncompressed Docker image size); content and upstream updates can change this.

The service runs as uid/gid 10001 on port 8080, with a read-only root, all capabilities dropped and
`no-new-privileges`. No ports, host paths, secrets or credential environment are supplied to `site`.
TLS, the Caddy admin API and config persistence are disabled inside that container. Caddy still performs
storage housekeeping, so only `/data` is writable: an ephemeral, 1 MiB, uid-owned tmpfs with
`noexec,nosuid,nodev`. Logs go to stdout/stderr with bounded Docker local-log rotation. Limits are
128 MiB RAM, half a CPU and 64 PIDs.

```mermaid
flowchart LR
  browser[Internet] --> caddy[Caddy: ports 80 and 443]
  caddy -->|proxy network: app.verwalino.de| app[app:8000]
  app -->|backend network| db[(db)]
  caddy -->|dedicated staging proxy: staging.verwalino.de| staging[staging-app:8000]
  staging -->|private staging backend| staging_db[(staging db)]
  caddy -->|internal site network: verwalino.de| site[site:8080]
```

Only Caddy and `site` join `verwalino_site`, an internal Docker network without outbound internet routing.
The app uses `proxy` and `backend`; db uses `backend`. Staging's app joins `verwalino-staging_proxy` and its
own private backend, as described in [the staging runbook](staging-and-canary.md). It never joins `site`.
The site cannot address app/db directly or read
host files, documents or credentials. Caddy remains a shared trust boundary: a compromised site can reach
Caddy, whose public app routes still require app authentication and organization isolation.

The operator-owned `Caddyfile.hosted` imports the production, staging and website snippets. Automatic HTTPS uses
the same certificate volumes as the app. Both site hosts receive the app's HSTS, nosniff, frame denial,
referrer policy, CSP and removal of the Server header, plus a restrictive Permissions-Policy.
`www.verwalino.de` redirects to `https://verwalino.de{uri}` with status 308. Scripts and fonts are local;
the CSP permits no inline scripts. Inline styles remain allowed for Astro's built CSS. Missing paths
return HTTP 404 with German or English error content; there is no SPA fallback.

## Enable the hosted extension once

Prerequisites: the server checkout must contain the reviewed deploy script, image/compose extension and
Caddy snippets from #24 and #25, normally through the first app release containing both. A site deployment
never updates that checkout. If the first main-push deploy runs before setup, it fails closed; rerun the
workflow after setup. The GHCR site package must be publicly readable (or use the operator's existing
registry credential store). No new key or user is needed.

DNS for `verwalino.de`, `www.verwalino.de` and `staging.verwalino.de` must point to the existing Caddy listener. The existing
private installer config must use `VERWALINO_MODE=https` and `VERWALINO_DOMAIN=app.verwalino.de`.
Never add site settings to that strict installer config. Keep the tracked generic Caddyfile unchanged:

```sh
cd /srv/verwalino/app
cp infra/Caddyfile.hosted /srv/verwalino/config/Caddyfile.hosted
chmod 640 /srv/verwalino/config/Caddyfile.hosted
```

Select a full published commit SHA from the successful Website build/sign job. Pull and pin it before the
first start, preserving the tag in the image reference so subsequent deployments can report its SHA:

```sh
SITE_SHA=<40-character-published-commit-sha>
site_tag="ghcr.io/itskeks/verwalino-site:$SITE_SHA"
docker pull "$site_tag"
site_digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$site_tag" |
  grep -m1 -E '^ghcr\.io/itskeks/verwalino-site@sha256:[a-f0-9]{64}$')
test -n "$site_digest"
export VERWALINO_SITE_IMAGE="$site_tag@${site_digest#*@}"
export VERWALINO_SERVER_ROOT=/srv/verwalino
export VERWALINO_CONFIG=/srv/verwalino/config/.env.selfhost
export VERWALINO_COOKIE_SECURE=true VERWALINO_COOKIE_NAME=__Host-hv_session
export VERWALINO_ALLOWED_HOSTS=app.verwalino.de,localhost,127.0.0.1
docker compose --project-name verwalino --env-file "$VERWALINO_CONFIG" \
  -f infra/docker-compose.yml -f infra/docker-compose.hosted-proxy.yml \
  -f infra/docker-compose.site.yml --profile https \
  up -d --no-deps site caddy
```

Both overlays are required together: they mount the operator-owned wrapper and all three reviewed snippets
read-only, and connect Caddy to `proxy`, `staging-proxy` and `site`. Create the external
`verwalino-staging_proxy` network first as described in [the staging runbook](staging-and-canary.md).
No mount is given to `site`. `--no-deps` leaves app/db and
staging alone; the first enable recreates Caddy to attach its additional networks. Check HTTPS, the 308 redirect,
the app and staging after this operator step. Enable staging separately with [its runbook](staging-and-canary.md).
An omitted or empty `VERWALINO_SITE_IMAGE` fails compose rendering with
`set VERWALINO_SITE_IMAGE to ghcr.io/itskeks/verwalino-site:<sha>@sha256:<digest>`.
Only full SHA tags are published; use the SHA plus digest pin for starts. The forced deploy supplies that pin itself.

The portable installer deliberately uses only the base compose file. An app release through
`deploy-server.sh vX.Y.Z` uses the presence of `config/Caddyfile.hosted` as the opt-in, including first
enablement before Caddy is attached to the site network. It first recovers the existing site's image
reference and refuses before checkout if that reference is unavailable. Before backup, image pinning and the installer,
it validates the wrapper and all three snippets in an isolated read-only Caddy container. After the installer,
it reapplies both overlays in one Caddy recreation, reusing the existing site's image reference without
recreating site or staging. After a **manual** `self-host.sh start`,
reapply the same three-file prefix with `up -d --no-deps caddy` before a site deploy. The production import
reads the checked-out release's generic Caddyfile; keep all three imports when updating the wrapper.

While the extension is enabled, `self-host.sh doctor` reports ports 80/443 as
`Durch einen fremden Dienst belegt; mit ss -ltnp oder lsof -iTCP:80 den Dienst ermitteln und dessen Portfreigabe korrigieren.`
(with `443` in the corresponding message). This is a false error: the ownership check expects only the
base compose file in Caddy's `com.docker.compose.project.config_files` label, but the extension records
all three compose files. Confirm Caddy owns the listeners using the compose prefix above.

`self-host.sh stop` also uses only the base model, so it leaves `site` running. Stop it explicitly using
the same setup environment and all three compose files:

```sh
docker compose --project-name verwalino --env-file "$VERWALINO_CONFIG" \
  -f infra/docker-compose.yml -f infra/docker-compose.hosted-proxy.yml \
  -f infra/docker-compose.site.yml --profile https \
  stop site
```

## Deployment and rollback

Main-push publication signs the image index after both platforms and their provenance/SBOMs are pushed.
Deployment uses only `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`, with strict
host-key checking. The server accepts exactly `vX.Y.Z`, `site <40-lowercase-hex-sha>` or
`staging <40-lowercase-hex-sha|vX.Y.Z>`, with stable tags having no suffix or leading zeroes. Malformed commands
exit 2 before side effects. A missing operator file, HTTPS configuration or running Caddy on the site
network refuses site deployment before pulling. Pull/digest/recreation/health failures exit nonzero.

The site path pulls only its own repository, requires a matching registry RepoDigest, and recreates only
the site service with `--no-deps --force-recreate --pull never`. Docker validates pulled content against
the registry digest. Server-side cosign verification is absent, matching the existing app deploy; the
workflow signs keylessly. Do not describe the deploy key as cryptographically restricted to main commits:
its grammar limits repository/tag shape, while image publication permissions remain a trust boundary.

For manual signature verification, use the selected SHA plus digest pin and the workflow's main-branch identity:

```sh
cosign verify \
  --certificate-identity https://github.com/itskeKs/verwalino/.github/workflows/website.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "$VERWALINO_SITE_IMAGE"
```

Read `previous site sha: ...` in the deployment log. Roll back explicitly through the same forced command:

```sh
ssh -T -i <existing-deploy-key> -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=yes <admin>@<host> "site <previous-sha>"
# Or on the server:
/srv/verwalino/app/scripts/deploy-server.sh site <previous-sha>
```

The previous SHA is recovered from the existing container's image reference, even if it is stopped.
It is printed before pulling/recreating, so preserve the log if a deploy fails. No known prior SHA means
the log says `unknown` and does not invent a rollback command. Retain SHA tags in GHCR; restarting an older
static site has no database implications. There is no automatic rollback. The site uses the same server
deploy lock/log directory as the app; check that no operation runs before removing a stale `.deploy.lock`.

## Troubleshooting and verification

Use the compose prefix from setup (same explicit project, env file and all three `-f` files) with `ps site caddy`
or `logs --tail 100 site caddy`. The site has no host listener. The deploy health probe runs the existing
Caddy image's BusyBox `wget -S -O /dev/null -T 2 http://site:8080/` through `docker exec` and requires the
first HTTP status to be 200, retrying up to 24 times with three-second pauses. This covers Docker DNS,
the shared network and the site listener, but not public DNS, certificate issuance or the public redirect.
Check those externally after rollout. The image's own healthcheck probes loopback port 8080.

If the site is refused as disabled, check `Caddyfile.hosted`, its import and Caddy's `verwalino_site`
membership. If digest resolution fails, verify the package/tag is published and readable; app/db and the
private config are untouched. If the site cannot start, check uid 10001 ownership of `/data` tmpfs and
that the image binary has no low-port capability. If public HTTPS fails while internal HTTP succeeds,
check Caddy's logs, DNS and certificates. Keep the operator wrapper's production, staging and site imports together.

Local contracts (`make test-installer`) fake Docker and never access a database. Build with
`docker buildx build --platform linux/amd64 -t verwalino-site:check website/`, then smoke-test that image
with a read-only root, all caps dropped, `no-new-privileges`, the `/data` tmpfs above and only the loopback
mapping `127.0.0.1:18089:8080`; remove the container afterward. Verify `/` and `/en/` return 200, and unknown
German/English paths return 404. Also test traversal/source-file requests, attempted writes/privilege gain,
app/db network isolation, malformed deploy commands, bad digest metadata and HTTP redirects/errors in
the readiness probe. Compose configuration and network-none Caddy validation do not prove live isolation.
