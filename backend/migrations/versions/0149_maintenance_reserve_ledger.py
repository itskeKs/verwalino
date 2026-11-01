"""Retain reserve adjustments and cash funding evidence without rewriting years."""
from alembic import op
from sqlalchemy import text

revision = '0149'
down_revision = '0148'
branch_labels = None
depends_on = None
PAYMENT_TYPES = "'KALTMIETE','NK_VORAUSZAHLUNG','HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG','NACHZAHLUNG','KAUTION'"
LINE_TYPES = "'COST','ADVANCE_PAYMENT','BALANCE','SUBTOTAL','OWNER_COST_TOTAL','PLAN_ADVANCE','ABRECHNUNGSSPITZE','RESERVE_CONTRIBUTION','RESERVE_OPENING','RESERVE_WITHDRAWAL','RESERVE_CLOSING','BANK_BALANCE','RECEIVABLE','PAYABLE'"


def reserve_computation_check():
    # Freeze the existing deferred-check implementation under this revision's
    # own name. A direct trigger dependency on 0115 prevents its isolated
    # downgrade rehearsals while the later reserve table still exists.
    definition = op.get_bind().scalar(text(
        "SELECT pg_get_functiondef('recheck_transaction_computations()'::regprocedure)"))
    original = 'FUNCTION public.recheck_transaction_computations()'
    assert definition.count(original) == 1
    op.execute(definition.replace(original, 'FUNCTION public.recheck_reserve_computations()'))
    op.execute('REVOKE ALL ON FUNCTION recheck_reserve_computations() FROM PUBLIC,app_rw,app_worker,app_review')


def patch_source_digest(reverse=False):
    signature = 'financial_computation_source_digest(uuid,uuid,uuid,boolean)'
    definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'), {'signature': signature})
    before, after = "'economic_plan_line'] LOOP", "'economic_plan_line','reserve_ledger_entry'] LOOP"
    if reverse:
        before, after = after, before
        if after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def patch_booking_scope(reverse=False):
    signature = 'validate_accounting_binding_baseline(public.accounting_scope_binding)'
    definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'), {'signature': signature})
    before = "'HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG'"
    after = before + ",'RUECKLAGENENTNAHME'"
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def retention_catalog(restore=False):
    # Frozen 0147 body: exempt only the reviewed row-conditional payment guard.
    # The reserve ledger remains retained by its privileges and append-only guard.
    definition = r'''
    CREATE OR REPLACE FUNCTION public.organization_retention_catalog()
      RETURNS TABLE(entity text,pk text[],disposition text,retain_when text)
      LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      WITH catalog AS (
        SELECT c.relname::text entity, keys.pk,
          -- Live SEPA consent/configuration has no runtime DELETE action,
          -- but that alone is not a voucher binding (ADR-0012). Any actual
          -- immutable DELETE trigger/policy still protects it below.
          (c.relname NOT IN ('sepa_creditor','sepa_mandate') AND (
          NOT has_table_privilege('app_rw',c.oid,'DELETE')
          OR (c.relrowsecurity AND NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid
            AND p.polcmd IN ('d','*') AND p.polpermissive AND pg_get_expr(p.polqual,p.polrelid)<>'false'))))
          OR EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND p.polcmd IN ('d','*')
            AND NOT p.polpermissive AND pg_get_expr(p.polqual,p.polrelid)='false')
          OR EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc f ON f.oid=t.tgfoid
            WHERE t.tgrelid=c.oid AND NOT t.tgisinternal AND t.tgenabled<>'D' AND (t.tgtype & 8)>0
            AND f.proname NOT IN (
              'organization_lifecycle_guard','lock_scope_principal_change','lock_scope_unit_history',
              'lock_financial_input_sources','lock_strict_property_source_binding',
              'lock_property_source_security_change','lock_knowledge_budget_policy',
              'lock_invoice_sources','lock_mapping_meter','task_acl_lock','sepa_input_lock','t55_input_lock',
              'recheck_transaction_computations','guard_declaration_worker_effect','guard_declaration_issue_review',
              'authorize_bound_financial_write','authorize_bound_payment_write','authorize_dunning_write',
              'guard_financial_source_input','guard_strict_financial_resource',
              'guard_property_source_security','guard_knowledge_budget_policy',
              'trg_cost_entry_immutability','protect_adopted_cost','trg_payment_immutability',
              'trg_settlement_run_immutability','trg_settlement_line_immutability',
              'trg_economic_plan_immutability','trg_economic_plan_line_immutability',
              'trg_dunning_notice_immutability','protect_dunning_quarantine')) protected
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN LATERAL (SELECT array_agg(a.attname::text ORDER BY k.pos) pk
          FROM pg_constraint p CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(num,pos)
          JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num
          WHERE p.conrelid=c.oid AND p.contype='p') keys ON true
        WHERE n.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute a
          WHERE a.attrelid=c.oid AND a.attname='organization_id' AND NOT a.attisdropped)
      ), classified AS (
        SELECT *, CASE
          WHEN pk IS NULL THEN 'unclassified'
          WHEN entity='organization_deletion' THEN 'lifecycle'
          WHEN entity IN ('user','membership','person') THEN 'anonymize'
          WHEN entity='password_reset_request' THEN 'delete'
          WHEN protected THEN 'retain'
          WHEN entity IN ('cost_entry','payment','settlement_run','settlement_line',
            'economic_plan','economic_plan_line','dunning_notice') THEN 'conditional'
          ELSE 'delete' END disposition FROM catalog
      ) SELECT entity,pk,disposition,CASE
        WHEN disposition='retain' THEN 'true'
        WHEN disposition<>'conditional' THEN 'false'
        WHEN entity='cost_entry' THEN $sql$t.status='confirmed' OR EXISTS(SELECT 1 FROM public.document_cost_link l JOIN public.allocation_set s ON s.id=l.allocation_set_id AND s.organization_id=l.organization_id WHERE l.organization_id=t.organization_id AND l.cost_entry_id=t.id AND s.kind='intake_reviewed')$sql$
        WHEN entity='payment' THEN $sql$t.status='booked'$sql$
        WHEN entity IN ('settlement_run','economic_plan') THEN $sql$t.status='finalized'$sql$
        WHEN entity='dunning_notice' THEN $sql$t.status='issued' OR EXISTS(SELECT 1 FROM public.dunning_quarantine_disposition q WHERE q.organization_id=t.organization_id AND q.notice_id=t.id)$sql$
        WHEN entity='settlement_line' THEN $sql$EXISTS(SELECT 1 FROM public.settlement_run r WHERE r.organization_id=t.organization_id AND r.id=t.settlement_run_id AND r.status='finalized')$sql$
        WHEN entity='economic_plan_line' THEN $sql$EXISTS(SELECT 1 FROM public.economic_plan r WHERE r.organization_id=t.organization_id AND r.id=t.economic_plan_id AND r.status='finalized')$sql$
        END FROM classified ORDER BY entity;
    $$;
    REVOKE ALL ON FUNCTION public.organization_retention_catalog() FROM PUBLIC,app_rw,app_worker,app_review;
    '''
    if not restore:
        definition = definition.replace("'protect_dunning_quarantine'",
            "'protect_dunning_quarantine','guard_reserve_payment_evidence'")
    op.execute(definition)


