#!/usr/bin/env bash
# Download a stable release bundle, verify it, then use its portable installer.
main() {
set -euo pipefail
export LC_ALL=C

fail() { printf 'Install failed: %s\n' "$1" >&2; exit 1; }
usage() {
    cat <<'HELP'
Usage: bash install.sh [--version vX.Y.Z] [--dir DIR] [--https DOMAIN] [--port PORT]
                       [--offline-bundle DIR] [--force]
Default: latest stable release, ./verwalino, HTTP on 127.0.0.1:18080.
Requires Bash, Docker/Compose v2 >= 2.24 with a reachable daemon, OpenSSL,
curl or wget, and sha256sum or shasum. Cosign is optional for signature checks.
--offline-bundle reads flat release assets locally and requires --version.
  No download/registry lookup is made by this wrapper; start still pulls images
  and needs registry access. With cosign, supply trusted trusted_root.json
  in the offline bundle directory to verify without fetching service keys.
--force retries an existing config with the same image, preserving its bytes.
  It cannot upgrade or reconfigure; omit --https and --port when retrying.
A non-empty DIR without .env.selfhost also requires --force to replace files.
Runs init, then doctor if available (failure stops installation), then start.
Writes installation files only under DIR; never overwrites .env.selfhost.
Setup URL/token pass directly to your terminal. Keep that output private.
HELP
}

version='' directory=./verwalino offline='' force=0
https='' port='' stage=''
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --force) force=1; shift ;;
        --version|--dir|--https|--port|--offline-bundle)
            [[ "$#" -ge 2 && -n "$2" && "$2" != --* ]] || fail "Missing value for $1."
            case "$1" in
                --version) version="$2" ;;
                --dir) directory="$2" ;;
                --https) https="$2" ;;
                --port) port="$2" ;;
                --offline-bundle) offline="$2" ;;
            esac
            shift 2 ;;
        *) fail "Unknown option: $1 (see --help)." ;;
    esac
