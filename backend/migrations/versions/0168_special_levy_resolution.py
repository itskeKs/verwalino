"""Permit only a one-time authority link without changing frozen levy evidence."""
from alembic import op
from sqlalchemy import text

revision = '0168'
down_revision = '0167'
branch_labels = None
depends_on = None

OLD_OPERATION = "IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Special levies are append-only' USING ERRCODE='23514'; END IF;"
NEW_OPERATION = "IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND TG_TABLE_NAME<>'special_levy') THEN RAISE EXCEPTION 'Special levies are append-only' USING ERRCODE='23514'; END IF;"
ANCHOR = "      IF TG_TABLE_NAME='special_levy' THEN"
LINK = """      IF TG_OP='UPDATE' THEN
        IF OLD.resolution_id IS NOT NULL OR NEW.resolution_id IS NULL
          OR (to_jsonb(NEW)-'resolution_id') IS DISTINCT FROM (to_jsonb(OLD)-'resolution_id') THEN
          RAISE EXCEPTION 'Only a first special levy resolution link is permitted' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM public.resolution r JOIN public.owners_meeting m
          ON m.id=r.meeting_id AND m.organization_id=r.organization_id
          WHERE r.id=NEW.resolution_id AND r.organization_id=NEW.organization_id
            AND m.property_id=NEW.property_id AND r.result='ANGENOMMEN') THEN
          RAISE EXCEPTION 'Special levy requires an adopted resolution in the same property' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
"""


def patch_guard(reverse=False):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('guard_special_levy()'::regprocedure)"))
    changes = [(OLD_OPERATION, NEW_OPERATION), (ANCHOR, LINK + ANCHOR)]
    for before, after in changes:
        if reverse:
            before, after = after, before
        if definition.count(before) != 1:
            raise RuntimeError('Unexpected special levy guard; repair forward')
        definition = definition.replace(before, after, 1)
    op.execute(definition)


def upgrade():
    patch_guard()
    # Keep historical INSERT semantics for native organization archive imports
    # and operator restores. Ordinary creation is guarded by the service; only
    # the existing nullable reference column gains this restricted UPDATE path.
    op.execute("""
        GRANT UPDATE(resolution_id) ON special_levy TO app_rw;
        CREATE POLICY special_levy_link ON special_levy FOR UPDATE USING (
          organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
          AND accounting_scope_id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid
          AND has_scope_capability(organization_id,accounting_scope_id,'prepare')
          AND special_levy_sources(organization_id,property_id,id,resolution_id,'prepare'))
        WITH CHECK (
          organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
          AND accounting_scope_id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid
          AND has_scope_capability(organization_id,accounting_scope_id,'prepare')
          AND special_levy_sources(organization_id,property_id,id,resolution_id,'prepare'));
    """)


def downgrade():
    op.execute('DROP POLICY special_levy_link ON special_levy')
    op.execute('REVOKE UPDATE(resolution_id) ON special_levy FROM app_rw')
    patch_guard(reverse=True)
