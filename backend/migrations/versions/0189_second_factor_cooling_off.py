"""Second-factor enrolment stays writable during the cooling-off period (issue #659).

An administrator whose factor was reset while the organization is in the
cooling-off period of its deletion must be able to enrol again, because
cancelling the deletion needs a confirmed factor in hosted mode. The 0147
lifecycle guard refuses every runtime write of an organization in cooling-off,
including the two factor tables, so the 0147-owned body is replaced here, not
edited: exactly user_second_factor and user_recovery_code are exempt, and only
while the deletion is in cooling-off. After the erasure they are refused like
every other table.
"""

from alembic import op
from sqlalchemy import text

revision = "0189"
down_revision = "0188"
branch_labels = None
depends_on = None


def patch_lifecycle_guard(reverse=False):
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef('public.organization_lifecycle_guard()'::regprocedure)"
        )
    )
    before = "AND d.status IN ('cooling_off','deleted_pending_retention')) THEN"
    after = (
        "AND d.status IN ('cooling_off','deleted_pending_retention')\n"
        "          AND NOT (d.status='cooling_off'\n"
        "            AND TG_TABLE_NAME IN ('user_second_factor','user_recovery_code'))) THEN"
    )
    if reverse:
        before, after = after, before
        if before not in definition and after in definition:
            return
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def upgrade():
    patch_lifecycle_guard()


def downgrade():
    patch_lifecycle_guard(reverse=True)
