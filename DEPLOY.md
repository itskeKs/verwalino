# Deployment

The whole app ships as **one Docker image**: a multi-stage build compiles the
React frontend to static files, and the FastAPI backend serves both the `/api`
and the built SPA. Database migrations run automatically on container start.

```
Browser ──HTTPS──> [reverse proxy / TLS] ──HTTP :8000──> app container ──> PostgreSQL
                                                          (API + SPA)        (RLS, 2 roles)
```

## Files

| File | Purpose |
| --- | --- |
| `Dockerfile` | Multi-stage image (frontend build → backend runtime serving the SPA) |
| `docker/entrypoint.sh` | `alembic upgrade head`, then start uvicorn |
| `docker-compose.prod.yml` | App + PostgreSQL for a single host |
| `infra/db/prod-init.sh` | Creates the `app_rw` role from `APP_RW_PASSWORD` |
| `.gitlab-ci.yml` | `test` → `build` (Kaniko → registry) → `deploy` (template) |
| `.env.example` | Required environment variables |
| `app/admin_cli.py` | Bootstrap the first organization + admin user |

## Run locally (prod-like)

```bash
cp .env.example .env        # then edit the secrets
docker compose -f docker-compose.prod.yml up -d --build
# first admin (fresh DB has no login):
docker compose -f docker-compose.prod.yml exec app \
  python -m app.admin_cli --org "Meine Hausverwaltung" \
  --email admin@example.de --password 'a-strong-password' --name "Admin"
# open http://localhost:8000   (set SESSION_COOKIE_SECURE=false in .env for plain HTTP)
```

## Database roles (RLS)

Two PostgreSQL roles, as in development:
- **`app_migrator`** — owns the tables, runs migrations (the DB superuser created
  via `POSTGRES_USER`). Used only by `alembic` and `admin_cli`.
- **`app_rw`** — the role the running app connects as; `NOBYPASSRLS`. Created by
  `infra/db/prod-init.sh` from `APP_RW_PASSWORD`; table-level grants are issued by
  the migrations. Row-Level Security is `FORCE`d on every tenant table.

Migrations run on every container start and are idempotent.

## GitLab CI/CD

Pipeline stages (`.gitlab-ci.yml`):

1. **test** — spins up a throwaway `postgres:16` service, creates `app_rw`, runs
   the full backend suite (`pytest`).
2. **build** — builds the image with **Kaniko** (no Docker daemon needed) and
   pushes `…:$CI_COMMIT_SHORT_SHA` + `…:latest` to the project Container Registry.
   Runs on the default branch and on tags.
3. **deploy** — **template**, manual trigger on the default branch. The example
   SSHes to a host and runs `docker compose pull && up -d`. **Adapt it to your
   target** (VM + compose, Kubernetes, a PaaS, …).

Registry auth in `build` uses the built-in `$CI_REGISTRY_*` variables — nothing
to configure. For the `deploy` template, add these **CI/CD variables**
(Settings → CI/CD → Variables; mask + protect the key):

| Variable | Meaning |
| --- | --- |
| `SSH_PRIVATE_KEY` | Key authorized on the deploy host |
| `DEPLOY_HOST` | Host name / IP |
| `DEPLOY_USER` | SSH user (in the `docker` group) |

The deploy host needs Docker, a checkout of `docker-compose.prod.yml` + a real
`.env` in `/opt/hausverwaltung`, and `docker login` to the registry.

## Required environment variables (the app)

`HV_`-prefixed (see `app/core/config.py`); compose builds them from `.env`:

| Variable | Notes |
| --- | --- |
| `HV_DATABASE_URL` | `app_rw` async URL (the running app) |
| `HV_DATABASE_URL_MIGRATOR` | `app_migrator` async URL |
| `HV_DATABASE_URL_SYNC` | `app_migrator` **psycopg2** URL (alembic + admin_cli) |
| `HV_SESSION_SECRET` | Long random secret — **must** be changed from the default |
| `HV_SESSION_COOKIE_SECURE` | `true` in production (HTTPS); `false` only for plain HTTP |
| `HV_DOCUMENT_STORAGE_ROOT` | Path for uploaded files (image default `/data/documents`) |

## Persistence & security checklist

- **Volumes**: `pgdata` (database) and `documents` (uploaded invoice/mandate
  files) — back both up. Uploaded documents live on disk, not in the DB.
- **TLS**: terminate HTTPS in a reverse proxy in front of `:8000` and keep
  `SESSION_COOKIE_SECURE=true`. The session cookie is `HttpOnly` + `SameSite=Lax`.
- **Secrets**: set strong `MIGRATOR_PASSWORD`, `APP_RW_PASSWORD`, `SESSION_SECRET`.
  Never commit `.env`.
- **No demo data** is seeded in production — use `admin_cli` for the first login.
- GoBD/DSGVO obligations and all `[§18/fachlich]` assumptions still require the
  professional sign-off noted in `docs/roadmap.md`.
