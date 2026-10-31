#!/usr/bin/env bash
# Deploys one app release or static-site commit on the hosted server, from /srv/verwalino/app,
# as the server's admin user, entered through a restricted CI key; no sudo is needed.
#
# Usage: deploy-server.sh vX.Y.Z | deploy-server.sh site <40-lowercase-hex-sha>
#
# The GitHub workflow .github/workflows/deploy.yaml runs "ssh ADMIN_USER@host vX.Y.Z". The CI
# key in that user's authorized_keys is restricted to
#   command="/srv/verwalino/app/scripts/deploy-server.sh $SSH_ORIGINAL_COMMAND",restrict
# so the login shell hands the client's text to this script as arguments, unquoted. The
# strict grammar below is therefore the whole input validation. Exactly one release tag
# or exactly "site" and one commit SHA is accepted; other shapes exit 2 before any work.
# Site deploys pull and resolve a registry digest, recreate ONLY site, and poll HTTP 200
# from Caddy over the internal site network. No checkout, backup, migration or config write.
# The previous site's SHA is printed for an explicit "site <previous-sha>" rollback.
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
SHA_RE='^[a-f0-9]{40}$'
SITE_REPO=ghcr.io/itskeks/verwalino-site
SITE_DIGEST_RE='^ghcr\.io/itskeks/verwalino-site@sha256:[a-f0-9]{64}$'
READY_ATTEMPTS=24   # 24 checks, 2 s timeout plus 3 s pause each: at most 117 s
export VERWALINO_SERVER_ROOT=$ROOT VERWALINO_CONFIG=$CONFIG

kind=app
if [[ $# -eq 1 && "$1" =~ $TAG_RE ]]; then
    TAG=$1
elif [[ $# -eq 2 && "$1" = site && "$2" =~ $SHA_RE ]]; then
    kind=site
    TAG=$2
else
    echo 'Usage: deploy-server.sh vX.Y.Z | deploy-server.sh site <40-lowercase-hex-sha>' >&2
    exit 2
fi
[[ -d "$LOGS" && -w "$LOGS" ]] || { echo "deploy: $LOGS is missing or not writable." >&2; exit 1; }
chmod 750 "$LOGS"
LOG=$LOGS/deploy-$(date -u +%Y%m%dT%H%M%SZ).log

# State read by the EXIT trap, hence global.
step='preparing'
locked=0
checked_out=0 pinned=0
prev_commit='' prev_tag='' prev_image='' digest_ref=''
prev_site_sha=unknown prev_site_image='' caddy_container=''
site_was_enabled=0

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
        if [[ "$rc" != 0 && "$kind" = site ]]; then
            if [[ "$prev_site_sha" != unknown ]]; then printf 'deploy: rollback: site %s\n' "$prev_site_sha" >&2; fi
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
        local new="$1" tmp line seen=0
        mkdir "$CONFIG.lock" 2>/dev/null || die "another installer operation is running (or a stale $CONFIG.lock needs operator review)."
        tmp=$(mktemp "$(dirname "$CONFIG")/.env.deploy.XXXXXX") || { rmdir "$CONFIG.lock"; die 'cannot create a temporary file next to the config.'; }
        while IFS= read -r line || [[ -n "$line" ]]; do
            case "$line" in
                VERWALINO_IMAGE=*) printf 'VERWALINO_IMAGE=%s\n' "$new"; seen=$((seen + 1)) ;;
                *) printf '%s\n' "$line" ;;
            esac
        done < "$CONFIG" > "$tmp"
        if [[ "$seen" != 1 ]]; then rm -f "$tmp"; rmdir "$CONFIG.lock"; die 'the config must contain exactly one VERWALINO_IMAGE line.'; fi
        chmod 600 "$tmp"
        mv -f "$tmp" "$CONFIG"
        pinned=1
        rmdir "$CONFIG.lock"
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
    site_compose() {
        VERWALINO_COOKIE_SECURE=true VERWALINO_COOKIE_NAME=__Host-hv_session \
        VERWALINO_ALLOWED_HOSTS="$(config_value VERWALINO_DOMAIN),localhost,127.0.0.1" \
        docker compose --project-name verwalino --env-file "$CONFIG" \
            -f "$APP/infra/docker-compose.yml" -f "$APP/infra/docker-compose.site.yml" \
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
        site_compose up -d --no-deps --force-recreate --pull never site
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
    if [[ "$kind" = site ]]; then deploy_site; exit 0; fi
    command -v git >/dev/null || die 'git is required.'
    command -v curl >/dev/null || die 'curl is required.'
    [[ -f "$CONFIG" && ! -L "$CONFIG" && -r "$CONFIG" && -w "$CONFIG" && -w "$(dirname "$CONFIG")" ]] \
        || die "the installer config $CONFIG must be a regular file this user can read and write, in a directory it can write to."
    if [[ -f "$ROOT/config/Caddyfile.hosted" ]] && site_caddy; then
        site_was_enabled=1
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

    # The portable installer deliberately knows nothing about the hosted extension.
    if [[ "$site_was_enabled" = 1 ]]; then
        step='restore hosted Caddy extension'
        VERWALINO_SITE_IMAGE=$prev_site_image site_compose up -d --no-deps caddy
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
