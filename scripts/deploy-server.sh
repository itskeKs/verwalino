#!/usr/bin/env bash
# Deploys one release, static-site commit or staging revision from /srv/verwalino/app,
# as the server's admin user, entered through a restricted CI key; no sudo is needed.
#
# Usage: deploy-server.sh vX.Y.Z | site <40-lowercase-hex-sha> | staging <40-lowercase-hex-sha|vX.Y.Z>
#
# The GitHub workflow .github/workflows/deploy.yaml runs "ssh ADMIN_USER@host vX.Y.Z". The CI
# key in that user's authorized_keys is restricted to
#   command="/srv/verwalino/app/scripts/deploy-server.sh $SSH_ORIGINAL_COMMAND",restrict
# so the login shell hands the client's text to this script as arguments, unquoted. The
# strict grammar below is therefore the whole input validation. Only the three shapes
# above are accepted; other shapes exit 2 before any work. The staging target
# exports only its runtime files and never checks out or starts the production stack.
# Site deploys pull and resolve a registry digest, recreate ONLY site, and poll HTTP 200
# from Caddy over the internal site network. No checkout, backup, migration or config write.
# The previous site's SHA is printed for an explicit "site <previous-sha>" rollback.
#
# Steps: fetch tags and check out the tag -> validate optional hosted proxy ->
# pull ghcr.io/itskeks/verwalino:TAG and resolve its digest -> backup (server-backup.sh) -> pin
# the digest in the installer config ->
# "self-host.sh start" (pull, stop app, migrate, start, wait for healthy) -> poll
# /api/health/ready for up to 120 s. The whole run is logged to
# /srv/verwalino/logs/deploy-<timestamp>.log.
#
# There is NO automatic rollback. A failed migration leaves the app stopped, and a started
# release may already have migrated the schema, so an older image is not a safe fallback.
# On failure the script prints the last 100 app log lines and exits non-zero; the operator
# decides (fix forward, or restore the pre-deploy backup). Automatic rollback is #9/#76.
#
# TODO(#76): replace the backup, pin and start steps by "scripts/self-host.sh update" once
# the installer has it (with #57 for backup and restore).
#
# VERWALINO_SERVER_ROOT (default /srv/verwalino) and VERWALINO_CONFIG (default
# $VERWALINO_SERVER_ROOT/config/.env.selfhost) locate the installation. The invoking user
# must be able to read and write the config and create files in its directory: the installer
# insists on mode 0600 and takes a lock directory next to it.
set -euo pipefail
trap '' PIPE HUP
export LC_ALL=C
umask 027

ROOT=${VERWALINO_SERVER_ROOT:-/srv/verwalino}
APP=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG=${VERWALINO_CONFIG:-$ROOT/config/.env.selfhost}
PRODUCTION_CONFIG=$CONFIG
LOGS=$ROOT/logs
IMAGE_REPO=ghcr.io/itskeks/verwalino
DIGEST_RE='^ghcr\.io/itskeks/verwalino@sha256:[a-f0-9]{64}$'
TAG_RE='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
SHA_RE='^[a-f0-9]{40}$'
SITE_REPO=ghcr.io/itskeks/verwalino-site
SITE_DIGEST_RE='^ghcr\.io/itskeks/verwalino-site@sha256:[a-f0-9]{64}$'
READY_ATTEMPTS=24   # 24 checks, 2 s timeout plus 3 s pause each: at most 117 s
export VERWALINO_SERVER_ROOT=$ROOT VERWALINO_CONFIG=$CONFIG

