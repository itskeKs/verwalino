---
description: Alembic migrations and organization isolation
paths:
  - "backend/migrations/**"
---

# Migrations

- Never change a committed migration; add a new revision under
  `backend/migrations/versions/`. The new revision becomes the release target everywhere:
  `TARGET` and `SUPPORTED` in `backend/app/deployment.py` (the preflight refuses an unknown
  head), `DEPLOY.md`'s accepted head and `backend/tests/test_deployment_upgrade.py` move with
  it, and the production instance moves only by a release.
- Give every new organization-owned table an `organization_id`, appropriate foreign
  keys, ENABLE/FORCE RLS and an organization policy. Follow
  `docs/adr/0001-organization-isolation-rls.md`; global reference tables are distinct.
- Keep `downgrade()` non-empty and meaningful; never remove its work to pass a test.
  Reject a downgrade that would silently lose data.
- Extend `backend/tests/test_rls_coverage.py` for new tenant tables and use the
  existing migration tests. Observe the test DB and compose limits in AGENTS.md.
