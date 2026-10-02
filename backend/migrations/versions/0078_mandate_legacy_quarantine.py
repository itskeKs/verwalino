"""Freeze unclassified mandate originals as contemporary quarantine observations."""
from alembic import op
from sqlalchemy import text

revision = '0078'
down_revision = '0077'
branch_labels = None
depends_on = None

COMPATIBILITY = "IF sphere IS NULL THEN RETURN NULLIF(current_setting('app.current_scope_id',true),'') IS NULL; END IF;"
QUARANTINE = "IF sphere IS NULL THEN RETURN false; END IF;"
OBSERVE = """
    INSERT INTO mandate_legacy_observation(organization_id,document_id,snapshot_digest)
    SELECT d.organization_id,d.id,encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex')
    FROM mandate_document d WHERE d.accounting_scope_id IS NULL
    ON CONFLICT(organization_id,document_id) DO NOTHING
"""


def _gate(before, after):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('public.mandate_source_access(uuid,uuid,text)'::regprocedure)"))
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected mandate source helper; refusing partial quarantine')
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute('LOCK TABLE property_source_security,mandate_document IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""
    ALTER TABLE mandate_document ADD CONSTRAINT uq_mandate_original_org_id UNIQUE(organization_id,id);
    CREATE TABLE mandate_legacy_observation (
      organization_id uuid NOT NULL REFERENCES organization(id), document_id uuid NOT NULL,
      snapshot_digest text NOT NULL CHECK(snapshot_digest ~ '^[0-9a-f]{64}$'),
      observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(organization_id,document_id),
      FOREIGN KEY(organization_id,document_id) REFERENCES mandate_document(organization_id,id)
    );
    ALTER TABLE mandate_legacy_observation ENABLE ROW LEVEL SECURITY;
    ALTER TABLE mandate_legacy_observation FORCE ROW LEVEL SECURITY;
    CREATE POLICY mandate_observation_private ON mandate_legacy_observation AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);
    REVOKE ALL ON mandate_legacy_observation FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER protect_mandate_observation BEFORE UPDATE OR DELETE ON mandate_legacy_observation
      FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();
    """)
    op.execute(OBSERVE)
    op.execute("""
    CREATE FUNCTION quarantine_mandate_original() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.accounting_scope_id IS NULL THEN
          RAISE EXCEPTION 'New mandate originals require an explicit scope' USING ERRCODE='42501'; END IF;
        RETURN NEW;
      END IF;
      IF OLD.accounting_scope_id IS NULL THEN
        RAISE EXCEPTION 'Unclassified mandate originals are retained in quarantine' USING ERRCODE='23514'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION quarantine_mandate_original() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER quarantine_mandate_original BEFORE INSERT OR UPDATE OR DELETE ON mandate_document
      FOR EACH ROW EXECUTE FUNCTION quarantine_mandate_original();
    """)
    _gate(COMPATIBILITY, QUARANTINE)


def downgrade():
    op.execute('LOCK TABLE property_source_security,mandate_document,mandate_legacy_observation IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM mandate_legacy_observation)
        OR EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
        RAISE EXCEPTION 'Retain legacy mandate quarantine; repair forward';
      END IF;
    END $$""")
    _gate(QUARANTINE, COMPATIBILITY)
    op.execute("""
    DROP TRIGGER quarantine_mandate_original ON mandate_document;
    DROP FUNCTION quarantine_mandate_original();
    DROP TABLE mandate_legacy_observation;
    ALTER TABLE mandate_document DROP CONSTRAINT uq_mandate_original_org_id;
    """)
