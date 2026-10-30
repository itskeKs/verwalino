# Runtime and Supply-Chain Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Produce an audited, locked, non-root image and a Traefik-integrated production stack with green frontend/backend quality gates.

**Architecture:** uv.lock and package-lock.json are the only dependency resolution inputs. A multi-stage image contains the built SPA, installed backend, migrations, and no app credentials. Compose separates db, one-shot migrate/bootstrap, and runtime app; Traefik labels provide the public alpha boundary without changing the shared proxy.

**Tech Stack:** uv, Python 3.12, Node 22, React 19, Vite/Vitest, Docker/Compose, PostgreSQL 16, Traefik 2.11, GitLab CI, pip-audit, npm audit, Trivy.

---

## File structure

- Modify **backend/pyproject.toml** and create **backend/uv.lock**.
- Modify **frontend/package.json**, **frontend/package-lock.json**, **frontend/vite.config.ts**, and **frontend/eslint.config.js**.
- Create **frontend/src/test/setup.ts**, **frontend/src/api/client.test.ts**, **frontend/src/auth/AuthContext.test.tsx**, and **frontend/src/pages/LoginPage.test.tsx**.
- Create **frontend/src/auth/useAuth.ts** and leave
  **frontend/src/auth/AuthContext.tsx** as the component-only provider module.
- Modify **frontend/src/api/endpoints.ts** and **frontend/src/api/types.ts** additively; types.ts already contains user work.
- Fix the two real errors in **frontend/src/pages/SettlementDetailPage.tsx**.
- Modify **backend/app/core/config.py**, **backend/app/main.py**, **Dockerfile**, and **docker/entrypoint.sh**.
- Modify **infra/db/prod-init.sh**, **docker-compose.prod.yml**, **.env.example**, **.dockerignore**, **.gitlab-ci.yml**, and **DEPLOY.md**.
- Create **scripts/verify-production.sh** for local/container smoke checks.

### Task 1: Baseline audits and exact version resolution

- [ ] **Step 1: Capture the protected tree**

Run:

~~~bash
git status --short --branch
git diff --stat
git diff --check
git diff -- frontend/src/api/types.ts \
  frontend/src/pages/CostEntriesPage.tsx \
  frontend/src/pages/CostEntryDetailPage.tsx \
  frontend/src/pages/InvoiceDocumentDetailPage.tsx
~~~

Expected: the existing additive user work is visible and preserved. Do not
mechanically rewrite the three dirty pages.

- [ ] **Step 2: Record current quality/audit failures**

Run:

~~~bash
cd frontend
npm run build
npm run lint
npm audit --omit=dev
cd ../backend
uv lock --check || true
uvx --from pip-audit pip-audit
~~~

Expected before implementation: frontend build passes, lint reports the known
35 errors, npm reports the known runtime advisories, no uv.lock exists, and
the backend audit identifies the known WeasyPrint/Python packaging findings.

- [ ] **Step 3: Resolve patched versions from authoritative package indexes**

Run:

~~~bash
npm view react-router-dom version
python3 -m pip index versions weasyprint
docker buildx imagetools inspect node:22-bookworm-slim
docker buildx imagetools inspect python:3.12-slim-bookworm
docker buildx imagetools inspect postgres:16-alpine
~~~

Choose the newest compatible patched React Router and WeasyPrint versions.
Record multi-platform manifest digests for the three base images in the
Dockerfile/Compose comments. Never use a digest copied from an unrelated
architecture-specific manifest.

### Task 2: Lock backend production dependencies

- [ ] **Step 1: Update dependency intent**

In **backend/pyproject.toml**:

- raise WeasyPrint to the patched major range beginning at 69,
- move psycopg2-binary into an **ops** optional extra used by migrations and
  bootstrap,
- retain the existing dev extra and application dependency boundaries.

The intended shape is:

~~~toml
"weasyprint>=69,<70",

[project.optional-dependencies]
ops = [
    "psycopg2-binary>=2.9,<3",
]
dev = [
    # existing test dependencies remain
]
~~~

- [ ] **Step 2: Generate and verify uv.lock**

Run:

~~~bash
cd backend
uv lock --python 3.12
uv lock --check
uv sync --locked --extra dev --extra ops
uv run pytest -q
~~~

Expected: uv.lock is created, a second lock check makes no change, and the full
backend suite passes from the locked environment.

- [ ] **Step 3: Audit the locked production set**

Run:

~~~bash
cd backend
uv export --locked --no-dev --extra ops --format requirements-txt \
  --output-file /tmp/hausverwaltung-runtime-requirements.txt
uvx --from pip-audit pip-audit \
  --requirement /tmp/hausverwaltung-runtime-requirements.txt
~~~

Expected: no known high or critical runtime vulnerability. Remove the
temporary export after inspection.

