"""Freeze the meeting date separately from the register entry date."""
from alembic import op
import sqlalchemy as sa

revision = '0167'
down_revision = '0166'
branch_labels = None
depends_on = None

# Primary keys make the source unique; every edge must also agree on tenant
# and property. A missing/foreign source is not a date we may guess.
SOURCE = """
    SELECT m.meeting_date
    FROM resolution r
    JOIN owners_meeting m ON m.id=r.meeting_id AND m.organization_id=r.organization_id
    JOIN property p ON p.id=m.property_id AND p.organization_id=m.organization_id
    WHERE r.id=e.resolution_id AND r.organization_id=e.organization_id
      AND m.property_id=e.property_id
"""
DATE_GUARD = """        IF NEW.resolution_date IS DISTINCT FROM OLD.resolution_date THEN
            RAISE EXCEPTION
                'Immutability: Cannot change resolution_date on register entry (id=%)', OLD.id;
        END IF;
"""
ANCHOR = '        IF NEW.entry_date IS DISTINCT FROM OLD.entry_date THEN'


def lock_sources():
    # Keep source validation and copying in one stable transaction. ALTER TABLE
    # needs the exclusive register lock anyway; source locks block concurrent writes.
    op.execute('LOCK TABLE resolution_register_entry IN ACCESS EXCLUSIVE MODE')
    op.execute('LOCK TABLE resolution, owners_meeting, property IN SHARE MODE')


def patch_immutability(reverse=False):
    # Preserve the later retention/operator exceptions, owner and execute grants.
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef('trg_register_entry_immutability()'::regprocedure)"))
    before, after = ANCHOR, DATE_GUARD + ANCHOR
    if reverse:
        before, after = after, before
    if definition.count(before) != 1 or (not reverse and DATE_GUARD in definition):
        raise RuntimeError('Unexpected register immutability function; repair forward')
    op.execute(definition.replace(before, after, 1))


def upgrade():
    lock_sources()
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM resolution_register_entry e WHERE ({SOURCE}) IS NULL) THEN
            RAISE EXCEPTION 'Cannot derive register meeting date from a valid resolution/meeting/property relationship; repair source integrity before upgrading';
        END IF;
    END $$""")
    op.add_column('resolution_register_entry', sa.Column('resolution_date', sa.Date(), nullable=True))
    # The existing trigger already freezes all old fields. Its new-column guard
    # is installed only after this single backfill; no trigger is disabled.
    op.execute(f'UPDATE resolution_register_entry e SET resolution_date=({SOURCE})')
    op.alter_column('resolution_register_entry', 'resolution_date', nullable=False)
    patch_immutability()


def downgrade():
    lock_sources()
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM resolution_register_entry e
            WHERE e.resolution_date IS DISTINCT FROM ({SOURCE})) THEN
            RAISE EXCEPTION 'Cannot discard register meeting date snapshot; repair forward';
        END IF;
    END $$""")
    patch_immutability(reverse=True)
    op.drop_column('resolution_register_entry', 'resolution_date')
