"""Require explicit export authority before returning property-source bytes.

Revision ID: 0058
Revises: 0057
"""
from alembic import op

revision = '0058'
down_revision = '0057'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    LOCK TABLE property_document,declaration_import,declaration_import_document,
      property_source_classification,property_source_security IN ACCESS EXCLUSIVE MODE NOWAIT;
    CREATE FUNCTION property_source_export_access(org uuid,document uuid,purpose text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; target uuid; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF purpose IS NULL OR purpose NOT IN ('SOURCE','ADMISSION_REVIEW','CLASSIFICATION_REVIEW') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL OR public.property_source_actor_role(org,actor) IS NULL THEN RETURN false; END IF;
      IF purpose='SOURCE' THEN
        RETURN public.property_source_permitted(org,'property_document',document,'read')
          AND public.property_source_permitted(org,'property_document',document,'export');
      END IF;
      -- Review is an explicit, own-custody exception for originals that cannot
      -- yet satisfy ordinary source proof. It never skips destination export.
      IF purpose='ADMISSION_REVIEW' THEN
        SELECT c.property_id INTO target FROM public.property_source_review_context(org,document,false) c;
      ELSE
        SELECT c.property_id INTO target FROM public.property_source_classification_context(org,document,false) c WHERE c.can_admit;
      END IF;
      IF target IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      IF EXISTS(SELECT 1 FROM public.property_source_classification c
        WHERE c.organization_id=org AND c.document_id=document AND c.decision='QUARANTINED') THEN RETURN false; END IF;
      RETURN public.property_data_property_actor_access(org,actor,target,'export');
    END $$;
    REVOKE ALL ON FUNCTION property_source_export_access(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_export_access(uuid,uuid,text) TO app_rw;
    """)


def downgrade():
    op.execute("""
    LOCK TABLE property_source_security IN ACCESS EXCLUSIVE MODE NOWAIT;
    DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict source export checks'; END IF; END $$;
    DROP FUNCTION property_source_export_access(uuid,uuid,text);
    """)
