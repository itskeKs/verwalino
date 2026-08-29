from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "property",
        sa.Column("mea_denominator", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "property",
        sa.Column(
            "mea_scale_status",
            sa.String(24),
            nullable=False,
            server_default="LEGACY_UNCONFIRMED",
        ),
    )
    op.create_check_constraint(
        op.f("ck_property_mea_scale_status"),
        "property",
        "mea_scale_status IN ('CONFIRMED','LEGACY_UNCONFIRMED')",
    )
    op.create_check_constraint(
        op.f("ck_property_mea_scale_consistency"),
        "property",
        "(mea_scale_status = 'LEGACY_UNCONFIRMED' AND mea_denominator IS NULL) "
        "OR (mea_scale_status = 'CONFIRMED' "
        "AND mea_denominator BETWEEN 1 AND 1000000000000)",
    )
    op.alter_column(
        "unit",
        "mea",
        existing_type=sa.Numeric(12, 6),
        type_=sa.Numeric(19, 6),
        existing_nullable=True,
    )
    op.add_column(
        "unit",
        sa.Column(
            "building_part",
            sa.String(255),
            nullable=False,
            server_default="",
        ),
    )
    # Existing pre-0023 data may contain legacy negative values. PostgreSQL
    # still enforces a NOT VALID check for new or updated rows, without
    # rejecting a safe upgrade of that historic data.
    op.execute(
        "ALTER TABLE unit ADD CONSTRAINT ck_unit_mea_nonnegative "
        "CHECK (mea IS NULL OR mea >= 0) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_unit_mea_nonnegative"), "unit")
    op.drop_column("unit", "building_part")
    op.alter_column(
        "unit",
        "mea",
        existing_type=sa.Numeric(19, 6),
        type_=sa.Numeric(12, 6),
        existing_nullable=True,
    )
    op.drop_constraint(op.f("ck_property_mea_scale_consistency"), "property")
    op.drop_constraint(op.f("ck_property_mea_scale_status"), "property")
    op.drop_column("property", "mea_scale_status")
    op.drop_column("property", "mea_denominator")
