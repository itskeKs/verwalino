"""Freeze property-source origins before activating property-data authorization.

Revision ID: 0047
Revises: 0046

No historical owner is inferred and no source visibility policy is activated.
"""
from alembic import op

revision = '0047'
down_revision = '0046'
branch_labels = None
depends_on = None

# Explicit allowlist of context columns. Payload/review/lifecycle fields retain
# their existing rules. Previously established immutable derived-source guards
# and append-only queue/pass ledgers remain authoritative.
CONTEXT = {
    'property': ('id', 'organization_id', 'created_at'),
    'unit': ('id', 'organization_id', 'created_at', 'property_id'),
    'property_document': ('id', 'organization_id', 'created_at', 'uploaded_by'),
    'declaration_import': ('id', 'organization_id', 'created_at', 'initiated_by', 'mode', 'target_property_id'),
    'declaration_import_document': ('id', 'organization_id', 'created_at', 'import_id', 'document_id'),
    'declaration_candidate': ('id', 'organization_id', 'created_at', 'import_id'),
    'declaration_candidate_evidence': ('id', 'organization_id', 'created_at', 'candidate_id', 'document_id'),
    'declaration_candidate_issue': ('id', 'organization_id', 'created_at', 'import_id', 'candidate_id'),
    'declaration_extraction_run': ('id', 'organization_id', 'created_at', 'import_id'),
}


def upgrade():
    op.execute("""
        CREATE FUNCTION guard_property_source_context() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
        DECLARE field_name text;
        BEGIN
            FOREACH field_name IN ARRAY TG_ARGV LOOP
                IF (to_jsonb(NEW)->field_name) IS DISTINCT FROM (to_jsonb(OLD)->field_name) THEN
                    RAISE EXCEPTION '% source context is immutable: %', TG_TABLE_NAME, field_name;
                END IF;
            END LOOP;
            RETURN NEW;
        END;
        $$
    """)
    for table, columns in CONTEXT.items():
        args = ','.join(f"'{column}'" for column in columns)
        op.execute(f'CREATE TRIGGER a_guard_property_source_context BEFORE UPDATE ON {table} '
                   f'FOR EACH ROW EXECUTE FUNCTION guard_property_source_context({args})')
    op.execute("""
        CREATE FUNCTION guard_declaration_applied_context() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.status = 'APPLIED' AND
               ROW(NEW.status,NEW.applied_property_id,NEW.approved_by,NEW.approved_at,
                   NEW.approval_idempotency_key,NEW.approval_result_json)
               IS DISTINCT FROM
               ROW(OLD.status,OLD.applied_property_id,OLD.approved_by,OLD.approved_at,
                   OLD.approval_idempotency_key,OLD.approval_result_json) THEN
                RAISE EXCEPTION 'declaration applied context is immutable';
            END IF;
            IF NEW.applied_property_id IS NOT NULL AND NEW.mode = 'EXISTING_PROPERTY'
               AND NEW.applied_property_id IS DISTINCT FROM NEW.target_property_id THEN
                RAISE EXCEPTION 'declaration applied property must equal its existing target';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute('CREATE TRIGGER guard_declaration_applied_context BEFORE INSERT OR UPDATE '
               'ON declaration_import FOR EACH ROW EXECUTE FUNCTION guard_declaration_applied_context()')


def downgrade():
    # A populated rollback would permit rewriting the very evidence future data
    # policies rely on. Pause writers and forward-repair instead.
    checks = ' OR '.join(f'EXISTS(SELECT 1 FROM public.{table})' for table in CONTEXT)
    op.execute(f"""DO $$ BEGIN IF {checks} THEN
        RAISE EXCEPTION 'cannot remove populated property source context guards';
        END IF; END $$""")
    op.execute('DROP TRIGGER guard_declaration_applied_context ON declaration_import')
    op.execute('DROP FUNCTION guard_declaration_applied_context()')
    for table in CONTEXT:
        op.execute(f'DROP TRIGGER a_guard_property_source_context ON {table}')
    op.execute('DROP FUNCTION guard_property_source_context()')
