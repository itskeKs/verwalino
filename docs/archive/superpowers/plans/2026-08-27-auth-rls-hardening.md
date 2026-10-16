# Auth and RLS Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Remove privileged database credentials from the running app, validate every session against current database state, and enforce safe credential handling.

**Architecture:** Migration 0023 exposes two narrowly scoped SECURITY DEFINER functions to app_rw. Login and per-request session validation use the existing app_rw pool through a focused auth_db module; only migrate/bootstrap jobs retain privileged URLs. Signed cookies carry identity only, while active state and role come from PostgreSQL on every authenticated request.

**Tech Stack:** PostgreSQL 16, Alembic, SQLAlchemy asyncio, FastAPI dependencies, Argon2id, itsdangerous, pytest/httpx.

---

## File structure

- Create **backend/migrations/versions/0023_auth_runtime_functions.py**: minimal auth lookup/session functions and grants.
- Create **backend/app/core/auth_db.py**: typed calls to those functions.
- Modify **backend/app/core/security.py**: identity-only session payload and robust password verification.
- Modify **backend/app/core/tenant.py**: async server-side principal validation before RBAC.
- Modify **backend/app/api/auth.py**: app_rw login lookup, uniform errors, session endpoint, hardened cookie.
- Modify **backend/app/api/users.py**: new-password policy.
- Modify **backend/app/admin_cli.py**: bootstrap password policy and non-secret CLI handling.
- Modify **backend/tests/conftest.py**: policy-compliant seed password and identity-only cookies.
- Create **backend/tests/test_auth_db_functions.py**.
- Create **backend/tests/test_auth_hardening.py**.
- Modify existing RBAC tests only to replace now-invalid short test passwords.

The untracked user migration 0022 is the immutable parent of the new migration.
Do not edit, rename, stage, or regenerate 0022.

### Task 1: Reconfirm migration and dirty-tree baseline

- [ ] **Step 1: Capture exact status and protected migration**

Run:

~~~bash
git status --short --branch
git diff --stat
git diff --check
sed -n '1,55p' backend/migrations/versions/0022_dunning_frozen_display_names.py
cd backend && alembic heads
~~~

Expected: 0022 is the single head and declares down_revision 0021. Existing
user changes match the recorded baseline.

- [ ] **Step 2: Run current auth/RLS regressions**

Run:

~~~bash
cd backend
pytest -q tests/test_tenant_isolation.py tests/test_rbac.py tests/test_rls_coverage.py
~~~

Expected: all pass before the auth refactor.

### Task 2: Add minimal database auth functions

- [ ] **Step 1: Write failing function and privilege tests**

Create **backend/tests/test_auth_db_functions.py** with real PostgreSQL cases:

~~~python
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_app_rw_can_lookup_one_login_without_tenant_context(
    app_rw_session, seed_data
):
    result = await app_rw_session.execute(
        text("SELECT * FROM public.app_auth_lookup(:email)"),
        {"email": "  ADMIN@ALPHA.DE  "},
    )
    row = result.one()
    assert row.user_id == seed_data["user_a_admin_id"]
    assert row.organization_id == seed_data["org_a_id"]
    assert row.role == "OWNER_ADMIN"


@pytest.mark.asyncio
async def test_app_rw_session_validation_returns_current_role(
    app_rw_session, seed_data
):
    result = await app_rw_session.execute(
        text("SELECT * FROM public.app_validate_session(:uid, :oid)"),
        {
            "uid": seed_data["user_a_readonly_id"],
            "oid": seed_data["org_a_id"],
        },
    )
    assert result.scalar_one() == "READ_ONLY"
~~~

Add cases proving:

- app_rw has EXECUTE on both exact signatures,
- PUBLIC does not have EXECUTE,
- direct unscoped SELECT from user still returns zero rows,
- an email occurring in two organizations returns zero rows,
- an inactive user and a mismatched organization return zero session rows.

Use temporary UUID rows and cleanup in try/finally. Never mutate the permanent
seed users without restoring them.

- [ ] **Step 2: Run the tests and verify RED**

Run:

~~~bash
cd backend
pytest -q tests/test_auth_db_functions.py
~~~

Expected: undefined-function failures.

- [ ] **Step 3: Create migration 0023**

Create **backend/migrations/versions/0023_auth_runtime_functions.py** with
revision 0023 and down_revision 0022. The upgrade SQL must implement this
shape:

~~~sql
CREATE FUNCTION public.app_auth_lookup(p_email text)
RETURNS TABLE (
    user_id uuid,
    organization_id uuid,
    password_hash text,
    is_active boolean,
    role text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
    WITH candidates AS (
        SELECT
            u.id AS user_id,
            u.organization_id,
            u.password_hash,
            u.is_active,
            m.role
        FROM public."user" AS u
        JOIN public.membership AS m
          ON m.user_id = u.id
         AND m.organization_id = u.organization_id
        WHERE lower(u.email) = lower(btrim(p_email))
    )
    SELECT *
    FROM candidates
    WHERE (SELECT count(*) FROM candidates) = 1
$function$;

REVOKE ALL ON FUNCTION public.app_auth_lookup(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.app_auth_lookup(text) TO app_rw;
~~~

Create public.app_validate_session(uuid, uuid) with the same security settings.
It returns exactly one role only when user ID, organization ID, active state,
and membership all agree. All table references are schema-qualified. Downgrade
drops both exact function signatures.

- [ ] **Step 4: Run migration and privilege tests**

Run:

~~~bash
cd backend
alembic downgrade 0022
alembic upgrade 0023
pytest -q tests/test_auth_db_functions.py
alembic heads
~~~

Expected: tests pass and the only head is 0023.

### Task 3: Introduce typed app_rw auth access

- [ ] **Step 1: Write failing auth_db unit/integration tests**

In **backend/tests/test_auth_hardening.py**, assert that a normalized known
email maps to a typed principal, an unknown/ambiguous email maps to None, and
session validation returns current role or None.

- [ ] **Step 2: Create app/core/auth_db.py**

Implement immutable records and functions:

~~~python
from dataclasses import dataclass
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class LoginPrincipal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    password_hash: str
    is_active: bool
    role: str


async def lookup_login_principal(
    session: AsyncSession,
    email: str,
) -> LoginPrincipal | None:
    result = await session.execute(
        text("SELECT * FROM public.app_auth_lookup(:email)"),
        {"email": email.strip()},
    )
    row = result.one_or_none()
    return None if row is None else LoginPrincipal(**row._mapping)


async def validate_session_principal(
    session: AsyncSession,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> str | None:
    result = await session.execute(
        text("SELECT role FROM public.app_validate_session(:uid, :oid)"),
        {"uid": user_id, "oid": organization_id},
    )
    return result.scalar_one_or_none()
~~~

Do not create a second engine. Both functions receive a session from the
existing app_rw factory.

- [ ] **Step 3: Run the focused tests**

Run:

~~~bash
cd backend
pytest -q tests/test_auth_hardening.py -k "principal or lookup or validate"
~~~

Expected: pass.

### Task 4: Stop trusting role data in cookies

- [ ] **Step 1: Write failing session regressions**

Add these behaviors to **backend/tests/test_auth_hardening.py**:

- a token for a real active user returns 200 from /api/auth/session,
- a token with an unknown user returns 401,
- a token with a mismatched organization returns 401,
- a READ_ONLY user cannot gain write rights by changing any legacy role field,
- disabling a temporary user invalidates its existing token immediately,
- changing a temporary membership changes the role returned by the next
  /api/auth/session request.

Expected session response:

~~~python
{
    "user_id": str(user_id),
    "organization_id": str(org_id),
    "role": "READ_ONLY",
}
~~~

- [ ] **Step 2: Run and verify RED**

Run:

~~~bash
cd backend
pytest -q tests/test_auth_hardening.py -k "session or disabled or role"
~~~

Expected: current code trusts the cookie or lacks /api/auth/session.

- [ ] **Step 3: Make session tokens identity-only**

Change **backend/app/core/security.py**:

~~~python
def create_session_token(
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> str:
    return _serializer.dumps(
        {"uid": str(user_id), "oid": str(organization_id)},
        salt=_SALT,
    )
~~~

decode_session_token validates only uid and oid. It must catch malformed UUID
at the dependency boundary and fail with 401 rather than 500.

- [ ] **Step 4: Validate the principal asynchronously before RBAC**

Make get_current_user in **backend/app/core/tenant.py** async. Decode identity,
open one app_rw transaction from get_session_factory, call
validate_session_principal, and return AuthContext with the database role.
FastAPI dependency caching ensures get_db and require_* share that validated
AuthContext within a request.

Do not move validation only into get_db: RBAC dependencies must never run on
an unvalidated role.

- [ ] **Step 5: Add /api/auth/session**

Add an authenticated GET endpoint in **backend/app/api/auth.py** returning the
same response shape as login from the validated AuthContext.

- [ ] **Step 6: Update fixtures and run RBAC/RLS**

Update direct token construction in **backend/tests/conftest.py** to the new
two-argument signature.

Run:

~~~bash
cd backend
pytest -q tests/test_auth_hardening.py
pytest -q tests/test_tenant_isolation.py tests/test_rbac.py
~~~

Expected: all pass, including role-change and disabled-user cases.

### Task 5: Remove migrator use from login and harden cookies

- [ ] **Step 1: Write failing login and cookie tests**

Add cases for:

- valid normalized email/password returns 200,
- unknown email and wrong password both return the same 401 body,
- inactive user also returns the same public 401 body,
- no-membership and ambiguous-email cases do not disclose why login failed,
- Set-Cookie contains HttpOnly, Secure, SameSite=Lax, Path=/, a bounded Max-Age,
- login module never reads database_url_migrator.

- [ ] **Step 2: Implement app_rw login**

Replace per-request create_async_engine in **backend/app/api/auth.py** with:

~~~python
async with get_unscoped_session() as session:
    principal = await lookup_login_principal(session, body.email)
~~~

Verify a precomputed dummy Argon2 hash when principal is None so unknown-email
requests still perform a password verification. Return one generic
"Invalid credentials" 401 for every rejected login. Create the identity-only
token after successful password, active-state, and role checks.

- [ ] **Step 3: Harden password verification errors**

In **backend/app/core/security.py**, catch Argon2 mismatch and invalid-hash
exceptions and return False. Never include a password or hash in logs.

- [ ] **Step 4: Make production cookie settings explicit**

Keep development defaults usable, but support these production values:

~~~text
HV_SESSION_COOKIE_NAME=__Host-hv_session
HV_SESSION_COOKIE_SECURE=true
HV_SESSION_MAX_AGE_SECONDS=7200
~~~

Set the same path, secure, httponly, and samesite attributes when deleting the
cookie.

- [ ] **Step 5: Run login tests**

Run:

~~~bash
cd backend
pytest -q tests/test_auth_hardening.py -k "login or cookie"
~~~

Expected: all pass and auth.py has no migrator-engine import or use.

### Task 6: Enforce new-password policy

- [ ] **Step 1: Write failing API and CLI policy tests**

Test 11-character, 12-character, 128-character, and 129-character passwords.
User creation accepts the inclusive 12..128 range and returns 422 outside it.
Admin CLI exits before connecting to PostgreSQL for an invalid password. Add a
--password-stdin mode and test that it reads one newline-terminated password
without requiring the secret in process arguments.

- [ ] **Step 2: Add one shared policy**

Add to **backend/app/core/security.py**:

~~~python
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def validate_new_password(password: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError("Password must contain 12 to 128 characters")
    return password
~~~

Call it from user creation and admin bootstrap. Map API ValueError to Pydantic
422 or a deliberate 422 HTTPException. Make --password and --password-stdin a
required mutually exclusive argparse group; deployment uses stdin, while the
legacy explicit argument remains available for local compatibility. Do not
enforce minimum length during login, because existing hashes must remain
usable; enforce only the 128 character input ceiling there.

- [ ] **Step 3: Update policy-invalid test fixtures mechanically**

Change seeded and success-path test passwords to at least 12 characters.
Forbidden-role tests must also send schema-valid passwords so their expected
403 remains an RBAC assertion.

Run:

~~~bash
rg -n '"password":|testpass|securepass' backend/tests
cd backend
pytest -q tests/test_auth_hardening.py tests/test_rbac.py
~~~

Expected: policy tests and RBAC semantics pass.

### Task 7: Auth package verification and checkpoint

- [ ] **Step 1: Verify migration topology**

Run:

~~~bash
cd backend
alembic heads
alembic history --verbose
~~~

Expected: one linear head, 0023, based on the protected 0022.

- [ ] **Step 2: Run security regressions**

Run:

~~~bash
cd backend
pytest -q \
  tests/test_auth_db_functions.py \
  tests/test_auth_hardening.py \
  tests/test_tenant_isolation.py \
  tests/test_rls_coverage.py \
  tests/test_rbac.py
~~~

Expected: all pass.

- [ ] **Step 3: Run the full backend suite**

Run:

~~~bash
cd backend
pytest -q
~~~

Expected: complete pass.

- [ ] **Step 4: Prove runtime code has no migrator dependency**

Run:

~~~bash
rg -n "database_url_migrator|HV_DATABASE_URL_MIGRATOR|HV_DATABASE_URL_SYNC" \
  backend/app/api/auth.py backend/app/core/tenant.py backend/app/core/auth_db.py
git diff --check
~~~

Expected: no match in runtime auth files and no whitespace errors.

- [ ] **Step 5: Record a no-commit checkpoint**

Do not commit. Return status, summary, changed_files, tests, migration/rollback
risks, and follow_up to the root orchestrator.
