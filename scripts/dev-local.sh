#!/bin/sh
# Start the full local development environment for testing.
#
#   scripts/dev-local.sh
#
# Starts the disposable test database (if not already running), runs
# migrations, creates a test admin user, starts the invoice worker and optional inbox worker, the
# FastAPI backend on port 18000 and the Vite dev server on port 13000.
#
# The production API (port 8000) is never touched.

set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA="$ROOT/infra"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BACKEND_PID=""
VITE_PID=""
INVOICE_WORKER_PID=""
INBOX_WORKER_PID=""

cleanup() {
  echo "Stopping local services..."
  for pid in "$BACKEND_PID" "$VITE_PID" "$INVOICE_WORKER_PID" "$INBOX_WORKER_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$BACKEND_PID" "$VITE_PID" "$INVOICE_WORKER_PID" "$INBOX_WORKER_PID"; do
    if [ -n "$pid" ]; then wait "$pid" 2>/dev/null || true; fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ------------------------------------------------------------------
# 1. Start disposable test database if not already running
# ------------------------------------------------------------------
if ! docker ps --format '{{.Names}}' | grep -q '^infra-postgres-1$'; then
  echo "Starting disposable test database..."
  docker compose -f "$INFRA/docker-compose.declaration-test.yml" up -d
  # Wait for health
  until docker compose -f "$INFRA/docker-compose.declaration-test.yml" exec -T postgres pg_isready -U app_migrator -d hausverwaltung; do
    echo "Waiting for PostgreSQL..."
    sleep 1
  done
  echo "Test database ready."
fi

# ------------------------------------------------------------------
# 2. Source test environment variables
# ------------------------------------------------------------------
. "$ROOT/scripts/test-env.sh"
export HV_DATABASE_URL_WORKER="postgresql+asyncpg://app_worker:worker_secret@127.0.0.1:55432/hausverwaltung"
export HV_DOCUMENT_STORAGE_ROOT="${HV_DOCUMENT_STORAGE_ROOT:-$(mktemp -d /tmp/hv-dev-documents.XXXXXX)}"
case "$HV_DOCUMENT_STORAGE_ROOT" in
  /tmp/*|/private/tmp/*) ;;
  *) echo "Local documents must use a separate directory under /tmp." >&2; exit 1 ;;
esac
mkdir -p "$HV_DOCUMENT_STORAGE_ROOT"
echo "Local document storage: $HV_DOCUMENT_STORAGE_ROOT"

# ------------------------------------------------------------------
# 3. Run migrations
# ------------------------------------------------------------------
echo "Running database migrations..."
cd "$BACKEND"
uv run alembic upgrade head
cd "$ROOT"

# ------------------------------------------------------------------
# 4. Create test admin user (idempotent)
# ------------------------------------------------------------------
echo "Ensuring test admin user exists..."
cd "$BACKEND"
uv run python -m app.admin_cli --org "Test Hausverwaltung" --email test@example.de --name "Test Admin" --password "testpassword"
cd "$ROOT"

# ------------------------------------------------------------------
# 5. Start FastAPI backend on port 18000
# ------------------------------------------------------------------
cd "$BACKEND"
echo "Checking invoice worker (database, storage, German OCR)..."
uv run python -m app.workers.invoice_extraction --healthcheck
echo "Checking inbox worker (database, storage, ClamAV signatures, German OCR)..."
if uv run python -m app.workers.workflow_runner --healthcheck; then
  echo "Starting local inbox worker..."
  uv run python -m app.workers.workflow_runner --all-organizations &
  INBOX_WORKER_PID=$!
else
  echo "Inbox worker skipped: check ClamAV (clamscan and signatures), worker database access, document storage and German OCR; API and UI will continue."
fi
echo "Starting local invoice worker..."
uv run python -m app.workers.invoice_extraction &
INVOICE_WORKER_PID=$!
echo "Starting FastAPI backend on :18000..."
uv run uvicorn app.main:app --host 127.0.0.1 --port 18000 &
BACKEND_PID=$!
cd "$ROOT"

# Wait for backend to be ready
echo "Waiting for backend..."
until curl -sf http://127.0.0.1:18000/api/health/live > /dev/null 2>&1; do
  echo "Waiting for backend..."
  sleep 1
done
echo "Backend ready on :18000."

# ------------------------------------------------------------------
# 6. Start Vite dev server on port 13000
# ------------------------------------------------------------------
echo "Starting Vite dev server on :13000..."
cd "$FRONTEND"
npm run dev -- --host 127.0.0.1 --port 13000 &
VITE_PID=$!
cd "$ROOT"

# Wait for Vite to be ready
echo "Waiting for Vite..."
until curl -sf http://127.0.0.1:13000 > /dev/null 2>&1; do
  echo "Waiting for Vite..."
  sleep 1
done
echo "Vite ready on :13000."

echo ""
echo "=========================================="
echo "Local dev environment ready."
echo "  Frontend: http://127.0.0.1:13000"
echo "  Backend:  http://127.0.0.1:18000"
echo "  Test DB:  :55432"
echo ""
echo "Login as test@example.de / testpassword"
echo ""
echo "Press Ctrl+C to stop."

if [ -n "$INBOX_WORKER_PID" ]; then
  wait "$BACKEND_PID" "$VITE_PID" "$INVOICE_WORKER_PID" "$INBOX_WORKER_PID"
else
  wait "$BACKEND_PID" "$VITE_PID" "$INVOICE_WORKER_PID"
fi