def upgrade():
    op.execute(f"""ALTER TABLE settlement_line DROP CONSTRAINT ck_settlement_line_ck_settlement_line_line_type;
      ALTER TABLE settlement_line ADD CONSTRAINT ck_settlement_line_ck_settlement_line_line_type
        CHECK(line_type IN ({LINE_TYPES},'RESERVE_LATE_CASH','RESERVE_ADJUSTMENT'));""")
    reserve_computation_check()
    op.execute(f"""ALTER TABLE payment DROP CONSTRAINT ck_payment_ck_payment_type;
      ALTER TABLE payment ADD CONSTRAINT ck_payment_ck_payment_type CHECK(payment_type IN ({PAYMENT_TYPES},'RUECKLAGENENTNAHME'));
      ALTER TABLE payment ADD CONSTRAINT ck_reserve_payout CHECK(payment_type<>'RUECKLAGENENTNAHME' OR
        (tenant_profile_id IS NULL AND owner_profile_id IS NULL AND lease_agreement_id IS NULL
          AND (direction='OUT' OR reverses_id IS NOT NULL)));""")
    op.execute('''
    ALTER TABLE resolution_register_entry ADD CONSTRAINT uq_reserve_register_org UNIQUE(organization_id,id);
    ALTER TABLE payment ADD CONSTRAINT uq_reserve_payment_org UNIQUE(organization_id,id);
    ALTER TABLE payment ADD COLUMN reserve_cost_entry_id uuid;
    ALTER TABLE payment ADD COLUMN reserve_register_entry_id uuid;
    ALTER TABLE payment ADD CONSTRAINT fk_reserve_payment_cost FOREIGN KEY(organization_id,reserve_cost_entry_id) REFERENCES cost_entry(organization_id,id);
    ALTER TABLE payment ADD CONSTRAINT fk_reserve_payment_register FOREIGN KEY(organization_id,reserve_register_entry_id) REFERENCES resolution_register_entry(organization_id,id);
    ALTER TABLE payment ADD CONSTRAINT ck_reserve_payment_evidence CHECK(
      (payment_type='RUECKLAGENENTNAHME' OR (reserve_cost_entry_id IS NULL AND reserve_register_entry_id IS NULL))
      AND (payment_type<>'RUECKLAGENENTNAHME' OR status<>'booked' OR reverses_id IS NOT NULL
        OR (reserve_cost_entry_id IS NOT NULL AND reserve_register_entry_id IS NOT NULL)));
    CREATE TABLE reserve_ledger_entry (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES organization(id), property_id uuid NOT NULL,
      settlement_run_id uuid NOT NULL, entry_date date NOT NULL,
      component varchar(16) NOT NULL CHECK(component IN ('OPENING','CONTRIBUTION','WITHDRAWAL','ADJUSTMENT','FUNDING')),
      amount numeric(14,2) NOT NULL CHECK(amount <> 'NaN'::numeric),
      reason varchar(2000) NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
      payment_id uuid, cost_entry_id uuid, register_entry_id uuid, unit_id uuid,
      created_by uuid NOT NULL REFERENCES "user"(id), created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(organization_id,property_id) REFERENCES property(organization_id,id),
      FOREIGN KEY(organization_id,settlement_run_id) REFERENCES settlement_run(organization_id,id),
      FOREIGN KEY(organization_id,payment_id) REFERENCES payment(organization_id,id),
      FOREIGN KEY(organization_id,unit_id) REFERENCES unit(organization_id,id),
      FOREIGN KEY(organization_id,cost_entry_id) REFERENCES cost_entry(organization_id,id),
      FOREIGN KEY(organization_id,register_entry_id) REFERENCES resolution_register_entry(organization_id,id),
      CHECK((unit_id IS NULL OR (component='CONTRIBUTION' AND payment_id IS NULL)) AND ((component='FUNDING' AND payment_id IS NOT NULL AND cost_entry_id IS NOT NULL AND register_entry_id IS NOT NULL AND amount=0)
         OR (component='CONTRIBUTION' AND payment_id IS NOT NULL AND cost_entry_id IS NULL AND register_entry_id IS NULL)
         OR (component='OPENING' AND payment_id IS NULL AND cost_entry_id IS NULL AND register_entry_id IS NULL)
         OR (component='CONTRIBUTION' AND payment_id IS NULL AND unit_id IS NOT NULL AND cost_entry_id IS NULL AND register_entry_id IS NULL)
         OR (component IN ('WITHDRAWAL','ADJUSTMENT') AND payment_id IS NULL AND cost_entry_id IS NOT NULL AND register_entry_id IS NOT NULL))),
      UNIQUE(organization_id,payment_id));
    CREATE INDEX ix_reserve_property_date ON reserve_ledger_entry(organization_id,property_id,entry_date);
    ALTER TABLE reserve_ledger_entry ENABLE ROW LEVEL SECURITY;
    ALTER TABLE reserve_ledger_entry FORCE ROW LEVEL SECURITY;
    CREATE POLICY reserve_read ON reserve_ledger_entry FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND financial_scope_access(organization_id,'settlement_run',settlement_run_id,'read')
      AND property_financial_disclosure_access(organization_id,property_id,'read'));
    CREATE POLICY reserve_insert ON reserve_ledger_entry FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
      AND financial_scope_access(organization_id,'settlement_run',settlement_run_id,'prepare')
      AND property_financial_disclosure_access(organization_id,property_id,'read'));
    REVOKE ALL ON reserve_ledger_entry FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT,INSERT ON reserve_ledger_entry TO app_rw;
    -- Invoker rights preserve RLS for service reads; guards already run as definer.
    -- VOLATILE sees writes serialized by financial-inputs, including this statement.
    CREATE FUNCTION reserve_funded_amount(org uuid,cost uuid,excluded_payment uuid) RETURNS numeric
    LANGUAGE sql VOLATILE SET search_path=pg_catalog,pg_temp AS $$
      WITH RECURSIVE cost_chain AS (
        SELECT c.id,c.superseded_by_id FROM public.cost_entry c
        WHERE c.organization_id=org AND c.id=cost
        UNION
        SELECT c.id,c.superseded_by_id FROM public.cost_entry c
        JOIN cost_chain prior ON c.id=prior.superseded_by_id OR c.superseded_by_id=prior.id
        WHERE c.organization_id=org
      )
      SELECT COALESCE((SELECT sum(p.amount) FROM public.payment p
        LEFT JOIN public.reserve_ledger_entry e ON e.organization_id=p.organization_id AND e.payment_id=p.id AND e.component='FUNDING'
        WHERE p.organization_id=org AND COALESCE(p.reserve_cost_entry_id,e.cost_entry_id) IN (SELECT id FROM cost_chain)
          AND p.status='booked' AND p.reverses_id IS NULL AND p.reversed_by_id IS NULL
          AND p.id IS DISTINCT FROM excluded_payment),0)
        + COALESCE((SELECT sum(CASE WHEN e.component='ADJUSTMENT' THEN abs(e.amount) ELSE e.amount END)
          FROM public.reserve_ledger_entry e WHERE e.organization_id=org AND e.cost_entry_id IN (SELECT id FROM cost_chain)
            AND e.payment_id IS NULL AND e.component IN ('WITHDRAWAL','ADJUSTMENT')),0)
    $$;
    REVOKE ALL ON FUNCTION reserve_funded_amount(uuid,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION reserve_funded_amount(uuid,uuid,uuid) TO app_rw;
    -- Canonical frozen annual reserve cells: Hamilton cents, stable unit/owner ties.
    -- Planning display, automatic cash and correction caps all consume these cells.
    CREATE FUNCTION reserve_plan_cells(payload jsonb,total numeric,starts date) RETURNS jsonb
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
      WITH owners AS (
        SELECT u->>'id' unit_id,u->>'number' unit_number,o->>'owner_profile_id' owner_profile_id,
          (u->>'mea')::numeric mea,COALESCE((o->>'mea_share')::numeric,0) owner_mea,
          count(*) OVER (PARTITION BY u->>'id') n,
          sum(COALESCE((o->>'mea_share')::numeric,0)) OVER (PARTITION BY u->>'id') denominator
        FROM jsonb_array_elements(payload->'inputs'->'units') u
        JOIN jsonb_array_elements(payload->'inputs'->'owner_periods') o ON o->>'unit_id'=u->>'id'
        WHERE (u->>'mea')::numeric>0 AND (o->>'from')::date<=starts
          AND (o->>'to' IS NULL OR (o->>'to')::date>=starts)
      ), weights AS (
        SELECT *,CASE WHEN n=1 THEN mea ELSE mea*owner_mea/NULLIF(denominator,0) END weight FROM owners
      ), exact AS (
        SELECT *,round(total,2)*weight/sum(weight) OVER () exact FROM weights
      ), ranked AS (
        SELECT *,floor(exact*100)/100 base,
          row_number() OVER (ORDER BY exact-floor(exact*100)/100 DESC,exact DESC,unit_id,owner_profile_id) rank
        FROM exact
      ) SELECT COALESCE(jsonb_agg(jsonb_build_object('unit_id',unit_id,'unit_number',unit_number,
          'owner_profile_id',owner_profile_id,'amount',
          (base+CASE WHEN rank<=round((round(total,2)-sum_base)*100) THEN 0.01 ELSE 0 END)::text)
          ORDER BY unit_id,owner_profile_id),'[]'::jsonb)
        FROM (SELECT *,sum(base) OVER () sum_base FROM ranked) cells
    $$;
    CREATE FUNCTION reserve_hausgeld_share(cash numeric,units text[],planned jsonb,owners jsonb) RETURNS numeric
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
      SELECT round(cash*COALESCE((SELECT sum((r->>'amount')::numeric)
          FROM jsonb_array_elements(planned) r WHERE r->>'unit_id'=ANY(units)),0)
        /NULLIF((SELECT sum((o->>'annual_advance')::numeric)
          FROM jsonb_array_elements(owners) o WHERE o->>'unit_id'=ANY(units)),0),2)
    $$;
    -- Allocate the already-rounded cash total to units, with no lost/extra cent.
    CREATE FUNCTION reserve_unit_cash(cash numeric,units text[],planned jsonb,owners jsonb)
    RETURNS TABLE(unit_id text,amount numeric) LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
      WITH weights AS (
        SELECT r->>'unit_id' unit_id,sum((r->>'amount')::numeric) weight
        FROM jsonb_array_elements(planned) r WHERE r->>'unit_id'=ANY(units) GROUP BY r->>'unit_id'
      ), exact AS (
        SELECT *,abs(public.reserve_hausgeld_share(cash,units,planned,owners))*weight/NULLIF(sum(weight) OVER (),0) exact
        FROM weights
      ), ranked AS (
        SELECT *,floor(exact*100)/100 base,
          row_number() OVER (ORDER BY exact-floor(exact*100)/100 DESC,exact DESC,unit_id) rank FROM exact
      ) SELECT unit_id,sign(cash)*(base+CASE WHEN rank<=round((abs(public.reserve_hausgeld_share(cash,units,planned,owners))
          -sum(base) OVER ())*100) THEN 0.01 ELSE 0 END) FROM ranked
    $$;
    -- Same exact/covering-plan selection for current-period automatic cash and caps.
    CREATE FUNCTION reserve_cash_plan(org uuid,property uuid,starts date,ends date,selected uuid,cash_date date)
    RETURNS uuid LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$
      WITH exact AS (
        SELECT p.id FROM public.economic_plan p WHERE p.organization_id=org AND p.property_id=property
          AND p.status='finalized' AND p.superseded_by_id IS NULL AND p.period_start=starts AND p.period_end=ends
          AND (selected IS NULL OR p.id=selected)
      ) SELECT COALESCE((SELECT id FROM exact WHERE (SELECT count(*) FROM exact)=1),
        (SELECT p.id FROM public.economic_plan p WHERE p.organization_id=org AND p.property_id=property
          AND p.status='finalized' AND p.period_start<=cash_date AND p.period_end>=cash_date
          AND selected IS NULL AND NOT EXISTS(SELECT 1 FROM exact)
          ORDER BY p.superseded_by_id IS NOT NULL,p.period_start DESC,p.id LIMIT 1))
    $$;
    -- Invoker reads preserve RLS; the definer ledger guard calls this same query.
    -- VOLATILE sees prior entries serialized by the organization financial lock.
    CREATE FUNCTION reserve_contribution_headroom(org uuid,run uuid,unit uuid) RETURNS numeric
    LANGUAGE sql VOLATILE SET search_path=pg_catalog,pg_temp AS $$
      WITH parent AS (SELECT * FROM public.settlement_run WHERE organization_id=org AND id=run),
      cash AS (
        SELECT p.*,CASE WHEN p.reverses_id IS NULL THEN a.amount ELSE -a.amount END assigned,
          public.reserve_cash_plan(org,r.property_id,r.period_start,r.period_end,r.economic_plan_id,p.value_date) plan_id,
          ARRAY(SELECT DISTINCT u.id::text FROM public.ownership_history h JOIN public.unit u
            ON u.organization_id=h.organization_id AND u.id=h.unit_id
            WHERE h.organization_id=org AND h.owner_profile_id=p.owner_profile_id AND u.property_id=p.property_id
              AND h.valid_from<=p.value_date AND (h.valid_to IS NULL OR h.valid_to>p.value_date)) units
        FROM public.payment p CROSS JOIN parent r LEFT JOIN public.reserve_ledger_entry a
          ON a.organization_id=org AND a.component='CONTRIBUTION' AND a.payment_id=COALESCE(p.reverses_id,p.id)
        WHERE p.organization_id=org AND p.property_id=r.property_id AND p.status='booked' AND p.cancelled_at IS NULL
          AND p.value_date BETWEEN r.period_start AND r.period_end
          AND p.payment_type IN ('HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL')
      ), movements AS (
        SELECT p.*,b.planned,b.owners,
          (SELECT jsonb_agg(jsonb_build_object('unit_id',c->>'unit_id','annual_advance',c->>'amount'))
            FROM jsonb_array_elements(b.planned) c) reserve_owners,
          CASE WHEN p.direction='IN' THEN p.amount ELSE -p.amount END signed
        FROM cash p JOIN public.economic_plan source ON source.organization_id=org AND source.id=p.plan_id
        CROSS JOIN LATERAL (SELECT public.reserve_plan_cells(source.snapshot_payload,source.reserve_contribution_total,source.period_start) planned,
          source.snapshot_payload->'result'->'owners' owners) b
      ) SELECT
        COALESCE((SELECT sum(s.amount) FROM movements p CROSS JOIN LATERAL
          public.reserve_unit_cash(p.amount,p.units,p.planned,p.owners) s
          WHERE p.direction='IN' AND p.payment_type='HAUSGELD_VORSCHUSS' AND s.unit_id=unit::text
            AND p.reverses_id IS NULL AND p.reversed_by_id IS NULL),0)
        -COALESCE((SELECT sum(s.amount) FROM movements p CROSS JOIN LATERAL public.reserve_unit_cash(
          CASE WHEN p.payment_type='RUECKLAGENANTEIL' THEN p.signed ELSE COALESCE(p.assigned,p.signed) END,
          p.units,p.planned,CASE WHEN p.assigned IS NOT NULL OR p.payment_type='RUECKLAGENANTEIL'
            THEN p.reserve_owners ELSE p.owners END) s WHERE s.unit_id=unit::text),0)
        -COALESCE((SELECT sum(e.amount) FROM public.reserve_ledger_entry e JOIN public.settlement_run r
          ON r.organization_id=e.organization_id AND r.id=e.settlement_run_id CROSS JOIN parent p
          WHERE e.organization_id=org AND e.property_id=p.property_id AND e.unit_id=unit AND e.component='CONTRIBUTION'
            AND r.period_start=p.period_start AND r.period_end=p.period_end),0)
    $$;
    REVOKE ALL ON FUNCTION reserve_plan_cells(jsonb,numeric,date),reserve_hausgeld_share(numeric,text[],jsonb,jsonb),
      reserve_unit_cash(numeric,text[],jsonb,jsonb),reserve_contribution_headroom(uuid,uuid,uuid),
      reserve_cash_plan(uuid,uuid,date,date,uuid,date) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION reserve_plan_cells(jsonb,numeric,date),reserve_hausgeld_share(numeric,text[],jsonb,jsonb),
      reserve_unit_cash(numeric,text[],jsonb,jsonb),reserve_contribution_headroom(uuid,uuid,uuid),
      reserve_cash_plan(uuid,uuid,date,date,uuid,date) TO app_rw;
    -- Shared by service queries and both opening-period guards. Invoker rights
    -- preserve RLS on service reads; trigger callers already run as definer.
    CREATE FUNCTION reserve_ledger_scope_matches(org uuid,prior_scope uuid,next_scope uuid,prior_mandate uuid,next_mandate uuid)
    RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $$
      SELECT prior_scope IS NOT DISTINCT FROM next_scope OR EXISTS(
        SELECT 1 FROM public.accounting_scope p, public.accounting_scope n
        WHERE p.organization_id=org AND n.organization_id=org AND p.id=prior_scope AND n.id=next_scope
          AND p.whole_property AND n.whole_property AND p.scope_type='WEG' AND n.scope_type='WEG'
          AND prior_mandate IS NOT DISTINCT FROM next_mandate)
    $$;
    REVOKE ALL ON FUNCTION reserve_ledger_scope_matches(uuid,uuid,uuid,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION reserve_ledger_scope_matches(uuid,uuid,uuid,uuid,uuid) TO app_rw;
    CREATE FUNCTION guard_reserve_opening_period() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF NEW.run_type NOT IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT') THEN RETURN NEW; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
      IF EXISTS(SELECT 1 FROM public.settlement_run r JOIN public.reserve_ledger_entry e
        ON e.organization_id=r.organization_id AND e.settlement_run_id=r.id
        WHERE r.organization_id=NEW.organization_id AND r.property_id=NEW.property_id
          AND (r.period_start>NEW.period_start OR (
            r.period_start<=NEW.period_end AND r.period_end>=NEW.period_start
            AND (r.period_start<>NEW.period_start OR r.period_end<>NEW.period_end)))
          AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
          AND e.component='OPENING' AND public.reserve_ledger_scope_matches(NEW.organization_id,
            public.financial_scope_id(r.organization_id,'settlement_run',r.id),
            public.financial_scope_id(NEW.organization_id,'settlement_run',NEW.id),
            r.management_mandate_id,NEW.management_mandate_id)) THEN
        RAISE EXCEPTION 'Cannot create an earlier or overlapping period around an imported reserve opening'
          USING ERRCODE='23514',CONSTRAINT='reserve_opening_import_exists';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_reserve_opening_period() FROM PUBLIC;
    -- bind_new_financial_resource runs first, so the new run's scope is known.
    CREATE TRIGGER guard_reserve_opening_period BEFORE INSERT ON settlement_run
      FOR EACH ROW EXECUTE FUNCTION guard_reserve_opening_period();
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON reserve_ledger_entry
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();
    CREATE TRIGGER a_lock_reserve_sources BEFORE INSERT OR UPDATE OR DELETE ON reserve_ledger_entry
      FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();
    CREATE TRIGGER a_reserve_financial_lock BEFORE INSERT OR UPDATE OR DELETE ON reserve_ledger_entry
      FOR EACH ROW EXECUTE FUNCTION lock_financial_input_sources();
    CREATE FUNCTION guard_reserve_ledger() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE parent public.settlement_run; sphere uuid; cash public.payment; cost public.cost_entry;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'GoBD: reserve ledger is append-only'; END IF;
      SELECT * INTO parent FROM public.settlement_run WHERE organization_id=NEW.organization_id AND id=NEW.settlement_run_id;
      sphere:=public.financial_scope_id(NEW.organization_id,'settlement_run',parent.id);
      IF parent.property_id IS DISTINCT FROM NEW.property_id OR parent.status<>'draft'
        OR parent.run_type NOT IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
        OR NEW.entry_date NOT BETWEEN parent.period_start AND parent.period_end
        OR NEW.created_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
        OR public.property_source_security_mode(NEW.organization_id)='PAUSED' THEN
        RAISE EXCEPTION 'Reserve ledger requires current writable period' USING ERRCODE='23514'; END IF;
      IF NEW.component='OPENING' AND EXISTS(SELECT 1 FROM public.settlement_run r
        WHERE r.organization_id=NEW.organization_id AND r.property_id=parent.property_id
          AND r.status='finalized' AND r.period_start=parent.period_start AND r.period_end=parent.period_end
          AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
          AND public.reserve_ledger_scope_matches(NEW.organization_id,
            public.financial_scope_id(r.organization_id,'settlement_run',r.id),sphere,
            r.management_mandate_id,parent.management_mandate_id)) THEN
        RAISE EXCEPTION 'GoBD: reserve opening period was finalized'
          USING ERRCODE='23514',CONSTRAINT='reserve_period_finalized'; END IF;
      IF EXISTS(SELECT 1 FROM public.settlement_run r WHERE r.organization_id=NEW.organization_id
        AND r.property_id=parent.property_id AND r.status='finalized' AND r.superseded_by_id IS NULL
        AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
        AND r.period_start<=NEW.entry_date AND r.period_end>=NEW.entry_date
        AND public.financial_scope_id(r.organization_id,'settlement_run',r.id) IS NOT DISTINCT FROM sphere) THEN
        RAISE EXCEPTION 'GoBD: reserve period is finalized' USING ERRCODE='23514'; END IF;
      IF NEW.component='OPENING' AND EXISTS(SELECT 1 FROM public.settlement_run r
        WHERE r.organization_id=NEW.organization_id AND r.property_id=NEW.property_id
          AND (r.period_start<parent.period_start OR (
            r.period_start<=parent.period_end AND r.period_end>=parent.period_start
            AND (r.period_start<>parent.period_start OR r.period_end<>parent.period_end)))
          AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
          AND r.superseded_by_id IS NULL
          AND public.reserve_ledger_scope_matches(NEW.organization_id,
            public.financial_scope_id(r.organization_id,'settlement_run',r.id),sphere,
            r.management_mandate_id,parent.management_mandate_id)) THEN
        RAISE EXCEPTION 'Reserve opening import is limited to first ledger year' USING ERRCODE='23514',CONSTRAINT='reserve_opening_import_only'; END IF;
      IF NEW.component='OPENING' AND NEW.amount=0 THEN
        RAISE EXCEPTION 'Reserve opening has no change' USING ERRCODE='23514',CONSTRAINT='reserve_amount_invalid'; END IF;
      IF NEW.component='CONTRIBUTION' AND NEW.payment_id IS NULL THEN
        IF NEW.unit_id IS NULL OR NEW.cost_entry_id IS NOT NULL OR NEW.register_entry_id IS NOT NULL
          OR NOT EXISTS(SELECT 1 FROM public.unit u WHERE u.organization_id=NEW.organization_id
            AND u.id=NEW.unit_id AND u.property_id=parent.property_id) THEN
          RAISE EXCEPTION 'Reserve contribution correction requires a unit of this property' USING ERRCODE='23514'; END IF;
        IF NEW.amount<=0 THEN
          RAISE EXCEPTION 'Reserve contribution correction must be positive'
            USING ERRCODE='23514',CONSTRAINT='reserve_amount_invalid'; END IF;
        IF NEW.amount>public.reserve_contribution_headroom(NEW.organization_id,parent.id,NEW.unit_id) THEN
          RAISE EXCEPTION 'Reserve contribution exceeds booked unit Hausgeld share'
            USING ERRCODE='23514',CONSTRAINT='reserve_contribution_exceeds_booked'; END IF;
      END IF;
      IF NEW.cost_entry_id IS NOT NULL OR NEW.component IN ('WITHDRAWAL','ADJUSTMENT') THEN
        SELECT * INTO cost FROM public.cost_entry WHERE organization_id=NEW.organization_id AND id=NEW.cost_entry_id FOR UPDATE;
        IF cost.id IS NULL OR cost.property_id IS DISTINCT FROM parent.property_id OR cost.status<>'confirmed' OR cost.superseded_by_id IS NOT NULL
          OR NOT public.financial_scope_access(NEW.organization_id,'cost_entry',cost.id,'read')
          OR NOT EXISTS(SELECT 1 FROM public.resolution_register_entry e JOIN public.resolution r
            ON r.organization_id=e.organization_id AND r.id=e.resolution_id WHERE e.organization_id=NEW.organization_id
            AND e.id=NEW.register_entry_id AND e.property_id=parent.property_id AND r.result='ANGENOMMEN') THEN
          RAISE EXCEPTION 'Reserve correction requires confirmed cost AND accepted registered resolution'
            USING ERRCODE='23514',CONSTRAINT='reserve_payout_evidence_required'; END IF;
        IF NEW.payment_id IS NULL THEN
          IF NEW.component='WITHDRAWAL' AND NEW.amount<=0 THEN
            RAISE EXCEPTION 'Reserve cash correction must be positive'
              USING ERRCODE='23514',CONSTRAINT='reserve_amount_invalid'; END IF;
          IF NEW.component IN ('WITHDRAWAL','ADJUSTMENT')
            AND abs(NEW.amount)+public.reserve_funded_amount(NEW.organization_id,cost.id,NULL)>cost.gross_amount THEN
            RAISE EXCEPTION 'Active reserve withdrawals exceed confirmed cost amount'
              USING ERRCODE='23514',CONSTRAINT='reserve_payout_evidence_required'; END IF;
        END IF;
      END IF;
      IF NEW.component='FUNDING' THEN
        SELECT * INTO cash FROM public.payment WHERE organization_id=NEW.organization_id AND id=NEW.payment_id;
        SELECT * INTO cost FROM public.cost_entry WHERE organization_id=NEW.organization_id AND id=NEW.cost_entry_id FOR UPDATE;
        IF cash.property_id IS DISTINCT FROM parent.property_id OR cost.property_id IS DISTINCT FROM parent.property_id
          OR cash.status<>'booked' OR cash.direction<>'OUT' OR cash.payment_type<>'RUECKLAGENENTNAHME'
          OR cash.reverses_id IS NOT NULL
          OR (cash.reserve_cost_entry_id IS NOT NULL AND (cash.reserve_cost_entry_id IS DISTINCT FROM NEW.cost_entry_id
              OR cash.reserve_register_entry_id IS DISTINCT FROM NEW.register_entry_id))
          OR NOT (cash.value_date BETWEEN parent.period_start AND parent.period_end OR EXISTS(
            SELECT 1 FROM public.settlement_run r WHERE r.organization_id=parent.organization_id
              AND r.property_id=parent.property_id AND r.status='finalized' AND r.superseded_by_id IS NULL
              AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
              AND cash.value_date BETWEEN r.period_start AND r.period_end
              AND r.period_end<parent.period_start AND cash.booked_at>r.finalized_at))
          OR cost.status<>'confirmed' OR cost.superseded_by_id IS NOT NULL
          OR NOT public.financial_scope_access(NEW.organization_id,'payment',cash.id,'read')
          OR NOT public.financial_scope_access(NEW.organization_id,'cost_entry',cost.id,'read')
          OR cash.amount+public.reserve_funded_amount(NEW.organization_id,cost.id,cash.id)>cost.gross_amount
          OR NOT EXISTS(SELECT 1 FROM public.resolution_register_entry e JOIN public.resolution r
            ON r.organization_id=e.organization_id AND r.id=e.resolution_id WHERE e.organization_id=NEW.organization_id
            AND e.id=NEW.register_entry_id AND e.property_id=parent.property_id AND r.result='ANGENOMMEN') THEN
          RAISE EXCEPTION 'Reserve funding requires coherent booked cost and registered resolution' USING ERRCODE='23514'; END IF;
      ELSIF NEW.payment_id IS NOT NULL THEN
        SELECT * INTO cash FROM public.payment WHERE organization_id=NEW.organization_id AND id=NEW.payment_id;
        IF (cash.direction='IN' AND (NEW.amount<0 OR NEW.amount>cash.amount))
          OR (cash.direction='OUT' AND (NEW.amount>0 OR NEW.amount< -cash.amount)) THEN
          RAISE EXCEPTION 'Reserve assignment exceeds signed payment'
            USING ERRCODE='23514',CONSTRAINT='reserve_assignment_amount'; END IF;
        IF cash.property_id IS DISTINCT FROM parent.property_id OR cash.status<>'booked'
          OR cash.payment_type<>'HAUSGELD_VORSCHUSS'
          OR NOT public.financial_scope_access(NEW.organization_id,'payment',cash.id,'read')
          OR NOT (cash.value_date BETWEEN parent.period_start AND parent.period_end OR EXISTS(
            SELECT 1 FROM public.settlement_run r WHERE r.organization_id=parent.organization_id
              AND r.property_id=parent.property_id AND r.status='finalized' AND r.superseded_by_id IS NULL
              AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
              AND cash.value_date BETWEEN r.period_start AND r.period_end
              AND r.period_end<parent.period_start AND cash.booked_at>r.finalized_at)) THEN
          RAISE EXCEPTION 'Reserve assignment requires a readable booked Hausgeld payment in this cash period' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_reserve_ledger BEFORE INSERT OR UPDATE OR DELETE ON reserve_ledger_entry
      FOR EACH ROW EXECUTE FUNCTION guard_reserve_ledger();
    CREATE FUNCTION guard_reserve_payment_evidence() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE cost public.cost_entry;
    BEGIN
      IF TG_OP='DELETE' AND current_user='app_migrator' AND session_user='app_migrator'
        AND current_setting('app.organization_erasure',true)='on' THEN RETURN OLD; END IF;
      IF TG_OP IN ('DELETE','UPDATE') AND OLD.bank_transaction_id IS NOT NULL THEN
        IF TG_OP='DELETE' OR NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at THEN
          IF OLD.payment_type<>'RUECKLAGENENTNAHME' OR OLD.status<>'draft' OR OLD.cancelled_at IS NOT NULL
            OR OLD.booked_at IS NOT NULL OR OLD.booked_by IS NOT NULL
            OR (OLD.reserve_cost_entry_id IS NOT NULL AND OLD.reserve_register_entry_id IS NOT NULL)
            OR EXISTS(SELECT 1 FROM public.reserve_ledger_entry e WHERE e.organization_id=OLD.organization_id AND e.payment_id=OLD.id)
            OR EXISTS(SELECT 1 FROM public.payment p WHERE p.organization_id=OLD.organization_id
              AND p.bank_transaction_id=OLD.bank_transaction_id AND p.id<>OLD.id AND p.cancelled_at IS NULL)
            OR NOT EXISTS(SELECT 1 FROM public.bank_transaction b WHERE b.organization_id=OLD.organization_id
              AND b.id=OLD.bank_transaction_id AND b.status='NEU') THEN
            RAISE EXCEPTION 'Retain bound imported payment' USING ERRCODE='23514',CONSTRAINT='reserve_imported_draft_bound'; END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          IF NEW.cancelled_at IS NULL OR (to_jsonb(NEW)-'cancelled_at'-'updated_at') IS DISTINCT FROM
              (to_jsonb(OLD)-'cancelled_at'-'updated_at') THEN
            RAISE EXCEPTION 'Release only cancels the unchanged draft' USING ERRCODE='23514',CONSTRAINT='reserve_imported_draft_bound'; END IF;
          RETURN NEW;
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      IF TG_OP='UPDATE' AND OLD.bank_transaction_id IS NOT NULL AND OLD.status='draft'
        AND (NEW.amount IS DISTINCT FROM OLD.amount OR NEW.direction IS DISTINCT FROM OLD.direction
          OR NEW.value_date IS DISTINCT FROM OLD.value_date OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id
          OR NEW.bank_transaction_id IS DISTINCT FROM OLD.bank_transaction_id OR NEW.cancelled_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Imported payment draft follows retained bank movement'
          USING ERRCODE='23514',CONSTRAINT='reserve_imported_draft_bound'; END IF;
      IF TG_OP='UPDATE' AND OLD.status='booked' THEN
        IF NEW.reserve_cost_entry_id IS DISTINCT FROM OLD.reserve_cost_entry_id
          OR NEW.reserve_register_entry_id IS DISTINCT FROM OLD.reserve_register_entry_id THEN
          RAISE EXCEPTION 'GoBD: booked reserve evidence is immutable'; END IF;
        RETURN NEW;
      END IF;
      IF NEW.payment_type='RUECKLAGENENTNAHME' AND NEW.status='booked' AND NEW.reverses_id IS NULL THEN
        SELECT * INTO cost FROM public.cost_entry WHERE organization_id=NEW.organization_id AND id=NEW.reserve_cost_entry_id FOR UPDATE;
        IF cost.id IS NULL OR cost.property_id IS DISTINCT FROM NEW.property_id OR cost.status<>'confirmed' OR cost.superseded_by_id IS NOT NULL
          OR NOT public.financial_scope_access(NEW.organization_id,'cost_entry',cost.id,'read')
          OR NOT EXISTS(SELECT 1 FROM public.resolution_register_entry e JOIN public.resolution r
            ON r.organization_id=e.organization_id AND r.id=e.resolution_id WHERE e.organization_id=NEW.organization_id
              AND e.id=NEW.reserve_register_entry_id AND e.property_id=NEW.property_id AND r.result='ANGENOMMEN')
          OR NEW.amount+public.reserve_funded_amount(NEW.organization_id,cost.id,NEW.id)>cost.gross_amount THEN
          RAISE EXCEPTION 'Reserve booking requires confirmed cost AND accepted registered resolution within cost amount'
            USING ERRCODE='23514',CONSTRAINT='reserve_payout_evidence_required'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_reserve_payment_evidence BEFORE INSERT OR UPDATE OR DELETE ON payment
      FOR EACH ROW EXECUTE FUNCTION guard_reserve_payment_evidence();
    CREATE FUNCTION reserve_history_access(org uuid,property uuid,period_end date) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR public.scope_actor_role(org) IS NULL
        OR NOT EXISTS(SELECT 1 FROM public.property p WHERE p.organization_id=org AND p.id=property)
        OR NOT public.property_financial_disclosure_access(org,property,'read')
        OR EXISTS(SELECT 1 FROM public.settlement_run r
        WHERE r.organization_id=org AND r.property_id=property AND r.status='finalized'
          AND r.superseded_by_id IS NULL AND r.period_end<=reserve_history_access.period_end
          AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
          AND NOT public.financial_scope_access(org,'settlement_run',r.id,'read')) THEN
        RAISE EXCEPTION 'Complete reserve history not accessible' USING ERRCODE='42501'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION reserve_history_access(uuid,uuid,date) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION reserve_history_access(uuid,uuid,date) TO app_rw;
    CREATE CONSTRAINT TRIGGER recheck_computation AFTER INSERT OR UPDATE OR DELETE ON reserve_ledger_entry
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION recheck_reserve_computations();
    ''')
    retention_catalog()
    patch_source_digest()
    patch_booking_scope()


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM reserve_ledger_entry) THEN
      RAISE EXCEPTION 'Retain reserve ledger evidence; populated downgrade refused'; END IF;
      IF EXISTS(SELECT 1 FROM payment WHERE payment_type='RUECKLAGENENTNAHME') THEN
      RAISE EXCEPTION 'Retain reserve payout bookings; populated downgrade refused'; END IF;
      IF EXISTS(SELECT 1 FROM settlement_line WHERE line_type IN ('RESERVE_LATE_CASH','RESERVE_ADJUSTMENT')) THEN
      RAISE EXCEPTION 'Retain reserve statement evidence; populated downgrade refused'; END IF; END $$;""")
    retention_catalog(restore=True)
    patch_source_digest(reverse=True)
    patch_booking_scope(reverse=True)
    op.execute("""DROP TRIGGER guard_reserve_opening_period ON settlement_run;
      DROP FUNCTION guard_reserve_opening_period();
      DROP TABLE reserve_ledger_entry;
      DROP FUNCTION IF EXISTS recheck_reserve_computations();
      DROP FUNCTION guard_reserve_ledger();
      DROP FUNCTION reserve_ledger_scope_matches(uuid,uuid,uuid,uuid,uuid);
      DROP FUNCTION reserve_history_access(uuid,uuid,date);
      DROP TRIGGER guard_reserve_payment_evidence ON payment;
      DROP FUNCTION guard_reserve_payment_evidence();
      DROP FUNCTION reserve_funded_amount(uuid,uuid,uuid);
      DROP FUNCTION IF EXISTS reserve_contribution_headroom(uuid,uuid,uuid);
      DROP FUNCTION IF EXISTS reserve_cash_plan(uuid,uuid,date,date,uuid,date);
      DROP FUNCTION IF EXISTS reserve_unit_cash(numeric,text[],jsonb,jsonb);
      DROP FUNCTION IF EXISTS reserve_hausgeld_share(numeric,text[],jsonb,jsonb);
      DROP FUNCTION IF EXISTS reserve_plan_cells(jsonb,numeric,date);
      ALTER TABLE payment DROP CONSTRAINT ck_reserve_payment_evidence;
      ALTER TABLE payment DROP COLUMN reserve_cost_entry_id;
      ALTER TABLE payment DROP COLUMN reserve_register_entry_id;
      ALTER TABLE resolution_register_entry DROP CONSTRAINT uq_reserve_register_org;""")
    op.execute('ALTER TABLE payment DROP CONSTRAINT uq_reserve_payment_org')
    op.execute(f"""ALTER TABLE settlement_line DROP CONSTRAINT ck_settlement_line_ck_settlement_line_line_type;
      ALTER TABLE settlement_line ADD CONSTRAINT ck_settlement_line_ck_settlement_line_line_type CHECK(line_type IN ({LINE_TYPES}));
      ALTER TABLE payment DROP CONSTRAINT ck_reserve_payout;
      ALTER TABLE payment DROP CONSTRAINT ck_payment_ck_payment_type;
      ALTER TABLE payment ADD CONSTRAINT ck_payment_ck_payment_type CHECK(payment_type IN ({PAYMENT_TYPES}));""")
