"""The organization import restores a scope binding for the reviewer the archive names (issue #716, ADR 0016).

The insert policy ``scope_insert`` of ``accounting_scope_binding`` (0032) binds ``reviewed_by`` of a new binding to the
signed-in user. After an import the archived reviewer of a binding (the clerk whose payment the trigger bound, a second
administrator who classified an account) is an inactive placeholder account, so the restore of that binding was refused
at ``accounting_scope_binding`` (sqlstate 42501), although the importing administrator holds every grant on the scope.
The reviewer stays the archived reviewer: the binding is restored with the placeholder as ``reviewed_by``, never with the
importing administrator, who reviewed nothing.

The condition ``reviewed_by = signed-in user`` gets exactly one alternative: the import is restoring this very row. The
import says so in the transaction-local setting ``app.organization_import_scope_binding`` (the pattern of
``app.organization_import_dunning_context``, 0195), a JSON object with the ``binding_id`` of the row and its
``reviewed_by``, set around that one insert and reset after it. The alternative is the function
``import_restores_scope_binding(binding, organization, reviewer)``. It accepts only when the setting names this row and
this reviewer, the reviewer is a placeholder of this organization (an inactive account whose password is the unusable
``!`` the import and an organization's deletion write; a colleague deactivated in the user list keeps their password and
is no placeholder) and the signed-in user is an active administrator of it. A missing, empty or malformed setting, or one
of another shape, answers false and never raises. Every other condition of the policy is unchanged and still holds for
the signed-in user: the organization, a live ``prepare`` grant on the scope and the origin ``REVIEWED``. The function
reads ``user`` with definer rights (like ``scope_actor_role``); only the runtime role may execute it.

No row is read or rewritten by the migration and no data can be lost: ``downgrade()`` restores the policy of 0032 and
drops the function, and needs no data check; an import then refuses a binding another user reviewed again at
``accounting_scope_binding``. Existing bindings are unchanged.

Revision ID: 0200
Revises: 0199
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0200"
down_revision: Union[str, None] = "0199"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FUNCTION = """
CREATE FUNCTION import_restores_scope_binding(binding uuid, org uuid, reviewer uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE restored jsonb;
BEGIN
  restored:=NULLIF(current_setting('app.organization_import_scope_binding',true),'')::jsonb;
  RETURN COALESCE(restored->>'binding_id'=binding::text AND restored->>'reviewed_by'=reviewer::text
    AND public.scope_actor_role(org)='OWNER_ADMIN'
    AND EXISTS(SELECT 1 FROM public."user" u WHERE u.organization_id=org AND u.id=reviewer
      AND NOT u.is_active AND u.password_hash='!'),false);
EXCEPTION WHEN data_exception THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION import_restores_scope_binding(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION import_restores_scope_binding(uuid,uuid,uuid) TO app_rw;
"""

TENANT = "organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid"
ACTOR = "NULLIF(current_setting('app.current_user_id',true),'')::uuid"
PREPARER = "has_scope_capability(organization_id,scope_id,'prepare') AND origin='REVIEWED'"


def upgrade() -> None:
    op.execute(FUNCTION)
    op.execute(f"""ALTER POLICY scope_insert ON accounting_scope_binding
      WITH CHECK ({TENANT} AND {PREPARER} AND (reviewed_by={ACTOR} OR import_restores_scope_binding(id,organization_id,reviewed_by)))""")


def downgrade() -> None:
    # The text of 0032.
    op.execute(f"ALTER POLICY scope_insert ON accounting_scope_binding WITH CHECK ({TENANT} AND {PREPARER} AND reviewed_by={ACTOR})")
    op.execute("DROP FUNCTION import_restores_scope_binding(uuid,uuid,uuid)")
