#!/usr/bin/env bash
# Local backup of the hosted server: database dump, documents volume and private config.
#
# Usage: server-backup.sh [LABEL]
#   Writes /srv/verwalino/backups/<UTC timestamp>[-LABEL]/ with db.dump (pg_dump custom format),
#   documents.tar, config.env (copy of the private installer config, secrets included),
#   MANIFEST and SHA256SUMS. deploy-server.sh calls it with LABEL "pre-<tag>"; the daily cron
#   job (DEPLOY.md, "Hosted server") calls it without a label.
#   Afterwards old backups are pruned: the newest backup of each of the last 14 days and of
#   each of the last 8 weeks (Monday to Sunday, UTC) stay, plus the 10 newest labelled ones.
#   Anything else in the directory is left alone.
#   VERWALINO_SERVER_ROOT (default /srv/verwalino) and VERWALINO_CONFIG (default
#   $VERWALINO_SERVER_ROOT/config/.env.selfhost) locate the installation.
#
# TODO(#57, #76): this is the minimal stand-in used until the installer has backup and restore
# commands (#57) and an update with automatic backup (#76). Replace the body with
# "scripts/self-host.sh backup" once they are merged and keep only the retention here.
#
# The containers are found by their Compose labels and used with plain "docker exec": the
# Compose file refuses to load without the variables self-host.sh derives from the config, so
# "docker compose exec" would have to duplicate that logic.
# The dump is taken first, then the documents: every document a dumped row refers to is in
# the archive; a file stored in between is only an unreferenced extra.
set -euo pipefail
export LC_ALL=C
umask 007

ROOT=${VERWALINO_SERVER_ROOT:-/srv/verwalino}
BACKUPS=$ROOT/backups
CONFIG=${VERWALINO_CONFIG:-$ROOT/config/.env.selfhost}
PROJECT=verwalino
DB_NAME=hausverwaltung
DB_USER=app_migrator
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_LABELLED=10
LABEL=${1:-}
WORK=""
LOCKED=0

fail() { printf 'backup: %s\n' "$1" >&2; exit 1; }
cleanup() {
    [[ -z "$WORK" ]] || rm -rf -- "$WORK"
    if [[ "$LOCKED" = 1 ]]; then rmdir "$BACKUPS/.lock"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $# -le 1 ]] || { echo 'Usage: server-backup.sh [LABEL]' >&2; exit 2; }
[[ -z "$LABEL" || "$LABEL" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo 'LABEL may contain only letters, digits, dot, underscore and dash.' >&2; exit 2; }

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# The only container (or volume) of this Compose project that carries the given label.
one_of() {
    local kind="$1" label="$2" found
    case "$kind" in
        container) found=$(docker ps --quiet --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$label" --filter status=running) ;;
        volume) found=$(docker volume ls --quiet --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.volume=$label") ;;
    esac
    [[ -n "$found" && "$found" != *$'\n'* ]] || fail "expected exactly one running $kind '$label' in project $PROJECT."
    printf '%s\n' "$found"
}

# Days since 1970-01-01 of a proleptic Gregorian date (Hinnant's algorithm), without date(1).
days_from_civil() {
    local y=$1 m=$((10#$2)) d=$((10#$3)) era yoe doy doe
    if (( m <= 2 )); then y=$((y - 1)); fi
    era=$(( y / 400 ))
    yoe=$(( y - era * 400 ))
    doy=$(( (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1 ))
    doe=$(( yoe * 365 + yoe / 4 - yoe / 100 + doy ))
    echo $(( era * 146097 + doe - 719468 ))
}

prune() {
    local dir name day week names='' kept_days=' ' kept_weeks=' ' days=0 weeks=0 labelled=0 keep
    for dir in "$BACKUPS"/*; do
        name=${dir##*/}
        if [[ -d "$dir" && ! -L "$dir" && "$name" =~ ^[0-9]{8}T[0-9]{6}Z(-.+)?$ ]]; then
            names+="$name"$'\n'
        fi
    done
    [[ -n "$names" ]] || return 0
    # Newest first; the first backup seen for a day or week is the newest of it.
    while IFS= read -r name; do
        keep=0
        if [[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
            day=${name:0:8}
            week=$(( ($(days_from_civil "${day:0:4}" "${day:4:2}" "${day:6:2}") + 3) / 7 ))
            if [[ "$kept_days" != *" $day "* && "$days" -lt "$KEEP_DAILY" ]]; then
                kept_days+="$day "; days=$((days + 1)); keep=1
            fi
            if [[ "$kept_weeks" != *" $week "* && "$weeks" -lt "$KEEP_WEEKLY" ]]; then
                kept_weeks+="$week "; weeks=$((weeks + 1)); keep=1
            fi
        else
            labelled=$((labelled + 1))
            if [[ "$labelled" -le "$KEEP_LABELLED" ]]; then keep=1; fi
        fi
        if [[ "$keep" = 0 ]]; then
            rm -rf -- "${BACKUPS:?}/$name"
            echo "backup: pruned $name"
        fi
    done < <(printf '%s' "$names" | sort -r)
}

command -v docker >/dev/null || fail 'docker is required.'
[[ -d "$BACKUPS" ]] || fail "$BACKUPS does not exist (see DEPLOY.md, Hosted server)."
[[ -f "$CONFIG" && ! -L "$CONFIG" && -r "$CONFIG" ]] || fail "installer config $CONFIG is missing or unreadable."
mkdir "$BACKUPS/.lock" 2>/dev/null || fail "another backup is running (or a stale $BACKUPS/.lock needs operator review)."
LOCKED=1

stamp=$(date -u +%Y%m%dT%H%M%SZ)
name=$stamp${LABEL:+-$LABEL}
[[ ! -e "$BACKUPS/$name" ]] || fail "$BACKUPS/$name already exists."
WORK=$BACKUPS/.partial-$name
mkdir "$WORK"

db=$(one_of container db)
docker exec "$db" pg_dump --format=custom --username "$DB_USER" --dbname "$DB_NAME" > "$WORK/db.dump"
# A truncated or unreadable dump fails here, not during a restore.
docker exec -i "$db" pg_restore --list < "$WORK/db.dump" > /dev/null || fail 'the database dump cannot be read back.'

volume=$(one_of volume documents)
helper_image=$(docker inspect --format '{{.Config.Image}}' "$db")
docker run --rm --pull never --network none --entrypoint tar --volume "$volume:/data:ro" \
    "$helper_image" -C /data -cf - . > "$WORK/documents.tar"
tar -tf "$WORK/documents.tar" > /dev/null || fail 'the documents archive cannot be read back.'

cat "$CONFIG" > "$WORK/config.env"
{
    printf 'created=%s\n' "$stamp"
    printf 'label=%s\n' "$LABEL"
    printf 'image=%s\n' "$(sed -n 's/^VERWALINO_IMAGE=//p' "$CONFIG")"
} > "$WORK/MANIFEST"
(cd "$WORK" && sha256 db.dump documents.tar config.env MANIFEST > SHA256SUMS)
chmod 640 "$WORK"/*

mv "$WORK" "$BACKUPS/$name"
WORK=""
echo "backup: created $BACKUPS/$name"
prune
