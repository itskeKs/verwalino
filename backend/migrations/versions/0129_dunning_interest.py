"""T35: frozen exact interest amounts and calculation provenance on dunning letters."""
from alembic import op
from sqlalchemy import text

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None


_INTEREST_GUARD = """        IF OLD.status = 'issued' AND (
            NEW.interest_amount IS DISTINCT FROM OLD.interest_amount OR
            NEW.interest_snapshot IS DISTINCT FROM OLD.interest_snapshot) THEN
            RAISE EXCEPTION 'Immutability: Cannot change interest on issued dunning_notice (id=%)', OLD.id;
        END IF;
"""


def _extend_immutability(adding):
    connection = op.get_bind()
    definition = connection.scalar(text(
        "SELECT pg_get_functiondef('trg_dunning_notice_immutability()'::regprocedure)"
    ))
    anchor = "        RETURN NEW;"
    if adding:
        if definition.count(anchor) != 1 or _INTEREST_GUARD in definition:
            raise RuntimeError("Unexpected dunning immutability definition")
        definition = definition.replace(anchor, _INTEREST_GUARD + anchor)
    else:
        if definition.count(_INTEREST_GUARD) != 1:
            raise RuntimeError("Missing dunning interest immutability guard")
        definition = definition.replace(_INTEREST_GUARD, "")
    connection.execute(text(definition))


def upgrade():
    op.execute("""
        ALTER TABLE dunning_notice
          ADD COLUMN interest_amount numeric(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN interest_snapshot jsonb;
        ALTER TABLE dunning_notice ADD CONSTRAINT ck_dunning_notice_total_sum
          CHECK(interest_snapshot IS NULL OR total_amount = principal_amount + fee_amount + interest_amount);
        ALTER TABLE dunning_notice ADD CONSTRAINT ck_dunning_notice_interest_provenance
          CHECK(interest_snapshot IS NOT NULL OR interest_amount = 0);
    """)
    _extend_immutability(True)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM dunning_notice
                  WHERE interest_snapshot IS NOT NULL OR interest_amount <> 0) THEN
            RAISE EXCEPTION 'Cannot downgrade while interest-backed dunning letters exist';
        END IF;
    END $$""")
    _extend_immutability(False)
    op.execute("""
        ALTER TABLE dunning_notice DROP CONSTRAINT ck_dunning_notice_interest_provenance;
        ALTER TABLE dunning_notice DROP CONSTRAINT ck_dunning_notice_total_sum;
        ALTER TABLE dunning_notice DROP COLUMN interest_snapshot, DROP COLUMN interest_amount;
    """)
