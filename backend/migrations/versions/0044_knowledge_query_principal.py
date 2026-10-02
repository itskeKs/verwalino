"""Persist knowledge query principals and fence source/answer accounting phases."""
from alembic import op

revision = '0044'
down_revision = '0043'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    ALTER TABLE property_knowledge_query_run ADD COLUMN initiated_by uuid,
      ADD CONSTRAINT fk_knowledge_query_actor FOREIGN KEY(organization_id,initiated_by)
        REFERENCES "user"(organization_id,id);

    CREATE FUNCTION lock_knowledge_budget(org uuid, window_start timestamptz) RETURNS void
    LANGUAGE sql VOLATILE SET search_path=pg_catalog,pg_temp AS $$
      SELECT pg_advisory_xact_lock(hashtextextended(org::text||':'||
        to_char(window_start AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||'+00:00',0));
    $$;

    CREATE FUNCTION guard_knowledge_principal() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid := NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      live boolean;
    BEGIN
      IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Knowledge organization mismatch' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      live := public.scope_actor_role(NEW.organization_id) IS NOT NULL;
      IF TG_OP='INSERT' THEN
        IF actor IS NULL OR NEW.initiated_by IS DISTINCT FROM actor OR NOT live THEN
          RAISE EXCEPTION 'Knowledge reservation requires live actor' USING ERRCODE='42501'; END IF;
      ELSE
        IF NEW.initiated_by IS DISTINCT FROM OLD.initiated_by THEN
          RAISE EXCEPTION 'Knowledge actor is immutable' USING ERRCODE='23514'; END IF;
        IF NEW.status='EXPIRED' THEN
          IF NOT live OR OLD.reservation_expires_at > clock_timestamp()
            OR NEW.outcome IS DISTINCT FROM 'RESERVATION_EXPIRED'
            OR NEW.error_code IS DISTINCT FROM 'RESERVATION_EXPIRED'
            OR NEW.estimated_cost_usd<>0 OR NEW.input_tokens<>0 OR NEW.output_tokens<>0
            OR NEW.latency_ms IS NOT NULL THEN
            RAISE EXCEPTION 'Knowledge reservation cannot expire' USING ERRCODE='42501'; END IF;
        ELSE
          IF actor IS NULL OR actor IS DISTINCT FROM OLD.initiated_by THEN
            RAISE EXCEPTION 'Knowledge finalization actor mismatch' USING ERRCODE='42501'; END IF;
          IF NOT live OR NEW.error_code='QA_PERMISSION_CHANGED' OR NEW.outcome='QA_PERMISSION_CHANGED' THEN
            IF live OR NEW.status<>'FINALIZED'
              OR NEW.outcome IS DISTINCT FROM 'QA_PERMISSION_CHANGED'
              OR NEW.error_code IS DISTINCT FROM 'QA_PERMISSION_CHANGED'
              OR NEW.estimated_cost_usd IS DISTINCT FROM OLD.reserved_cost_usd
              OR NEW.input_tokens<>0 OR NEW.output_tokens<>0 OR NEW.latency_ms IS NOT NULL THEN
              RAISE EXCEPTION 'Knowledge permission cancellation metadata invalid' USING ERRCODE='42501'; END IF;
            INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
              VALUES(NEW.organization_id,actor,'property_knowledge_query_run',NEW.id,'knowledge.permission_cancelled',
                jsonb_build_object('actor_user_id',actor,'reason','QA_PERMISSION_CHANGED'));
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_knowledge_principal BEFORE INSERT OR UPDATE ON property_knowledge_query_run
      FOR EACH ROW EXECUTE FUNCTION guard_knowledge_principal();

    CREATE FUNCTION cancel_unauthorized_knowledge(org uuid, run uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE item public.property_knowledge_query_run;
      actor uuid := NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid OR actor IS NULL THEN
        RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT * INTO item FROM public.property_knowledge_query_run WHERE organization_id=org AND id=run;
      IF NOT FOUND OR item.initiated_by IS DISTINCT FROM actor OR item.status<>'RESERVED'
        OR public.scope_actor_role(org) IS NOT NULL THEN RETURN false; END IF;
      PERFORM public.lock_knowledge_budget(org,item.budget_window_start);
      UPDATE public.property_knowledge_query_run SET status='FINALIZED',outcome='QA_PERMISSION_CHANGED',
        error_code='QA_PERMISSION_CHANGED',estimated_cost_usd=reserved_cost_usd,
        input_tokens=0,output_tokens=0,latency_ms=NULL,finalized_at=clock_timestamp()
        WHERE organization_id=org AND id=run AND status='RESERVED';
      RETURN FOUND;
    END $$;
    REVOKE ALL ON FUNCTION cancel_unauthorized_knowledge(uuid,uuid),guard_knowledge_principal(),
      lock_knowledge_budget(uuid,timestamptz) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION cancel_unauthorized_knowledge(uuid,uuid),lock_knowledge_budget(uuid,timestamptz) TO app_rw;
    ''')


def downgrade():
    op.execute('''
    DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_knowledge_query_run WHERE initiated_by IS NOT NULL) THEN
      RAISE EXCEPTION 'Retain populated knowledge principal evidence'; END IF; END $$;
    DROP TRIGGER guard_knowledge_principal ON property_knowledge_query_run;
    DROP FUNCTION guard_knowledge_principal(),cancel_unauthorized_knowledge(uuid,uuid),lock_knowledge_budget(uuid,timestamptz);
    ALTER TABLE property_knowledge_query_run DROP COLUMN initiated_by;
    ''')
