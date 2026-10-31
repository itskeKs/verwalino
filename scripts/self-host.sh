#!/usr/bin/env bash
# Portable first installation. Never source the configuration as shell code.
set -euo pipefail
export LC_ALL=C
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${VERWALINO_CONFIG:-$ROOT/.env.selfhost}"
[[ "$CONFIG" = /* ]] || CONFIG="$PWD/$CONFIG"
TMP_DIR=""
LOCKED=0
RESTORE_DB=0
RESTORE_SCRATCH=0
RESTORE_WINDOW=0
RESTORE_ACTIVE=0
RESTORE_DOCUMENTS_STARTED=0
RESTORE_LIVE_EXISTED=0
RESTORE_SWAP_FINISHED=0

fail() { printf '%s\n' "$1" >&2; exit 1; }
cleanup() {
    local status=$?
    trap '' PIPE
    set +e
    # Let recovery finish if the original failure was a dropped connection or
    # an interrupt; another signal must not interrupt the rename-back sequence.
    trap '' HUP INT TERM
    if [[ "$status" != 0 && "$RESTORE_ACTIVE" = 1 ]]; then
        if [[ "$RESTORE_WINDOW" = 0 ]]; then
            if [[ "$RESTORE_SCRATCH" = 1 ]]; then
                compose exec -T db dropdb -U app_migrator --if-exists hausverwaltung_restore || \
                    printf 'Could not remove scratch database hausverwaltung_restore; remove it before retrying restore.\n' >&2 || true
            fi
            printf 'Restore failed before cutover; old database and documents are untouched. Retry restore after fixing the reported error.\n' >&2 || true
            archive_tool discard-documents || printf 'Could not remove staged documents; inspect .restore-* in the documents volume.\n' >&2 || true
        else
            recover_restore
        fi
    fi
    if [[ "$RESTORE_DB" = 1 ]]; then
        compose stop db || printf 'Could not stop db; run: bash scripts/self-host.sh stop\n' >&2 || true
    fi
    [[ -z "$TMP_DIR" ]] || rm -rf "$TMP_DIR"
    if [[ "$LOCKED" = 1 ]]; then rmdir "$CONFIG.lock"; fi
    return "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

config_filename_valid() { [[ "${CONFIG##*/}" =~ ^\.env\.[a-zA-Z0-9._-]+$ ]]; }

usage() {
    cat <<'HELP'
Usage: bash scripts/self-host.sh COMMAND
  init [--https DOMAIN] [--port PORT] [--image REGISTRY/IMAGE@sha256:DIGEST]
       [--proxy-subnet PRIVATE_IPV4_CIDR]
  start                         Build/pull, migrate, start; print browser setup token if empty
  admin --org NAME --email EMAIL [--name NAME] [--password-stdin]
  diagnose [--out DIR]           Write a redacted bundle beside the config in diagnostics/
  status                        Show this installation's containers
  doctor                        Diagnose setup; German hints, exit 1 on blockers
  stop                          Stop containers without deleting data
  backup [--out DIR]            Archive the running database and documents
  restore FILE [--yes]          Replace data in a stopped installation
Default: local HTTP on 127.0.0.1:18080; no AI, SMTP or extraction worker.
Request IDs are always enabled; optional error tracking stays off.
HV_SUPPORT_URL selects the manual report link (export it; not a private config key).
HV_DIAGNOSTICS_DIR is an API process setting for the per-host error buffer; see DEPLOY.md.
HV_ERROR_TRACKING_* keys are not installer settings; see DEPLOY.md.
Export HV_RELEASE_ID to label tracker releases (otherwise app version 0.1.0).
Optional inbox-worker (profile inbox, ClamAV signatures): see DEPLOY.md.
Start and stop also stop an existing inbox-worker; restart it after app readiness.
VERWALINO_CONFIG selects the private config file (default: .env.selfhost).
No automatic update, downgrade or volume deletion is provided.
Backups exclude private config/secrets and certificates; keep config separately.
Backup output defaults to the private checkout backups/ directory; the checkout root is refused.
Restore stages the database and documents before replacing the stopped installation.
Leftover .restore-* directories require checking live data and removing the directory before retrying.
On first start, open the printed /setup URL and enter the one-time token (24 h).
Another start replaces an unused token. The token is never stored in this file.
The admin command is the alternative to browser setup.
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
    private_config_mode || fail 'Configuration must have permissions 0600 or 0400.'
    read_config
    validate_settings
    config_environment
}
private_config_mode() {
    local permissions
    permissions=$(stat -c '%a' "$CONFIG" 2>/dev/null || stat -f '%Lp' "$CONFIG")
    [[ "$permissions" = 600 || "$permissions" = 400 ]]
}
read_config() {
    local line key value seen='|'
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
}
config_environment() {
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

stop_inbox_worker() {
    local container_id
    container_id=$(compose ps --all --quiet inbox-worker)
    if [[ -n "$container_id" ]]; then compose stop inbox-worker; fi
}

config_fingerprint() {
    local key
    for key in VERWALINO_MODE VERWALINO_DOMAIN VERWALINO_PORT VERWALINO_IMAGE VERWALINO_PROXY_SUBNET; do
        printf '%s=%s\n' "$key" "${!key}"
    done | openssl dgst -sha256 | awk '{print $NF}'
}

archive_tool() {
    # Override the server entrypoint. Root is needed for the private host bind
    # and app-owned files. Only CHOWN/DAC_OVERRIDE are added to the dropped caps.
    compose run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE --entrypoint python \
        --env "BACKUP_OWNER=$(id -u):$(id -g)" --volume "$TMP_DIR:/backup" app -c "$(cat "$ROOT/backend/app/selfhost_backup.py")" \
        "$@" /backup /data/documents < /dev/null
}

doctor_report() {
    case "$1" in
        OK) doctor_ok=$((doctor_ok + 1)) ;;
        WARN) doctor_warn=$((doctor_warn + 1)) ;;
        FEHLER) doctor_errors=$((doctor_errors + 1)) ;;
    esac
    printf '%s %s: %s\n' "$1" "$2" "$3"
}

