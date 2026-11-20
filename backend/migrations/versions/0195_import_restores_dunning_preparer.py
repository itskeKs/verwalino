"""The organization import restores a dunning context for the preparer the archive names (issue #694, ADR 0016).

``protect_dunning_context()`` (0039) binds ``created_by`` of a new context to the signed-in user. After an import the
archived preparer of a notice (a clerk, a second administrator) is an inactive placeholder account, so the restore of
that notice was refused at ``dunning_scope_context`` (sqlstate 42501), although the importing administrator holds every
grant on the scope. The preparer stays the archived preparer: the context is restored with the placeholder as
``created_by``, never with the importing administrator, who prepared nothing.

The condition ``created_by = signed-in user`` gets exactly one alternative: the import is restoring this very row. The
import says so in the transaction-local setting ``app.organization_import_dunning_context`` (the pattern of
``app.organization_import_registrations``, 0147), a JSON object with the ``notice_id`` of the context and its
``created_by``, set around that one insert and reset after it. The guard accepts the alternative only when the setting
names this row and this ``created_by`` and ``created_by`` is a placeholder of this organization: an inactive account
whose password is the unusable ``!`` the import (and an organization's deletion) writes. A colleague deactivated in the
user list keeps their password and is no placeholder. Every other condition is unchanged and still holds for the
signed-in user: the organization, the selected scope, a live ``prepare`` grant on it and a context that precedes its
notice. UPDATE and DELETE stay refused, setting or not.

The function keeps definer rights, the fixed search path, both advisory locks and its ACL (``CREATE OR REPLACE``).
No row is read or rewritten by the migration and no data can be lost: ``downgrade()`` restores the text of 0039.

Revision ID: 0195
Revises: 0194
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0195"
down_revision: Union[str, None] = "0194"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RESTORING = """
CREATE OR REPLACE FUNCTION protect_dunning_context() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE restored jsonb;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Dunning scope context is immutable' USING ERRCODE='23514'; END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
  PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
  restored:=NULLIF(current_setting('app.organization_import_dunning_context',true),'')::jsonb;
  IF NULLIF(current_setting('app.current_org_id',true),'') IS NOT NULL AND (
    NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
    OR (NEW.created_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
      AND NOT (restored IS NOT NULL
        AND (restored->>'notice_id')::uuid IS NOT DISTINCT FROM NEW.dunning_notice_id
        AND (restored->>'created_by')::uuid IS NOT DISTINCT FROM NEW.created_by
        AND EXISTS(SELECT 1 FROM public."user" u WHERE u.organization_id=NEW.organization_id AND u.id=NEW.created_by
          AND NOT u.is_active AND u.password_hash='!')))
    OR NEW.scope_id IS DISTINCT FROM NULLIF(current_setting('app.current_scope_id',true),'')::uuid
    OR NOT public.has_scope_capability(NEW.organization_id,NEW.scope_id,'prepare')
    OR EXISTS(SELECT 1 FROM public.dunning_notice n WHERE n.organization_id=NEW.organization_id AND n.id=NEW.dunning_notice_id)) THEN
    RAISE EXCEPTION 'Explicit preparation context required for a new notice' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;
"""

# The text of 0039, which downgrade() restores.
ORIGINAL = """
CREATE OR REPLACE FUNCTION protect_dunning_context() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Dunning scope context is immutable' USING ERRCODE='23514'; END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
  PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
  IF NULLIF(current_setting('app.current_org_id',true),'') IS NOT NULL AND (
    NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
    OR NEW.created_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
    OR NEW.scope_id IS DISTINCT FROM NULLIF(current_setting('app.current_scope_id',true),'')::uuid
    OR NOT public.has_scope_capability(NEW.organization_id,NEW.scope_id,'prepare')
    OR EXISTS(SELECT 1 FROM public.dunning_notice n WHERE n.organization_id=NEW.organization_id AND n.id=NEW.dunning_notice_id)) THEN
    RAISE EXCEPTION 'Explicit preparation context required for a new notice' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(RESTORING)


def downgrade() -> None:
    op.execute(ORIGINAL)
