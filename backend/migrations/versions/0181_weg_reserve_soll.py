"""Allow the planned reserve Soll in owner settlement lines."""
from alembic import op

revision = '0181'
down_revision = '0180'
branch_labels = None
depends_on = None

LINE_TYPES = "'COST','ADVANCE_PAYMENT','BALANCE','SUBTOTAL','OWNER_COST_TOTAL','PLAN_ADVANCE','ABRECHNUNGSSPITZE','RESERVE_CONTRIBUTION','RESERVE_OPENING','RESERVE_WITHDRAWAL','RESERVE_CLOSING','BANK_BALANCE','RECEIVABLE','PAYABLE','RESERVE_LATE_CASH','RESERVE_ADJUSTMENT'"


def upgrade():
    op.execute(f"""ALTER TABLE settlement_line DROP CONSTRAINT ck_settlement_line_ck_settlement_line_line_type;
      ALTER TABLE settlement_line ADD CONSTRAINT ck_settlement_line_ck_settlement_line_line_type
        CHECK(line_type IN ({LINE_TYPES},'RESERVE_SOLL'));""")


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM settlement_line WHERE line_type='RESERVE_SOLL') THEN
        RAISE EXCEPTION 'Retain planned reserve statement evidence; populated downgrade refused';
      END IF; END $$;""")
    op.execute(f"""ALTER TABLE settlement_line DROP CONSTRAINT ck_settlement_line_ck_settlement_line_line_type;
      ALTER TABLE settlement_line ADD CONSTRAINT ck_settlement_line_ck_settlement_line_line_type
        CHECK(line_type IN ({LINE_TYPES}));""")
