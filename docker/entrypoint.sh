#!/bin/sh
# Runtime containers only start the application. Operations services supply an
# explicit command (alembic/admin CLI), so no privileged DB URL is ever needed
# on the default app path.
set -eu

if [ "$#" -eq 0 ]; then
  exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "$WEB_CONCURRENCY" \
    --proxy-headers \
    --forwarded-allow-ips="$FORWARDED_ALLOW_IPS"
fi

exec "$@"
