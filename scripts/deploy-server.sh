#!/usr/bin/env bash
# Deploys one release tag on the hosted server. Runs on the server, from /srv/verwalino/app,
# as the server's admin user, entered through a restricted CI key; no sudo is needed.
#
# Usage: deploy-server.sh vX.Y.Z      exactly one argument
#
# The GitHub workflow .github/workflows/deploy.yaml runs "ssh ADMIN_USER@host vX.Y.Z". The CI
# key in that user's authorized_keys is restricted to
#   command="/srv/verwalino/app/scripts/deploy-server.sh $SSH_ORIGINAL_COMMAND",restrict
# so the login shell hands the client's text to this script as arguments, unquoted. The
# strict tag check below is therefore the whole input validation: anything with spaces,
# separators or a different shape is a usage error (exit 2) before anything else happens.
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
export LC_ALL=C
umask 002

ROOT=${VERWALINO_SERVER_ROOT:-/srv/verwalino}
APP=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG=${VERWALINO_CONFIG:-$ROOT/config/.env.selfhost}
LOGS=$ROOT/logs
IMAGE_REPO=ghcr.io/itskeks/verwalino
DIGEST_RE='^ghcr\.io/itskeks/verwalino@sha256:[a-f0-9]{64}$'
TAG_RE='^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$'
READY_ATTEMPTS=24   # 24 checks, 2 s timeout plus 3 s pause each: at most 117 s
export VERWALINO_SERVER_ROOT=$ROOT VERWALINO_CONFIG=$CONFIG

[[ $# -eq 1 && "$1" =~ $TAG_RE ]] || { echo 'Usage: deploy-server.sh vX.Y.Z (one release tag)' >&2; exit 2; }
TAG=$1
[[ -d "$LOGS" && -w "$LOGS" ]] || { echo "deploy: $LOGS is missing or not writable." >&2; exit 1; }
LOG=$LOGS/deploy-$(date -u +%Y%m%dT%H%M%SZ).log

# State read by the EXIT trap, hence global.
step='preparing'
locked=0
prev_commit='' prev_tag='' prev_image='' digest_ref=''

main() {
    die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }
    git_app() { git -c "safe.directory=$APP" -C "$APP" "$@"; }
    config_value() { sed -n "s/^$1=//p" "$CONFIG"; }
    on_exit() {
        local rc=$?
        if [[ "$locked" = 1 ]]; then rmdir "$LOGS/.deploy.lock"; fi
        if [[ "$rc" != 0 ]]; then
            printf 'deploy: FAILED at step "%s" (exit %s). Log: %s\n' "$step" "$rc" "$LOG" >&2
            printf 'deploy: no automatic rollback. Before this run: tag %s, commit %s, image %s.\n' \
                "${prev_tag:-unknown}" "${prev_commit:-unknown}" "${prev_image:-unknown}" >&2
            printf 'deploy: an older image is not a safe fallback once migrations ran; fix forward or restore the pre-deploy backup (#9, #76).\n' >&2
        fi
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

    printf 'deploy %s started %s by %s; log %s\n' "$TAG" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(id -un)" "$LOG"
    step='preconditions'
    mkdir "$LOGS/.deploy.lock" 2>/dev/null || die "another deploy is running (or a stale $LOGS/.deploy.lock needs operator review)."
    locked=1
    command -v docker >/dev/null || die 'docker is required.'
    command -v git >/dev/null || die 'git is required.'
    command -v curl >/dev/null || die 'curl is required.'
    [[ -f "$CONFIG" && ! -L "$CONFIG" && -r "$CONFIG" && -w "$CONFIG" && -w "$(dirname "$CONFIG")" ]] \
        || die "the installer config $CONFIG must be a regular file this user can read and write, in a directory it can write to."
    prev_image=$(config_value VERWALINO_IMAGE)
    prev_commit=$(git_app rev-parse --short HEAD)
    prev_tag=$(git_app describe --tags --exact-match 2>/dev/null || echo none)
    [[ -z "$(git_app status --porcelain --untracked-files=no)" ]] || die "the checkout $APP has local changes."

    step='fetch and check out the tag'
    git_app fetch --tags origin
    git_app rev-parse --verify --quiet "refs/tags/$TAG^{commit}" >/dev/null || die "tag $TAG does not exist in the repository."
    git_app merge-base --is-ancestor "refs/tags/$TAG^{commit}" refs/remotes/origin/main || die "tag $TAG is not on main."
    git_app checkout --quiet --detach "refs/tags/$TAG"

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

# One line on purpose: bash has parsed all of it before git checks out a different copy of this file.
main 2>&1 | tee -a "$LOG"; exit "${PIPESTATUS[0]}"
