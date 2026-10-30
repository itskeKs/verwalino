#!/usr/bin/env bash
# Assemble the same unsigned payload for release signing and checkout scenarios.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "${DIGEST:-}" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'DIGEST must be a SHA-256 image manifest digest.' >&2; exit 1; }
[[ "$#" -le 1 ]] || { echo 'Usage: DIGEST=sha256:... bash scripts/build-release-bundle.sh [DIR]' >&2; exit 1; }
destination="${1:-release-assets}"
mkdir "$destination"
cp "$ROOT/install.sh" "$ROOT/scripts/self-host.sh" "$ROOT/infra/docker-compose.yml" \
    "$ROOT/infra/Caddyfile" "$ROOT/infra/db/prod-init.sh" "$destination/"
cp "$ROOT/.env.example" "$destination/env.example"
printf '%s\n' "$DIGEST" > "$destination/IMAGE_DIGEST"
cd "$destination"
if command -v sha256sum >/dev/null; then checksum=(sha256sum)
else checksum=(shasum -a 256)
fi
"${checksum[@]}" install.sh self-host.sh docker-compose.yml Caddyfile prod-init.sh env.example IMAGE_DIGEST > SHA256SUMS
