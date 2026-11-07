"""Bind frozen asset-report recipients through the shared retention catalog."""
from importlib import import_module

from alembic import op
from sqlalchemy import text

revision = '0162'
down_revision = '0161'
branch_labels = None
depends_on = None


def upgrade():
    previous = import_module('migrations.versions.0159_person_retention_bindings')
    previous._copy_function('person_retention_categories()', 'person_retention_categories',
                            'person_retention_categories_before0162')
    op.execute('REVOKE ALL ON FUNCTION person_retention_categories_before0162() FROM PUBLIC,app_rw,app_worker,app_review')
    for name, args in [('retention_source_until', 'text,jsonb'), ('organization_retention_plan', 'uuid')]:
        previous._copy_function(f'{name}({args})', name, name + '_before0162')
        op.execute(f'REVOKE ALL ON FUNCTION {name}_before0162({args}) FROM PUBLIC,app_rw,app_worker,app_review')
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('retention_source_until(text,jsonb)'::regprocedure)"))
    old = "item->>'run_type'='BK_MIETER'"
    assert definition.count(old) == 1
    op.execute(definition.replace(old, "item->>'run_type' IN ('BK_MIETER','VERMOEGENSBERICHT')"))
    op.execute(r'''
    CREATE FUNCTION public.report_retention_recipients(item jsonb)
      RETURNS TABLE(owner_profile_id uuid,unit_id uuid)
      LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT (a->>'owner_profile_id')::uuid,(a->>'unit_id')::uuid
      FROM jsonb_array_elements(CASE WHEN item->>'status'='finalized' AND item->>'run_type'='VERMOEGENSBERICHT'
        AND jsonb_typeof(item->'snapshot_payload'->'addressees')='array'
        THEN item->'snapshot_payload'->'addressees' ELSE '[]'::jsonb END) a;
    $$;
    REVOKE ALL ON FUNCTION public.report_retention_recipients(jsonb) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE OR REPLACE FUNCTION public.person_retention_categories() RETURNS TABLE(
      entity_type text,kind text,retain_when text,recipient_join text,until_sql text,coherent_sql text,access_sql text)
      LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $catalog$
      SELECT * FROM public.person_retention_categories_before0162()
      UNION ALL SELECT 'settlement_run','FINALIZED_OWNERSHIP',
        $q$t.status='finalized' AND t.run_type='VERMOEGENSBERICHT'$q$,
        $q$JOIN LATERAL public.report_retention_recipients(to_jsonb(t)) a ON true
          JOIN public.owner_profile p ON p.id=a.owner_profile_id AND p.person_id=$2
          LEFT JOIN public.unit u ON u.id=a.unit_id$q$,
        $q$public.retention_source_until('settlement_run',to_jsonb(t))$q$,
        $q$t.organization_id=$1 AND p.organization_id=$1 AND u.organization_id=$1 AND u.property_id=t.property_id$q$,
        $q$public.financial_scope_access($1,'settlement_run',t.id,$3)$q$;
    $catalog$;
    ''')

    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('organization_retention_plan(uuid)'::regprocedure)"))
    marker = '      -- Retain required ancestors'
    assert definition.count(marker) == 1
    edges = '''      -- Snapshot recipient edges share eligibility with the person catalog.
      INSERT INTO pg_temp.organization_retention_work AS existing
        SELECT 'owner_profile',jsonb_build_object('id',p.id),max(w.retention_until),
          'ADR-0012: frozen report recipient',max(w.person_until)
        FROM public.settlement_run t JOIN pg_temp.organization_retention_work w
          ON w.entity='settlement_run' AND w.identity=jsonb_build_object('id',t.id)
        JOIN LATERAL public.report_retention_recipients(to_jsonb(t)) a ON true
        JOIN public.owner_profile p ON p.id=a.owner_profile_id AND p.organization_id=t.organization_id
        JOIN public.unit u ON u.id=a.unit_id AND u.organization_id=t.organization_id AND u.property_id=t.property_id
        WHERE t.organization_id=org GROUP BY p.id
        ON CONFLICT(entity,identity) DO UPDATE SET retention_until=GREATEST(existing.retention_until,EXCLUDED.retention_until),
          person_until=GREATEST(existing.person_until,EXCLUDED.person_until);
'''
    op.execute(definition.replace(marker, edges + marker))


def downgrade():
    if op.get_bind().scalar(text("SELECT EXISTS(SELECT 1 FROM settlement_run WHERE status='finalized' "
        "AND run_type='VERMOEGENSBERICHT' AND jsonb_array_length(CASE WHEN jsonb_typeof(snapshot_payload->'addressees')='array' "
        "THEN snapshot_payload->'addressees' ELSE '[]'::jsonb END)>0)")):
        raise RuntimeError('Cannot remove retained asset-report recipient bindings')
    previous = import_module('migrations.versions.0159_person_retention_bindings')
    previous._copy_function('person_retention_categories_before0162()', 'person_retention_categories_before0162',
                            'person_retention_categories')
    op.execute('DROP FUNCTION person_retention_categories_before0162()')
    for name, args in [('organization_retention_plan', 'uuid'), ('retention_source_until', 'text,jsonb')]:
        previous._copy_function(f'{name}_before0162({args})', name + '_before0162', name)
        op.execute(f'DROP FUNCTION {name}_before0162({args})')
    op.execute('DROP FUNCTION public.report_retention_recipients(jsonb)')