### Task 3: Add frontend tests and current-session loading

- [ ] **Step 1: Install exact test tooling and patched router**

Run npm install with exact versions resolved in Task 1:

~~~bash
cd frontend
npm install --save-exact react-router-dom@7.18.2
npm install --save-dev --save-exact \
  vitest jsdom @testing-library/react @testing-library/jest-dom \
  @testing-library/user-event
~~~

If the authoritative query returned a newer patched compatible router than
7.18.2, use that exact value and document it in the handoff. package-lock.json
must be the only transitive resolution source.

- [ ] **Step 2: Add the test runner**

Add scripts to package.json:

~~~json
{
  "test": "vitest run",
  "test:watch": "vitest"
}
~~~

Import defineConfig from vitest/config in **frontend/vite.config.ts**, retain the
React plugin and development proxy, and add:

~~~typescript
test: {
  environment: "jsdom",
  setupFiles: ["./src/test/setup.ts"],
  restoreMocks: true,
},
~~~

Create **frontend/src/test/setup.ts**:

~~~typescript
import "@testing-library/jest-dom/vitest";
~~~

- [ ] **Step 3: Write failing API client tests**

Create **frontend/src/api/client.test.ts** with tests that mock fetch and prove:

- credentials is include,
- JSON errors become ApiError with status/detail,
- 204 returns undefined,
- multipart requests do not force a JSON Content-Type.

Representative assertion:

~~~typescript
expect(fetch).toHaveBeenCalledWith(
  "/api/example",
  expect.objectContaining({ credentials: "include" }),
);
~~~

- [ ] **Step 4: Replace sessionStorage role recovery**

Add GET /api/auth/session to **frontend/src/api/endpoints.ts** using the existing
LoginResponse type. In AuthProvider, initial loading calls that endpoint and
stores only its server response in React state. Remove all hv_session
sessionStorage reads/writes.

Move AuthContextValue, the context object, and useAuth into
**frontend/src/auth/useAuth.ts** so AuthContext.tsx exports only AuthProvider and
the Fast Refresh rule remains enabled. Update all useAuth imports with a
targeted search, not a global formatter.

- [ ] **Step 5: Test AuthProvider and LoginPage**

Create tests proving:

- successful session probe renders authenticated children,
- 401 session probe renders logged-out state without console noise,
- login posts email/password and stores the returned principal only in memory,
- logout calls the endpoint and clears state,
- LoginPage displays the generic API error and disables submit while pending.

Run:

~~~bash
cd frontend
npm test
~~~

Expected: all new tests pass.

### Task 4: Make lint green without rewriting every loader

- [ ] **Step 1: Configure the reviewed effect exception**

In **frontend/eslint.config.js**, keep the recommended Hook configuration but
set only:

~~~javascript
rules: {
  "react-hooks/set-state-in-effect": "off",
},
~~~

Add a comment explaining that the project intentionally starts cancellable
asynchronous resource loaders from effects and will revisit the rule when a
query abstraction replaces those loaders. Do not disable exhaustive-deps,
immutability, rules-of-hooks, or the full plugin.

- [ ] **Step 2: Fix SettlementDetailPage real errors**

Move initStructuredInputs above the callback that captures it, or convert it to
a useCallback declared before reload. Remove the unused _allUnits parameter and
all corresponding arguments. Preserve behavior and avoid unrelated formatting.

- [ ] **Step 3: Verify all frontend gates**

Run:

~~~bash
cd frontend
npm run lint
npm test
npm run build
npm audit --omit=dev
~~~

Expected: lint, tests, build, and runtime audit all pass with zero reported
runtime vulnerabilities. The existing Vite bundle warning may remain only if
it is still below the explicitly documented performance threshold; it is not a
security failure.

### Task 5: Make production app configuration explicit

- [ ] **Step 1: Write failing production-config tests**

Extend **backend/tests/test_main_security.py** to construct Settings with:

~~~python
Settings(
    session_cookie_name="__Host-hv_session",
    session_cookie_secure=True,
    allowed_hosts="verwaltung.<customer>.immo",
    enable_api_docs=False,
)
~~~

Assert parsed allowed hosts are exact and production docs URLs become None.

- [ ] **Step 2: Add production settings**

Modify **backend/app/core/config.py**:

~~~python
allowed_hosts: str = "localhost,127.0.0.1,test"
enable_api_docs: bool = True

@property
def allowed_host_list(self) -> list[str]:
    return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]
~~~

Build FastAPI with conditional docs/openapi URLs and add TrustedHostMiddleware
using allowed_host_list. Production Compose sets only verwaltung.<customer>.immo.

- [ ] **Step 3: Run main/auth tests**

Run:

~~~bash
cd backend
uv run pytest -q tests/test_main_security.py tests/test_auth_hardening.py
~~~

