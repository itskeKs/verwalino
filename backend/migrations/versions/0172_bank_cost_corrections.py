"""Keep assigned bank evidence on the original cost across correction versions."""
from alembic import op
from sqlalchemy import text

revision = '0172'
down_revision = '0171'
branch_labels = None
depends_on = None

OPEN_BEFORE = """        SELECT c.gross_amount+coalesce((SELECT sum(b.amount) FROM public.bank_transaction b
          WHERE b.organization_id=org AND b.cost_entry_id=cost),0)
        FROM public.cost_entry c WHERE c.organization_id=org AND c.id=cost
          AND public.financial_scope_access(org,'cost_entry',cost,'read')
          AND NOT EXISTS(SELECT 1 FROM public.bank_transaction b WHERE b.organization_id=org AND b.cost_entry_id=cost
            AND NOT public.financial_scope_access(org,'bank_account',b.bank_account_id,'read'))"""
OPEN_AFTER = """        SELECT c.gross_amount-public.bank_cost_covered(org,cost)
        FROM public.cost_entry c WHERE c.organization_id=org AND c.id=cost
          AND public.financial_scope_access(org,'cost_entry',cost,'read')
          AND public.bank_cost_scopes_match(org,cost,public.financial_scope_id(org,'cost_entry',cost))
          AND NOT EXISTS(SELECT 1 FROM public.bank_cost_versions(org,cost,true) v
            JOIN public.bank_transaction b ON b.organization_id=org AND b.cost_entry_id=v.id
            WHERE NOT public.financial_scope_access(org,'cost_entry',b.cost_entry_id,'read')
               OR NOT public.financial_scope_access(org,'bank_account',b.bank_account_id,'read'))"""
HEAD_BEFORE = "OR source.status<>'confirmed' OR source.superseded_by_id IS NOT NULL OR NEW.amount>=0 OR NEW.status<>'ZUGEORDNET'"
HEAD_AFTER = "OR source.status<>'confirmed' OR (TG_OP='UPDATE' AND source.superseded_by_id IS NOT NULL) OR NEW.amount>=0 OR NEW.status<>'ZUGEORDNET'"
COVERED_BEFORE = """        SELECT coalesce(-sum(amount),0) INTO covered FROM public.bank_transaction
          WHERE organization_id=NEW.organization_id AND cost_entry_id=source.id AND id<>NEW.id;
        IF covered-NEW.amount>source.gross_amount THEN
          RAISE EXCEPTION 'Bank debits exceed cost total' USING ERRCODE='23514'; END IF;"""
COVERED_AFTER = """        IF source.superseded_by_id IS NOT NULL AND public.bank_cost_next(source) IS NULL THEN
          RAISE EXCEPTION 'Historical reviewed cost lacks an explicit bank successor'
            USING ERRCODE='23514',CONSTRAINT='bank_cost_mapping_required'; END IF;
        -- Restore may insert an already assigned line on a historical version.
        -- Validate every affected successor, regardless of archive row order.
        IF TG_OP='UPDATE' AND NOT public.bank_cost_scopes_match(NEW.organization_id,source.id,cost_scope) THEN
          RAISE EXCEPTION 'Classify all covered costs and bank accounts in the same scope'
            USING ERRCODE='23514',CONSTRAINT='bank_cost_chain_boundary'; END IF;
        PERFORM public.check_bank_cost_successors(NEW.organization_id,source.id,
          source.property_id,cost_scope,-NEW.amount,NULL);"""
BIND_BEFORE = """          ON other.organization_id=b.organization_id AND (
            (binding.cost_entry_id=b.cost_entry_id AND other.bank_account_id=b.bank_account_id)
            OR (binding.bank_account_id=b.bank_account_id AND other.cost_entry_id=b.cost_entry_id))"""
BIND_AFTER = """          CROSS JOIN LATERAL public.bank_cost_versions(b.organization_id,b.cost_entry_id,false) v
          JOIN public.accounting_scope_binding other ON other.organization_id=b.organization_id AND (
            (binding.cost_entry_id=v.id AND other.bank_account_id=b.bank_account_id)
            OR (binding.bank_account_id=b.bank_account_id AND other.cost_entry_id=v.id))"""
