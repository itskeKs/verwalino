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

## One-time preparation on Moon

Run as `chris` without sudo. The host must have Docker, Compose, `curl`,
`openssl`, and the existing external `traefik` network. This check must print
exactly `172.21.0.0/16`; otherwise stop rather than changing proxy trust
settings blindly:

```sh
docker network inspect traefik --format '{{(index .IPAM.Config 0).Subnet}}'
```

Create only private deployment directories and a host-only environment file:

```sh
root=/home/chris/apps/hausverwaltung-alpha
install -d -m 700 "$root" "$root/releases" "$root/shared"
install -m 600 .env.example "$root/shared/.env"
```

Generate the three database/session values without entering a cleartext secret
in shell history. The values exist only in the current shell and the mode-0600
environment file; unset them immediately afterwards. This rewrites the blank
placeholders instead of appending duplicate dotenv keys.

```sh
migrator_password=$(openssl rand -hex 32)
app_rw_password=$(openssl rand -hex 32)
session_secret=$(openssl rand -hex 32)
tmp_env=$(mktemp "$root/shared/.env.XXXXXX")
chmod 600 "$tmp_env"
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    MIGRATOR_PASSWORD=) printf 'MIGRATOR_PASSWORD=%s\n' "$migrator_password" ;;
    APP_RW_PASSWORD=) printf 'APP_RW_PASSWORD=%s\n' "$app_rw_password" ;;
    SESSION_SECRET=) printf 'SESSION_SECRET=%s\n' "$session_secret" ;;
    FORWARDED_ALLOW_IPS=*) printf 'FORWARDED_ALLOW_IPS=172.21.0.0/16\n' ;;
    *) printf '%s\n' "$line" ;;
  esac
done < "$root/shared/.env" > "$tmp_env"
mv -f "$tmp_env" "$root/shared/.env"
unset migrator_password app_rw_password session_secret
```

Leave `APP_IMAGE=` blank in that file for the host-build flow. The helper
supplies the immutable local `hausverwaltung-alpha:<release-id>` tag inline.
An optional registry flow may set only an immutable digest, never a tag.

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

Before starting PostgreSQL it builds the exact local image, creates a
mode-0600 CycloneDX SBOM in `shared/`, and runs the pinned Trivy vulnerability
HIGH/CRITICAL (`--scanners vuln --ignore-unfixed`) gate. It then uses the fixed
order `db → migrate → bootstrap → app`, each health wait is bounded, verifies
Alembic head `0023`, closed host ports, app-only database credentials and the
runtime `app_rw` role. It polls the public Traefik/Let's Encrypt route for at
most 180 seconds and, before atomically moving `current`, checks HTTPS/TLS,
the public unauthenticated session boundary, security headers, secure session
cookie, login/session/organization, traversal rejection, upload 413 and
readiness.

Any failure after the new app starts stops **only that new app**. It leaves the
database, named volumes and logs untouched and never automatically rolls back
either schema or application. `current` is changed only after the full smoke;
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

The running app receives only `app_rw`; verify no migrator URL leaked into it:

```sh
root=/home/chris/apps/hausverwaltung-alpha
release=$(readlink -f "$root/current")
image="hausverwaltung-alpha:$(basename "$release")"
APP_IMAGE="$image" docker compose --project-name hausverwaltung-alpha \
  --env-file "$root/shared/.env" -f "$release/docker-compose.prod.yml" ps
```

There is no automatic rollback. Only if an earlier image is compatible with the
already-migrated schema, explicitly start that app with its own release Compose
file and then move `current`; do not run downgrade or stop PostgreSQL/volumes.

## Optional GitLab deploy

The GitLab deploy job stays disabled until `HV_ENABLE_CI_DEPLOY=true` on the
protected default branch. It requires protected/masked `SSH_PRIVATE_KEY`,
`SSH_KNOWN_HOSTS`, `DEPLOY_HOST`, `DEPLOY_USER`,
and `HV_SMOKE_ADMIN_PASSWORD`. The admin password is passed to the remote
helper only through standard input, never archive content or command
arguments. CI first requires successful image build, Trivy scan and isolated
container smoke; the remote helper repeats its host-build scan and full public
smoke before promotion.