Expected: pass.

### Task 6: Build an immutable non-root runtime image

- [ ] **Step 1: Refactor the Dockerfile into locked stages**

Use these responsibilities:

~~~dockerfile
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run lint && npm test && npm run build

FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS backend-builder
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra ops --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev --extra ops --no-editable
~~~

Re-resolve these manifest-list digests in Task 1. If an upstream tag has moved,
review the new image and deliberately replace the recorded digest; never drop
the digest merely to make a pull succeed.

The final Python stage uses the same pinned Python manifest, installs only
WeasyPrint runtime libraries, copies /app/.venv to the identical final path,
copies Alembic files, migrations, and built frontend, creates UID 10001, and
runs as 10001:10001. Keeping the venv path identical preserves generated
console-script shebangs. The final stage must not run pip or npm.

- [ ] **Step 2: Make the entrypoint runtime-only**

Replace docker/entrypoint.sh with a runtime dispatcher:

~~~sh
#!/bin/sh
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
~~~

Compose supplies defaults for WEB_CONCURRENCY and FORWARDED_ALLOW_IPS. No
Alembic call and no privileged URL is allowed on the default app path. The
migrate/bootstrap services pass an explicit command, which the dispatcher
executes without first starting Uvicorn.

- [ ] **Step 3: Build and inspect locally**

Run:

~~~bash
docker build --pull -t hausverwaltung:hardening-test .
docker image inspect hausverwaltung:hardening-test \
  --format '{{.Config.User}} {{json .Config.Entrypoint}}'
docker run --rm --entrypoint python hausverwaltung:hardening-test \
  -c 'import app, weasyprint; print("imports-ok")'
~~~

Expected: user 10001:10001 (or appuser with UID 10001), runtime-only
entrypoint, and imports-ok.

### Task 7: Separate migrate/bootstrap/app in hardened Compose

- [ ] **Step 1: Fix safe database-role initialization**

Change **infra/db/prod-init.sh** to pass APP_RW_PASSWORD as a psql variable and
quote it with PostgreSQL format %L. Do not interpolate the password into a
dollar-quoted heredoc.

The core pattern is:

~~~sh
psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_rw_password="$APP_RW_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE app_rw LOGIN PASSWORD %L NOBYPASSRLS NOCREATEDB NOCREATEROLE',
  :'app_rw_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw')
\gexec
GRANT CONNECT ON DATABASE hausverwaltung TO app_rw;
SQL
~~~

Deployment secrets remain URL-safe hexadecimal, so database URLs need no
ambiguous percent encoding.

- [ ] **Step 2: Implement the service split**

docker-compose.prod.yml must have:

- db using
  postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685,
  with no ports, healthcheck, pgdata, log limits, and only the internal backend
  network;
- migrate as a one-shot ops-profile service with only HV_DATABASE_URL_SYNC;
- bootstrap as a one-shot ops-profile service with only
  HV_DATABASE_URL_SYNC;
- app with only HV_DATABASE_URL plus non-secret runtime settings;
- app with no ports, read_only, tmpfs /tmp, cap_drop ALL,
  no-new-privileges, UID 10001, pids/memory/CPU/log limits, healthcheck,
  documents volume, backend network, and external traefik network.

The running app environment must not contain HV_DATABASE_URL_MIGRATOR or
HV_DATABASE_URL_SYNC.

- [ ] **Step 3: Add exact Traefik labels**

Define two routers on the app:

~~~yaml
traefik.enable: "true"
traefik.docker.network: "traefik"
traefik.http.routers.hausverwaltung-alpha.rule: "Host(\`verwaltung.<customer>.immo\`)"
traefik.http.routers.hausverwaltung-alpha.entrypoints: "websecure"
traefik.http.routers.hausverwaltung-alpha.tls: "true"
traefik.http.routers.hausverwaltung-alpha.tls.certresolver: "letsencrypt"
traefik.http.routers.hausverwaltung-alpha.service: "hausverwaltung-alpha"
traefik.http.services.hausverwaltung-alpha.loadbalancer.server.port: "8000"
~~~

Use the same hausverwaltung-alpha prefix for every router and middleware name
to prevent global Docker-provider collisions. The general router applies Basic
Auth, security headers, and a 30 MiB buffering limit. A higher-priority
Host+Path(/api/auth/login) router applies the same middlewares plus a
5-per-minute, burst-5 rate limit.

Security headers must implement the approved CSP:

~~~text
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
font-src 'self' data:; img-src 'self' data:; connect-src 'self';
object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
~~~

Set HSTS for one year, content-type nosniff, frame deny, and referrer-policy
no-referrer. Compose interpolation of a bcrypt hash must preserve dollar signs;
the .env example documents doubling them for Compose.