BIND_JOIN_BEFORE = "FROM public.bank_transaction b JOIN public.accounting_scope_binding other\n" + BIND_BEFORE
BIND_JOIN_AFTER = "FROM public.bank_transaction b\n" + BIND_AFTER


PREVIEW_SIGNATURE = '(uuid,uuid,uuid)'
PREPARE_SIGNATURE = '(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean)'
VISUAL_PREPARE_SIGNATURE = '(uuid,uuid,uuid,text,uuid,uuid,text,text,boolean,boolean,boolean)'
PREVIEW_CHANGES = [
    ('impacts:=public.allocation_correction_impacts(org,old.id,target);',
     'PERFORM public.require_bank_correction_read(org,old.id);\n    impacts:=public.allocation_correction_impacts(org,old.id,target);'),
    ("'vat_amount',c.vat_amount::text,'tenant_amount',c.apportionable_tenant_amount::text) ORDER BY l.position)",
     """'vat_amount',c.vat_amount::text,'tenant_amount',c.apportionable_tenant_amount::text)
    || CASE WHEN covered.amount>0 THEN jsonb_build_object('bank_covered_amount',round(covered.amount,2)::text)
            ELSE '{}'::jsonb END ORDER BY l.position)"""),
    ('WHERE l.organization_id=org AND l.allocation_set_id=old.id;\n  SELECT jsonb_agg',
     """CROSS JOIN LATERAL (SELECT public.bank_cost_covered(org,c.id) AS amount) covered
    WHERE l.organization_id=org AND l.allocation_set_id=old.id;
  SELECT jsonb_agg"""),
]
PREPARE_CHANGES = [
    ('  SELECT * INTO prior FROM public.allocation_correction',
     """  IF public.bank_cost_mapping_input()<>'{}'::jsonb THEN
    digest:=encode(sha256(convert_to(jsonb_build_array(digest,public.bank_cost_mapping_input())::text,'UTF8')),'hex');
  END IF;
  SELECT * INTO prior FROM public.allocation_correction"""),
    ("RAISE EXCEPTION 'Correction evidence or predecessor changed' USING ERRCODE='23505';END IF;",
     """RAISE EXCEPTION 'Correction evidence or predecessor changed' USING ERRCODE='23505';END IF;
  PERFORM public.validate_bank_cost_mapping(preview,public.bank_cost_mapping_input());"""),
]
COMMAND_CHANGES = [
    ('UPDATE public.cost_entry c SET superseded_by_id=costs[1] FROM public.document_cost_link l',
     """UPDATE public.cost_entry c SET superseded_by_id=costs[1],
    bank_coverage_successor_id=(SELECT replacement.cost_entry_id FROM public.document_cost_link replacement
      WHERE replacement.organization_id=org AND replacement.allocation_set_id=created
        AND replacement.allocation_line_id=(public.bank_cost_mapping_input()->>c.id::text)::uuid)
    FROM public.document_cost_link l"""),
]


