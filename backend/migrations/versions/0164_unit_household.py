"""Store independent dated occupied/vacancy household counts without guessing."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0164'
down_revision = '0163'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('unit_household',
        sa.Column('id', UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organization.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('unit_id', UUID(as_uuid=True), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('persons', sa.Integer(), nullable=False),
        sa.Column('is_vacancy', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id', 'unit_id'], ['unit.organization_id', 'unit.id'],
            name='fk_unit_household_unit_org', ondelete='RESTRICT'),
        sa.UniqueConstraint('unit_id', 'is_vacancy', 'valid_from', name='uq_unit_household_stream_date'),
        sa.CheckConstraint('persons >= 0', name='persons_nonnegative'),
    )
    op.create_index('ix_unit_household_organization_id', 'unit_household', ['organization_id'])
    op.execute("""
      ALTER TABLE unit_household ENABLE ROW LEVEL SECURITY;
      ALTER TABLE unit_household FORCE ROW LEVEL SECURITY;
      GRANT SELECT, INSERT, DELETE ON unit_household TO app_rw;
    """)
    for action, capability in [('SELECT', 'read'), ('INSERT', 'prepare'), ('DELETE', 'prepare')]:
        actor = "IS NOT NULL" if action == 'SELECT' else "IN ('OWNER_ADMIN','CLERK')"
        predicate = f"""organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND scope_actor_role(organization_id) {actor}
          AND EXISTS(SELECT 1 FROM unit u WHERE u.organization_id=unit_household.organization_id
            AND u.id=unit_household.unit_id
            AND property_master_access(u.organization_id,u.property_id,'{capability}'))"""
        clause = 'WITH CHECK' if action == 'INSERT' else 'USING'
        op.execute(f'CREATE POLICY unit_household_{action.lower()} ON unit_household FOR {action} {clause} ({predicate})')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM unit_household)')):
        raise RuntimeError('Cannot discard saved household history; repair forward')
    op.drop_table('unit_household')
