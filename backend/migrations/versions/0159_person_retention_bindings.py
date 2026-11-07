"""Share person-retention categories and retain structured booked recipients."""
from alembic import op
from sqlalchemy import text

revision = '0159'
down_revision = '0155'
branch_labels = None
depends_on = None

PERSON_SEEDS = """tab.relname IN ('cost_entry','payment','settlement_run','settlement_line',
            'economic_plan','economic_plan_line','dunning_notice','invoice_document','letter_document')"""
CATALOG_SEEDS = "EXISTS(SELECT 1 FROM public.person_retention_categories() c WHERE c.entity_type=tab.relname)"


def _copy_function(signature, source, target):
    definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),
                                     {'signature': signature})
    header = f'FUNCTION public.{source}('
    assert definition.count(header) == 1
    op.execute(definition.replace(header, f'FUNCTION public.{target}(', 1))


def upgrade():
    # Keep the prior definitions privately for an exact, guarded downgrade.
    for name, arguments in (('privacy_retention_facts', 'uuid,uuid'),
                            ('privacy_financial_context_complete', 'uuid,uuid,text')):
        _copy_function(f'{name}({arguments})', name, name + '_before0159')
        op.execute(f'REVOKE ALL ON FUNCTION {name}_before0159({arguments}) FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute(r'''
    -- Trusted SQL fragments, never caller input. NULL kinds classify additional
    -- organization roots whose generic FK dependencies stay operator-private.
    CREATE FUNCTION public.person_retention_categories() RETURNS TABLE(
      entity_type text,kind text,retain_when text,recipient_join text,until_sql text,coherent_sql text,access_sql text)
      LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $catalog$
      VALUES
      ('cost_entry','COST_ENTRY_VENDOR',$q$t.status='confirmed'$q$,
        $q$JOIN public.vendor p ON p.id=t.vendor_id AND p.person_id=$2$q$,
        $q$COALESCE((SELECT max(d.retention_until) FROM public.invoice_document d
          WHERE d.cost_entry_id=t.id OR EXISTS(SELECT 1 FROM public.document_cost_link l
            WHERE l.organization_id=d.organization_id AND l.source_document_id=d.id AND l.cost_entry_id=t.id)),
          make_date(extract(year FROM t.service_period_to)::int+8,12,31))$q$,
        $q$t.organization_id=$1 AND p.organization_id=$1 AND NOT EXISTS(SELECT 1 FROM public.invoice_document d
          WHERE (d.cost_entry_id=t.id OR EXISTS(SELECT 1 FROM public.document_cost_link l
            WHERE l.organization_id=d.organization_id AND l.source_document_id=d.id AND l.cost_entry_id=t.id))
          AND d.organization_id<>$1)$q$,'true'),
      ('settlement_run','SETTLEMENT_ADDRESSEE',$q$t.status='finalized' AND t.run_type='BK_MIETER'$q$,
        $q$JOIN public.tenant_profile p ON p.id=t.tenant_profile_id AND p.person_id=$2$q$,
        $q$make_date(extract(year FROM t.period_end)::int+8,12,31)$q$,
        't.organization_id=$1 AND p.organization_id=$1','true'),
      ('settlement_run','FINALIZED_OWNERSHIP',
        $q$t.status='finalized' AND t.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')$q$,
        $q$JOIN public.settlement_line ln ON ln.settlement_run_id=t.id
          JOIN public.owner_profile p ON p.id=ln.addressee_owner_profile_id AND p.person_id=$2$q$,
        $q$make_date(extract(year FROM t.period_end)::int+8,12,31)$q$,
        't.organization_id=$1 AND ln.organization_id=$1 AND p.organization_id=$1','true'),
      ('economic_plan','FINALIZED_OWNERSHIP',$q$t.status='finalized'$q$,
        $q$JOIN public.economic_plan_line ln ON ln.economic_plan_id=t.id
          JOIN public.owner_profile p ON p.id=ln.owner_profile_id AND p.person_id=$2$q$,
        $q$make_date(extract(year FROM t.period_end)::int+8,12,31)$q$,
        't.organization_id=$1 AND ln.organization_id=$1 AND p.organization_id=$1','true'),
      ('dunning_notice','DUNNING',$q$t.status='issued' AND (p.person_id=$2 OR o.person_id=$2)$q$,
        $q$LEFT JOIN public.tenant_profile p ON p.id=t.tenant_profile_id
          LEFT JOIN public.owner_profile o ON o.id=t.owner_profile_id$q$,
        $q$make_date(extract(year FROM t.issue_date)::int+8,12,31)$q$,
        't.organization_id=$1 AND COALESCE(p.organization_id,o.organization_id)=$1','true'),
      ('payment','BOOKED_PAYMENT',$q$t.status='booked' AND (p.person_id=$2 OR o.person_id=$2)$q$,
        $q$LEFT JOIN public.tenant_profile p ON p.id=t.tenant_profile_id
          LEFT JOIN public.owner_profile o ON o.id=t.owner_profile_id$q$,
        $q$COALESCE(t.retention_until,make_date(extract(year FROM t.value_date)::int+8,12,31))$q$,
        $q$t.organization_id=$1 AND (p.id IS NULL OR p.organization_id=$1)
          AND (o.id IS NULL OR o.organization_id=$1)$q$,
        $q$public.financial_scope_access($1,'payment',t.id,$3)$q$),
      ('settlement_line',NULL,NULL,NULL,NULL,NULL,NULL),
      ('economic_plan_line',NULL,NULL,NULL,NULL,NULL,NULL),
      ('invoice_document',NULL,NULL,NULL,NULL,NULL,NULL),
      ('letter_document',NULL,NULL,NULL,NULL,NULL,NULL);
    $catalog$;
    REVOKE ALL ON FUNCTION public.person_retention_categories() FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION public.person_retention_bindings(org uuid,person uuid,capability text)
      RETURNS TABLE(kind text,entity_type text,entity uuid,until_date date,coherent boolean,accessible boolean)
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE category record;
    BEGIN
      FOR category IN SELECT c.* FROM public.person_retention_categories() c WHERE c.kind IS NOT NULL LOOP
        RETURN QUERY EXECUTE format('SELECT DISTINCT %L::text,%L::text,t.id,%s,%s,%s
          FROM public.%I t %s WHERE (%s)',category.kind,category.entity_type,
          category.until_sql,category.coherent_sql,category.access_sql,
          category.entity_type,category.recipient_join,category.retain_when)
          USING org,person,capability;
      END LOOP;
    END $$;
    REVOKE ALL ON FUNCTION public.person_retention_bindings(uuid,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE OR REPLACE FUNCTION public.privacy_retention_facts(org uuid,person uuid)
      RETURNS TABLE(kind text,entity uuid,until_date date,coherent boolean)
      LANGUAGE sql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT DISTINCT f.entity_type,f.entity,f.until_date,f.coherent
        FROM public.person_retention_bindings(org,person,'read') f;
    $$;

    CREATE OR REPLACE FUNCTION public.privacy_financial_context_complete(org uuid,person uuid,capability text)
      RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF NOT COALESCE(public.privacy_financial_context_complete_before0159(org,person,capability),false)
        THEN RETURN false; END IF;
      RETURN NOT EXISTS(SELECT 1 FROM public.person_retention_bindings(org,person,capability) f
        WHERE NOT COALESCE(f.coherent AND f.accessible,false));
    END $$;

    -- Only this checked adapter is callable by runtime. It exposes exactly the
    -- person's direct bindings after the existing complete-context admission.
    CREATE FUNCTION public.person_retention_view(org uuid,person uuid,capability text)
      RETURNS TABLE(kind text,entity_type text,entity uuid,until_date date)
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','export')
        OR public.scope_actor_role(org) IS NULL
        OR NOT COALESCE(public.person_source_access(org,person,capability),false)
        OR NOT public.privacy_financial_context_complete(org,person,capability) THEN
        RAISE EXCEPTION 'Complete person and financial access required' USING ERRCODE='42501'; END IF;
      RETURN QUERY SELECT DISTINCT f.kind,f.entity_type,f.entity,f.until_date
        FROM public.person_retention_bindings(org,person,capability) f;
      IF NOT public.person_source_access(org,person,capability)
        OR NOT public.privacy_financial_context_complete(org,person,capability) THEN
        RAISE EXCEPTION 'Complete person and financial access required' USING ERRCODE='42501'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION public.person_retention_view(uuid,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.person_retention_view(uuid,uuid,text) TO app_rw;
    ''')
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('organization_retention_plan(uuid)'::regprocedure)"))
    assert definition.count(PERSON_SEEDS) == 1
    op.execute(definition.replace(PERSON_SEEDS, CATALOG_SEEDS))


def downgrade():
    op.execute('''LOCK TABLE public.person,public.payment,public.organization_deletion IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.organization_deletion)
        OR EXISTS(SELECT 1 FROM public.person p JOIN public.payment t ON t.organization_id=p.organization_id
          LEFT JOIN public.tenant_profile tp ON tp.id=t.tenant_profile_id
          LEFT JOIN public.owner_profile op ON op.id=t.owner_profile_id
          WHERE p.processing_restricted AND t.status='booked' AND (tp.person_id=p.id OR op.person_id=p.id)) THEN
        RAISE EXCEPTION 'Retain reconciled person erasure decisions; repair forward'; END IF; END $$;''')
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('organization_retention_plan(uuid)'::regprocedure)"))
    assert definition.count(CATALOG_SEEDS) == 1
    op.execute(definition.replace(CATALOG_SEEDS, PERSON_SEEDS))
    op.execute('DROP FUNCTION public.person_retention_view(uuid,uuid,text)')
    for name, arguments in (('privacy_financial_context_complete', 'uuid,uuid,text'),
                            ('privacy_retention_facts', 'uuid,uuid')):
        _copy_function(f'{name}_before0159({arguments})', name + '_before0159', name)
        op.execute(f'DROP FUNCTION {name}_before0159({arguments})')
    op.execute('DROP FUNCTION public.person_retention_bindings(uuid,uuid,text),public.person_retention_categories()')