def patch_functions(reverse=False):
    changes = [
        ('bank_cost_open_amount(uuid,uuid)', [(OPEN_BEFORE, OPEN_AFTER)]),
        ('guard_bank_cost_assignment()', [(HEAD_BEFORE, HEAD_AFTER), (COVERED_BEFORE, COVERED_AFTER),
                                           ('account public.bank_account; covered numeric;', 'account public.bank_account;')]),
        ('validate_accounting_binding(public.accounting_scope_binding)', [(BIND_JOIN_BEFORE, BIND_JOIN_AFTER)]),
    ]
    changes.extend((name + PREVIEW_SIGNATURE, PREVIEW_CHANGES) for name in (
        'allocation_correction_preview', 'visual_allocation_correction_preview', 'allocation_replacement_preview'))
    changes.extend([
        ('prepare_allocation_correction' + PREPARE_SIGNATURE, PREPARE_CHANGES),
        ('prepare_allocation_replacement' + PREPARE_SIGNATURE, PREPARE_CHANGES),
        ('prepare_visual_allocation_correction' + VISUAL_PREPARE_SIGNATURE, PREPARE_CHANGES),
        ('correct_allocation' + PREPARE_SIGNATURE[:-1] + ',uuid)', COMMAND_CHANGES),
        ('replace_allocation_source' + PREPARE_SIGNATURE[:-1] + ',uuid)', COMMAND_CHANGES),
        ('correct_visual_allocation' + VISUAL_PREPARE_SIGNATURE[:-1] + ',uuid,uuid)', COMMAND_CHANGES),
        ('protect_adopted_cost()', [
            ("(to_jsonb(NEW)-ARRAY['superseded_by_id','updated_at'])=(to_jsonb(OLD)-ARRAY['superseded_by_id','updated_at'])",
             "(to_jsonb(NEW)-ARRAY['superseded_by_id','bank_coverage_successor_id','updated_at'])"
             "=(to_jsonb(OLD)-ARRAY['superseded_by_id','bank_coverage_successor_id','updated_at'])")
        ]),
    ])
    for signature, replacements in changes:
        source = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:sig AS regprocedure))'), {'sig': signature})
        for before, after in replacements:
            old, new = (after, before) if reverse else (before, after)
            if source.count(old) != 1:
                raise RuntimeError(f'Unexpected definition of {signature} near {old!r}; repair forward')
            source = source.replace(old, new, 1)
        op.execute(source)


