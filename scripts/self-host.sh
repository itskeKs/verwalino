#!/usr/bin/env bash
# Portable first installation. Never source the configuration as shell code.
set -euo pipefail
export LC_ALL=C
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${VERWALINO_CONFIG:-$ROOT/.env.selfhost}"
[[ "$CONFIG" = /* ]] || CONFIG="$PWD/$CONFIG"
TMP_DIR=""
LOCKED=0

fail() { printf '%s\n' "$1" >&2; exit 1; }
cleanup() {
    [[ -z "$TMP_DIR" ]] || rm -rf "$TMP_DIR"
    if [[ "$LOCKED" = 1 ]]; then rmdir "$CONFIG.lock"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "${CONFIG##*/}" =~ ^\.env\.[a-zA-Z0-9._-]+$ ]] || fail 'Configuration filename must start with .env. (kept out of Git and image builds).'

usage() {
    cat <<'HELP'
Usage: bash scripts/self-host.sh COMMAND
  init [--https DOMAIN] [--port PORT] [--image REGISTRY/IMAGE@sha256:DIGEST]
       [--proxy-subnet PRIVATE_IPV4_CIDR]
  start                         Build/pull, migrate, then start the application
  admin --org NAME --email EMAIL [--name NAME] [--password-stdin]
  status                        Show this installation's containers
  stop                          Stop containers without deleting data
Default: local HTTP on 127.0.0.1:18080; no AI, SMTP or extraction worker.
VERWALINO_CONFIG selects the private config file (default: .env.selfhost).
No automatic update, downgrade or volume deletion is provided.
HELP
}

validate_port() {
    [[ "$1" =~ ^[1-9][0-9]{3,4}$ ]] && (( 10#$1 >= 1024 && 10#$1 <= 65535 ))
}
validate_domain() {
    local domain="$1" label
    [[ ${#domain} -le 253 && "$domain" =~ ^[a-z0-9.-]+$ && "$domain" = *.* && "$domain" != *..* ]] || return 1
    [[ "${domain##*.}" =~ [a-z] ]] || return 1
    local labels
    IFS=. read -r -a labels <<< "$domain"
    for label in "${labels[@]}"; do
        [[ ${#label} -le 63 && "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || return 1
    done
    [[ "$domain" != *. ]]
}
validate_image() {
    [[ "$1" = verwalino:local || "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[a-f0-9]{64}$ ]]
}
validate_subnet() {
    local subnet="$1" address prefix octet number=0
    [[ "$subnet" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/(2[4-8])$ ]] || return 1
    address="${subnet%/*}"; prefix="${subnet#*/}"
    local octets
    IFS=. read -r -a octets <<< "$address"
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] && (( 10#$octet <= 255 )) || return 1
        number=$(( (number << 8) + 10#$octet ))
    done
    # Only aligned private subnets may become trusted proxy networks.
    (( (number & ((1 << (32-prefix)) - 1)) == 0 )) || return 1
    (( (number >> 24) == 10 || (number >> 20) == 2753 || (number >> 16) == 49320 ))
}
validate_settings() {
    validate_port "$VERWALINO_PORT" || fail 'Port must be between 1024 and 65535.'
    validate_image "$VERWALINO_IMAGE" || fail 'Use the local build or an explicit image SHA-256 digest.'
    validate_subnet "$VERWALINO_PROXY_SUBNET" || fail 'Proxy subnet must be an aligned private IPv4 /24 through /28.'
    case "$VERWALINO_MODE" in
        local) [[ -z "$VERWALINO_DOMAIN" ]] || fail 'Local mode must not set a domain.' ;;
        https) validate_domain "$VERWALINO_DOMAIN" || fail 'HTTPS requires a lowercase DNS hostname, without scheme or port.' ;;
        *) fail 'Unknown installation mode.' ;;
    esac
}
lock_config() {
    mkdir "$CONFIG.lock" 2>/dev/null || fail 'Another operation is running (or a stale .lock directory needs operator review).'
    LOCKED=1
}
load_config() {
    [[ ! -L "$CONFIG" && -f "$CONFIG" ]] || fail 'Run init first; symlink configurations are refused.'
    local permissions line key value seen='|'
    permissions=$(stat -c '%a' "$CONFIG" 2>/dev/null || stat -f '%Lp' "$CONFIG")
    [[ "$permissions" = 600 ]] || fail 'Configuration must have permissions 0600.'
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in ''|\#*) continue ;; esac
        [[ "$line" = *=* ]] || fail 'Invalid configuration line.'
        key="${line%%=*}"; value="${line#*=}"
        case "$key" in
            VERWALINO_MODE|VERWALINO_DOMAIN|VERWALINO_PORT|VERWALINO_IMAGE|VERWALINO_PROXY_SUBNET|MIGRATOR_PASSWORD|APP_RW_PASSWORD|APP_WORKER_PASSWORD|APP_REVIEW_PASSWORD|SESSION_SECRET) ;;
            *) fail 'Unknown configuration key.' ;;
        esac
        [[ "$seen" != *"|$key|"* ]] || fail 'Duplicate configuration key.'
        seen="$seen$key|"
        export "$key=$value"
    done < "$CONFIG"
    for key in VERWALINO_MODE VERWALINO_DOMAIN VERWALINO_PORT VERWALINO_IMAGE VERWALINO_PROXY_SUBNET MIGRATOR_PASSWORD APP_RW_PASSWORD APP_WORKER_PASSWORD APP_REVIEW_PASSWORD SESSION_SECRET; do
        [[ "$seen" = *"|$key|"* ]] || fail 'Incomplete configuration; restore the original file, do not regenerate secrets.'
    done
    for key in MIGRATOR_PASSWORD APP_RW_PASSWORD APP_WORKER_PASSWORD APP_REVIEW_PASSWORD SESSION_SECRET; do
        [[ "${!key}" =~ ^[a-f0-9]{64}$ ]] || fail 'Secrets must be 64 lowercase hexadecimal characters.'
    done
    validate_settings
    export VERWALINO_ALLOWED_HOSTS='localhost,127.0.0.1'
    export VERWALINO_COOKIE_SECURE=false VERWALINO_COOKIE_NAME=hv_session
    if [[ "$VERWALINO_MODE" = https ]]; then
        export VERWALINO_ALLOWED_HOSTS="$VERWALINO_DOMAIN,localhost,127.0.0.1"
        export VERWALINO_COOKIE_SECURE=true VERWALINO_COOKIE_NAME=__Host-hv_session
    fi
    # Explicit files/projects and profiles must not inherit caller overrides.
    unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_ENV_FILES
}
compose() {
    local profiles=(--profile ops)
    if [[ "$VERWALINO_MODE" = https ]]; then profiles+=(--profile https); fi
    docker compose --project-name verwalino --env-file "$CONFIG" \
        -f "$ROOT/infra/docker-compose.yml" "${profiles[@]}" "$@"
}

init_config() {
    local explicit="$#" option key
    VERWALINO_MODE=local VERWALINO_DOMAIN='' VERWALINO_PORT=18080
    VERWALINO_IMAGE=verwalino:local VERWALINO_PROXY_SUBNET=172.30.97.0/28
    while [[ "$#" -gt 0 ]]; do
        option="$1"
        [[ "$#" -ge 2 ]] || fail 'Missing init option value.'
        case "$option" in
            --https) VERWALINO_MODE=https; VERWALINO_DOMAIN="$2" ;;
            --port) VERWALINO_PORT="$2" ;;
            --image) VERWALINO_IMAGE="$2" ;;
            --proxy-subnet) VERWALINO_PROXY_SUBNET="$2" ;;
            *) fail 'Unknown init option.' ;;
        esac
        shift 2
    done
    validate_settings
    [[ ! -L "$CONFIG" ]] || fail 'Symlink configurations are refused.'
    if [[ -e "$CONFIG" ]]; then
        [[ "$explicit" = 0 ]] || fail 'Configuration exists; init never reconfigures an installation.'
        load_config
        printf 'Existing configuration kept: %s\n' "$CONFIG"
        return
    fi
    command -v openssl >/dev/null || fail 'OpenSSL is required to generate secrets.'
    [[ -d "$(dirname "$CONFIG")" ]] || fail 'Configuration parent directory must already exist.'
    umask 077
    lock_config
    TMP_DIR=$(mktemp -d "$(dirname "$CONFIG")/.env.verwalino-init.XXXXXX")
    {
        printf '# Verwalino private installation configuration. Back up with your data.\n'
        for key in VERWALINO_MODE VERWALINO_DOMAIN VERWALINO_PORT VERWALINO_IMAGE VERWALINO_PROXY_SUBNET; do
            printf '%s=%s\n' "$key" "${!key}"
        done
        for key in MIGRATOR_PASSWORD APP_RW_PASSWORD APP_WORKER_PASSWORD APP_REVIEW_PASSWORD SESSION_SECRET; do
            value=$(openssl rand -hex 32) || fail 'Secret generation failed.'
            [[ "$value" =~ ^[a-f0-9]{64}$ ]] || fail 'Invalid random secret.'
            printf '%s=%s\n' "$key" "$value"
        done
    } > "$TMP_DIR/config"
    # Hard-link publication fails atomically if another file appeared meanwhile.
    ln "$TMP_DIR/config" "$CONFIG" || fail 'Configuration appeared concurrently; nothing overwritten.'
    printf 'Configuration created: %s\nNext: bash scripts/self-host.sh start\n' "$CONFIG"
}

admin() {
    local org='' email='' name=Administrator from_stdin=0 password confirmation
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --org|--email|--name)
                [[ "$#" -ge 2 && -n "$2" ]] || fail 'Missing admin option value.'
                case "$1" in --org) org="$2" ;; --email) email="$2" ;; --name) name="$2" ;; esac
                shift 2 ;;
            --password-stdin) from_stdin=1; shift ;;
            *) fail 'Unknown admin option; passwords are accepted only via terminal or stdin.' ;;
        esac
    done
    [[ -n "$org" && -n "$email" ]] || fail 'Admin requires --org and --email.'
    if [[ "$from_stdin" = 1 ]]; then
        IFS= read -r password || fail 'Password stdin requires one newline-terminated line.'
    else
        [[ -t 0 ]] || fail 'For non-interactive bootstrap use --password-stdin.'
        IFS= read -r -s -p 'Admin password (12-128 characters): ' password
        printf '\n' >&2
        IFS= read -r -s -p 'Repeat password: ' confirmation
        printf '\n' >&2
        [[ "$password" = "$confirmation" ]] || fail 'Passwords do not match.'
    fi
    # These are byte bounds; the CLI enforces the exact Unicode character policy.
    [[ ${#password} -ge 12 && ${#password} -le 512 ]] || fail 'Admin password must contain 12-128 characters.'
    lock_config
    printf '%s\n' "$password" | compose run --rm --no-deps -T bootstrap \
        --org "$org" --email "$email" --name "$name" --password-stdin
}

command="${1:-help}"
[[ "$#" = 0 ]] || shift
case "$command" in
    help|--help|-h) usage; exit 0 ;;
    init) init_config "$@"; exit 0 ;;
    start|status|stop) [[ "$#" = 0 ]] || fail 'This command accepts no additional arguments.' ;;
    admin) ;;
    *) usage >&2; fail 'Unknown command.' ;;
esac
load_config
command -v docker >/dev/null || fail 'Docker with Compose v2 is required.'
case "$command" in
    start)
        lock_config
        if [[ "$VERWALINO_IMAGE" = verwalino:local ]]; then
            compose build app
        else
            compose pull app
        fi
        compose up -d --wait --wait-timeout 120 db
        # Do not run migrations alongside an already running web writer.
        compose stop app
        compose run --rm --no-deps -T migrate
        compose up -d --no-deps --wait --wait-timeout 120 app
        if [[ "$VERWALINO_MODE" = https ]]; then
            compose up -d --no-deps caddy
            printf 'App ready; Caddy certificate provisioning may still be pending: https://%s\n' "$VERWALINO_DOMAIN"
        else
            printf 'App ready: http://127.0.0.1:%s\n' "$VERWALINO_PORT"
        fi
        printf 'Create the first admin with: bash scripts/self-host.sh admin --org NAME --email EMAIL\n'
        ;;
    admin) admin "$@" ;;
    status) compose ps ;;
    stop) lock_config; compose stop ;;
esac