done
[[ -z "$offline" || -n "$version" ]] || fail '--offline-bundle requires --version vX.Y.Z.'
stable_version() { [[ "$1" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; }
[[ -z "$version" ]] || stable_version "$version" || fail 'Use a stable version vX.Y.Z (no prereleases or leading zeroes).'

# Resolve paths before changing directory. Never inherit an external config path.
[[ "$directory" = /* ]] || directory="$PWD/$directory"
[[ -z "$offline" || "$offline" = /* ]] || offline="$PWD/$offline"
[[ ! -L "$directory" ]] || fail 'Symlink installation directories are refused.'
export VERWALINO_CONFIG="$directory/.env.selfhost"
[[ ! -L "$VERWALINO_CONFIG" ]] || fail 'Symlink configurations are refused.'
if [[ -e "$VERWALINO_CONFIG" ]]; then
    [[ "$force" = 1 ]] || fail 'Configuration exists; use --force to retry the same release without changing it.'
    [[ -z "$https" && -z "$port" ]] || fail '--force cannot reconfigure an existing installation.'
fi
for path in scripts infra infra/db install.sh scripts/self-host.sh infra/docker-compose.yml \
            infra/Caddyfile infra/db/prod-init.sh env.example IMAGE_DIGEST SHA256SUMS SHA256SUMS.sigstore.json; do
    [[ ! -L "$directory/$path" ]] || fail "Symlink destination refused: $path."
done

if [[ -d "$directory" && ! -e "$VERWALINO_CONFIG" && "$force" != 1 ]]; then
    for path in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
        [[ ! -e "$path" && ! -L "$path" ]] \
            || fail 'Installation directory is not empty; use --force to replace installation files.'
    done
fi

command -v docker >/dev/null || fail 'Docker with Compose v2 is required.'
compose_version=$(docker compose version --short 2>/dev/null) || fail 'Docker Compose v2 >= 2.24 is required.'
[[ "$compose_version" =~ ^v?2\.([0-9]+)\. ]] && (( 10#${BASH_REMATCH[1]} >= 24 )) \
    || fail 'Docker Compose v2 >= 2.24 is required.'
docker info >/dev/null 2>&1 || fail 'Docker daemon is unreachable; start Docker and check your access.'
command -v openssl >/dev/null || fail 'OpenSSL is required to generate installation secrets.'
if command -v curl >/dev/null; then downloader=curl
elif command -v wget >/dev/null; then downloader=wget
else fail 'Install curl or wget to download release assets.'
fi
if command -v sha256sum >/dev/null; then checksum=sha256sum
elif command -v shasum >/dev/null; then checksum=shasum
else fail 'Install sha256sum or shasum to verify release checksums.'
fi

download() {
    if [[ "$downloader" = curl ]]; then
        curl -fsSL --proto '=https' --proto-redir '=https' -o "$2" "$1" || fail "Download failed: $1."
    else
        wget --https-only -q -O "$2" "$1" || fail "Download failed: $1."
    fi
}
cleanup() { [[ -z "$stage" ]] || rm -rf "$stage"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
umask 077
mkdir -p "$directory"
stage=$(mktemp -d "$directory/.install.XXXXXX")

if [[ -z "$version" ]]; then
    download 'https://api.github.com/repos/itskeKs/verwalino/releases/latest' "$stage/latest.json"
    version=$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$stage/latest.json")
    if grep -Eq '"prerelease"[[:space:]]*:[[:space:]]*true' "$stage/latest.json"; then
        fail 'The API did not return a stable release.'
    fi
    stable_version "$version" || fail 'The API did not return a stable version vX.Y.Z.'
fi
printf 'Installing Verwalino %s in %s\n' "$version" "$directory"

# GitHub assets are flat; the verified files are arranged for self-host.sh below.
payload=(install.sh self-host.sh docker-compose.yml Caddyfile prod-init.sh env.example IMAGE_DIGEST)
assets=("${payload[@]}" SHA256SUMS SHA256SUMS.sigstore.json)
for file in "${assets[@]}"; do
    if [[ -n "$offline" ]]; then
        [[ -f "$offline/$file" && ! -L "$offline/$file" ]] || fail "Missing regular bundle file: $file."
        cp "$offline/$file" "$stage/$file"
    else
        download "https://github.com/itskeKs/verwalino/releases/download/$version/$file" "$stage/$file"
    fi
done

identity="https://github.com/itskeKs/verwalino/.github/workflows/release-image.yaml@refs/tags/$version"
issuer=https://token.actions.githubusercontent.com
if command -v cosign >/dev/null; then
    verify_args=(verify-blob --bundle "$stage/SHA256SUMS.sigstore.json"
        --certificate-identity "$identity" --certificate-oidc-issuer "$issuer")
    if [[ -n "$offline" ]]; then
        [[ -f "$offline/trusted_root.json" && ! -L "$offline/trusted_root.json" ]] \
            || fail 'Offline cosign verification requires independently trusted trusted_root.json in the bundle directory.'
        cp "$offline/trusted_root.json" "$stage/trusted_root.json"
        verify_args+=(--trusted-root "$stage/trusted_root.json")
    fi
    TUF_ROOT="$stage/.sigstore" cosign "${verify_args[@]}" "$stage/SHA256SUMS" \
        || fail 'Checksum signature verification failed.'
else
    printf 'Cosign is not installed; checksum signature has NOT been verified.\n'
    printf 'Install cosign, then verify (or verify a downloaded bundle before running it):\n'
    printf 'cosign verify-blob --bundle %q --certificate-identity %q --certificate-oidc-issuer %q %q\n' \
        "$directory/SHA256SUMS.sigstore.json" "$identity" "$issuer" "$directory/SHA256SUMS"
fi

# Do not let an incomplete manifest, duplicate entry or foreign path skip a file.
seen='|' count=0
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^([a-f0-9]{64})[[:space:]][\ *]([a-zA-Z0-9._-]+)$ ]] || fail 'Malformed checksum entry.'
    expected="${BASH_REMATCH[1]}" file="${BASH_REMATCH[2]}"
    case "$file" in
        install.sh|self-host.sh|docker-compose.yml|Caddyfile|prod-init.sh|env.example|IMAGE_DIGEST) ;;
        *) fail "Unexpected checksum file: $file." ;;
    esac
    [[ "$seen" != *"|$file|"* ]] || fail "Duplicate checksum entry: $file."
    seen="$seen$file|"
    count=$((count + 1))
    if [[ "$checksum" = sha256sum ]]; then actual=$(sha256sum "$stage/$file")
    else actual=$(shasum -a 256 "$stage/$file")
    fi
    [[ "${actual%% *}" = "$expected" ]] || fail "Checksum mismatch: $file."
done < "$stage/SHA256SUMS"
[[ "$count" = "${#payload[@]}" ]] || fail 'Checksum manifest does not cover every bundle file.'
printf 'All %s release payload checksums verified.\n' "$count"

digest=$(cat "$stage/IMAGE_DIGEST")
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'IMAGE_DIGEST must contain one SHA-256 image index digest.'
image="ghcr.io/itskeks/verwalino@$digest"
if [[ -e "$VERWALINO_CONFIG" ]]; then
    saved_image=$(sed -n 's/^VERWALINO_IMAGE=//p' "$VERWALINO_CONFIG")
    [[ "$saved_image" = "$image" ]] || fail '--force cannot change the configured image; select the original --version.'
fi

mkdir -p "$directory/scripts" "$directory/infra/db"
chmod 755 "$stage/install.sh" "$stage/self-host.sh" "$stage/prod-init.sh"
mv "$stage/self-host.sh" "$directory/scripts/self-host.sh"
mv "$stage/docker-compose.yml" "$directory/infra/docker-compose.yml"
mv "$stage/Caddyfile" "$directory/infra/Caddyfile"
mv "$stage/prod-init.sh" "$directory/infra/db/prod-init.sh"
for file in install.sh env.example IMAGE_DIGEST SHA256SUMS SHA256SUMS.sigstore.json; do
    mv "$stage/$file" "$directory/$file"
done
cd "$directory"
if [[ -e "$VERWALINO_CONFIG" ]]; then
    bash scripts/self-host.sh init </dev/null || fail 'Self-host init failed.'
else
    init_args=(--image "$image")
    [[ -z "$https" ]] || init_args+=(--https "$https")
    [[ -z "$port" ]] || init_args+=(--port "$port")
    bash scripts/self-host.sh init "${init_args[@]}" </dev/null || fail 'Self-host init failed.'
fi
help_text=$(bash scripts/self-host.sh help </dev/null 2>&1 || true)
if [[ "$help_text" == *doctor* ]]; then
    bash scripts/self-host.sh doctor </dev/null || fail 'Self-host doctor check failed.'
fi
# Keep stdout/stderr attached: never capture or persist the setup token.
bash scripts/self-host.sh start </dev/null || fail 'Self-host start failed; retry this release with --force after resolving the error.'
}

main "$@"
