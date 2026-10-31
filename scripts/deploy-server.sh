#!/usr/bin/env bash
# Deploys one release tag on the hosted server. Runs on the server, from /srv/verwalino/app,
# as the server's admin user, entered through a restricted CI key; no sudo is needed.
#
# Usage: deploy-server.sh vX.Y.Z | staging <40-character-sha|vX.Y.Z>
#
# The GitHub workflow .github/workflows/deploy.yaml runs "ssh ADMIN_USER@host vX.Y.Z". The CI
# key in that user's authorized_keys is restricted to
#   command="/srv/verwalino/app/scripts/deploy-server.sh $SSH_ORIGINAL_COMMAND",restrict
# so the login shell hands the client's text to this script as arguments, unquoted. The
# strict grammar below is therefore the whole input validation. The staging target
# exports only its runtime files and never checks out or starts the production stack.
#
# Steps: fetch tags and check out the tag -> pull ghcr.io/itskeks/verwalino:TAG and resolve
# its digest -> backup (server-backup.sh) -> pin the digest in the installer config ->
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
LOGS=$ROOT/logs
IMAGE_REPO=ghcr.io/itskeks/verwalino
DIGEST_RE='^ghcr\.io/itskeks/verwalino@sha256:[a-f0-9]{64}$'
TAG_RE='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
READY_ATTEMPTS=24   # 24 checks, 2 s timeout plus 3 s pause each: at most 117 s
export VERWALINO_SERVER_ROOT=$ROOT VERWALINO_CONFIG=$CONFIG

target=release
if [[ $# -eq 1 && "$1" =~ $TAG_RE ]]; then
    TAG=$1
elif [[ $# -eq 2 && "$1" = staging && ( "$2" =~ ^[a-f0-9]{40}$ || "$2" =~ $TAG_RE ) ]]; then
    target=staging
    TAG=$2
    CONFIG=$ROOT/config/.env.staging
    export VERWALINO_CONFIG=$CONFIG
else
    echo 'Usage: deploy-server.sh vX.Y.Z | staging <40-character-sha|vX.Y.Z>' >&2
    exit 2
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

main() {
    die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }
    git_app() { git -c "safe.directory=$APP" -C "$APP" "$@"; }
    config_value() { sed -n "s/^$1=//p" "$CONFIG"; }
    # Runs through "trap on_exit EXIT" below; shellcheck does not follow that for a nested function.
    # shellcheck disable=SC2317
    on_exit() {
        local rc=$?
        if [[ "$rc" != 0 && "$checked_out" = 1 && "$pinned" = 0 ]]; then
            git_app checkout --quiet --detach "$prev_commit" || printf 'deploy: could not restore checkout %s.\n' "$prev_commit" >&2
        fi
        if [[ "$locked" = 1 ]]; then rmdir "$LOGS/.deploy.lock"; fi
        if [[ "$rc" != 0 ]]; then
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
        step='resolve staging revision on main'
        git_app fetch --tags origin main
        if [[ "$TAG" =~ $TAG_RE ]]; then ref="refs/tags/$TAG"; fi
        staging_sha=$(git_app rev-parse --verify --quiet "$ref^{commit}") || die "staging revision $TAG does not exist."
        [[ "$staging_sha" =~ ^[a-f0-9]{40}$ ]] || die 'invalid staging commit SHA.'
        git_app merge-base --is-ancestor "$staging_sha" refs/remotes/origin/main || die "staging revision $TAG is not on main."
        step='pull staging image'
        docker pull "$IMAGE_REPO:sha-$staging_sha"
        digest_ref=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE_REPO:sha-$staging_sha" | grep -m1 -E "$DIGEST_RE" || true)
        [[ -n "$digest_ref" ]] || die 'no registry digest for staging after the pull.'
        step='export staging runtime files'
        mkdir -p "$ROOT/staging"
        git_app archive "$staging_sha" infra/docker-compose.staging.yml infra/db/prod-init.sh | tar -x -C "$ROOT/staging"
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
        docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
            --env "VERWALINO_DOMAIN=$VERWALINO_DOMAIN" \
            --mount "type=bind,src=$ROOT/config/Caddyfile.hosted,dst=/etc/caddy/Caddyfile,readonly" \
            --mount "type=bind,src=$APP/infra/Caddyfile,dst=/etc/caddy/Caddyfile.production,readonly" \
            --mount "type=bind,src=$APP/infra/Caddyfile.staging,dst=/etc/caddy/Caddyfile.staging,readonly" \
            caddy:2.10.2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
        docker compose --project-name verwalino --env-file "$CONFIG" \
            -f "$APP/infra/docker-compose.yml" -f "$APP/infra/docker-compose.hosted-proxy.yml" \
            --profile https up -d --no-deps caddy
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

    printf 'deploy %s started %s by %s; log %s\n' "$TAG" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(id -un)" "$LOG"
    step='preconditions'
    mkdir "$LOGS/.deploy.lock" 2>/dev/null || die "another deploy is running (or a stale $LOGS/.deploy.lock needs operator review)."
    locked=1
    command -v docker >/dev/null || die 'docker is required.'
    command -v git >/dev/null || die 'git is required.'
    command -v curl >/dev/null || die 'curl is required.'
    [[ -f "$CONFIG" && ! -L "$CONFIG" && -r "$CONFIG" && -w "$CONFIG" && -w "$(dirname "$CONFIG")" ]] \
        || die "the installer config $CONFIG must be a regular file this user can read and write, in a directory it can write to."
    # Do not inherit caller-selected projects/profiles or a production image override.
    unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_ENV_FILES
    if [[ "$target" = staging ]]; then deploy_staging; fi
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
    "$APP/scripts/self-host.sh" start 2>&1 | sed -E 's/^(Setup token: )[a-f0-9]{64}$/\1[hidden by deploy-server.sh]/'

    if [[ -f "$ROOT/config/Caddyfile.hosted" ]]; then
        step='validate and restore hosted proxy extension'
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
