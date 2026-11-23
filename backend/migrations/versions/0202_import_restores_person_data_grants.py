"""The organization import restores a contact data grant as the archive has it (issue #851, ADR 0016).

``protect_person_data_setup()`` (0068, 0201) refused three kinds of archived grant at ``person_data_grant`` (sqlstate 42501,
"Grant requires matching actor and active member", or 23514 for the third):

* a grant to a member who is not active: the import maps every archived user except the importing administrator to an
  inactive placeholder account, so a grant to a clerk, to a second administrator, or to the first administrator beside
  the living source organization had a placeholder as grantee;
* a grant that is already revoked: the trigger takes only an unrevoked insert, and a revocation is a later update by the
  signed-in user, which the import cannot replay;
* a grant whose expiry has passed: the trigger stamps ``created_at`` with the clock at every insert, so the restored
  ``expires_at`` lay before ``created_at``, and the table requires ``expires_at > created_at`` (``person_data_grant_check1``).
  The CHECK is right and stays; the stamp is what moves it.

Each of the three has exactly one alternative, and each holds only for the very row the import is restoring. The import
names that row in the transaction-local setting ``app.organization_import_person_data_grant`` of 0201, which now carries the
grant, its grantor and its grantee (JSON ``grant_id``, ``granted_by``, ``user_id``), set around that one insert and reset after
it. One setting authorises one primary-key row, as before. The function ``import_restores_person_data(kind, row, organization,
account)`` gets two more kinds beside ``domain`` and ``grant`` (the grantor):

* ``grantee``: the setting names this grant and this grantee, the grantee is a placeholder of this organization (an inactive
  account whose password is the unusable ``!`` the import and an organization's deletion write; a colleague deactivated in the
  user list keeps their password and is no placeholder), and the signed-in user is an active administrator of it. The
  condition "the grantee is an active member" gets this alternative. A placeholder needs no membership row: the import
  inserts the memberships in no fixed order relative to the grants, and an account that cannot sign in holds no role
  (``scope_actor_role`` asks for an active user).
* ``history``: the setting names this grant, and the signed-in user is an active administrator of the organization. No account
  is involved. The grant then keeps what the archive has of its history: its revocation (``revoked_at`` and ``revoked_by``
  may be set at the insert) and the time it was made (``created_at`` is not stamped again). The conditions "unrevoked" and
  "stamped with the clock" get this alternative. ``expires_at > created_at`` holds again as it did in the source, and the table
  is unchanged. ``revoked_by`` is any account of the organization (the composite foreign key sees to that): who revoked is
  history, and revoking gives nobody a right.

Nothing else changes. A missing, empty or malformed setting, or one of another shape, answers false and never raises, so the
trigger refuses with its own error. A grant the import does not name is checked exactly as before and stamped with the clock.
Every other condition still holds for the signed-in user: the organization, an active administrator, and for a grant that
the grantor is the signed-in user or a named placeholder. UPDATE and DELETE are unchanged, setting or not. What a restored
grant means follows from the access function ``has_person_data_capability``, which this migration does not touch: it asks for
an active member with a membership, so a grant to a placeholder gives nothing until an administrator reactivates the account
(and sets a password), and a revoked or expired grant gives nothing whoever holds the account. The trigger's own audit event
(``person_data.grant_prepared``, actor the importing administrator) is unchanged.

The function is not executable by the runtime role: only ``protect_person_data_setup()``, which runs with its owner's rights,
calls it. ``CREATE OR REPLACE`` keeps both functions' definer rights, search path, ``row_security`` setting, owner and ACL,
and both triggers. No row is read or rewritten by the migration and no data can be lost: ``downgrade()`` restores the text of
0201 exactly and needs no data check; an import then refuses these grants again at ``person_data_grant``. Existing rows are
unchanged.

Revision ID: 0202
Revises: 0201
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0202"
down_revision: Union[str, None] = "0201"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK = """
CREATE OR REPLACE FUNCTION import_restores_person_data(kind text, row_id uuid, org uuid, actor uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE restored jsonb;
BEGIN
  IF kind='domain' THEN
    restored:=NULLIF(current_setting('app.organization_import_person_data_domain',true),'')::jsonb;
    IF restored->>'domain_id' IS DISTINCT FROM row_id::text OR restored->>'created_by' IS DISTINCT FROM actor::text THEN
      RETURN false; END IF;
  ELSIF kind IN ('grant','grantee','history') THEN
    restored:=NULLIF(current_setting('app.organization_import_person_data_grant',true),'')::jsonb;
    IF restored->>'grant_id' IS DISTINCT FROM row_id::text
      OR (kind='grant' AND restored->>'granted_by' IS DISTINCT FROM actor::text)
      OR (kind='grantee' AND restored->>'user_id' IS DISTINCT FROM actor::text) THEN
      RETURN false; END IF;
  ELSE
    RETURN false;
  END IF;
  RETURN COALESCE(public.scope_actor_role(org)='OWNER_ADMIN'
    AND (kind='history' OR EXISTS(SELECT 1 FROM public."user" u WHERE u.organization_id=org AND u.id=actor
      AND NOT u.is_active AND u.password_hash='!')),false);
EXCEPTION WHEN data_exception THEN RETURN false;
END $$;
"""

RESTORING = """
CREATE OR REPLACE FUNCTION protect_person_data_setup() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; action text; history boolean;
BEGIN
  IF TG_OP='DELETE' OR (TG_TABLE_NAME='person_data_domain' AND TG_OP='UPDATE') THEN
    RAISE EXCEPTION 'Person data evidence is immutable' USING ERRCODE='23514'; END IF;
  IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
    RAISE EXCEPTION 'Person data organization mismatch' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
  IF public.scope_actor_role(NEW.organization_id) IS DISTINCT FROM 'OWNER_ADMIN' THEN
    RAISE EXCEPTION 'Active administrator required' USING ERRCODE='42501'; END IF;
  IF TG_OP='INSERT' THEN
    IF TG_TABLE_NAME='person_data_domain' THEN
      NEW.created_at:=clock_timestamp();
      IF NEW.created_by IS DISTINCT FROM actor
        AND NOT public.import_restores_person_data('domain',NEW.id,NEW.organization_id,NEW.created_by) THEN
        RAISE EXCEPTION 'Domain actor mismatch' USING ERRCODE='42501'; END IF;
      action:='person_data.domain_prepared';
    ELSE
      history:=public.import_restores_person_data('history',NEW.id,NEW.organization_id,NULL);
      IF NOT history THEN NEW.created_at:=clock_timestamp(); END IF;
      IF (NEW.granted_by IS DISTINCT FROM actor
          AND NOT public.import_restores_person_data('grant',NEW.id,NEW.organization_id,NEW.granted_by))
        OR (NOT history AND (NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL))
        OR (NOT EXISTS(SELECT 1 FROM public.membership m JOIN public."user" u ON u.organization_id=m.organization_id AND u.id=m.user_id
            WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id AND u.is_active)
          AND NOT public.import_restores_person_data('grantee',NEW.id,NEW.organization_id,NEW.user_id)) THEN
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

# The texts of 0201, which downgrade() restores.
PREVIOUS_CHECK = """
CREATE OR REPLACE FUNCTION import_restores_person_data(kind text, row_id uuid, org uuid, actor uuid) RETURNS boolean
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
"""

PREVIOUS = """
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


def upgrade() -> None:
    op.execute(CHECK)
    op.execute(RESTORING)


def downgrade() -> None:
    op.execute(PREVIOUS_CHECK)
    op.execute(PREVIOUS)
