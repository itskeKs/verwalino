"""Share reasoned commercial position rules without editing confirmed costs."""
from alembic import op
from sqlalchemy import text

revision = '0169'
down_revision = '0168'
branch_labels = None
depends_on = None

DIGEST_BEFORE = "ARRAY['property','unit','cost_entry','payment'"
DIGEST_AFTER = "ARRAY['commercial_preallocation','property','unit','cost_entry','payment'"


def patch_digest(reverse=False):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('financial_computation_source_digest(uuid,uuid,uuid,boolean)'::regprocedure)"))
    before, after = (DIGEST_AFTER, DIGEST_BEFORE) if reverse else (DIGEST_BEFORE, DIGEST_AFTER)
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected computation source digest; repair forward')
    op.execute(definition.replace(before, after, 1))


def upgrade():
    # Keep predecessor-only rollback rehearsals independent of this later
    # table, following the reserve ledger's revision-owned trigger function.
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('recheck_transaction_computations()'::regprocedure)"))
    original = 'FUNCTION public.recheck_transaction_computations()'
    if definition.count(original) != 1:
        raise RuntimeError('Unexpected computation guard; repair forward')
    op.execute(definition.replace(original, 'FUNCTION public.recheck_commercial_computations()'))
    op.execute('REVOKE ALL ON FUNCTION recheck_commercial_computations() FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute('''CREATE TABLE commercial_preallocation (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      property_id uuid NOT NULL, cost_entry_id uuid NOT NULL,
      commercial_share_percent numeric(5,2), commercial_amount numeric(14,2), commercial_reason varchar(2000) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,cost_entry_id),
      FOREIGN KEY(organization_id,cost_entry_id) REFERENCES cost_entry(organization_id,id),
      FOREIGN KEY(organization_id,property_id) REFERENCES property(organization_id,id),
      CHECK(num_nonnulls(commercial_share_percent,commercial_amount)=1),
      CHECK(commercial_share_percent IS NULL OR commercial_share_percent BETWEEN 0 AND 100),
      CHECK(commercial_amount IS NULL OR commercial_amount>=0 AND commercial_amount<'Infinity'::numeric),
      CHECK(length(btrim(commercial_reason))>0));
      CREATE INDEX commercial_preallocation_property ON commercial_preallocation(organization_id,property_id);
      ALTER TABLE commercial_preallocation ENABLE ROW LEVEL SECURITY;
      ALTER TABLE commercial_preallocation FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON commercial_preallocation FROM PUBLIC,app_rw,app_worker,app_review;
      GRANT SELECT,INSERT,UPDATE,DELETE ON commercial_preallocation TO app_rw;
    ''')
    for action, capability in [('SELECT', 'read'), ('INSERT', 'prepare'), ('UPDATE', 'prepare'), ('DELETE', 'prepare')]:
        actor = 'IS NOT NULL' if action == 'SELECT' else "IN ('OWNER_ADMIN','CLERK')"
        predicate = f"""organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND scope_actor_role(organization_id) {actor}
          AND property_financial_disclosure_access(organization_id,property_id,'read')
          AND financial_scope_access(organization_id,'cost_entry',cost_entry_id,'{capability}')
          AND EXISTS(SELECT 1 FROM cost_entry c WHERE c.organization_id=commercial_preallocation.organization_id
            AND c.id=commercial_preallocation.cost_entry_id AND c.property_id=commercial_preallocation.property_id)"""
        clause = f'WITH CHECK ({predicate})' if action == 'INSERT' else f'USING ({predicate})'
        if action == 'UPDATE':
            clause += f' WITH CHECK ({predicate})'
        op.execute(f'CREATE POLICY commercial_preallocation_{action.lower()} ON commercial_preallocation FOR {action} {clause}')
    op.execute('''CREATE FUNCTION guard_commercial_preallocation() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      DECLARE item public.commercial_preallocation; source public.cost_entry;
      BEGIN
        item:=CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||item.organization_id::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||item.organization_id::text,0));
        IF session_user<>'app_migrator' AND NOT public.property_financial_source_access(item.organization_id,item.property_id) THEN
          RAISE EXCEPTION 'Complete financial source authority required' USING ERRCODE='42501'; END IF;
        IF TG_OP='UPDATE' AND (NEW.organization_id,NEW.property_id,NEW.cost_entry_id,NEW.id,NEW.created_at)
          IS DISTINCT FROM (OLD.organization_id,OLD.property_id,OLD.cost_entry_id,OLD.id,OLD.created_at) THEN
          RAISE EXCEPTION 'Commercial rule source identity is immutable' USING ERRCODE='23514'; END IF;
        SELECT * INTO source FROM public.cost_entry WHERE organization_id=item.organization_id AND id=item.cost_entry_id;
        -- Historical confirmed sources keep their rule through archive restore.
        -- Ordinary commands resolve only active sources in the settlement service.
        IF source.id IS NULL OR source.property_id<>item.property_id OR source.status<>'confirmed' THEN
          RAISE EXCEPTION 'Confirmed commercial rule source required' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM public.settlement_run r WHERE r.organization_id=item.organization_id
          AND r.property_id=item.property_id AND r.run_type='BK_MIETER' AND r.status='finalized' AND r.superseded_by_id IS NULL
          AND public.correction_snapshot_cost_ids(r.snapshot_payload) ? item.cost_entry_id::text) THEN
          RAISE EXCEPTION 'Commercial rule already used by a finalized statement' USING ERRCODE='P2020'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END $$;
      REVOKE ALL ON FUNCTION guard_commercial_preallocation() FROM PUBLIC,app_rw,app_worker,app_review;
      CREATE TRIGGER guard_commercial_preallocation BEFORE INSERT OR UPDATE OR DELETE ON commercial_preallocation
        FOR EACH ROW EXECUTE FUNCTION guard_commercial_preallocation();
      CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON commercial_preallocation
        FOR EACH ROW EXECUTE FUNCTION organization_lifecycle_guard();
      CREATE TRIGGER a_lock_financial_source_input BEFORE INSERT OR UPDATE OR DELETE ON commercial_preallocation
        FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();
      CREATE CONSTRAINT TRIGGER recheck_computation AFTER INSERT OR UPDATE OR DELETE ON commercial_preallocation
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION recheck_commercial_computations();
    ''')
    patch_digest()


def downgrade():
    op.execute('LOCK TABLE commercial_preallocation IN ACCESS EXCLUSIVE MODE NOWAIT')
    if op.get_bind().scalar(text("SELECT EXISTS(SELECT 1 FROM commercial_preallocation) OR EXISTS(SELECT 1 FROM settlement_run WHERE snapshot_payload::text LIKE '%\"commercial_reason\"%')")):
        raise RuntimeError('Retain commercial preallocation evidence; repair forward')
    patch_digest(reverse=True)
    op.execute('DROP TABLE commercial_preallocation; DROP FUNCTION guard_commercial_preallocation(),recheck_commercial_computations()')
