---
description: Session organization context and API isolation
paths:
  - "backend/app/api/**"
  - "backend/app/core/tenant.py"
  - "backend/app/core/db.py"
---

# API layer

- Derive the active organization from the authenticated session through
  `backend/app/core/tenant.py`; never trust an organization ID supplied by the client.
- Keep organization filters and transaction-local RLS context. A foreign record
  must look absent (404), including when used as a parent or foreign key.
- Reuse existing authorization dependencies and service boundaries; do not bypass
  checks with a privileged database connection.
- Every new or changed route joins `features/organization_isolation.feature` and
  the route isolation sweep in issue #4. Reuse
  `backend/tests/test_tenant_isolation.py` and the BDD step library.
