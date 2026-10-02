"""Close tenant and import-context edges without inferring historical ownership.

Revision ID: 0048
Revises: 0047
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision='0048'
down_revision='0047'
branch_labels=None
depends_on=None

# Counts only: no source text or guessed repair is emitted by the migration.
PRECHECK = """
DO $$ DECLARE bad_units bigint; bad_issues bigint; bad_evidence bigint;
BEGIN
    SELECT count(*) INTO bad_units FROM unit u JOIN property p ON p.id=u.property_id
        WHERE u.organization_id<>p.organization_id;
    SELECT count(*) INTO bad_issues FROM declaration_candidate_issue i
        JOIN declaration_candidate c ON c.organization_id=i.organization_id AND c.id=i.candidate_id
        WHERE c.import_id<>i.import_id;
    SELECT count(*) INTO bad_evidence FROM declaration_candidate_evidence e
        JOIN declaration_candidate c ON c.organization_id=e.organization_id AND c.id=e.candidate_id
        WHERE NOT EXISTS (SELECT 1 FROM declaration_import_document d
            WHERE d.organization_id=e.organization_id AND d.import_id=c.import_id AND d.document_id=e.document_id);
    IF bad_units+bad_issues+bad_evidence>0 THEN
        RAISE EXCEPTION 'source link reconciliation required: units=%, issues=%, evidence=%',
            bad_units,bad_issues,bad_evidence;
    END IF;
END $$
"""


def evidence_context_guard(include_import):
    op.execute('DROP TRIGGER a_guard_property_source_context ON declaration_candidate_evidence')
    columns="'id','organization_id','created_at','candidate_id','document_id'"
    if include_import:
        columns+=",'import_id'"
    op.execute('CREATE TRIGGER a_guard_property_source_context BEFORE UPDATE ON declaration_candidate_evidence '
               f'FOR EACH ROW EXECUTE FUNCTION guard_property_source_context({columns})')


def upgrade():
    # Acquire final DDL-strength locks without waiting. A partial lock followed
    # by a wait/upgrade could deadlock normal child-first graph replacement or
    # property-first approval. Busy readers/writers require a quiet-window retry;
    # the failed migration transaction releases every acquired lock.
    op.execute('LOCK TABLE property,unit,declaration_candidate_evidence,declaration_candidate_issue,'
               'declaration_candidate,declaration_import_document IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute(PRECHECK)
    op.create_foreign_key('fk_unit_property_tenant','unit','property',
                          ['organization_id','property_id'],['organization_id','id'],ondelete='RESTRICT')
    op.create_unique_constraint('uq_declaration_candidate_import_context','declaration_candidate',
                                ['organization_id','import_id','id'])
    op.create_unique_constraint('uq_declaration_document_import_context','declaration_import_document',
                                ['organization_id','import_id','document_id'])
    op.create_foreign_key('fk_declaration_issue_import_context','declaration_candidate_issue','declaration_candidate',
                          ['organization_id','import_id','candidate_id'],['organization_id','import_id','id'],ondelete='RESTRICT')
    op.add_column('declaration_candidate_evidence',sa.Column('import_id',UUID(as_uuid=True),nullable=True))
    # This is an exact copy from the existing immutable candidate parent. The
    # preflight separately proves the document is an actual source of that import.
    op.execute('UPDATE declaration_candidate_evidence e SET import_id=c.import_id FROM declaration_candidate c '
               'WHERE c.organization_id=e.organization_id AND c.id=e.candidate_id')
    op.alter_column('declaration_candidate_evidence','import_id',nullable=False)
    op.create_foreign_key('fk_declaration_evidence_candidate_import_context','declaration_candidate_evidence','declaration_candidate',
                          ['organization_id','import_id','candidate_id'],['organization_id','import_id','id'],ondelete='RESTRICT')
    op.create_foreign_key('fk_declaration_evidence_document_import_context','declaration_candidate_evidence','declaration_import_document',
                          ['organization_id','import_id','document_id'],['organization_id','import_id','document_id'],ondelete='RESTRICT')
    op.create_index('ix_declaration_evidence_import_document','declaration_candidate_evidence',['organization_id','import_id','document_id'])
    # Preserve old writers during expansion. Invoker rights deliberately require
    # the candidate to be visible; no source-reading privilege is added.
    op.execute("""CREATE FUNCTION fill_declaration_evidence_import() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
        IF NEW.import_id IS NULL THEN
            SELECT c.import_id INTO NEW.import_id FROM public.declaration_candidate c
                WHERE c.organization_id=NEW.organization_id AND c.id=NEW.candidate_id;
        END IF;
        RETURN NEW;
        END $$""")
    op.execute('CREATE TRIGGER fill_declaration_evidence_import BEFORE INSERT ON declaration_candidate_evidence '
               'FOR EACH ROW EXECUTE FUNCTION fill_declaration_evidence_import()')
    # Replace weaker edges after validating their composite replacements. Keeping
    # duplicate candidate/property FKs would make ORM implicit joins ambiguous.
    op.drop_constraint('fk_unit_property_id_property','unit',type_='foreignkey')
    op.drop_constraint('fk_declaration_candidate_issue_candidate_tenant','declaration_candidate_issue',type_='foreignkey')
    op.drop_constraint('fk_declaration_candidate_evidence_candidate_tenant','declaration_candidate_evidence',type_='foreignkey')
    evidence_context_guard(True)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM unit) OR EXISTS(SELECT 1 FROM declaration_candidate_issue)
        OR EXISTS(SELECT 1 FROM declaration_candidate_evidence) THEN
        RAISE EXCEPTION 'cannot remove populated source link constraints'; END IF; END $$""")
    op.create_foreign_key('fk_unit_property_id_property','unit','property',['property_id'],['id'],ondelete='RESTRICT')
    op.create_foreign_key('fk_declaration_candidate_issue_candidate_tenant','declaration_candidate_issue','declaration_candidate',
        ['organization_id','candidate_id'],['organization_id','id'],ondelete='RESTRICT')
    op.create_foreign_key('fk_declaration_candidate_evidence_candidate_tenant','declaration_candidate_evidence','declaration_candidate',
        ['organization_id','candidate_id'],['organization_id','id'],ondelete='RESTRICT')
    evidence_context_guard(False)
    op.execute('DROP TRIGGER fill_declaration_evidence_import ON declaration_candidate_evidence')
    op.execute('DROP FUNCTION fill_declaration_evidence_import()')
    op.drop_index('ix_declaration_evidence_import_document',table_name='declaration_candidate_evidence')
    op.drop_constraint('fk_declaration_evidence_document_import_context','declaration_candidate_evidence',type_='foreignkey')
    op.drop_constraint('fk_declaration_evidence_candidate_import_context','declaration_candidate_evidence',type_='foreignkey')
    op.drop_column('declaration_candidate_evidence','import_id')
    op.drop_constraint('fk_declaration_issue_import_context','declaration_candidate_issue',type_='foreignkey')
    op.drop_constraint('uq_declaration_document_import_context','declaration_import_document',type_='unique')
    op.drop_constraint('uq_declaration_candidate_import_context','declaration_candidate',type_='unique')
    op.drop_constraint('fk_unit_property_tenant','unit',type_='foreignkey')
