"""Append-only rollback of scoped effects; unclassified compatibility is retained."""
from alembic import op

revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None

BASE = 'SELECT COALESCE(public.scope_actor_role(org) IS NOT NULL'
GATED = "SELECT COALESCE((capability IN ('read','export') OR public.scoped_effects_enabled(org)) AND public.scope_actor_role(org) IS NOT NULL"


def _gate(before, after):
    # CREATE OR REPLACE preserves the OID used by all existing RLS policies.
    connection = op.get_bind()
    from sqlalchemy import text
    definition = connection.scalar(text("SELECT pg_get_functiondef('public.has_scope_capability(uuid,uuid,text)'::regprocedure)"))
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected scope capability function; refusing a partial pause gate')
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute('''
    CREATE TABLE scope_control_event (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
      actor_user_id uuid NOT NULL, request_key uuid NOT NULL,
      mode text NOT NULL CHECK(mode IN ('compatibility','paused')),
      reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
      previous_event_id uuid, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id), UNIQUE(organization_id,actor_user_id,request_key),
      UNIQUE NULLS NOT DISTINCT(organization_id,previous_event_id),
      FOREIGN KEY(organization_id,actor_user_id) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,previous_event_id) REFERENCES scope_control_event(organization_id,id)
    );
    CREATE INDEX ix_scope_control_current ON scope_control_event(organization_id,sequence DESC);
    CREATE TRIGGER protect_scope_control_event BEFORE UPDATE OR DELETE ON scope_control_event
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    ALTER TABLE scope_control_event ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scope_control_event FORCE ROW LEVEL SECURITY;
    CREATE POLICY scope_control_read ON scope_control_event FOR SELECT
      USING (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND scope_actor_role(organization_id)='OWNER_ADMIN');
    CREATE POLICY scope_control_no_direct_insert ON scope_control_event FOR INSERT WITH CHECK (false);
    REVOKE ALL ON scope_control_event FROM PUBLIC,app_rw;
    GRANT SELECT ON scope_control_event TO app_rw;
    CREATE POLICY scope_control_audit_read ON audit_event AS RESTRICTIVE FOR SELECT
      USING (entity_type<>'scope_control_event' OR scope_actor_role(organization_id)='OWNER_ADMIN');

    CREATE FUNCTION scoped_effects_enabled(org uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN COALESCE((SELECT mode='compatibility' FROM public.scope_control_event
        WHERE organization_id=org ORDER BY sequence DESC LIMIT 1),true);
    END $$;

    CREATE FUNCTION change_scoped_effect_control(org uuid, request uuid, target text, expected uuid, explanation text) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid; prior public.scope_control_event; receipt public.scope_control_event;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Scope control not accessible' USING ERRCODE='42501'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS DISTINCT FROM 'OWNER_ADMIN' THEN
        RAISE EXCEPTION 'Scope control not accessible' USING ERRCODE='42501'; END IF;
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      IF request IS NULL OR target IS NULL OR target NOT IN ('compatibility','paused')
        OR explanation IS NULL OR length(trim(explanation)) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Invalid scope control command' USING ERRCODE='22023'; END IF;
      explanation:=trim(explanation);
      SELECT * INTO receipt FROM public.scope_control_event
        WHERE organization_id=org AND actor_user_id=actor AND request_key=request;
      IF FOUND THEN
        IF receipt.mode<>target OR receipt.previous_event_id IS DISTINCT FROM expected OR receipt.reason<>explanation THEN
          RAISE EXCEPTION 'Retry key belongs to another command'; END IF;
        RETURN to_jsonb(receipt);
      END IF;
      SELECT * INTO prior FROM public.scope_control_event WHERE organization_id=org ORDER BY sequence DESC LIMIT 1;
      IF prior.id IS DISTINCT FROM expected OR COALESCE(prior.mode,'compatibility')=target THEN
        RAISE EXCEPTION 'Scope control changed; reload the current state'; END IF;
      INSERT INTO public.scope_control_event(organization_id,actor_user_id,request_key,mode,reason,previous_event_id)
        VALUES(org,actor,request,target,explanation,expected) RETURNING * INTO receipt;
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(org,actor,'scope_control_event',receipt.id,'scope.effects_'||target,
          jsonb_build_object('mode',target,'previous_event_id',expected,'reason',explanation));
      RETURN to_jsonb(receipt);
    END $$;
    REVOKE ALL ON FUNCTION scoped_effects_enabled(uuid),change_scoped_effect_control(uuid,uuid,text,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION scoped_effects_enabled(uuid),change_scoped_effect_control(uuid,uuid,text,uuid,text) TO app_rw;
    ''')
    _gate(BASE, GATED)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM scope_control_event) THEN
      RAISE EXCEPTION 'Preserve scoped rollback history; use an audited resume instead of downgrade';
      END IF; END $$""")
    _gate(GATED, BASE)
    op.execute('''
      DROP POLICY scope_control_audit_read ON audit_event;
      DROP FUNCTION change_scoped_effect_control(uuid,uuid,text,uuid,text),scoped_effects_enabled(uuid);
      DROP TABLE scope_control_event;
    ''')
