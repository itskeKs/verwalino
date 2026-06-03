# syntax=docker/dockerfile:1
#
# Single deployable image for the Hausverwaltung MVP:
#   - Stage 1 builds the React/Vite frontend to static files.
#   - Stage 2 is the FastAPI backend, which ALSO serves the built SPA
#     (HV_FRONTEND_DIST) so the whole app runs in one container behind /.
# Database migrations run on container start (see docker/entrypoint.sh).

# ---- Stage 1: build the frontend ----
FROM node:22-bookworm-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # -> /fe/dist

# ---- Stage 2: backend runtime (serves API + built SPA) ----
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HV_FRONTEND_DIST=/app/frontend_dist \
    HV_DOCUMENT_STORAGE_ROOT=/data/documents

# System libraries required by WeasyPrint (PDF rendering) -- needed at import time.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
      libcairo2 libgdk-pixbuf-2.0-0 libffi8 \
      fonts-dejavu-core shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the backend (runtime deps from pyproject) + psycopg2 for Alembic.
COPY backend/ ./
RUN pip install . psycopg2-binary

# Built SPA from stage 1.
COPY --from=frontend /fe/dist ./frontend_dist

# Entrypoint: migrate, then serve.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/documents \
    && chown -R appuser:appuser /data

USER appuser
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
