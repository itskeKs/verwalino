#!/bin/sh
# Start the full local development environment for testing.
#
#   scripts/dev-local.sh
#
# Starts the disposable test database (if not already running), runs
# migrations, creates a test admin user, starts the FastAPI backend on
# port 18000 and the Vite dev server on port 13000.
#
# The production API (port 8000) is never touched.

set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA="$ROOT/infra"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

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
uv run python -m app.admin_cli --org "Test Hausverwaltung" --email test@example.de --name "Test Admin" --password "testpassword" 2>/dev/null || true
cd "$ROOT"

# ------------------------------------------------------------------
# 5. Start FastAPI backend on port 18000
# ------------------------------------------------------------------
echo "Starting FastAPI backend on :18000..."
cd "$BACKEND"
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
uv run vite --host 127.0.0.1 --port 13000 &
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

# Cleanup on exit
cleanup() {
  echo ""
  echo "Stopping services..."
  kill $BACKEND_PID 2>/dev/null || true
  kill $VITE_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait
