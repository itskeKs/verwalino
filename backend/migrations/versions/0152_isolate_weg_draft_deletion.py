"""Confine draft-deletion locks and preserve source-limited draft visibility."""
from alembic import op
from sqlalchemy import text

revision = '0152'
down_revision = '0151'
branch_labels = None
depends_on = None

SOURCE_BEFORE = """IF run.id IS NULL OR NOT public.financial_scope_access(org,'settlement_run',target,'read')
        OR NOT public.financial_parent_source_access(org,'settlement_run',target,run.property_id) THEN
        RETURN 'NOT_FOUND';
      END IF;"""
SOURCE_AFTER = """IF run.id IS NULL OR NOT public.financial_scope_access(org,'settlement_run',target,'read') THEN
        RETURN 'NOT_FOUND';
      END IF;
      IF NOT public.financial_parent_source_access(org,'settlement_run',target,run.property_id) THEN
        -- Match financial_source_disclosure: only pristine drafts remain
        -- visible without current access to the copied result's sources.
        IF run.status='draft' AND run.snapshot_payload IS NULL THEN
          RETURN 'SETTLEMENT_DELETE_SOURCE_ACCESS_REQUIRED';
        END IF;
        RETURN 'NOT_FOUND';
      END IF;"""

LOCK_BEFORE = """DECLARE org uuid; selected uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      -- Statement-level acquisition precedes tuple/FK locks. Trusted maintenance
      -- without tenant context locks organizations in a stable order too.
      FOR org IN SELECT DISTINCT r.organization_id FROM public.settlement_run r
        WHERE r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
          AND (selected IS NULL OR r.organization_id=selected) ORDER BY r.organization_id LOOP
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      END LOOP;
      RETURN NULL;
    END"""
LOCK_AFTER = """DECLARE selected uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      -- Check the principal before taking even an ACL lock. RLS alone
      -- must not let DELETE 0 lock another organization's financial inputs.
      IF selected IS NULL OR (public.scope_actor_role(selected) IS NULL AND NOT (
        session_user='app_migrator' AND COALESCE(current_setting('app.organization_erasure',true),'')='on'
      )) THEN
        RAISE EXCEPTION 'A valid organization principal is required' USING ERRCODE='42501';
      END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||selected::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||selected::text,0));
      RETURN NULL;
    END"""


def replace_body(signature, before, after):
    definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),
                                      {'signature': signature})
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute('LOCK TABLE settlement_run IN SHARE ROW EXCLUSIVE MODE NOWAIT')
    replace_body('lock_weg_draft_delete()', LOCK_BEFORE, LOCK_AFTER)
    replace_body('weg_draft_delete_reason(uuid,uuid)', SOURCE_BEFORE, SOURCE_AFTER)


def downgrade():
    op.execute('LOCK TABLE settlement_run IN SHARE ROW EXCLUSIVE MODE NOWAIT')
    replace_body('weg_draft_delete_reason(uuid,uuid)', SOURCE_AFTER, SOURCE_BEFORE)
    replace_body('lock_weg_draft_delete()', LOCK_AFTER, LOCK_BEFORE)
