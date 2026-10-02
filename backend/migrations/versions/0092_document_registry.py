"""Register existing originals through unique typed adapters without copying bytes."""
from alembic import op

revision='0092'
down_revision='0091'
branch_labels=None
depends_on=None
SOURCES=('invoice_document','property_document','mandate_document')


def upgrade():
    op.execute('LOCK TABLE invoice_document,property_document,mandate_document IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute('''
    CREATE TABLE document_ref (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organization(id),
      source_kind text NOT NULL, invoice_document_id uuid, property_document_id uuid, mandate_document_id uuid,
      content_sha256 varchar(64) NOT NULL, mime_type varchar(100) NOT NULL, content_byte_size bigint NOT NULL,
      registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(organization_id,id), UNIQUE(organization_id,invoice_document_id),
      UNIQUE(organization_id,property_document_id), UNIQUE(organization_id,mandate_document_id),
      CHECK((source_kind='invoice_document' AND invoice_document_id IS NOT NULL AND property_document_id IS NULL AND mandate_document_id IS NULL)
         OR (source_kind='property_document' AND property_document_id IS NOT NULL AND invoice_document_id IS NULL AND mandate_document_id IS NULL)
         OR (source_kind='mandate_document' AND mandate_document_id IS NOT NULL AND invoice_document_id IS NULL AND property_document_id IS NULL)),
      FOREIGN KEY(organization_id,invoice_document_id) REFERENCES invoice_document(organization_id,id) ON DELETE RESTRICT,
      FOREIGN KEY(organization_id,property_document_id) REFERENCES property_document(organization_id,id) ON DELETE RESTRICT,
      FOREIGN KEY(organization_id,mandate_document_id) REFERENCES mandate_document(organization_id,id) ON DELETE RESTRICT);
    CREATE INDEX ix_document_ref_page ON document_ref(organization_id,lower(source_kind),id);
    ALTER TABLE document_ref ENABLE ROW LEVEL SECURITY;
    ALTER TABLE document_ref FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON document_ref FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT ON document_ref TO app_rw;

    -- Invoker access deliberately uses each original's actual RLS, not a new ACL.
    CREATE FUNCTION document_ref_visible(ref public.document_ref) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF ref.organization_id IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||ref.organization_id::text,0));
      IF public.scope_actor_role(ref.organization_id) IS NULL THEN RETURN false; END IF;
      IF ref.source_kind='invoice_document' THEN
        RETURN EXISTS(SELECT 1 FROM public.invoice_document WHERE organization_id=ref.organization_id AND id=ref.invoice_document_id);
      ELSIF ref.source_kind='property_document' THEN
        RETURN EXISTS(SELECT 1 FROM public.property_document WHERE organization_id=ref.organization_id AND id=ref.property_document_id);
      ELSIF ref.source_kind='mandate_document' THEN
        RETURN EXISTS(SELECT 1 FROM public.mandate_document WHERE organization_id=ref.organization_id AND id=ref.mandate_document_id);
      END IF;
      RETURN false;
    END $$;
    REVOKE ALL ON FUNCTION document_ref_visible(public.document_ref) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION document_ref_visible(public.document_ref) TO app_rw;
    CREATE POLICY document_ref_read ON document_ref FOR SELECT USING(document_ref_visible(document_ref));
    CREATE POLICY document_ref_no_insert ON document_ref FOR INSERT WITH CHECK(false);

    CREATE FUNCTION validate_document_ref() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE digest text; media text; bytes bigint;
    BEGIN
      IF NEW.source_kind='invoice_document' THEN
        SELECT content_sha256,mime_type,content_byte_size INTO digest,media,bytes FROM public.invoice_document WHERE organization_id=NEW.organization_id AND id=NEW.invoice_document_id;
      ELSIF NEW.source_kind='property_document' THEN
        SELECT content_sha256,mime_type,content_byte_size INTO digest,media,bytes FROM public.property_document WHERE organization_id=NEW.organization_id AND id=NEW.property_document_id;
      ELSIF NEW.source_kind='mandate_document' THEN
        SELECT content_sha256,mime_type,content_byte_size INTO digest,media,bytes FROM public.mandate_document WHERE organization_id=NEW.organization_id AND id=NEW.mandate_document_id;
      ELSE RAISE EXCEPTION 'Unknown original adapter' USING ERRCODE='23514'; END IF;
      IF NOT FOUND OR (NEW.content_sha256,NEW.mime_type,NEW.content_byte_size) IS DISTINCT FROM (digest,media,bytes) THEN
        RAISE EXCEPTION 'Original reference must match its exact source' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER validate_document_ref BEFORE INSERT ON document_ref FOR EACH ROW EXECUTE FUNCTION validate_document_ref();
    CREATE TRIGGER protect_document_ref BEFORE UPDATE OR DELETE ON document_ref FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    CREATE FUNCTION register_original_document() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      INSERT INTO public.document_ref(organization_id,source_kind,invoice_document_id,property_document_id,mandate_document_id,content_sha256,mime_type,content_byte_size)
        VALUES(NEW.organization_id,TG_TABLE_NAME,CASE WHEN TG_TABLE_NAME='invoice_document' THEN NEW.id END,
          CASE WHEN TG_TABLE_NAME='property_document' THEN NEW.id END,CASE WHEN TG_TABLE_NAME='mandate_document' THEN NEW.id END,
          NEW.content_sha256,NEW.mime_type,NEW.content_byte_size);
      RETURN NEW;
    END $$;
    CREATE FUNCTION preserve_registered_original() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF (NEW.id,NEW.organization_id,NEW.content_sha256,NEW.mime_type,NEW.content_byte_size)
        IS DISTINCT FROM (OLD.id,OLD.organization_id,OLD.content_sha256,OLD.mime_type,OLD.content_byte_size) THEN
        RAISE EXCEPTION 'Registered original identity and content are immutable; create a new version' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION validate_document_ref(),register_original_document(),preserve_registered_original() FROM PUBLIC,app_rw,app_worker,app_review;
    ''')
    for source in SOURCES:
        op.execute(f'''INSERT INTO document_ref(organization_id,source_kind,{source}_id,content_sha256,mime_type,content_byte_size)
          SELECT organization_id,'{source}',id,content_sha256,mime_type,content_byte_size FROM {source};
          CREATE TRIGGER register_original_document AFTER INSERT ON {source} FOR EACH ROW EXECUTE FUNCTION register_original_document();
          CREATE TRIGGER preserve_registered_original BEFORE UPDATE OF id,organization_id,content_sha256,mime_type,content_byte_size ON {source}
            FOR EACH ROW EXECUTE FUNCTION preserve_registered_original();''')


def downgrade():
    op.execute('LOCK TABLE invoice_document,property_document,mandate_document,document_ref IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM document_ref) THEN RAISE EXCEPTION 'Retain original registry references; repair forward'; END IF; END $$")
    for source in SOURCES:
        op.execute(f'DROP TRIGGER register_original_document ON {source}; DROP TRIGGER preserve_registered_original ON {source};')
    op.execute('''DROP FUNCTION register_original_document(),preserve_registered_original();
      DROP POLICY document_ref_read ON document_ref;
      DROP FUNCTION document_ref_visible(public.document_ref);
      DROP TABLE document_ref;
      DROP FUNCTION validate_document_ref();''')
