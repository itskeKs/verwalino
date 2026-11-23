"""The organization import restores a contact data domain and a grant for the actor the archive names (issue #817, ADR 0016).

``protect_person_data_setup()`` (0068) binds ``created_by`` of a new ``person_data_domain`` and ``granted_by`` of a new
``person_data_grant`` to the signed-in user. After an import the archived creator or grantor of a row (a second
administrator, or an administrator whose sign-in address the importing administrator does not carry) is an inactive
placeholder account, so the restore of that row was refused at ``person_data_domain`` or ``person_data_grant`` (sqlstate
42501, "Domain actor mismatch"), although the signed-in importing administrator is an active administrator of the
organization. The creator and the grantor stay the archived accounts: the row is restored with the placeholder as
``created_by`` or ``granted_by``, never with the importing administrator, who made neither.

The condition ``created_by = signed-in user`` (``granted_by = signed-in user`` for a grant) gets exactly one alternative:
the import is restoring this very row. The import says so in a transaction-local setting, the pattern of
``app.organization_import_dunning_context`` (0195) and ``app.organization_import_scope_binding`` (0200): a JSON object
with the id of the row and its actor, set around that one insert and reset after it. A domain has the setting
``app.organization_import_person_data_domain`` (``domain_id``, ``created_by``), a grant
``app.organization_import_person_data_grant`` (``grant_id``, ``granted_by``); each is read only for its own table. The
alternative is the function ``import_restores_person_data(kind, row, organization, actor)``. It accepts only when the
setting names this row and this actor, the actor is a placeholder of this organization (an inactive account whose password
is the unusable ``!`` the import and an organization's deletion write; a colleague deactivated in the user list keeps their
password and is no placeholder) and the signed-in user is an active administrator of it. A missing, empty or malformed
setting, or one of another shape, answers false and never raises, so the trigger refuses with its own error. Every other
condition of the trigger is unchanged and still holds for the signed-in user: the organization, an active administrator, and
for a grant an unrevoked grant for an active member. UPDATE and DELETE stay as they were, setting or not.

The function is not executable by the runtime role: only ``protect_person_data_setup()``, which runs with its owner's
rights, calls it. ``CREATE OR REPLACE`` keeps the trigger function's definer rights, search path, ``row_security`` setting,
owner and ACL, and both triggers. No row is read or rewritten by the migration and no data can be lost: ``downgrade()``
restores the text of 0068 and drops the function and needs no data check; an import then refuses a domain or a grant another
administrator made again at ``person_data_domain``. Existing rows are unchanged.

Revision ID: 0201
Revises: 0200
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0201"
down_revision: Union[str, None] = "0200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK = """
CREATE FUNCTION import_restores_person_data(kind text, row_id uuid, org uuid, actor uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE restored jsonb;
BEGIN
  IF kind='domain' THEN
    restored:=NULLIF(current_setting('app.organization_import_person_data_domain',true),'')::jsonb;
    IF restored->>'domain_id' IS DISTINCT FROM row_id::text OR restored->>'created_by' IS DISTINCT FROM actor::text THEN
      RETURN false; END IF;
  ELSIF kind='grant' THEN
    restored:=NULLIF(current_setting('app.organization_import_person_data_grant',true),'')::jsonb;
    IF restored->>'grant_id' IS DISTINCT FROM row_id::text OR restored->>'granted_by' IS DISTINCT FROM actor::text THEN
      RETURN false; END IF;
  ELSE
    RETURN false;
  END IF;
  RETURN COALESCE(public.scope_actor_role(org)='OWNER_ADMIN'
    AND EXISTS(SELECT 1 FROM public."user" u WHERE u.organization_id=org AND u.id=actor
      AND NOT u.is_active AND u.password_hash='!'),false);
EXCEPTION WHEN data_exception THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION import_restores_person_data(text,uuid,uuid,uuid) FROM PUBLIC;
"""

RESTORING = """
CREATE OR REPLACE FUNCTION protect_person_data_setup() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; action text;
BEGIN
  IF TG_OP='DELETE' OR (TG_TABLE_NAME='person_data_domain' AND TG_OP='UPDATE') THEN
    RAISE EXCEPTION 'Person data evidence is immutable' USING ERRCODE='23514'; END IF;
  IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
    RAISE EXCEPTION 'Person data organization mismatch' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
  IF public.scope_actor_role(NEW.organization_id) IS DISTINCT FROM 'OWNER_ADMIN' THEN
    RAISE EXCEPTION 'Active administrator required' USING ERRCODE='42501'; END IF;
  IF TG_OP='INSERT' THEN
    NEW.created_at:=clock_timestamp();
    IF TG_TABLE_NAME='person_data_domain' THEN
      IF NEW.created_by IS DISTINCT FROM actor
        AND NOT public.import_restores_person_data('domain',NEW.id,NEW.organization_id,NEW.created_by) THEN
        RAISE EXCEPTION 'Domain actor mismatch' USING ERRCODE='42501'; END IF;
      action:='person_data.domain_prepared';
    ELSE
      IF (NEW.granted_by IS DISTINCT FROM actor
          AND NOT public.import_restores_person_data('grant',NEW.id,NEW.organization_id,NEW.granted_by))
        OR NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL
        OR NOT EXISTS(SELECT 1 FROM public.membership m JOIN public."user" u ON u.organization_id=m.organization_id AND u.id=m.user_id
          WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id AND u.is_active) THEN
        RAISE EXCEPTION 'Grant requires matching actor and active member' USING ERRCODE='42501'; END IF;
      action:='person_data.grant_prepared';
    END IF;
  ELSE
    IF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL OR NEW.revoked_by IS DISTINCT FROM actor
      OR (to_jsonb(NEW)-'revoked_at'-'revoked_by') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at'-'revoked_by') THEN
      RAISE EXCEPTION 'Only one-way person data grant revocation is allowed' USING ERRCODE='23514'; END IF;
    NEW.revoked_at:=clock_timestamp(); action:='person_data.grant_revoked';
  END IF;
  INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
    VALUES(NEW.organization_id,actor,TG_TABLE_NAME,NEW.id,action,jsonb_build_object('reason_code',NEW.reason_code));
  RETURN NEW;
END $$;
"""

# The text of 0068, which downgrade() restores.
ORIGINAL = """
CREATE OR REPLACE FUNCTION protect_person_data_setup() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; action text;
BEGIN
  IF TG_OP='DELETE' OR (TG_TABLE_NAME='person_data_domain' AND TG_OP='UPDATE') THEN
    RAISE EXCEPTION 'Person data evidence is immutable' USING ERRCODE='23514'; END IF;
  IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
    RAISE EXCEPTION 'Person data organization mismatch' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
  IF public.scope_actor_role(NEW.organization_id) IS DISTINCT FROM 'OWNER_ADMIN' THEN
    RAISE EXCEPTION 'Active administrator required' USING ERRCODE='42501'; END IF;
  IF TG_OP='INSERT' THEN
    NEW.created_at:=clock_timestamp();
    IF TG_TABLE_NAME='person_data_domain' THEN
      IF NEW.created_by IS DISTINCT FROM actor THEN RAISE EXCEPTION 'Domain actor mismatch' USING ERRCODE='42501'; END IF;
      action:='person_data.domain_prepared';
    ELSE
      IF NEW.granted_by IS DISTINCT FROM actor OR NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL
        OR NOT EXISTS(SELECT 1 FROM public.membership m JOIN public."user" u ON u.organization_id=m.organization_id AND u.id=m.user_id
          WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id AND u.is_active) THEN
        RAISE EXCEPTION 'Grant requires matching actor and active member' USING ERRCODE='42501'; END IF;
      action:='person_data.grant_prepared';
    END IF;
  ELSE
    IF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL OR NEW.revoked_by IS DISTINCT FROM actor
      OR (to_jsonb(NEW)-'revoked_at'-'revoked_by') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at'-'revoked_by') THEN
      RAISE EXCEPTION 'Only one-way person data grant revocation is allowed' USING ERRCODE='23514'; END IF;
    NEW.revoked_at:=clock_timestamp(); action:='person_data.grant_revoked';
  END IF;
  INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
    VALUES(NEW.organization_id,actor,TG_TABLE_NAME,NEW.id,action,jsonb_build_object('reason_code',NEW.reason_code));
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(CHECK)
    op.execute(RESTORING)


def downgrade() -> None:
    op.execute(ORIGINAL)
    op.execute("DROP FUNCTION import_restores_person_data(text,uuid,uuid,uuid)")
