"""Check complete property-source authority before financial calculation effects.

Revision ID: 0059
Revises: 0058
"""
from alembic import op

revision = '0059'
down_revision = '0058'
branch_labels = None
depends_on = None

TABLES = ('settlement_run','settlement_line','economic_plan','economic_plan_line','coverage_declaration')


def upgrade():
    op.execute('LOCK TABLE property,unit,property_source_security,' + ','.join(TABLES) + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    CREATE FUNCTION property_financial_source_access(org uuid,property uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      -- Reuse the complete expected source graph, never RLS-filtered search
      -- hits. This has no dependency on Q&A provider or budget configuration.
      IF NOT public.knowledge_query_actor_access(org,actor,property) THEN RETURN false; END IF;
      RETURN NOT EXISTS(SELECT 1 FROM public.unit u WHERE u.organization_id=org AND u.property_id=property
        AND NOT public.property_source_actor_access(org,actor,'unit',u.id,'read'));
    END $$;
    REVOKE ALL ON FUNCTION property_financial_source_access(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_financial_source_access(uuid,uuid) TO app_rw;

    CREATE FUNCTION guard_financial_source_input() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context jsonb; previous jsonb; target uuid; old_target uuid; org uuid; mode text;
    BEGIN
      IF TG_OP='DELETE' THEN context:=to_jsonb(OLD); ELSE context:=to_jsonb(NEW); END IF;
      org:=(context->>'organization_id')::uuid;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT COALESCE((SELECT s.mode FROM public.property_source_security s WHERE s.organization_id=org),'PREPARATION') INTO mode;
      IF mode='PREPARATION' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END IF;
      IF TG_TABLE_NAME IN ('settlement_run','economic_plan') THEN
        IF TG_OP='DELETE' AND context->>'snapshot_payload' IS NULL THEN RETURN OLD; END IF;
        IF TG_OP='INSERT' AND context->>'snapshot_payload' IS NULL AND context->>'status'='draft' THEN RETURN NEW; END IF;
        IF TG_OP='UPDATE' AND NEW.snapshot_payload IS NOT DISTINCT FROM OLD.snapshot_payload
          AND NEW.snapshot_sha256 IS NOT DISTINCT FROM OLD.snapshot_sha256 AND NEW.status IS DISTINCT FROM 'finalized'
          AND NEW.property_id IS NOT DISTINCT FROM OLD.property_id THEN RETURN NEW; END IF;
        target:=(context->>'property_id')::uuid;
      ELSIF TG_TABLE_NAME='coverage_declaration' THEN
        IF context->>'settlement_run_id' IS NOT NULL THEN
          SELECT r.property_id INTO target FROM public.settlement_run r WHERE r.organization_id=org AND r.id=(context->>'settlement_run_id')::uuid;
        ELSE
          SELECT p.property_id INTO target FROM public.economic_plan p WHERE p.organization_id=org AND p.id=(context->>'economic_plan_id')::uuid;
        END IF;
        IF target IS DISTINCT FROM (context->>'property_id')::uuid THEN
          RAISE EXCEPTION 'Coverage property differs from financial subject' USING ERRCODE='23514'; END IF;
      ELSIF TG_TABLE_NAME='settlement_line' THEN
        SELECT r.property_id INTO target FROM public.settlement_run r WHERE r.organization_id=org AND r.id=(context->>'settlement_run_id')::uuid;
      ELSE
        SELECT p.property_id INTO target FROM public.economic_plan p WHERE p.organization_id=org AND p.id=(context->>'economic_plan_id')::uuid;
      END IF;
      IF TG_OP='UPDATE' THEN
        previous:=to_jsonb(OLD);
        IF TG_TABLE_NAME='settlement_line' THEN
          SELECT r.property_id INTO old_target FROM public.settlement_run r WHERE r.organization_id=OLD.organization_id AND r.id=OLD.settlement_run_id;
        ELSIF TG_TABLE_NAME='economic_plan_line' THEN
          SELECT p.property_id INTO old_target FROM public.economic_plan p WHERE p.organization_id=OLD.organization_id AND p.id=OLD.economic_plan_id;
        ELSIF TG_TABLE_NAME='coverage_declaration' THEN
          IF previous->>'settlement_run_id' IS NOT NULL THEN
            SELECT r.property_id INTO old_target FROM public.settlement_run r WHERE r.organization_id=OLD.organization_id AND r.id=OLD.settlement_run_id;
          ELSE
            SELECT p.property_id INTO old_target FROM public.economic_plan p WHERE p.organization_id=OLD.organization_id AND p.id=OLD.economic_plan_id;
          END IF;
        ELSE old_target:=(previous->>'property_id')::uuid; END IF;
        IF old_target IS DISTINCT FROM target AND NOT public.property_financial_source_access(OLD.organization_id,old_target) THEN
          RAISE EXCEPTION 'Complete financial source authority required' USING ERRCODE='42501'; END IF;
      END IF;
      IF NOT COALESCE(public.property_source_actor_role(org,NULLIF(current_setting('app.current_user_id',true),'')::uuid) IN ('OWNER_ADMIN','CLERK'),false)
        OR NOT public.property_financial_source_access(org,target) THEN
        RAISE EXCEPTION 'Complete financial source authority required' USING ERRCODE='42501'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    REVOKE ALL ON FUNCTION guard_financial_source_input() FROM PUBLIC,app_rw;
    """)
    for table in TABLES:
        op.execute(f'CREATE TRIGGER a_lock_financial_source_input BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')
        op.execute(f'CREATE TRIGGER guard_financial_source_input BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH ROW EXECUTE FUNCTION guard_financial_source_input()')


def downgrade():
    op.execute('LOCK TABLE property_source_security,' + ','.join(TABLES) + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict financial source checks'; END IF; END $$""")
    for table in TABLES:
        op.execute(f'DROP TRIGGER guard_financial_source_input ON {table}')
        op.execute(f'DROP TRIGGER a_lock_financial_source_input ON {table}')
    op.execute('DROP FUNCTION guard_financial_source_input(),property_financial_source_access(uuid,uuid)')
