"""T33: retain frozen levy sources in the existing immutable dunning context."""
from alembic import op
from sqlalchemy import text

revision = '0128'
down_revision = '0127'
branch_labels = None
depends_on = None


_PLAN_VALIDATION = """        SELECT * INTO plan FROM public.economic_plan WHERE organization_id=org AND id=ctx.economic_plan_id FOR SHARE;
        IF sphere.scope_type<>'WEG' OR plan.id IS NULL OR plan.status<>'finalized'
          OR ctx.period_start<plan.period_start OR ctx.period_end>plan.period_end
          OR NOT EXISTS(SELECT 1 FROM public.accounting_scope_binding b WHERE b.organization_id=org AND b.economic_plan_id=plan.id AND b.scope_id=sphere.id)
          OR NOT EXISTS(SELECT 1 FROM public.economic_plan_line ln WHERE ln.organization_id=org AND ln.economic_plan_id=plan.id AND ln.owner_profile_id=notice.owner_profile_id AND ln.line_type='OWNER_ADVANCE') THEN
          RAISE EXCEPTION 'Dunning owner requires a finalized plan in the same scope' USING ERRCODE='23514';
        END IF;"""
_LEVY_VALIDATION = """        IF sphere.scope_type<>'WEG' OR ctx.lease_agreement_id IS NOT NULL THEN
          RAISE EXCEPTION 'Dunning owner requires WEG sources' USING ERRCODE='23514';
        END IF;
        IF ctx.economic_plan_id IS NOT NULL THEN
""" + _PLAN_VALIDATION + """
        ELSIF cardinality(ctx.special_levy_ids)=0 THEN
          RAISE EXCEPTION 'Dunning owner requires a finalized plan or frozen levies' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM unnest(ctx.special_levy_ids) source(id)
          WHERE source.id IS NULL OR NOT EXISTS(
            SELECT 1 FROM public.special_levy l JOIN public.special_levy_allocation a
              ON a.organization_id=l.organization_id AND a.special_levy_id=l.id
            WHERE l.organization_id=org AND l.id=source.id AND l.accounting_scope_id=sphere.id
              AND l.property_id=sphere.property_id AND l.due_date BETWEEN ctx.period_start AND ctx.period_end
              AND a.owner_profile_id=notice.owner_profile_id)) THEN
          RAISE EXCEPTION 'Dunning frozen levy, owner, period and scope must match' USING ERRCODE='23514';
        END IF;"""


def _replace_validation(old, new):
    connection = op.get_bind()
    definition = connection.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)"))
    if definition.count(old) != 1:
        raise RuntimeError('Unexpected dunning context validation definition')
    connection.execute(text(definition.replace(old, new)))


def upgrade():
    op.execute("""
    ALTER TABLE dunning_scope_context ADD COLUMN special_levy_ids uuid[] NOT NULL DEFAULT '{}';
    DO $$ DECLARE name text; BEGIN
      SELECT conname INTO STRICT name FROM pg_constraint
        WHERE conrelid='dunning_scope_context'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%num_nonnulls%';
      EXECUTE format('ALTER TABLE dunning_scope_context DROP CONSTRAINT %I',name);
    END $$;
    ALTER TABLE dunning_scope_context ADD CONSTRAINT ck_dunning_source_kind CHECK(
      (lease_agreement_id IS NOT NULL AND economic_plan_id IS NULL AND cardinality(special_levy_ids)=0)
      OR (lease_agreement_id IS NULL AND (economic_plan_id IS NOT NULL OR cardinality(special_levy_ids)>0)));
    """)
    _replace_validation(_PLAN_VALIDATION, _LEVY_VALIDATION)


def downgrade():
    # Never strip provenance from an existing financial letter.
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM dunning_scope_context WHERE cardinality(special_levy_ids)>0) THEN
        RAISE EXCEPTION 'Cannot downgrade while levy-backed dunning contexts exist';
      END IF;
    END $$""")
    _replace_validation(_LEVY_VALIDATION, _PLAN_VALIDATION)
    op.execute("""
    ALTER TABLE dunning_scope_context DROP CONSTRAINT ck_dunning_source_kind;
    ALTER TABLE dunning_scope_context ADD CONSTRAINT dunning_scope_context_check
      CHECK(num_nonnulls(lease_agreement_id,economic_plan_id)=1);
    ALTER TABLE dunning_scope_context DROP COLUMN special_levy_ids;
    """)