doctor_version_at_least() {
    local version="${1#v}" major minor
    [[ "$version" =~ ^([0-9]+)\.([0-9]+)(\.|$) ]] || return 1
    major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"
    (( 10#$major > $2 || (10#$major == $2 && 10#$minor >= $3) ))
}

doctor_disk() {
    local label="$1" path="$2" available
    # df -P uses one row per filesystem; -k makes the thresholds portable.
    if [[ -z "$path" ]] || ! available=$(df -Pk "$path" 2>/dev/null | awk 'END {print $4}') ||
        [[ ! "$available" =~ ^[0-9]+$ ]]; then
        doctor_report WARN "$label" 'Speicher nicht messbar; freien Platz am Docker-Host bzw. in Docker Desktop und im Checkout mit df -Pk prüfen.'
    elif (( available < 2097152 )); then
        doctor_report FEHLER "$label" 'Weniger als 2 GiB frei; Speicher freigeben oder Datenträger vergrößern, dann doctor erneut ausführen.'
    elif (( available < 10485760 )); then
        doctor_report WARN "$label" 'Weniger als 10 GiB frei; vor Backups und Updates Speicher freigeben oder Datenträger vergrößern.'
    else
        doctor_report OK "$label" 'Mindestens 10 GiB frei; vor Backups zusätzlichen Platz für Datenkopien vorhalten.'
    fi
}

doctor_container() {
    local service="$1" row state health id hint
    hint="Mit bash scripts/self-host.sh start starten; Protokolle: $doctor_logs $service"
    if [[ "$doctor_compose_ready" != 1 ]] || ! row=$(compose ps --all --format '{{.State}}|{{.Health}}|{{.ID}}' "$service" 2>/dev/null); then
        doctor_report WARN "Container $service" "Status nicht prüfbar; Docker und Konfiguration prüfen. $hint"
        return
    fi
    IFS='|' read -r state health id <<< "$row"
    printf -v "doctor_${service}_state" '%s' "$state"
    printf -v "doctor_${service}_id" '%s' "$id"
    case "$state:$health" in
        running:healthy|running:)
            if [[ "$health" = healthy || "$service" = caddy ]]; then
                doctor_report OK "Container $service" "Läuft${health:+ und ist gesund}; bei Problemen Protokolle ansehen: $doctor_logs $service"
            else
                doctor_report WARN "Container $service" "Healthcheck fehlt. $hint"
            fi ;;
        restarting:*|*:unhealthy|dead:*|paused:*) doctor_report FEHLER "Container $service" "Zustand $state${health:+/$health}. $hint" ;;
        *) doctor_report WARN "Container $service" "Zustand ${state:-nicht angelegt}${health:+/$health}. $hint" ;;
    esac
}

doctor_owned_port() {
    local service="$1" port="$2" internal="$3" state_key="doctor_${1}_state" id_key="doctor_${1}_id" owner mapping
    [[ "$doctor_compose_ready" = 1 && "${!state_key}" = running && -n "${!id_key}" ]] || return 1
    owner=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "${!id_key}" 2>/dev/null) || return 1
    [[ "$owner" = "$ROOT/infra/docker-compose.yml" ]] || return 1
    mapping=$(compose port "$service" "$internal" 2>/dev/null) || return 1
    [[ "$mapping" = *":$port" ]]
}

# Return 0 for an occupied port, 1 for free, 2 for unavailable inspection.
doctor_port_busy() {
    local port="$1" output status
    if command -v ss >/dev/null && output=$(ss -H -ltn "sport = :$port" 2>/dev/null); then
        [[ -n "$output" ]] && return 0
        return 1
    fi
    if command -v lsof >/dev/null; then
        status=0
        output=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null) || status=$?
        [[ -n "$output" && "$status" = 0 ]] && return 0
        [[ "$status" = 1 ]] && return 1
    fi
    if command -v nc >/dev/null; then
        if nc -z -w 2 127.0.0.1 "$port" >/dev/null 2>&1 || nc -z -w 2 ::1 "$port" >/dev/null 2>&1; then return 0; fi
        return 1
    fi
    return 2
}

doctor_port() {
    local port="$1" service="$2" internal="$3" status=0
    doctor_port_busy "$port" || status=$?
    if [[ "$status" = 2 ]]; then
        doctor_report WARN "Port $port" 'Belegung nicht prüfbar; ss (iproute2), lsof oder nc installieren und doctor erneut ausführen.'
    elif doctor_owned_port "$service" "$port" "$internal"; then
        doctor_report OK "Port $port" "Vom eigenen Container $service veröffentlicht; bei Problemen $doctor_logs $service ausführen."
    elif [[ "$status" = 0 ]]; then
        doctor_report FEHLER "Port $port" "Durch einen fremden Dienst belegt; mit ss -ltnp oder lsof -iTCP:$port den Dienst ermitteln und dessen Portfreigabe korrigieren."
    else
        doctor_report OK "Port $port" 'Frei; die Installation kann diesen Port beim nächsten start belegen.'
    fi
}