def upgrade():
    op.execute("""LOCK TABLE cost_entry,bank_transaction IN ACCESS EXCLUSIVE MODE NOWAIT;
      ALTER TABLE cost_entry ADD COLUMN bank_coverage_successor_id uuid,
        ADD CONSTRAINT fk_cost_entry_bank_successor FOREIGN KEY(organization_id,bank_coverage_successor_id)
          REFERENCES cost_entry(organization_id,id),
        ADD CONSTRAINT ck_cost_entry_bank_successor CHECK(bank_coverage_successor_id IS NULL OR (
          status='confirmed' AND superseded_by_id IS NOT NULL AND bank_coverage_successor_id<>id));
      CREATE INDEX ix_cost_entry_bank_predecessor ON cost_entry(organization_id,superseded_by_id)
        WHERE superseded_by_id IS NOT NULL;
      CREATE INDEX ix_cost_entry_bank_mapped_predecessor ON cost_entry(organization_id,bank_coverage_successor_id)
        WHERE bank_coverage_successor_id IS NOT NULL;
      CREATE FUNCTION bank_cost_next(cost public.cost_entry) RETURNS uuid
        LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$
        SELECT CASE WHEN (cost).bank_coverage_successor_id IS NOT NULL THEN (cost).bank_coverage_successor_id
          WHEN (cost).superseded_by_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s
              ON s.organization_id=l.organization_id AND s.id=l.allocation_set_id
            WHERE l.organization_id=(cost).organization_id AND l.cost_entry_id=(cost).id AND s.kind='intake_reviewed')
          THEN (cost).superseded_by_id END
      $$;
      CREATE FUNCTION bank_cost_versions(org uuid,cost uuid,backwards boolean) RETURNS TABLE(id uuid)
        LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$
        WITH RECURSIVE versions AS (
          SELECT c.id,public.bank_cost_next(c) AS next_id FROM public.cost_entry c WHERE c.organization_id=org AND c.id=cost
          UNION
          SELECT c.id,public.bank_cost_next(c) FROM versions v JOIN public.cost_entry c
            ON c.organization_id=org AND ((backwards AND (c.bank_coverage_successor_id=v.id OR c.superseded_by_id=v.id)
              AND public.bank_cost_next(c)=v.id) OR (NOT backwards AND c.id=v.next_id))
        )
        SELECT v.id FROM versions v
      $$;
      CREATE FUNCTION bank_cost_covered(org uuid,cost uuid) RETURNS numeric
        LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$
        SELECT coalesce(-sum(b.amount),0) FROM public.bank_cost_versions(org,cost,true) v
          JOIN public.bank_transaction b ON b.organization_id=org AND b.cost_entry_id=v.id
      $$;
      CREATE FUNCTION bank_cost_scopes_match(org uuid,cost uuid,sphere uuid) RETURNS boolean
        LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$
        SELECT NOT EXISTS(SELECT 1 FROM public.bank_cost_versions(org,cost,true) v
          JOIN public.bank_transaction b ON b.organization_id=org AND b.cost_entry_id=v.id
          LEFT JOIN public.accounting_scope_binding cb ON cb.organization_id=org AND cb.cost_entry_id=v.id
          LEFT JOIN public.accounting_scope_binding ab ON ab.organization_id=org AND ab.bank_account_id=b.bank_account_id
          WHERE cb.scope_id IS DISTINCT FROM sphere OR ab.scope_id IS DISTINCT FROM sphere)
      $$;
      CREATE FUNCTION check_bank_cost_successors(org uuid,cost uuid,property uuid,sphere uuid,
                                                  additional numeric, predecessor uuid) RETURNS void
        LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
      DECLARE successor record;
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM public.cost_entry c WHERE c.organization_id=org AND c.id=cost) THEN
          RAISE EXCEPTION 'Covered successor must belong to the same organization'
            USING ERRCODE='23514',CONSTRAINT='bank_cost_chain_boundary'; END IF;
        FOR successor IN SELECT c.*, binding.scope_id,public.bank_cost_next(c) AS next_id FROM public.bank_cost_versions(org,cost,false) v
          JOIN public.cost_entry c ON c.organization_id=org AND c.id=v.id
          LEFT JOIN public.accounting_scope_binding binding ON binding.organization_id=org AND binding.cost_entry_id=c.id
        LOOP
          IF successor.property_id<>property OR successor.id=predecessor
            OR (sphere IS NOT NULL AND successor.scope_id IS NOT NULL AND successor.scope_id<>sphere)
            OR (successor.superseded_by_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.cost_entry next
                  WHERE next.organization_id=org AND next.id=successor.next_id)) THEN
            RAISE EXCEPTION 'Covered successor must retain its property and accounting scope'
              USING ERRCODE='23514',CONSTRAINT='bank_cost_chain_boundary'; END IF;
          IF successor.gross_amount<public.bank_cost_covered(org,successor.id)+additional THEN
            RAISE EXCEPTION 'Corrected cost total cannot fall below linked bank coverage'
              USING ERRCODE='23514',CONSTRAINT='bank_cost_coverage_floor'; END IF;
        END LOOP;
      END $$;
      CREATE FUNCTION guard_covered_cost_correction() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      DECLARE covered numeric; sphere uuid; next_id uuid; reviewed boolean;
      BEGIN
        IF TG_OP='INSERT' THEN
          IF NEW.bank_coverage_successor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Explicit bank successor requires native reviewed correction provenance'
              USING ERRCODE='23514',CONSTRAINT='bank_cost_mapping_required';
          END IF;
          RETURN NEW;
        END IF;
        IF (NEW.id,NEW.organization_id,NEW.property_id,NEW.gross_amount,NEW.superseded_by_id,NEW.bank_coverage_successor_id)
          IS NOT DISTINCT FROM (OLD.id,OLD.organization_id,OLD.property_id,OLD.gross_amount,OLD.superseded_by_id,OLD.bank_coverage_successor_id)
          AND NOT (NEW.status='confirmed' AND OLD.status<>'confirmed') THEN RETURN NEW; END IF;
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||OLD.organization_id::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||OLD.organization_id::text,0));
        SELECT public.bank_cost_covered(OLD.organization_id,OLD.id) INTO covered;
        IF NEW.bank_coverage_successor_id IS DISTINCT FROM OLD.bank_coverage_successor_id AND (
          covered=0 OR OLD.superseded_by_id IS NOT NULL OR NEW.bank_coverage_successor_id IS NULL OR NOT EXISTS(
            SELECT 1 FROM public.document_cost_link prior JOIN public.allocation_set replacement
              ON replacement.organization_id=prior.organization_id AND replacement.predecessor_id=prior.allocation_set_id
            JOIN public.document_cost_link target ON target.organization_id=replacement.organization_id
              AND target.allocation_set_id=replacement.id AND target.cost_entry_id=NEW.bank_coverage_successor_id
            WHERE prior.organization_id=OLD.organization_id AND prior.cost_entry_id=OLD.id
              AND replacement.primary_cost_id=NEW.superseded_by_id
              AND public.correction_transaction_admission(OLD.organization_id,replacement.id))) THEN
          RAISE EXCEPTION 'Bank successor requires the reviewed correction'
            USING ERRCODE='23514',CONSTRAINT='bank_cost_mapping_required'; END IF;
        IF covered=0 THEN RETURN NEW; END IF;
        IF (NEW.id,NEW.organization_id,NEW.property_id) IS DISTINCT FROM (OLD.id,OLD.organization_id,OLD.property_id)
          OR (OLD.superseded_by_id IS NOT NULL AND NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id) THEN
          RAISE EXCEPTION 'Covered cost lineage cannot be moved or detached'
            USING ERRCODE='23514',CONSTRAINT='bank_cost_chain_boundary'; END IF;
        IF NEW.gross_amount<covered THEN
          RAISE EXCEPTION 'Corrected cost total cannot fall below linked bank coverage'
            USING ERRCODE='23514',CONSTRAINT='bank_cost_coverage_floor'; END IF;
        IF NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id THEN
          SELECT EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s
            ON s.organization_id=l.organization_id AND s.id=l.allocation_set_id
            WHERE l.organization_id=OLD.organization_id AND l.cost_entry_id=OLD.id AND s.kind='intake_reviewed')
            INTO reviewed;
          next_id:=CASE WHEN reviewed THEN NEW.bank_coverage_successor_id ELSE NEW.superseded_by_id END;
          IF next_id IS NULL THEN
            RAISE EXCEPTION 'Covered reviewed cost requires an explicit bank successor'
              USING ERRCODE='23514',CONSTRAINT='bank_cost_mapping_required'; END IF;
          SELECT scope_id INTO sphere FROM public.accounting_scope_binding
            WHERE organization_id=OLD.organization_id AND cost_entry_id=OLD.id;
          IF (SELECT scope_id FROM public.accounting_scope_binding
              WHERE organization_id=OLD.organization_id AND cost_entry_id=next_id)
              IS DISTINCT FROM sphere THEN
            RAISE EXCEPTION 'Covered successor must retain its exact accounting scope'
              USING ERRCODE='23514',CONSTRAINT='bank_cost_chain_boundary'; END IF;
          PERFORM public.check_bank_cost_successors(OLD.organization_id,next_id,
            OLD.property_id,sphere,covered,OLD.id);
        END IF;
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION bank_cost_next(public.cost_entry),bank_cost_versions(uuid,uuid,boolean),bank_cost_covered(uuid,uuid),
        bank_cost_scopes_match(uuid,uuid,uuid),
        check_bank_cost_successors(uuid,uuid,uuid,uuid,numeric,uuid),guard_covered_cost_correction()
        FROM PUBLIC,app_rw,app_worker,app_review;
      CREATE TRIGGER guard_covered_cost_correction
        BEFORE INSERT OR UPDATE OF id,organization_id,property_id,gross_amount,superseded_by_id,status,bank_coverage_successor_id ON cost_entry
        FOR EACH ROW EXECUTE FUNCTION guard_covered_cost_correction();
      ALTER TABLE cost_entry ENABLE ALWAYS TRIGGER guard_covered_cost_correction;
      SELECT public.check_bank_cost_successors(b.organization_id,b.cost_entry_id,c.property_id,s.scope_id,0,NULL)
        FROM (SELECT DISTINCT organization_id,cost_entry_id FROM public.bank_transaction WHERE cost_entry_id IS NOT NULL) b
        JOIN public.cost_entry c ON c.organization_id=b.organization_id AND c.id=b.cost_entry_id
        LEFT JOIN public.accounting_scope_binding s ON s.organization_id=c.organization_id AND s.cost_entry_id=c.id;
    """)
    op.execute(MAPPING_SQL)
    patch_functions()


