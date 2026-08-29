# Remove Gateway Basic Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the outer Traefik Basic-Auth prompt while keeping HTTPS hardening, login throttling, secure application sessions, and all data authorization intact.

**Architecture:** Traefik continues to terminate TLS and apply the security, upload-buffer, and login-rate middlewares, but no longer declares or attaches a Basic-Auth middleware. The public smoke gate treats the health endpoint and SPA as public, then proves that the application session endpoint still rejects an unauthenticated request before exercising the normal admin login.

**Tech Stack:** Docker Compose, Traefik v2 labels, POSIX shell deployment tests, GitLab CI, FastAPI application authentication.

---

### Task 1: Specify the passwordless gateway behavior

**Files:**
- Modify: `scripts/test-deploy-production.sh`

- [ ] **Step 1: Add failing regression assertions**

Before executing the fake deployment, assert that the production deploy helper contains neither a gateway-password prompt nor `--netrc-file`, that the Compose file contains no `basicauth` label, and that GitLab CI contains no `HV_SMOKE_GATEWAY_PASSWORD`. Change the fake public live response from a Basic-Auth `401` to `200`, and feed only `AlphaAdminPass12` to each deploy-helper invocation.

- [ ] **Step 2: Run the regression probe and verify RED**

Run:

```sh
sh scripts/test-deploy-production.sh
```

Expected: immediate `FAIL` because the current deployment still references gateway authentication.

### Task 2: Remove Basic Auth without weakening application authentication

**Files:**
- Modify: `docker-compose.prod.yml`
- Modify: `scripts/deploy-production.sh`
- Modify: `scripts/verify-production.sh`
- Modify: `scripts/test-deploy-production.sh`
- Modify: `.env.example`
- Modify: `.gitlab-ci.yml`
- Modify: `DEPLOY.md`

- [ ] **Step 1: Remove the Traefik Basic-Auth middleware**

Delete both `hausverwaltung-alpha-basic.basicauth.*` labels. Set the main router middleware chain to `hausverwaltung-alpha-security,hausverwaltung-alpha-buffer` and the login router chain to `hausverwaltung-alpha-security,hausverwaltung-alpha-buffer,hausverwaltung-alpha-login-rate`. Do not change TLS, security headers, body limits, rate limits, networks, ports, or container restrictions.

- [ ] **Step 2: Make the deployment gate accept one secret only**

Remove `gateway_password`, `gateway_netrc`, its cleanup, and all `--netrc-file` arguments. Read only `Admin password` from stdin. Replace `wait_for_public_gateway` with a bounded public-app wait that succeeds only on HTTPS `200` from `/api/health/live`. Keep the exact HTTP-to-HTTPS redirect test and security-header checks. Add an unauthenticated request to `/api/auth/session` that must return `401`, then retain the authenticated login/session/organization, cookie, traversal, upload-limit, runtime-role, and promotion gates.

- [ ] **Step 3: Update isolated runtime verification**

Remove the required `BASIC_AUTH_USERS` environment variable and bcrypt-label assertion. Instead, assert that the app container has no Basic-Auth labels and that both router middleware labels contain only the approved chains from Step 1.

- [ ] **Step 4: Remove gateway secrets from CI and documentation**

Delete `BASIC_AUTH_USERS` from `.env.example`; remove the pinned `httpd` verifier generation from the container-smoke job; remove `HV_SMOKE_GATEWAY_PASSWORD` and pass only `HV_SMOKE_ADMIN_PASSWORD` to the remote deploy helper. Rewrite current deployment documentation so it states that the login page is public while application data still requires the app session. Leave historical design and completed plan documents unchanged.

- [ ] **Step 5: Verify GREEN**

Run:

```sh
sh scripts/test-deploy-production.sh
docker compose --env-file .env.example -f docker-compose.prod.yml config >/dev/null
```

For Compose validation, supply temporary non-empty `MIGRATOR_PASSWORD`, `APP_RW_PASSWORD`, `SESSION_SECRET`, and immutable `APP_IMAGE` values in the process environment. Expected: the regression probe passes and rendered Compose contains no Basic-Auth middleware.

### Task 3: Verify and deploy the routing-only release

**Files:**
- Verify: all changed files and deployed Moon state

- [ ] **Step 1: Run the full local gates**

Run the deploy regression probe, shell syntax checks, Compose rendering and label assertions, backend test suite, frontend lint/tests/build/runtime audit, and `git diff --check`. Confirm no gateway verifier or gateway smoke-password reference remains in current operational files.

- [ ] **Step 2: Deploy through the hardened release workflow**

Transfer a new source release to `/home/chris/apps/hausverwaltung-alpha/releases/`, atomically remove the obsolete `BASIC_AUTH_USERS` line from the mode-0600 remote `.env`, and invoke the updated deploy helper with only the existing application-admin password through hidden standard input. Do not touch unrelated containers, networks, volumes, or databases.

- [ ] **Step 3: Verify the public boundary**

Require trusted HTTPS, permanent HTTP redirect, public `200` for `/` and `/api/health/ready`, no `WWW-Authenticate` header, all security headers, unauthenticated `401` from `/api/auth/session`, and successful admin login/session/organization checks. Confirm the app and database remain healthy, use no host ports, migration remains `0023`, runtime DB role remains `app_rw`, and all pre-existing foreign containers are unchanged.

- [ ] **Step 4: Preserve repository state**

Do not commit, push, or merge. Keep the existing hardening worktree and report the new uncommitted operational diff.
