"""Mark a mistaken ownership row as corrected instead of rewriting it (issue #668).

A co-owner's share or start date entered wrongly cannot be fixed in place: finalized
settlements, plans and special levies were calculated from the row, and the history
is append-only. A correction therefore sets ``corrected_at`` and ``corrected_by`` on
the mistaken row (its values stay untouched) and the API inserts a replacement row
with the corrected values. Every calculation reads rows with ``corrected_at IS NULL``.

The no-overlap constraint of 0003 must not count a corrected row, otherwise its
replacement for the same person could never be inserted, so it becomes a partial
exclusion over the uncorrected rows. The SQL functions that decide on the dates of an
ownership row are replaced with bodies that skip corrected rows too: the reserve
contribution headroom of 0149, the SEV owner coverage check of 0033, the payment
computation context of 0036 and the dunning context guard of 0163. The functions that
only follow the relation of a row (source access, coverage evidence, special levy
provenance, retention) keep reading every row, so a row that evidence points to stays
resolvable.
"""
from alembic import op
from sqlalchemy import text

revision = '0190'
down_revision = '0189'
branch_labels = None
depends_on = None

# (function signature, text before, text after); each anchor occurs exactly once in the body.
PATCHES = (
    ('public.reserve_contribution_headroom(uuid,uuid,uuid)',
     """WHERE h.organization_id=org AND h.owner_profile_id=p.owner_profile_id AND u.property_id=p.property_id
              AND h.valid_from<=p.value_date""",
     """WHERE h.organization_id=org AND h.owner_profile_id=p.owner_profile_id AND u.property_id=p.property_id
              AND h.corrected_at IS NULL AND h.valid_from<=p.value_date"""),
    ('public.validate_sev_owner(uuid,uuid,uuid,uuid)',
     "WHERE h.organization_id=org AND h.unit_id=unit AND h.owner_profile_id=owner;",
     "WHERE h.organization_id=org AND h.unit_id=unit AND h.owner_profile_id=owner AND h.corrected_at IS NULL;"),
    ('public.payment_computation_context_valid(uuid,uuid,uuid)',
     "WHERE h.organization_id=org AND h.owner_profile_id=owner AND public.unit_in_financial_context(org,h.unit_id)",
     "WHERE h.organization_id=org AND h.owner_profile_id=owner AND h.corrected_at IS NULL AND public.unit_in_financial_context(org,h.unit_id)"),
    ('public.validate_dunning_context()',
     "JOIN public.ownership_history h ON h.unit_id=u.id AND h.organization_id=u.organization_id",
     "JOIN public.ownership_history h ON h.unit_id=u.id AND h.organization_id=u.organization_id AND h.corrected_at IS NULL"),
)


def patch_functions(reverse=False):
    for signature, before, after in PATCHES:
        definition = op.get_bind().scalar(text(f"SELECT pg_get_functiondef('{signature}'::regprocedure)"))
        old, new = (after, before) if reverse else (before, after)
        assert definition.count(old) == 1, signature
        op.execute(definition.replace(old, new))


def upgrade():
    op.execute('LOCK TABLE ownership_history IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute('''
    ALTER TABLE ownership_history
      ADD COLUMN corrected_at timestamptz,
      ADD COLUMN corrected_by uuid,
      ADD CONSTRAINT ck_ownership_history_correction CHECK ((corrected_at IS NULL) = (corrected_by IS NULL)),
      ADD CONSTRAINT fk_ownership_history_corrected_by FOREIGN KEY (organization_id, corrected_by)
        REFERENCES "user"(organization_id, id);
    ALTER TABLE ownership_history DROP CONSTRAINT excl_ownership_no_overlap;
    ALTER TABLE ownership_history ADD CONSTRAINT excl_ownership_no_overlap
      EXCLUDE USING gist (unit_id WITH =, owner_profile_id WITH =, daterange(valid_from, valid_to, '[)') WITH &&)
      WHERE (corrected_at IS NULL);
    ''')
    patch_functions()


def downgrade():
    op.execute('LOCK TABLE ownership_history IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM ownership_history WHERE corrected_at IS NOT NULL)
      THEN RAISE EXCEPTION 'Cannot discard corrected ownership rows'; END IF; END $$""")
    patch_functions(reverse=True)
    op.execute('''
    ALTER TABLE ownership_history DROP CONSTRAINT excl_ownership_no_overlap;
    ALTER TABLE ownership_history ADD CONSTRAINT excl_ownership_no_overlap
      EXCLUDE USING gist (unit_id WITH =, owner_profile_id WITH =, daterange(valid_from, valid_to, '[)') WITH &&);
    ALTER TABLE ownership_history
      DROP CONSTRAINT fk_ownership_history_corrected_by,
      DROP CONSTRAINT ck_ownership_history_correction,
      DROP COLUMN corrected_by,
      DROP COLUMN corrected_at;
    ''')
