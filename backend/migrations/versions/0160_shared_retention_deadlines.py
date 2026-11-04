"""Use the existing evidence dates in every person-retention consumer."""
from importlib import import_module

from alembic import op
from sqlalchemy import text

revision = '0160'
down_revision = '0159'
branch_labels = None
depends_on = None

PREVIOUS_DEADLINE = """COALESCE(NULLIF(to_jsonb(t)->>'retention_until','')::date,
          make_date(extract(year FROM COALESCE(NULLIF(to_jsonb(t)->>'period_end','')::date,
            GREATEST(NULLIF(to_jsonb(t)->>'invoice_date','')::date,NULLIF(to_jsonb(t)->>'service_period_to','')::date),NULLIF(to_jsonb(t)->>'issue_date','')::date,
            NULLIF(to_jsonb(t)->>'value_date','')::date,NULLIF(to_jsonb(t)->>'created_at','')::timestamptz::date,
            NULLIF(to_jsonb(t)->>'registered_at','')::timestamptz::date,CURRENT_DATE))::integer+8,12,31))"""
FUNCTIONS = (('person_retention_categories', ''),
             ('person_retention_bindings', 'uuid,uuid,text'),
             ('organization_retention_plan', 'uuid'))


def upgrade():
    previous = import_module('migrations.versions.0159_person_retention_bindings')
    for name, arguments in FUNCTIONS:
        previous._copy_function(f'{name}({arguments})', name, name + '_before0160')
        op.execute(f'REVOKE ALL ON FUNCTION {name}_before0160({arguments}) FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute(r'''
    -- Exact extraction of the organization's existing date precedence. The
    -- statutory/provisional duration is unchanged; no financial row is edited.
    CREATE FUNCTION public.retention_base_until(item jsonb) RETURNS date
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT COALESCE(NULLIF(item->>'retention_until','')::date,
        make_date(extract(year FROM COALESCE(NULLIF(item->>'period_end','')::date,
          GREATEST(NULLIF(item->>'invoice_date','')::date,NULLIF(item->>'service_period_to','')::date),
          NULLIF(item->>'issue_date','')::date,NULLIF(item->>'value_date','')::date,
          NULLIF(item->>'created_at','')::timestamptz::date,
          NULLIF(item->>'registered_at','')::timestamptz::date,CURRENT_DATE))::integer+8,12,31));
    $$;
    REVOKE ALL ON FUNCTION public.retention_base_until(jsonb) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION public.retention_source_until(entity_type text,item jsonb) RETURNS date
      LANGUAGE plpgsql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE deadline date:=public.retention_base_until(item);
      org uuid:=(item->>'organization_id')::uuid; source_id uuid:=(item->>'id')::uuid;
    BEGIN
      IF entity_type='cost_entry' THEN
        deadline:=GREATEST(deadline,(SELECT max(public.retention_base_until(to_jsonb(d)))
          FROM public.invoice_document d WHERE d.organization_id=org
          AND (d.cost_entry_id=source_id OR EXISTS(SELECT 1 FROM public.document_cost_link l
            WHERE l.organization_id=org AND l.source_document_id=d.id AND l.cost_entry_id=source_id))));
      ELSIF entity_type='settlement_run' AND item->>'run_type'='BK_MIETER' THEN
        -- The run's tenant is an ancestor of all retained lines in this run.
        deadline:=GREATEST(deadline,(SELECT max(public.retention_base_until(to_jsonb(l)))
          FROM public.settlement_line l WHERE l.organization_id=org AND l.settlement_run_id=source_id));
      ELSIF entity_type='settlement_line' THEN
        -- A line carries its own addressee. Never copy another owner's line
        -- deadline back from the structural parent onto this recipient.
        deadline:=GREATEST(deadline,(SELECT public.retention_base_until(to_jsonb(r))
          FROM public.settlement_run r WHERE r.organization_id=org AND r.id=(item->>'settlement_run_id')::uuid));
      ELSIF entity_type='economic_plan_line' THEN
        deadline:=GREATEST(deadline,(SELECT public.retention_base_until(to_jsonb(p))
          FROM public.economic_plan p WHERE p.organization_id=org AND p.id=(item->>'economic_plan_id')::uuid));
      END IF;
      RETURN deadline;
    END $$;
    REVOKE ALL ON FUNCTION public.retention_source_until(text,jsonb) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE OR REPLACE FUNCTION public.person_retention_categories() RETURNS TABLE(
      entity_type text,kind text,retain_when text,recipient_join text,until_sql text,coherent_sql text,access_sql text)
      LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT c.entity_type,c.kind,c.retain_when,c.recipient_join,
        CASE WHEN c.kind IS NULL THEN NULL
          WHEN c.entity_type='settlement_run' AND c.kind='FINALIZED_OWNERSHIP' THEN
            $q$GREATEST(public.retention_source_until('settlement_run',to_jsonb(t)),
              public.retention_source_until('settlement_line',to_jsonb(ln)))$q$
          WHEN c.entity_type='economic_plan' THEN
            $q$GREATEST(public.retention_source_until('economic_plan',to_jsonb(t)),
              public.retention_source_until('economic_plan_line',to_jsonb(ln)))$q$
          ELSE format('public.retention_source_until(%L,to_jsonb(t))',c.entity_type) END,
        c.coherent_sql,c.access_sql FROM public.person_retention_categories_before0160() c;
    $$;
    ''')
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('person_retention_bindings(uuid,uuid,text)'::regprocedure)"))
    changes = [
        ('SELECT DISTINCT %L::text,%L::text,t.id,%s,%s,%s',
         'SELECT %L::text,%L::text,t.id,max(%s),bool_and(%s),bool_and(%s)'),
        ("FROM public.%I t %s WHERE (%s)'", "FROM public.%I t %s WHERE (%s) GROUP BY t.id'"),
    ]
    for before, after in changes:
        assert definition.count(before) == 1
        definition = definition.replace(before, after)
    op.execute(definition)
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('organization_retention_plan(uuid)'::regprocedure)"))
    before = 'deadline:=$sql$' + PREVIOUS_DEADLINE + '$sql$;'
    assert definition.count(before) == 1
    op.execute(definition.replace(before,
        "deadline:=format('public.retention_source_until(%L,to_jsonb(t))',tab.relname);"))


def downgrade():
    # This revision changes functions only. Restore them exactly; 0159's
    # separate persisted-decision guards remain authoritative below this step.
    previous = import_module('migrations.versions.0159_person_retention_bindings')
    for name, arguments in reversed(FUNCTIONS):
        previous._copy_function(f'{name}_before0160({arguments})', name + '_before0160', name)
        op.execute(f'DROP FUNCTION {name}_before0160({arguments})')
    op.execute('DROP FUNCTION public.retention_source_until(text,jsonb),public.retention_base_until(jsonb)')