target=release
if [[ $# -eq 1 && "$1" =~ $TAG_RE ]]; then
    TAG=$1
elif [[ $# -eq 2 && "$1" = site && "$2" =~ $SHA_RE ]]; then
    target=site
    TAG=$2
elif [[ $# -eq 2 && "$1" = staging && ( "$2" =~ $SHA_RE || "$2" =~ $TAG_RE ) ]]; then
    target=staging
    TAG=$2
    CONFIG=$ROOT/config/.env.staging
    export VERWALINO_CONFIG=$CONFIG
else
    echo 'Usage: deploy-server.sh vX.Y.Z | site <40-lowercase-hex-sha> | staging <40-lowercase-hex-sha|vX.Y.Z>' >&2
    exit 2
fi

# Production app opt-in is separate from the portable installer's private config.
# Validate before log creation/chmod, locks, checkout, backup or service operations.
APP_CONFIG=$ROOT/config/.env.hosted-app
app_overlay=0 app_mode=selfhost app_public_host='' app_allowed_hosts=''
invalid_app_config() { printf 'deploy: %s\n' "$*" >&2; exit 2; }
if [[ "$target" = release && ( -e "$APP_CONFIG" || -L "$APP_CONFIG" ) ]]; then
    [[ -f "$APP_CONFIG" && ! -L "$APP_CONFIG" && -r "$APP_CONFIG" ]] || \
        invalid_app_config 'the hosted app config must be a readable regular file, not a symlink.'
    permissions=$(stat -c '%a' "$APP_CONFIG" 2>/dev/null || stat -f '%Lp' "$APP_CONFIG")
    [[ "$permissions" = 600 ]] || invalid_app_config 'the hosted app config must have permissions 0600.'
    seen='|'
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in ''|\#*) continue ;; esac
        [[ "$line" = *=* ]] || invalid_app_config 'Invalid hosted app configuration line.'
        key=${line%%=*} value=${line#*=}
        [[ "$seen" != *"|$key|"* ]] || invalid_app_config 'Duplicate hosted app configuration key.'
        seen="$seen$key|"
        case "$key" in
            HV_DEPLOYMENT_MODE) app_mode=$value ;;
            HV_PUBLIC_HOST) app_public_host=$value ;;
            HV_ALLOWED_HOSTS) app_allowed_hosts=$value ;;
            *) invalid_app_config 'Unknown hosted app configuration key.' ;;
        esac
    done < "$APP_CONFIG"
    [[ "$app_mode" = selfhost || "$app_mode" = hosted ]] || \
        invalid_app_config 'HV_DEPLOYMENT_MODE must be selfhost or hosted.'
    dns_label='[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?'
    [[ ${#app_public_host} -le 253 && "$app_public_host" =~ ^$dns_label(\.$dns_label)+$ && "${app_public_host##*.}" =~ [a-z] ]] || \
        invalid_app_config 'HV_PUBLIC_HOST must be a lowercase DNS hostname, without scheme or port.'
    [[ "$app_allowed_hosts" =~ ^[a-z0-9.,-]+$ && ",$app_allowed_hosts," = *",$app_public_host,"* ]] || \
        invalid_app_config 'HV_ALLOWED_HOSTS must be a comma-separated list without spaces including HV_PUBLIC_HOST exactly.'
    [[ ",$app_allowed_hosts," = *',127.0.0.1,'* ]] || \
        invalid_app_config 'HV_ALLOWED_HOSTS must include 127.0.0.1 for the app health checks.'
    app_overlay=1
fi
[[ -d "$LOGS" && -w "$LOGS" ]] || { echo "deploy: $LOGS is missing or not writable." >&2; exit 1; }
chmod 750 "$LOGS"
log_prefix=deploy
if [[ "$target" = staging ]]; then log_prefix=deploy-staging; fi
LOG=$LOGS/$log_prefix-$(date -u +%Y%m%dT%H%M%SZ).log

# State read by the EXIT trap, hence global.
step='preparing'
locked=0
checked_out=0 pinned=0
prev_commit='' prev_tag='' prev_image='' digest_ref=''
prev_site_sha=unknown prev_site_image='' caddy_container=''

main() {
    die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }
    git_app() { git -c "safe.directory=$APP" -C "$APP" "$@"; }
    config_value() { sed -n "s/^$1=//p" "$CONFIG"; }
    # Runs through "trap on_exit EXIT" below; shellcheck does not follow that for a nested function.
    # shellcheck disable=SC2317,SC2329
    on_exit() {
        local rc=$?
        if [[ "$rc" != 0 && "$checked_out" = 1 && "$pinned" = 0 ]]; then
            git_app checkout --quiet --detach "$prev_commit" || printf 'deploy: could not restore checkout %s.\n' "$prev_commit" >&2
        fi
        if [[ "$locked" = 1 ]]; then rmdir "$LOGS/.deploy.lock"; fi
        if [[ "$rc" != 0 && "$target" = site ]]; then
            if [[ "$prev_site_sha" != unknown ]]; then printf 'deploy: rollback: site %s\n' "$prev_site_sha" >&2; fi
            printf 'deploy: FAILED at step "%s" (exit %s). Log: %s\n' "$step" "$rc" "$LOG" >&2
        elif [[ "$rc" != 0 && "$target" = staging ]]; then
            if [[ -n "$prev_commit" ]]; then printf 'deploy: rollback: staging %s (only if compatible with the migrated schema).\n' "$prev_commit" >&2; fi
            printf 'deploy: FAILED at step "%s" (exit %s). Log: %s\n' "$step" "$rc" "$LOG" >&2
        elif [[ "$rc" != 0 ]]; then
            printf 'deploy: no automatic rollback. Before this run: tag %s, commit %s, image %s.\n' \
                "${prev_tag:-unknown}" "${prev_commit:-unknown}" "${prev_image:-unknown}" >&2
            printf 'deploy: an older image is not a safe fallback once migrations ran; fix forward or restore the pre-deploy backup (#9, #76).\n' >&2
            printf 'deploy: FAILED at step "%s" (exit %s). Log: %s\n' "$step" "$rc" "$LOG" >&2
        fi
        exit "$rc"
    }
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # Replace VERWALINO_IMAGE in the installer config atomically; the file keeps mode 0600.
    pin_image() {
        local new="$1" tmp line seen=0 sha_seen=0
        mkdir "$CONFIG.lock" 2>/dev/null || die "another installer operation is running (or a stale $CONFIG.lock needs operator review)."
        tmp=$(mktemp "$(dirname "$CONFIG")/.env.deploy.XXXXXX") || { rmdir "$CONFIG.lock"; die 'cannot create a temporary file next to the config.'; }
        while IFS= read -r line || [[ -n "$line" ]]; do
            case "$line" in
                VERWALINO_IMAGE=*) printf 'VERWALINO_IMAGE=%s\n' "$new"; seen=$((seen + 1)) ;;
                HV_RELEASE_ID=*)
                    if [[ "$target" = staging ]]; then
                        printf 'HV_RELEASE_ID=%s\n' "$staging_sha"; sha_seen=$((sha_seen + 1))
                    else printf '%s\n' "$line"; fi ;;
                *) printf '%s\n' "$line" ;;
            esac
        done < "$CONFIG" > "$tmp"
        if [[ "$seen" != 1 ]]; then rm -f "$tmp"; rmdir "$CONFIG.lock"; die 'the config must contain exactly one VERWALINO_IMAGE line.'; fi
        if [[ "$target" = staging && "$sha_seen" != 1 ]]; then rm -f "$tmp"; rmdir "$CONFIG.lock"; die 'the staging config must contain exactly one HV_RELEASE_ID line.'; fi
        chmod 600 "$tmp"
        mv -f "$tmp" "$CONFIG"
        pinned=1
        rmdir "$CONFIG.lock"
    }

    staging_compose() {
        # Pinning through the environment takes precedence over the private env file.
        VERWALINO_IMAGE=$digest_ref HV_RELEASE_ID=$staging_sha docker compose \
            --project-name verwalino-staging --env-file "$CONFIG" \
            -f "$ROOT/staging/infra/docker-compose.staging.yml" --profile ops "$@"
    }

    deploy_staging() {
        local ref=$TAG staging_sha
        local permissions
        permissions=$(stat -c '%a' "$CONFIG" 2>/dev/null || stat -f '%Lp' "$CONFIG")
        [[ "$permissions" = 600 ]] || die 'the staging config must have permissions 0600.'
        prev_image=$(config_value VERWALINO_IMAGE)
        prev_commit=$(config_value HV_RELEASE_ID)
        printf 'previous staging sha: %s; image: %s\n' "${prev_commit:-none}" "${prev_image:-none}"
        step='check independent staging secrets'
        if [[ ! -f "$PRODUCTION_CONFIG" || -L "$PRODUCTION_CONFIG" || ! -r "$PRODUCTION_CONFIG" ]]; then
            printf 'deploy: cannot compare staging secrets: production config must be a readable regular file.\n' >&2
            exit 2
        fi
        local key secret production_key production_secret
        local secret_keys=(SESSION_SECRET MIGRATOR_PASSWORD APP_RW_PASSWORD APP_WORKER_PASSWORD APP_REVIEW_PASSWORD)
        for key in "${secret_keys[@]}"; do
            secret=$(config_value "$key")
            if [[ ! "$secret" =~ ^[a-f0-9]{64}$ ]]; then
                printf 'deploy: staging %s must contain one independent 64-character lowercase hex secret.\n' "$key" >&2
                exit 2
            fi
            for production_key in "${secret_keys[@]}"; do
                production_secret=$(sed -n "s/^$production_key=//p" "$PRODUCTION_CONFIG")
                if [[ "$secret" = "$production_secret" ]]; then
                    printf 'deploy: staging %s reuses a production secret; generate independent staging secrets.\n' "$key" >&2
                    exit 2
                fi
            done
        done
        step='resolve staging revision on main'
        git_app fetch --tags origin main
        if [[ "$TAG" =~ $TAG_RE ]]; then ref="refs/tags/$TAG"; fi
        staging_sha=$(git_app rev-parse --verify --quiet "$ref^{commit}") || die "staging revision $TAG does not exist."
        [[ "$staging_sha" =~ ^[a-f0-9]{40}$ ]] || die 'invalid staging commit SHA.'
        git_app merge-base --is-ancestor "$staging_sha" refs/remotes/origin/main || die "staging revision $TAG is not on main."
        step='export staging runtime files'
        mkdir -p "$ROOT/staging"
        git_app archive "$staging_sha" infra/docker-compose.staging.yml infra/db/prod-init.sh \
            infra/Caddyfile infra/Caddyfile.staging infra/Caddyfile.site | tar -x -C "$ROOT/staging"
        # Postgres runs init scripts as another uid; undo the deploy user's umask.
        chmod 755 "$ROOT/staging/infra/db" "$ROOT/staging/infra/db/prod-init.sh"
        if [[ -f "$ROOT/config/Caddyfile.hosted" ]]; then
            step='validate hosted proxy extension'
            validate_hosted_proxy "$ROOT/staging/infra"
        fi
        step='pull staging image'
        docker pull "$IMAGE_REPO:sha-$staging_sha"
        digest_ref=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE_REPO:sha-$staging_sha" | grep -m1 -E "$DIGEST_RE" || true)
        [[ -n "$digest_ref" ]] || die 'no registry digest for staging after the pull.'
        step='validate staging compose'
        staging_compose config -q
        step='staging database'
        staging_compose up -d --wait --wait-timeout 120 db
        step='staging migration'
        staging_compose stop staging-app
        staging_compose run --rm --no-deps -T migrate
        step='recreate staging app'
        staging_compose up -d --no-deps --force-recreate --wait --wait-timeout 120 staging-app
        step='staging health check'
        staging_compose exec -T staging-app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=3)"
        step='record successful staging image and sha'
        pin_image "$digest_ref"
        printf 'deploy staging %s OK: %s\n' "$staging_sha" "$digest_ref"
        exit 0
    }

    hosted_proxy() {
        # This operator-only opt-in survives the installer's stock Caddy reconciliation.
        # Never source a secret file as shell code. The installer already validated it.
        local key
        for key in VERWALINO_IMAGE VERWALINO_DOMAIN VERWALINO_PORT VERWALINO_PROXY_SUBNET \
                MIGRATOR_PASSWORD APP_RW_PASSWORD APP_WORKER_PASSWORD APP_REVIEW_PASSWORD SESSION_SECRET; do
            export "$key=$(config_value "$key")"
        done
        export VERWALINO_ALLOWED_HOSTS="$VERWALINO_DOMAIN,localhost,127.0.0.1"
        export VERWALINO_COOKIE_SECURE=true VERWALINO_COOKIE_NAME=__Host-hv_session
        VERWALINO_SITE_IMAGE=$prev_site_image hosted_compose up -d --no-deps caddy
    }

    hosted_app_compose() {
        local files=(-f "$APP/infra/docker-compose.yml" -f "$APP/infra/docker-compose.hosted-app.yml")
        local profiles=(--profile ops)
        local secure=false cookie=hv_session
        if [[ "$(config_value VERWALINO_MODE)" = https ]]; then
            profiles+=(--profile https)
            secure=true cookie=__Host-hv_session
        fi
        if [[ -f "$ROOT/config/Caddyfile.hosted" ]]; then
            files+=(-f "$APP/infra/docker-compose.hosted-proxy.yml" -f "$APP/infra/docker-compose.site.yml")
        fi
        HV_DEPLOYMENT_MODE=$app_mode HV_PUBLIC_HOST=$app_public_host HV_ALLOWED_HOSTS=$app_allowed_hosts \
        VERWALINO_COOKIE_SECURE=$secure VERWALINO_COOKIE_NAME=$cookie \
        VERWALINO_ALLOWED_HOSTS=$app_allowed_hosts VERWALINO_SITE_IMAGE=$prev_site_image \
        docker compose --project-name verwalino --env-file "$CONFIG" \
            "${files[@]}" "${profiles[@]}" "$@"
    }

    start_hosted_app() {
        # Managed server start: never briefly boot a hosted app with selfhost policy.
        # The portable installer validates its own config via status before checkout.
        local key line value inbox_container
        mkdir "$CONFIG.lock" 2>/dev/null || die "another installer operation is running (or a stale $CONFIG.lock needs operator review)."
        # Keep cleanup in a subshell so every failure releases the installer lock.
        (
            trap 'rmdir "$CONFIG.lock"' EXIT
            # Match the installer's private-file precedence, including optional mail.
            for key in HV_SMTP_HOST HV_SMTP_PORT HV_SMTP_FROM HV_SMTP_SECURITY HV_SMTP_USERNAME HV_SMTP_PASSWORD HV_SMTP_TIMEOUT_SECONDS; do
                export "$key="
            done
            while IFS= read -r line || [[ -n "$line" ]]; do
                case "$line" in ''|\#*) continue ;; esac
                key=${line%%=*}
                export "$key=${line#*=}"
            done < "$CONFIG"
            for key in HV_SMTP_HOST HV_SMTP_FROM; do
                value=${!key}
                value="${value#"${value%%[![:space:]]*}"}"
                value="${value%"${value##*[![:space:]]}"}"
                export "$key=$value"
            done
            hosted_app_compose config -q
            hosted_app_compose pull app
            hosted_app_compose up -d --wait --wait-timeout 120 db
            inbox_container=$(hosted_app_compose ps --all --quiet inbox-worker)
            if [[ -n "$inbox_container" ]]; then
                hosted_app_compose stop inbox-worker
            fi
            hosted_app_compose stop app
            hosted_app_compose run --rm --no-deps -T migrate
            hosted_app_compose up -d --no-deps --wait --wait-timeout 120 app
            if [[ "$(config_value VERWALINO_MODE)" = https ]]; then
                hosted_app_compose up -d --no-deps caddy
            fi
        )
    }

    validate_hosted_proxy() {
        local runtime=${1:-$APP/infra}
        # The stock binary's file capability must remain in the bounding set to exec.
        docker run --rm --network none --read-only --cap-drop ALL --cap-add NET_BIND_SERVICE --security-opt no-new-privileges \
            --env "VERWALINO_DOMAIN=$(sed -n 's/^VERWALINO_DOMAIN=//p' "$PRODUCTION_CONFIG")" \
            --mount "type=bind,src=$ROOT/config/Caddyfile.hosted,dst=/etc/caddy/Caddyfile,readonly" \
            --mount "type=bind,src=$runtime/Caddyfile,dst=/etc/caddy/Caddyfile.production,readonly" \
            --mount "type=bind,src=$runtime/Caddyfile.staging,dst=/etc/caddy/Caddyfile.staging,readonly" \
            --mount "type=bind,src=$runtime/Caddyfile.site,dst=/etc/caddy/Caddyfile.site,readonly" \
            caddy:2.10.2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
    }

    # Polls the loopback listener the installer publishes; true as soon as the app is ready.
    wait_ready() {
        local port url attempt
        port=$(config_value VERWALINO_PORT)
        [[ "$port" =~ ^[0-9]{4,5}$ ]] || die 'VERWALINO_PORT is missing from the config.'
        url=http://127.0.0.1:$port/api/health/ready
        for ((attempt = 1; attempt <= READY_ATTEMPTS; attempt++)); do
            if curl --fail --silent --max-time 2 --output /dev/null "$url"; then
                echo "app ready after $attempt check(s): $url"
                return 0
            fi
            if [[ "$attempt" -lt "$READY_ATTEMPTS" ]]; then sleep 3; fi
        done
        return 1
    }

    # Explicit files/project; never inherit a caller's Compose routing. Secret values
    # stay in the existing private env file and are not passed into the site container.
    hosted_compose() {
        VERWALINO_COOKIE_SECURE=true VERWALINO_COOKIE_NAME=__Host-hv_session \
        VERWALINO_ALLOWED_HOSTS="$(config_value VERWALINO_DOMAIN),localhost,127.0.0.1" \
        docker compose --project-name verwalino --env-file "$CONFIG" \
            -f "$APP/infra/docker-compose.yml" -f "$APP/infra/docker-compose.hosted-proxy.yml" \
            -f "$APP/infra/docker-compose.site.yml" \
            --profile https "$@"
    }
    site_caddy() {
        caddy_container=$(docker ps --quiet --filter label=com.docker.compose.project=verwalino \
            --filter label=com.docker.compose.service=caddy)
        [[ -n "$caddy_container" && "$caddy_container" != *$'\n'* ]] || return 1
        [[ "$(docker inspect --format '{{with index .NetworkSettings.Networks "verwalino_site"}}enabled{{end}}' "$caddy_container")" = enabled ]]
    }
    previous_site() {
        local container sha
        container=$(docker ps --all --quiet --filter label=com.docker.compose.project=verwalino \
            --filter label=com.docker.compose.service=site)
        [[ -n "$container" && "$container" != *$'\n'* ]] || return 0
        prev_site_image=$(docker inspect --format '{{.Config.Image}}' "$container")
        sha=${prev_site_image#"$SITE_REPO:"}; sha=${sha%%@*}
        if [[ "$prev_site_image" = "$SITE_REPO:"* && "$sha" =~ $SHA_RE ]]; then prev_site_sha=$sha; fi
    }
    wait_site() {
        local attempt response status
        for ((attempt = 1; attempt <= READY_ATTEMPTS; attempt++)); do
            if response=$(docker exec "$caddy_container" wget -S -O /dev/null -T 2 http://site:8080/ 2>&1); then
                status=$(printf '%s\n' "$response" | sed -n 's/^  HTTP\/[^ ]* \([0-9]*\).*/\1/p' | head -n 1)
                if [[ "$status" = 200 ]]; then
                    printf 'site ready after %s check(s): HTTP 200 on / via Caddy network\n' "$attempt"
                    return 0
                fi
            fi
            if [[ "$attempt" -lt "$READY_ATTEMPTS" ]]; then sleep 3; fi
        done
        return 1
    }
    deploy_site() {
        [[ -f "$CONFIG" && ! -L "$CONFIG" && -r "$CONFIG" ]] || die 'the installer config must be a readable regular file.'
        if ! { [[ -f "$ROOT/config/Caddyfile.hosted" && "$(config_value VERWALINO_MODE)" = https ]] && site_caddy; }; then
            die 'site extension is not enabled; see docs/operations/website-hosting.md.'
        fi
        previous_site
        printf 'previous site sha: %s\n' "$prev_site_sha"
        if [[ "$prev_site_sha" != unknown ]]; then printf 'rollback: site %s\n' "$prev_site_sha"; fi

        step='pull the site image'
        docker pull "$SITE_REPO:$TAG"
        digest_ref=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$SITE_REPO:$TAG" | grep -m1 -E "$SITE_DIGEST_RE" || true)
        [[ -n "$digest_ref" ]] || die "no registry digest for $SITE_REPO:$TAG after the pull."
        echo "site image $digest_ref"
        # Keep the commit in Config.Image for rollback discovery; pin recreation by digest.
        export VERWALINO_SITE_IMAGE="$SITE_REPO:$TAG@${digest_ref#*@}"
        step='recreate only site'
        hosted_compose up -d --no-deps --force-recreate --pull never site
        step='site health check'
        wait_site || die 'site did not answer HTTP 200 within about 120 s.'
        step='done'
        printf 'deploy site %s OK: %s\n' "$TAG" "$digest_ref"
    }

    printf 'deploy %s started %s by %s; log %s\n' "$TAG" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(id -un)" "$LOG"
    step='preconditions'
    mkdir "$LOGS/.deploy.lock" 2>/dev/null || die "another deploy is running (or a stale $LOGS/.deploy.lock needs operator review)."
    locked=1
    command -v docker >/dev/null || die 'docker is required.'
    # Do not inherit caller-selected Compose routing for any target.
    unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_ENV_FILES
    if [[ "$target" = site ]]; then deploy_site; exit 0; fi
    command -v git >/dev/null || die 'git is required.'
    command -v curl >/dev/null || die 'curl is required.'
    [[ -f "$CONFIG" && ! -L "$CONFIG" && -r "$CONFIG" && -w "$CONFIG" && -w "$(dirname "$CONFIG")" ]] \
        || die "the installer config $CONFIG must be a regular file this user can read and write, in a directory it can write to."
    if [[ "$target" = staging ]]; then deploy_staging; fi
    if [[ "$app_overlay" = 1 ]]; then
        "$APP/scripts/self-host.sh" status >/dev/null
    fi
    if [[ -f "$ROOT/config/Caddyfile.hosted" ]]; then
        if ! docker network inspect verwalino-staging_proxy >/dev/null 2>&1; then
            printf 'deploy: external network verwalino-staging_proxy is required; create it with docker network create --subnet <dedicated-staging-proxy-subnet> verwalino-staging_proxy (use the unused subnet from .env.staging).\n' >&2
            exit 2
        fi
        previous_site
        [[ -n "$prev_site_image" ]] || die 'cannot recover the existing site image for Caddy restoration.'
    fi
    prev_image=$(config_value VERWALINO_IMAGE)
    prev_commit=$(git_app rev-parse --short HEAD)
    prev_tag=$(git_app describe --tags --exact-match 2>/dev/null || echo none)
    [[ -z "$(git_app status --porcelain --untracked-files=no)" ]] || die "the checkout $APP has local changes."

    step='fetch and check out the tag'
    git_app fetch --tags origin
    git_app rev-parse --verify --quiet "refs/tags/$TAG^{commit}" >/dev/null || die "tag $TAG does not exist in the repository."
    git_app merge-base --is-ancestor "refs/tags/$TAG^{commit}" refs/remotes/origin/main || die "tag $TAG is not on main."
    git_app checkout --quiet --detach "refs/tags/$TAG"
    checked_out=1

    # Fail before pulling, backup or installer reconciliation can change the running stack.
    if [[ -f "$ROOT/config/Caddyfile.hosted" ]]; then
        step='validate hosted proxy extension'
        validate_hosted_proxy
    fi

    step='pull the image'
    docker pull "$IMAGE_REPO:$TAG"
    digest_ref=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE_REPO:$TAG" | grep -m1 -E "$DIGEST_RE" || true)
    [[ -n "$digest_ref" ]] || die "no registry digest for $IMAGE_REPO:$TAG after the pull."
    echo "image $digest_ref"

    step='backup'
    # TODO(#57, #76): server-backup.sh is a minimal stand-in for the installer's backup command.
    "$APP/scripts/server-backup.sh" "pre-$TAG"

    step='pin the image digest'
    pin_image "$digest_ref"

    step='install (pull, migrate, restart)'
    # An empty database makes the installer print a one-time setup token; it must not reach logs.
    if [[ "$app_overlay" = 1 ]]; then
        start_hosted_app
    else
        "$APP/scripts/self-host.sh" start 2>&1 | sed -E 's/^(Setup token: )[a-f0-9]{64}$/\1[hidden by deploy-server.sh]/'
    fi

    if [[ "$app_overlay" = 0 && -f "$ROOT/config/Caddyfile.hosted" ]]; then
        step='restore hosted proxy extension'
        hosted_proxy
    fi
    step='health check'
    if ! wait_ready; then
        echo "app did not become ready within about 120 s; last 100 lines of its log:" >&2
        app_container=$(docker ps --all --quiet --filter label=com.docker.compose.project=verwalino --filter label=com.docker.compose.service=app | head -n 1)
        if [[ -n "$app_container" ]]; then docker logs --tail 100 "$app_container" 2>&1 || true; else echo 'no app container found' >&2; fi
        exit 1
    fi

    step='done'
    printf 'deploy %s OK: %s\n' "$TAG" "$digest_ref"
    exit 0  # an explicit exit makes bash 3.2 run the EXIT trap of this pipeline subshell too
}

# Drain the relay after stdout closes so tee can finish the log and main can run its EXIT trap.
# One line on purpose: bash has parsed all of it before git checks out a different copy of this file.
main 2>&1 | tee -a "$LOG" | { cat 2>/dev/null || cat >/dev/null; }; exit "${PIPESTATUS[0]}"