def downgrade():
    op.execute('LOCK TABLE cost_entry,bank_transaction IN ACCESS EXCLUSIVE MODE NOWAIT')
    if op.get_bind().scalar(text("""SELECT EXISTS(SELECT 1 FROM bank_transaction b JOIN cost_entry c
        ON c.organization_id=b.organization_id AND c.id=b.cost_entry_id WHERE c.superseded_by_id IS NOT NULL)
        OR EXISTS(SELECT 1 FROM cost_entry WHERE bank_coverage_successor_id IS NOT NULL)""")):
        raise RuntimeError('Retain coverage across corrected costs; repair forward')
    patch_functions(reverse=True)
    op.execute("""DROP TRIGGER guard_covered_cost_correction ON cost_entry;
      DROP FUNCTION require_bank_correction_read(uuid,uuid),bank_cost_mapping_input(),validate_bank_cost_mapping(jsonb,jsonb);
      DROP FUNCTION guard_covered_cost_correction(),check_bank_cost_successors(uuid,uuid,uuid,uuid,numeric,uuid),
        bank_cost_covered(uuid,uuid),bank_cost_scopes_match(uuid,uuid,uuid),bank_cost_versions(uuid,uuid,boolean);
      DROP FUNCTION bank_cost_next(public.cost_entry);
      DROP INDEX ix_cost_entry_bank_predecessor,ix_cost_entry_bank_mapped_predecessor;
      ALTER TABLE cost_entry DROP COLUMN bank_coverage_successor_id;""")