- [ ] **Step 4: Validate resolved Compose**

Use non-secret test values:

~~~bash
MIGRATOR_PASSWORD=0123456789abcdef \
APP_RW_PASSWORD=fedcba9876543210 \
SESSION_SECRET=0123456789abcdef0123456789abcdef \
BASIC_AUTH_USERS='alpha:$$2y$$05$$abcdefghijklmnopqrstuuuuuuuuuuuuuuuuuuuuuuuuuu' \
APP_IMAGE=hausverwaltung:hardening-test \
docker compose -f docker-compose.prod.yml config > /tmp/hv-compose.yml
~~~

Inspect the output, then remove it. Expected: no host ports for app/db, app has
no migrator variables, external network is named traefik, internal network is
internal, and all labels resolve.

### Task 8: Add image smoke, audits, CI, and runbook

- [ ] **Step 1: Create scripts/verify-production.sh**

The script accepts a Compose file and performs:

~~~sh
docker compose -f "$HV_VERIFY_COMPOSE_FILE" up -d db
docker compose -f "$HV_VERIFY_COMPOSE_FILE" --profile ops run --rm migrate
docker compose -f "$HV_VERIFY_COMPOSE_FILE" up -d app
~~~

It waits with a bounded loop for Docker health, calls live and ready from
inside the app network, checks that docker inspect on app lacks migrator
variables, and exits nonzero on any failure. A trap stops only the verification
project; it never prunes global images or volumes.

- [ ] **Step 2: Expand .gitlab-ci.yml**

Add distinct jobs for:

- backend uv sync --locked and pytest,
- frontend npm ci, lint, test, build, npm audit --omit=dev,
- exported-lock pip-audit,
- immutable image build/push,
- SBOM and Trivy HIGH/CRITICAL scan with --ignore-unfixed using
  aquasec/trivy:0.69.0@sha256:33f816d414b9d582d25bb737ffa4a632ae34e222f7ec1b50252cef0ce2266006,
- container smoke against fresh PostgreSQL,
- manual deploy using SSH_KNOWN_HOSTS rather than live ssh-keyscan.

The deploy job uses an immutable commit tag, runs migrate once, starts app,
waits for readiness, and retains the previous tag. Do not place the alpha host,
SSH key, Basic Auth password, or application credentials in the repository.

- [ ] **Step 3: Rewrite DEPLOY.md and .env.example**

Document:

- domain verwaltung.<customer>.immo and existing Traefik network/resolver,
- URL-safe secret generation with openssl rand -hex 32,
- bcrypt Basic Auth verifier generation over stdin with
  httpd:2.4-alpine@sha256:1b766f17b84026429b7cb243317b142921b24432336e798bc881c43f45ed9567,
- db -> migrate -> app -> bootstrap order,
- runtime app contains no migrator secret,
- health/smoke/rollback commands,
- explicit no-backup alpha risk,
- password rotation updates PostgreSQL first, then .env, then affected service,
- no public app/db ports and no changes to foreign containers.

- [ ] **Step 4: Run all local gates**

Run:

~~~bash
cd backend && uv run pytest -q
cd ../frontend && npm ci && npm run lint && npm test && npm run build \
  && npm audit --omit=dev
cd ..
docker compose -f docker-compose.prod.yml config --quiet
docker build -t hausverwaltung:hardening-test .
~~~

Then run pip-audit, Trivy, and scripts/verify-production.sh with a disposable
Compose project. Expected: every command passes.

### Task 9: Final no-commit checkpoint

- [ ] **Step 1: Inspect scope and protected user work**

Run:

~~~bash
git diff --check
git status --short
git diff --stat
git diff -- frontend/src/api/types.ts \
  frontend/src/pages/CostEntriesPage.tsx \
  frontend/src/pages/CostEntryDetailPage.tsx \
  frontend/src/pages/InvoiceDocumentDetailPage.tsx
~~~

Expected: the original user additions remain and hardening changes are
additive. No secret or generated .env is tracked.

- [ ] **Step 2: Search for forbidden production exposure**

Run:

~~~bash
rg -n 'ports:|HV_DATABASE_URL_MIGRATOR|HV_DATABASE_URL_SYNC|APP_PORT' \
  docker-compose.prod.yml docker/entrypoint.sh DEPLOY.md
rg -n 'change-me|10zolotarev|<host>' \
  . --glob '!frontend/node_modules/**' --glob '!backend/.venv/**'
~~~

Expected: ports do not expose app/db, privileged URLs appear only in
migrate/bootstrap documentation/services, and no real secret or obsolete
domain is present.

- [ ] **Step 3: Record a no-commit checkpoint**

Do not commit. Return status, summary, changed_files, exact commands/results,
remaining image findings, deployment prerequisites, and follow_up.
