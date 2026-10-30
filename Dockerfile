# syntax=docker/dockerfile:1
#
# Digests below were resolved as multi-platform manifest lists on 2026-08-27.
# Keep them pinned: a tag is not an immutable deployment input.

# Only static assets reach runtime; run frontend checks without target emulation.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run lint && npm test && npm run build

FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS backend-builder
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra ops --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev --extra ops --extra ocr --no-editable

FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HV_FRONTEND_DIST=/app/frontend_dist \
    HV_DOCUMENT_STORAGE_ROOT=/data/documents \
    PATH=/app/.venv/bin:$PATH

# WeasyPrint's runtime libraries. The global pip/ensurepip tooling is removed
# after installation; application commands use only the locked virtualenv.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
      libcairo2 libgdk-pixbuf-2.0-0 libffi8 \
      libbrotli1 libexpat1 libpcre2-8-0 libssh2-1 perl-base \
      fonts-dejavu-core shared-mime-info \
      poppler-utils qpdf tesseract-ocr tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
              /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /data/documents /app/frontend_dist

WORKDIR /app
COPY --from=backend-builder --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 backend/app ./app
COPY --chown=10001:10001 backend/alembic.ini ./alembic.ini
COPY --chown=10001:10001 backend/migrations ./migrations
COPY --from=frontend --chown=10001:10001 /fe/dist ./frontend_dist
COPY docker/entrypoint.sh docker/worker-entrypoint.sh /usr/local/bin/
RUN chmod 0555 /usr/local/bin/entrypoint.sh /usr/local/bin/worker-entrypoint.sh \
    && chown -R 10001:10001 /data /app \
    && test ! -e /usr/local/lib/python3.12/site-packages/pip \
    && ! python -m pip --version >/dev/null 2>&1 \
    && ! /usr/local/bin/python -m pip --version >/dev/null 2>&1 \
    && python -c 'import weasyprint; import app.main' \
    && alembic --version \
    && python -m app.admin_cli --help >/dev/null \
    && python -m app.workers.declaration_import --help >/dev/null

USER 10001:10001
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
