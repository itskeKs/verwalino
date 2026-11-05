"""Store optional property state for statutory rent deadlines."""
from alembic import op
import sqlalchemy as sa

revision = '0166'
down_revision = '0165'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('property', sa.Column('federal_state', sa.String(2), nullable=True))
    op.create_check_constraint(op.f('ck_property_ck_property_federal_state'), 'property',
        "federal_state IN ('BW','BY','BE','BB','HB','HH','HE','MV','NI','NW','RP','SL','SN','ST','SH','TH')")


def downgrade():
    op.execute('LOCK TABLE property IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM property WHERE federal_state IS NOT NULL)')):
        raise RuntimeError('Cannot discard property holiday context; repair forward')
    op.drop_constraint(op.f('ck_property_ck_property_federal_state'), 'property', type_='check')
    op.drop_column('property', 'federal_state')