MAPPING_SQL = """
CREATE FUNCTION require_bank_correction_read(org uuid,predecessor uuid) RETURNS void
  LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF EXISTS(SELECT 1 FROM public.document_cost_link l
    CROSS JOIN LATERAL public.bank_cost_versions(org,l.cost_entry_id,true) v
    JOIN public.bank_transaction b ON b.organization_id=org AND b.cost_entry_id=v.id
    WHERE l.organization_id=org AND l.allocation_set_id=predecessor
      AND (NOT public.financial_scope_access(org,'cost_entry',v.id,'read')
        OR NOT public.financial_scope_access(org,'bank_account',b.bank_account_id,'read')
        OR NOT public.bank_cost_scopes_match(org,l.cost_entry_id,
          public.financial_scope_id(org,'cost_entry',l.cost_entry_id)))) THEN
    RAISE EXCEPTION 'Bank coverage is not available for this review' USING ERRCODE='42501';
  END IF;
END $$;

-- This transaction-local value is request input only, never authority. The
-- native command validates it against the locked preview and attests its digest.
CREATE FUNCTION bank_cost_mapping_input() RETURNS jsonb
  LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$
  SELECT coalesce(nullif(current_setting('app.bank_cost_mapping',true),'')::jsonb,'{}'::jsonb)
$$;

CREATE FUNCTION validate_bank_cost_mapping(preview jsonb,mapping jsonb) RETURNS void
  LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE previous jsonb:=preview->'old'->'costs'; proposed jsonb:=preview->'proposed'->'costs';
BEGIN
  IF jsonb_typeof(mapping)<>'object' THEN
    RAISE EXCEPTION 'Bank successors must be an object' USING ERRCODE='23514',CONSTRAINT='bank_cost_mapping_required';
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_each_text(mapping) m
      WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(previous) c
        WHERE c->>'cost_entry_id'=m.key)
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(proposed) l WHERE l->>'allocation_line_id'=m.value)) THEN
    RAISE EXCEPTION 'Bank successor source or proposed line is inaccessible' USING ERRCODE='42501';
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(previous) c
      WHERE (c ? 'bank_covered_amount') IS DISTINCT FROM (mapping ? (c->>'cost_entry_id'))) THEN
    RAISE EXCEPTION 'Select one proposed line for every covered old cost'
      USING ERRCODE='23514',CONSTRAINT='bank_cost_mapping_required';
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(proposed) l
    JOIN jsonb_array_elements(previous) c ON mapping->>(c->>'cost_entry_id')=l->>'allocation_line_id'
    GROUP BY l HAVING sum((c->>'bank_covered_amount')::numeric)>(l->>'gross_amount')::numeric) THEN
    RAISE EXCEPTION 'Proposed cost total cannot fall below inherited bank coverage'
      USING ERRCODE='23514',CONSTRAINT='bank_cost_coverage_floor';
  END IF;
END $$;
REVOKE ALL ON FUNCTION require_bank_correction_read(uuid,uuid),bank_cost_mapping_input(),
  validate_bank_cost_mapping(jsonb,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;
"""
