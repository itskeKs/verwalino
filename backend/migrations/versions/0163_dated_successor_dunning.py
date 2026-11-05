"""Accept dated successors of a scoped plan without rewriting original claims."""
from importlib import import_module

from alembic import op
from sqlalchemy import text

revision = '0163'
down_revision = '0162'
branch_labels = None
depends_on = None

ORIGINAL = "NOT EXISTS(SELECT 1 FROM public.economic_plan_line ln WHERE ln.organization_id=org AND ln.economic_plan_id=plan.id AND ln.owner_profile_id=notice.owner_profile_id AND ln.line_type='OWNER_ADVANCE')"
DATED = """NOT EXISTS(SELECT 1 FROM public.economic_plan_line ln
            JOIN public.unit u ON u.id=ln.unit_id AND u.organization_id=ln.organization_id
            JOIN public.ownership_history h ON h.unit_id=u.id AND h.organization_id=u.organization_id
            WHERE ln.organization_id=org AND ln.economic_plan_id=plan.id AND ln.line_type='OWNER_ADVANCE'
              AND u.property_id=plan.property_id AND u.property_id=sphere.property_id
              AND h.owner_profile_id=notice.owner_profile_id AND h.valid_from<=ctx.period_end
              AND (h.valid_to IS NULL OR h.valid_to>ctx.period_start)
              AND (sphere.whole_property OR EXISTS(SELECT 1 FROM public.accounting_scope_unit su
                WHERE su.organization_id=org AND su.scope_id=sphere.id AND su.unit_id=u.id)))"""


def upgrade():
    previous = import_module('migrations.versions.0159_person_retention_bindings')
    previous._copy_function('validate_dunning_context()', 'validate_dunning_context', 'validate_dunning_context_before0163')
    op.execute('REVOKE ALL ON FUNCTION validate_dunning_context_before0163() FROM PUBLIC,app_rw,app_worker,app_review')
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)"))
    assert definition.count(ORIGINAL) == 1
    op.execute(definition.replace(ORIGINAL, '(' + ORIGINAL + ' AND ' + DATED + ')'))


def downgrade():
    # An old guard cannot represent the persisted provenance of successor claims.
    if op.get_bind().scalar(text("""SELECT EXISTS(
        SELECT 1 FROM dunning_scope_context c JOIN dunning_notice n
          ON n.id=c.dunning_notice_id AND n.organization_id=c.organization_id
        WHERE c.economic_plan_id IS NOT NULL AND n.owner_profile_id IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM economic_plan_line ln
            WHERE ln.organization_id=c.organization_id AND ln.economic_plan_id=c.economic_plan_id
              AND ln.owner_profile_id=n.owner_profile_id AND ln.line_type='OWNER_ADVANCE'))""")):
        raise RuntimeError('Cannot remove dated successor dunning provenance')
    previous = import_module('migrations.versions.0159_person_retention_bindings')
    previous._copy_function('validate_dunning_context_before0163()', 'validate_dunning_context_before0163', 'validate_dunning_context')
    op.execute('DROP FUNCTION validate_dunning_context_before0163()')