doctor_sql() {
    compose exec -T db psql -X --set ON_ERROR_STOP=1 -U app_migrator -d hausverwaltung -Atc "$1" 2>/dev/null
}

doctor_database() {
    local table schema target count
    if [[ "$doctor_db_state" != running ]]; then
        doctor_report WARN Migration 'Datenbank läuft nicht; bash scripts/self-host.sh start ausführen und doctor wiederholen.'
        doctor_report WARN Organisation 'Datenbank läuft nicht; nach start mit bash scripts/self-host.sh admin --org NAME --email EMAIL die erste Organisation anlegen.'
        return
    fi
    if ! table=$(doctor_sql "SELECT to_regclass('public.alembic_version')"); then
        doctor_report WARN Migration "Datenbankabfrage fehlgeschlagen; $doctor_logs db ausführen und ursprüngliche Konfiguration prüfen."
    elif [[ -z "$table" ]]; then
        doctor_report WARN Migration 'Noch nicht initialisiert; bash scripts/self-host.sh start initialisiert und migriert die Datenbank.'
    elif ! schema=$(doctor_sql 'SELECT version_num FROM alembic_version'); then
        doctor_report WARN Migration "Schema nicht lesbar; $doctor_logs db ausführen und bash scripts/self-host.sh start prüfen."
    elif [[ -z "$schema" ]]; then
        doctor_report WARN Migration 'Noch nicht initialisiert; bash scripts/self-host.sh start initialisiert und migriert die Datenbank.'
    elif [[ ! "$schema" =~ ^[0-9]{4}$ ]]; then
        doctor_report WARN Migration 'Schema unbekannt oder mehrdeutig; alembic_version prüfen, vor manuellen Änderungen ein Backup erstellen.'
    elif ! docker image inspect "$VERWALINO_IMAGE" >/dev/null 2>&1; then
        # Diagnostics must not build or pull missing images.
        doctor_report WARN Migration 'Konfiguriertes Image fehlt; nach Backup mit bash scripts/self-host.sh start bereitstellen und doctor wiederholen.'
    elif ! target=$(docker run --rm --pull never --network none --read-only --user 10001:10001 \
        --cap-drop ALL --security-opt no-new-privileges --entrypoint python "$VERWALINO_IMAGE" \
        -c 'from app.deployment import TARGET; print(TARGET)' < /dev/null 2>/dev/null) ||
        [[ ! "$target" =~ ^[0-9]{4}$ ]]; then
        doctor_report WARN Migration 'Imageziel nicht lesbar; das konfigurierte Image bereitstellen und doctor wiederholen.'
    elif (( 10#$schema > 10#$target )); then
        doctor_report FEHLER Migration "Datenbank $schema ist neuer als Imageziel $target; passendes neueres Image verwenden, kein Downgrade ausführen."
    elif (( 10#$schema < 10#$target )); then
        doctor_report WARN Migration "Datenbank $schema liegt hinter Imageziel $target; nach Backup bash scripts/self-host.sh start ausführen, start migriert."
    else
        doctor_report OK Migration "Datenbank und Image stehen auf $target; vor einem Versionswechsel ein Backup erstellen."
    fi
    if ! count=$(doctor_sql 'SELECT count(*) FROM organization') || [[ ! "$count" =~ ^[0-9]+$ ]]; then
        doctor_report WARN Organisation "Organisationen nicht lesbar; $doctor_logs db prüfen, dann start und bei Bedarf bash scripts/self-host.sh admin --org NAME --email EMAIL ausführen."
    elif [[ "$count" = 0 ]]; then
        doctor_report WARN Organisation 'Noch keine Organisation; bash scripts/self-host.sh admin --org NAME --email EMAIL ausführen.'
    else
        doctor_report OK Organisation "$count Organisation(en) vorhanden; Administratorzugang bei Bedarf mit bash scripts/self-host.sh admin --org NAME --email EMAIL anlegen."
    fi
}

doctor_dns_addresses() {
    if command -v getent >/dev/null; then
        { getent ahosts "$VERWALINO_DOMAIN"; getent hosts "$VERWALINO_DOMAIN"; } 2>/dev/null | awk '{print $1}'
    elif command -v dig >/dev/null; then
        { dig +time=2 +tries=1 +short A "$VERWALINO_DOMAIN"; dig +time=2 +tries=1 +short AAAA "$VERWALINO_DOMAIN"; } 2>/dev/null
    elif command -v host >/dev/null; then
        host -W 2 "$VERWALINO_DOMAIN" 2>/dev/null | awk '/has address|has IPv6 address/ {print $NF}'
    else
        return 2
    fi
}

doctor_public_addresses() {
    local addresses address
    if command -v ip >/dev/null; then
        addresses=$(ip -o addr show scope global 2>/dev/null) || return 0
    elif command -v ifconfig >/dev/null; then
        addresses=$(ifconfig 2>/dev/null) || return 0
    else
        return 0
    fi
    while IFS= read -r address; do
        address="${address%/*}"; address="${address%%%*}"
        case "$address" in
            0.*|10.*|127.*|169.254.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) continue ;;
        esac
        if [[ "$address" =~ ^[0-9]+\. && "${address%%.*}" -lt 224 ]] || [[ "$address" =~ ^[23][0-9a-fA-F]{3}: ]]; then
            printf '%s\n' "$address"
        fi
    done < <(printf '%s\n' "$addresses" | awk '{for (i=1;i<NF;i++) if ($i=="inet" || $i=="inet6") print $(i+1)}')
}

doctor_dns() {
    local resolved addresses public address matches=0 total=0
    if [[ "$doctor_domain_valid" != 1 ]]; then
        doctor_report WARN DNS 'Domain ungültig; VERWALINO_DOMAIN in der privaten Konfiguration korrigieren und DNS erneut prüfen.'
        doctor_report WARN DNS-Ziel 'Domain ungültig; zuerst Domain korrigieren, dann A/AAAA mit den öffentlichen Hostadressen vergleichen.'
        return
    fi
    resolved=$(doctor_dns_addresses) || :
    addresses=$(printf '%s\n' "$resolved" | awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$|^[0-9a-fA-F]*:[0-9a-fA-F:]+$/ {print}' | sort -u)
    if ! command -v getent >/dev/null && ! command -v dig >/dev/null && ! command -v host >/dev/null; then
        doctor_report WARN DNS 'Kein DNS-Werkzeug vorhanden; getent, dig oder host installieren und doctor erneut ausführen.'
    elif [[ -z "$addresses" ]]; then
        doctor_report FEHLER DNS 'Keine A/AAAA-Adresse gefunden; DNS-Einträge der konfigurierten Domain anlegen bzw. korrigieren.'
    else
        doctor_report OK DNS 'A/AAAA-Auflösung vorhanden; bei Änderungen DNS-Einträge erneut mit doctor prüfen.'
    fi
    public=$(doctor_public_addresses)
    if [[ -z "$public" || -z "$addresses" ]]; then
        doctor_report WARN DNS-Ziel 'Öffentliche Hostadresse oder DNS-Ziel nicht bestimmbar (z. B. NAT/Docker Desktop); A/AAAA im DNS manuell mit der öffentlichen Serveradresse vergleichen.'
        return
    fi
    while IFS= read -r address; do
        total=$((total + 1))
        case $'\n'"$public"$'\n' in *$'\n'"$address"$'\n'*) matches=$((matches + 1)) ;; esac
    done <<< "$addresses"
    if [[ "$matches" = 0 ]]; then
        doctor_report WARN DNS-Ziel 'DNS zeigt auf keine öffentliche Schnittstellenadresse dieses Hosts; Proxy (z. B. Cloudflare), NAT/Elastic IP oder /etc/hosts können dies erklären. A/AAAA und öffentliches Ziel manuell prüfen.'
    elif [[ "$matches" != "$total" ]]; then
        doctor_report WARN DNS-Ziel 'Nicht alle DNS-Adressen passen zum Host; insbesondere veraltete AAAA-Einträge korrigieren oder entfernen.'
    else
        doctor_report OK DNS-Ziel 'DNS-Adressen passen zu öffentlichen Hostadressen; externe Erreichbarkeit zusätzlich von außerhalb prüfen.'
    fi
}

# macOS has no timeout command. Bound the TLS handshake without adding a dependency.
doctor_timeout() (
    "$@" &
    local process=$! timer status=0
    (
        sleep 8 &
        local sleeper=$!
        trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM INT
        wait "$sleeper"
        kill "$process" 2>/dev/null
    ) >/dev/null 2>&1 &
    timer=$!
    wait "$process" || status=$?
    kill "$timer" 2>/dev/null || :
    wait "$timer" 2>/dev/null || :
    return "$status"
)

doctor_https() {
    local port certificate
    if validate_domain "$VERWALINO_DOMAIN"; then
        doctor_domain_valid=1
        doctor_report OK Domain 'DNS-Hostname gültig; bei Domainwechsel Konfiguration und DNS gemeinsam prüfen.'
    else
        doctor_report FEHLER Domain 'Ungültiger DNS-Hostname; VERWALINO_DOMAIN ohne Schema, Port oder Großbuchstaben eintragen.'
    fi
    doctor_dns
    for port in 80 443; do
        doctor_port "$port" caddy "$port"
        if ! command -v nc >/dev/null; then
            doctor_report WARN "Erreichbarkeit $port" "Verbindung nicht prüfbar; nc installieren und nc -z -w 2 127.0.0.1 $port ausführen."
        elif nc -z -w 2 127.0.0.1 "$port" >/dev/null 2>&1; then
            doctor_report OK "Erreichbarkeit $port" 'Lokal erreichbar; Firewall und Weiterleitung zusätzlich von außerhalb prüfen.'
        else
            doctor_report WARN "Erreichbarkeit $port" 'Lokal nicht erreichbar; bash scripts/self-host.sh start ausführen, danach Portfreigaben und Firewall prüfen.'
        fi
    done
    if [[ "$doctor_caddy_state" != running ]]; then
        doctor_report WARN Zertifikat 'Caddy läuft nicht; bash scripts/self-host.sh start ausführen und Zertifikatsausstellung abwarten.'
    elif [[ "$doctor_domain_valid" != 1 ]] || ! certificate=$(compose exec -T caddy find /data/caddy/certificates -type f -name "$VERWALINO_DOMAIN.crt" 2>/dev/null) || [[ -z "$certificate" ]]; then
        doctor_report WARN Zertifikat "Kein Domainzertifikat lesbar; DNS und Portfreigaben prüfen, dann $doctor_logs caddy ausführen und Ausstellung abwarten."
    else
        doctor_report OK Zertifikat "Domainzertifikat im Caddy-Datenvolume vorhanden; bei TLS-Problemen $doctor_logs caddy ausführen."
    fi
    if [[ "$doctor_domain_valid" != 1 ]] || ! command -v openssl >/dev/null ||
        ! certificate=$(doctor_timeout openssl s_client -connect 127.0.0.1:443 -servername "$VERWALINO_DOMAIN" -showcerts < /dev/null 2>/dev/null) ||
        ! printf '%s\n' "$certificate" | openssl x509 -noout -enddate >/dev/null 2>&1; then
        doctor_report WARN Zertifikatslaufzeit 'TLS-Zertifikat nicht prüfbar; OpenSSL installieren, Caddy starten und Verbindung zu 127.0.0.1:443 mit der Domain als SNI prüfen.'
    elif ! printf '%s\n' "$certificate" | openssl x509 -checkend 1209600 -noout >/dev/null 2>&1; then
        doctor_report WARN Zertifikatslaufzeit "Zertifikat läuft innerhalb von 14 Tagen ab oder ist abgelaufen; DNS/Ports und Erneuerungsfehler mit $doctor_logs caddy prüfen."
    else
        doctor_report OK Zertifikatslaufzeit 'Zertifikat noch mindestens 14 Tage gültig; automatische Erneuerung in den Caddy-Protokollen beobachten.'
    fi
}

doctor() {
    local doctor_ok=0 doctor_warn=0 doctor_errors=0 docker_present=0 daemon_ready=0 compose_ready=0
    local config_present=0 config_private=0 config_parsed=0 config_valid=0 doctor_compose_ready=0 doctor_domain_valid=0
    local doctor_db_state='' doctor_app_state='' doctor_caddy_state='' doctor_db_id='' doctor_app_id='' doctor_caddy_id=''
    local version docker_root='' doctor_logs
    doctor_logs="docker compose --project-name verwalino --env-file \"$CONFIG\" -f \"$ROOT/infra/docker-compose.yml\" logs --tail 100"
    if command -v docker >/dev/null; then
        docker_present=1
        doctor_report OK Docker 'Docker vorhanden; bei Problemen Docker-Daemon und Kontext prüfen.'
    else
        doctor_report FEHLER Docker 'Docker fehlt; Docker Engine ab Version 24 oder Docker Desktop installieren.'
    fi
    if [[ "$docker_present" = 1 ]] && docker info >/dev/null 2>&1; then
        daemon_ready=1
        doctor_report OK Docker-Daemon 'Docker-Daemon erreichbar; diesen Docker-Kontext für start beibehalten.'
    else
        doctor_report FEHLER Docker-Daemon 'Docker-Daemon nicht erreichbar; Docker starten und Zugriffsrechte sowie docker context prüfen.'
    fi
    if [[ "$daemon_ready" = 1 ]] && version=$(docker info -f '{{.ServerVersion}}' 2>/dev/null) && doctor_version_at_least "$version" 24 0; then
        doctor_report OK Docker-Version 'Docker mindestens Version 24; bei Aktualisierungen die Mindestversion beibehalten.'
    else
        doctor_report FEHLER Docker-Version 'Docker-Version unbekannt oder älter als 24; Daemonzugriff herstellen bzw. Docker aktualisieren.'
    fi
    if [[ "$docker_present" = 1 ]] && version=$(docker compose version --short 2>/dev/null) && doctor_version_at_least "$version" 2 24; then
        compose_ready=1
        doctor_report OK Compose 'Compose mindestens 2.24; dieses Compose-Plugin für start verwenden.'
    else
        doctor_report FEHLER Compose 'Compose ab 2.24 fehlt; Docker-Compose-Plugin installieren oder aktualisieren.'
    fi
    if config_filename_valid && [[ ! -L "$CONFIG" && -f "$CONFIG" ]]; then
        config_present=1
        doctor_report OK Konfiguration 'Private Konfigurationsdatei vorhanden; Originaldatei separat sichern.'
    else
        doctor_report FEHLER Konfiguration 'Konfiguration fehlt oder ist unsicher; bei neuer Installation init ausführen, sonst Originaldatei als reguläre .env.*-Datei wiederherstellen (kein Symlink).'
    fi
    if [[ "$config_present" = 1 ]] && private_config_mode 2>/dev/null; then
        config_private=1
        doctor_report OK Dateirechte 'Modus 0600 oder 0400; private Zugriffsrechte beibehalten.'
    else
        doctor_report FEHLER Dateirechte "Private reguläre Datei wiederherstellen und chmod 600 \"$CONFIG\" ausführen; kein Gruppen- oder Weltzugriff."
    fi
    if [[ "$config_present" = 1 ]] && (read_config) >/dev/null 2>&1; then
        read_config
        config_parsed=1
    fi
    if [[ "$config_parsed" = 1 ]] && (validate_settings) >/dev/null 2>&1; then
        config_valid=1
        config_environment
        doctor_report OK Einstellungen 'Konfiguration vollständig und gültig; Geheimnisse beim Bearbeiten beibehalten.'
    else
        doctor_report FEHLER Einstellungen 'Konfiguration unvollständig oder ungültig; Originaldatei wiederherstellen bzw. Werte gemäß DEPLOY.md korrigieren, bestehende Geheimnisse nicht neu erzeugen.'
    fi
    if [[ "$daemon_ready" = 1 ]]; then docker_root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null) || docker_root=''; fi
    doctor_disk 'Speicher Docker' "$docker_root"
    doctor_disk 'Speicher Checkout' "$ROOT"
    if [[ "$config_valid" = 1 && "$config_private" = 1 && "$daemon_ready" = 1 && "$compose_ready" = 1 ]]; then doctor_compose_ready=1; fi
    doctor_container db
    doctor_container app
    if [[ "$config_parsed" = 1 && "$VERWALINO_MODE" = https ]]; then doctor_container caddy; fi
    if [[ "$config_valid" = 1 && "$VERWALINO_MODE" = local ]]; then
        doctor_port "$VERWALINO_PORT" app 8000
    elif [[ "$config_parsed" != 1 ]]; then
        doctor_report WARN Port 'Installationsmodus oder Port nicht lesbar; private Konfiguration wiederherstellen und doctor erneut ausführen.'
    fi
    doctor_database
    if [[ "$config_parsed" = 1 && "$VERWALINO_MODE" = https ]]; then doctor_https; fi
    printf 'Zusammenfassung: %s OK, %s WARN, %s FEHLER.\n' "$doctor_ok" "$doctor_warn" "$doctor_errors"
    [[ "$doctor_errors" = 0 ]]
}

database_sql() {
    compose exec -T db psql -X --set ON_ERROR_STOP=1 -U app_migrator -d postgres -Atc "$1"
}

database_state() {
    database_sql "SELECT name || '=' || CASE WHEN EXISTS (SELECT 1 FROM pg_database WHERE datname=name) THEN 'present' ELSE 'absent' END FROM (VALUES ('hausverwaltung'), ('hausverwaltung_restore'), ('hausverwaltung_previous')) AS databases(name)"
}

recover_restore() {
    local state documents_state rollback_failed=0 restored_live=0
    state=$(database_state) || state=unknown
    # Query actual names: interruption can happen after SQL committed but before
    # the shell received its exit code. Never discard either database here.
    if [[ "$state" = *'hausverwaltung_previous=present'* ]]; then
        if [[ "$state" = *'hausverwaltung=present'* ]]; then
            if [[ "$state" = *'hausverwaltung_restore=absent'* ]] && \
                database_sql 'ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_restore'; then
                :
            else
                rollback_failed=1
            fi
        fi
        if [[ "$rollback_failed" = 0 ]]; then
            database_sql 'ALTER DATABASE hausverwaltung_previous RENAME TO hausverwaltung' || rollback_failed=1
        fi
        if [[ "$RESTORE_DOCUMENTS_STARTED" = 1 ]]; then
            archive_tool rollback-documents || rollback_failed=1
        fi
    elif [[ "$state" = *'hausverwaltung=present'* && "$state" = *'hausverwaltung_restore=absent'* ]]; then
        if [[ "$RESTORE_LIVE_EXISTED" = 1 || "$RESTORE_SWAP_FINISHED" = 1 ]]; then
            restored_live=1
        else
            # With no original database, an incomplete swap can still return
            # to the initial absent-live state without discarding restored data.
            database_sql 'ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_restore' || rollback_failed=1
            if [[ "$RESTORE_DOCUMENTS_STARTED" = 1 ]]; then
                archive_tool rollback-documents || rollback_failed=1
            fi
        fi
    elif [[ "$state" = unknown ]]; then
        rollback_failed=1
    fi
    # Recovery must finish even if diagnostics cannot be written (closed stderr
    # or a disconnected pipeline). The EXIT trap ignores PIPE and disables -e.
    printf 'Restore failed during cutover; keep the app stopped.\n' >&2 || true
    state=$(database_state) || state=unknown
    printf 'Installation state:\n%s\n' "$state" >&2 || true
    documents_state=$(archive_tool document-state) || documents_state='unknown; inspect /data/documents/.restore-*/documents and /data/documents/.restore-*/previous'
    printf 'documents=%s\n' "$documents_state" >&2 || true
    if [[ "$restored_live" = 1 ]]; then
        if [[ "$RESTORE_LIVE_EXISTED" = 1 ]]; then
            printf 'The previous database was already dropped; restored data remains live.\n' >&2 || true
        else
            printf 'No previous database existed; restored data remains live.\n' >&2 || true
        fi
        printf 'After verifying the live database and documents, remove the .restore-<id> directory shown above, then run: bash scripts/self-host.sh start\n' >&2 || true
        return
    fi
    if [[ "$RESTORE_LIVE_EXISTED" = 0 ]]; then
        printf 'No previous database existed; the restored database is retained under the name shown above. Keep the app stopped until recovery is verified.\n' >&2 || true
    fi
    if [[ "$rollback_failed" = 1 ]]; then
        printf 'Automatic rollback failed. Inspect the exact state above before running manual recovery.\n' >&2 || true
    fi
    printf '%s\n' \
        'Recovery SQL (psql -X --set ON_ERROR_STOP=1 -U app_migrator -d postgres in the db service):' \
        'If hausverwaltung_previous and hausverwaltung exist, and hausverwaltung_restore is absent: ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_restore;' \
        'Then, if hausverwaltung_previous exists and hausverwaltung is absent: ALTER DATABASE hausverwaltung_previous RENAME TO hausverwaltung;' \
        'For documents, move any installed restored entries back into the staged documents directory above, then move previous entries back to /data/documents. Keep every .restore-* directory until recovery is verified.' \
        'After verifying the database and documents, remove the scratch database hausverwaltung_restore and retained .restore-* directory before retrying the archive.' >&2 || true
}

backup() {
    local out="$ROOT/backups" archive schema
    if [[ "$#" != 0 ]]; then
        [[ "$#" = 2 && "$1" = --out && -n "$2" ]] || fail 'Usage: backup [--out DIR]'
        out="$2"
    fi
    command -v openssl >/dev/null || fail 'OpenSSL is required for configuration fingerprints.'
    umask 077
    lock_config
    [[ -d "$out" ]] || mkdir -p -m 700 "$out"
    out=$(cd "$out" && pwd -P)
    [[ "$out" != "$(cd "$ROOT" && pwd -P)" ]] || fail 'Backup output must not be the checkout root.'
    archive="$out/verwalino-backup-$(date -u +%Y%m%dT%H%M%SZ).tar"
    TMP_DIR=$(mktemp -d "$out/.verwalino-backup.XXXXXX")
    # The installer lock excludes migrations; pg_dump takes a consistent online
    # snapshot. Documents are copied afterwards and may include later uploads.
    schema=$(compose exec -T db psql -X --set ON_ERROR_STOP=1 -U app_migrator -d hausverwaltung \
        -Atc 'SELECT version_num FROM alembic_version')
    [[ "$schema" =~ ^[0-9]{4}$ ]] || fail 'Missing or ambiguous database schema head.'
    compose exec -T db pg_dump -U app_migrator -d hausverwaltung -Fc > "$TMP_DIR/db.dump"
    archive_tool pack "$schema" "$VERWALINO_IMAGE" "$(config_fingerprint)"
    # Same-filesystem publication never overwrites another backup in this second.
    if ! ln "$TMP_DIR/archive.tar" "$archive" 2>/dev/null; then
        [[ ! -e "$archive" ]] || fail 'Backup destination exists; nothing overwritten.'
        # Rootless/user-namespace ownership can forbid linking a readable file.
        # Copy privately, then publish the caller-owned copy without overwriting.
        cp "$TMP_DIR/archive.tar" "$TMP_DIR/publication.tar" || fail 'Could not copy backup for publication.'
        ln "$TMP_DIR/publication.tar" "$archive" || fail 'Backup destination exists; nothing overwritten.'
    fi
    printf 'Backup: %s (%s bytes)\n' "$archive" "$(wc -c < "$archive" | tr -d ' ')"
}

restore() {
    local file="${1:-}" yes=0 running target confirmation schema restored_schema previous live
    [[ -n "$file" && -f "$file" ]] || fail 'Usage: restore FILE [--yes]'
    shift
    if [[ "$#" != 0 ]]; then
        [[ "$#" = 1 && "$1" = --yes ]] || fail 'Usage: restore FILE [--yes]'
        yes=1
    fi
    command -v openssl >/dev/null || fail 'OpenSSL is required for configuration fingerprints.'
    umask 077
    lock_config
    running=$(compose ps --all --status running --status restarting --status paused --quiet app migrate bootstrap inbox-worker)
    [[ -z "$running" ]] || fail 'Installation is running; first run: bash scripts/self-host.sh stop'
    TMP_DIR=$(mktemp -d "$(dirname "$CONFIG")/.env.verwalino-restore.XXXXXX")
    # Work on a private copy so a changed input cannot bypass verification.
    cp "$file" "$TMP_DIR/archive.tar"
    target=$(archive_tool target)
    [[ "$target" =~ ^[0-9]{4}$ ]] || fail 'Cannot read the image release target.'
    schema=$(archive_tool verify "$target" "$(config_fingerprint)")
    archive_tool check-documents
    if [[ "$yes" = 0 ]]; then
        printf 'This replaces the application database and all documents. Type restore to confirm: ' >&2
        IFS= read -r confirmation || fail 'Restore cancelled; use --yes for non-interactive restore.'
        [[ "$confirmation" = restore ]] || fail 'Restore cancelled.'
    fi
    # Set before startup so cleanup stops db even after a readiness failure.
    RESTORE_DB=1
    RESTORE_ACTIVE=1
    compose up -d --wait --wait-timeout 120 db
    previous=$(database_sql "SELECT 1 FROM pg_database WHERE datname='hausverwaltung_previous'")
    [[ -z "$previous" ]] || fail 'hausverwaltung_previous already exists; keep the app stopped and inspect it alongside hausverwaltung and any .restore-* directory before recovery. Rename it back if the live database is absent, or remove it only after verifying which data to keep. Verify the live data, then remove any retained .restore-* directory, then retry restore.'
    compose exec -T db pg_restore --list < "$TMP_DIR/db.dump" > /dev/null
    compose exec -T db createdb -U app_migrator -T template0 hausverwaltung_restore
    RESTORE_SCRATCH=1
    compose exec -T db pg_restore -U app_migrator -d hausverwaltung_restore \
        --exit-on-error --single-transaction < "$TMP_DIR/db.dump"
    restored_schema=$(compose exec -T db psql -X --set ON_ERROR_STOP=1 -U app_migrator -d hausverwaltung_restore \
        -Atc 'SELECT version_num FROM alembic_version')
    [[ "$restored_schema" = "$schema" ]] || fail 'Restored database schema head does not match the manifest.'
    archive_tool stage-documents
    live=$(database_sql "SELECT 1 FROM pg_database WHERE datname='hausverwaltung'")
    if [[ -n "$live" ]]; then RESTORE_LIVE_EXISTED=1; fi
    RESTORE_WINDOW=1
    if [[ "$RESTORE_LIVE_EXISTED" = 1 ]]; then
        database_sql 'ALTER DATABASE hausverwaltung RENAME TO hausverwaltung_previous'
    fi
    database_sql 'ALTER DATABASE hausverwaltung_restore RENAME TO hausverwaltung'
    RESTORE_DOCUMENTS_STARTED=1
    archive_tool swap-documents
    RESTORE_SWAP_FINISHED=1
    if [[ "$RESTORE_LIVE_EXISTED" = 1 ]]; then
        database_sql 'DROP DATABASE hausverwaltung_previous'
    fi
    archive_tool discard-documents
    RESTORE_ACTIVE=0
    compose stop db
    RESTORE_DB=0
    printf 'Restored database and documents. Next: bash scripts/self-host.sh start\n'
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

diagnose() {
    local out="${CONFIG%/*}/diagnostics" states destination schema_head
    if [[ "$#" != 0 ]]; then [[ "$#" = 2 && "$1" = --out && -n "$2" ]] || fail 'Use diagnose [--out DIR].'; out="$2"; fi
    [[ ! -L "$out" ]] || fail 'Symlink output directories are refused.'
    umask 077
    if [[ ! -e "$out" ]]; then mkdir -p "$out"; chmod 700 "$out"; fi
    [[ -d "$out" ]] || fail 'Output path is not a directory.'
    [[ -w "$out" ]] || fail 'Output directory is not writable.'
    TMP_DIR=$(mktemp -d "$out/.diagnose.XXXXXX")
    destination="$out/verwalino-diagnostics-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    states=$(compose ps --all --format json)
    if ! schema_head=$(compose exec -T db psql --no-psqlrc -U app_migrator -d hausverwaltung --set ON_ERROR_STOP=1 -Atc 'SELECT version_num FROM public.alembic_version'); then
        schema_head=''
    fi
    compose logs --no-color --no-log-prefix --tail 2000 app | compose run --rm --no-deps -T --entrypoint python app -m app.diagnostics --containers-json "$states" --schema-head "$schema_head" --image "$VERWALINO_IMAGE" > "$TMP_DIR/bundle"
    ln "$TMP_DIR/bundle" "$destination"
    printf '%s\n' "$destination"
}

prepare_setup() {
    local count token_hash issued
    SETUP_TOKEN=''
    count=$(compose exec -T db psql --no-psqlrc -U app_migrator -d hausverwaltung \
        --set ON_ERROR_STOP=1 -Atc 'SELECT count(*) FROM public.organization')
    [[ "$count" =~ ^[0-9]+$ ]] || fail 'Could not determine whether setup is required.'
    [[ "$count" = 0 ]] || return 0
    command -v openssl >/dev/null || fail 'OpenSSL is required to generate the setup token.'
    SETUP_TOKEN=$(openssl rand -hex 32) || fail 'Setup token generation failed.'
    [[ "$SETUP_TOKEN" =~ ^[a-f0-9]{64}$ ]] || fail 'Invalid random setup token.'
    token_hash=$(printf '%s' "$SETUP_TOKEN" | openssl dgst -sha256)
    token_hash="${token_hash##* }"
    [[ "$token_hash" =~ ^[a-f0-9]{64}$ ]] || fail 'Setup token hashing failed.'
    # Only the digest reaches psql, its arguments or Docker. Recheck under the
    # same organization lock as browser setup and CLI bootstrap before rotating.
    issued=$(printf '%s\n' "BEGIN;
LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE;
INSERT INTO public.setup_token(token_hash)
SELECT decode('$token_hash','hex') WHERE NOT EXISTS(SELECT 1 FROM public.organization)
ON CONFLICT (singleton) DO UPDATE
SET token_hash=EXCLUDED.token_hash,created_at=clock_timestamp(),used_at=NULL
RETURNING singleton;
COMMIT;" | compose exec -T db psql --no-psqlrc -U app_migrator -d hausverwaltung \
        --set ON_ERROR_STOP=1 -qAt)
    [[ "$issued" = t ]] || fail 'Setup token was not issued; retry start to recheck the installation.'
}

command="${1:-help}"
[[ "$#" = 0 ]] || shift
if [[ "$command" != doctor ]]; then
    config_filename_valid || fail 'Configuration filename must start with .env. (kept out of Git and image builds).'
fi
case "$command" in
    help|--help|-h) usage; exit 0 ;;
    init) init_config "$@"; exit 0 ;;
    doctor) [[ "$#" = 0 ]] || fail 'Dieser Befehl akzeptiert keine zusätzlichen Argumente.'; doctor; exit $? ;;
    start|status|stop) [[ "$#" = 0 ]] || fail 'This command accepts no additional arguments.' ;;
    admin|diagnose|backup|restore) ;;
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
        # Do not run migrations alongside an already running writer.
        stop_inbox_worker
        compose stop app
        compose run --rm --no-deps -T migrate
        prepare_setup
        compose up -d --no-deps --wait --wait-timeout 120 app
        if [[ "$VERWALINO_MODE" = https ]]; then
            compose up -d --no-deps caddy
            APP_URL="https://$VERWALINO_DOMAIN"
            printf 'App ready; Caddy certificate provisioning may still be pending: %s\n' "$APP_URL"
        else
            APP_URL="http://127.0.0.1:$VERWALINO_PORT"
            printf 'App ready: %s\n' "$APP_URL"
        fi
        if [[ -n "$SETUP_TOKEN" ]]; then
            printf 'Browser setup: %s/setup\nSetup token: %s\nValid once for 24 hours. Another start replaces this token.\n' "$APP_URL" "$SETUP_TOKEN"
            unset SETUP_TOKEN
            printf 'CLI alternative: bash scripts/self-host.sh admin --org NAME --email EMAIL\n'
        fi
        ;;
    admin) admin "$@" ;;
    diagnose) diagnose "$@" ;;
    backup) backup "$@" ;;
    restore) restore "$@" ;;
    status) compose ps ;;
    stop) lock_config; stop_inbox_worker; compose stop ;;
esac
