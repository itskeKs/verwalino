"""Explicit new mandate sources; retain classified originals and archive evidence."""
from alembic import op

revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None


def _scope_lookup(add):
    from sqlalchemy import text
    original = "IF kind='dunning_notice' THEN"
    extended = "IF kind='mandate_document' THEN SELECT accounting_scope_id INTO sphere FROM public.mandate_document WHERE organization_id=org AND id=resource; RETURN sphere; ELSIF kind='dunning_notice' THEN"
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('public.financial_scope_id(uuid,text,uuid)'::regprocedure)"))
    before, after = (original, extended) if add else (extended, original)
    # An empty development schema may predate this branch within revision 0042.
    if not add and before not in definition and definition.count(original) == 1:
        return
    if definition.count(before) != 1:
        raise RuntimeError('Unexpected financial scope lookup; refusing a partial source adapter')
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute('''
    ALTER TABLE accounting_scope ADD CONSTRAINT uq_scope_mandate_context UNIQUE(organization_id,management_mandate_id,id);
    ALTER TABLE mandate_document ADD COLUMN accounting_scope_id uuid, ADD COLUMN scope_reason text,
      ADD CONSTRAINT ck_mandate_document_scope_reason CHECK((accounting_scope_id IS NULL AND scope_reason IS NULL) OR
        (accounting_scope_id IS NOT NULL AND scope_reason IS NOT NULL AND length(trim(scope_reason)) BETWEEN 1 AND 2000)),
      ADD CONSTRAINT ck_mandate_document_scope_storage CHECK(accounting_scope_id IS NULL OR
        (content_sha256 ~ '^[0-9a-f]{64}$' AND storage_uri=organization_id::text||'/'||left(content_sha256,2)||'/'||content_sha256||'.bin')),
      ADD COLUMN archived_at timestamptz, ADD COLUMN archived_by uuid, ADD COLUMN archive_reason text,
      ADD CONSTRAINT fk_mandate_document_scope FOREIGN KEY(organization_id,management_mandate_id,accounting_scope_id)
        REFERENCES accounting_scope(organization_id,management_mandate_id,id),
      ADD CONSTRAINT fk_mandate_document_archiver FOREIGN KEY(organization_id,archived_by) REFERENCES "user"(organization_id,id),
      ADD CONSTRAINT ck_mandate_document_archive CHECK(
        (archived_at IS NULL AND archived_by IS NULL AND archive_reason IS NULL) OR
        (accounting_scope_id IS NOT NULL AND archived_at IS NOT NULL AND archived_by IS NOT NULL
          AND archive_reason IS NOT NULL AND length(trim(archive_reason)) BETWEEN 1 AND 2000));
    CREATE INDEX ix_mandate_document_scope ON mandate_document(organization_id,accounting_scope_id,id);

    CREATE FUNCTION mandate_source_access(org uuid, sphere uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      IF capability NOT IN ('read','prepare','export') THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF sphere IS NULL THEN RETURN NULLIF(current_setting('app.current_scope_id',true),'') IS NULL; END IF;
      IF NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL
        AND sphere<>NULLIF(current_setting('app.current_scope_id',true),'')::uuid THEN RETURN false; END IF;
      RETURN public.has_scope_capability(org,sphere,'read') AND public.has_scope_capability(org,sphere,capability);
    END $$;
    CREATE FUNCTION mandate_source_audit_access(org uuid, resource uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT public.mandate_source_access(org,(SELECT accounting_scope_id FROM public.mandate_document
        WHERE organization_id=org AND id=resource),'read')
    $$;
    CREATE FUNCTION protect_scoped_mandate_source() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='DELETE' THEN
        IF OLD.accounting_scope_id IS NOT NULL THEN
          RAISE EXCEPTION 'Classified mandate originals must be archived' USING ERRCODE='23514'; END IF;
        RETURN OLD;
      END IF;
      IF TG_OP='UPDATE' AND OLD.accounting_scope_id IS DISTINCT FROM NEW.accounting_scope_id THEN
        RAISE EXCEPTION 'Mandate source context is immutable; legacy classification requires a separate reviewed workflow' USING ERRCODE='23514'; END IF;
      IF NEW.accounting_scope_id IS NULL THEN RETURN NEW; END IF;
      IF NOT public.mandate_source_access(NEW.organization_id,NEW.accounting_scope_id,'prepare') THEN
        RAISE EXCEPTION 'Mandate source preparation capability required' USING ERRCODE='42501'; END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.uploaded_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid OR NEW.archived_at IS NOT NULL THEN
          RAISE EXCEPTION 'A new scoped source needs its current uploader and cannot be archived' USING ERRCODE='23514'; END IF;
      ELSE
        IF (to_jsonb(NEW)-ARRAY['archived_at','archived_by','archive_reason','updated_at'])
          IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['archived_at','archived_by','archive_reason','updated_at']) THEN
          RAISE EXCEPTION 'Classified mandate original is immutable' USING ERRCODE='23514'; END IF;
        IF OLD.archived_at IS NOT NULL OR NEW.archived_at IS NULL
          OR NEW.archived_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
          OR NEW.archive_reason IS NULL OR length(trim(NEW.archive_reason)) NOT BETWEEN 1 AND 2000 THEN
          RAISE EXCEPTION 'Archive requires a first transition, current actor and reason' USING ERRCODE='23514'; END IF;
        NEW.archived_at:=clock_timestamp(); NEW.archive_reason:=trim(NEW.archive_reason);
        INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
          VALUES(NEW.organization_id,NEW.archived_by,'mandate_document',NEW.id,'mandate_document.archived',
            jsonb_build_object('scope_id',NEW.accounting_scope_id,'reason',NEW.archive_reason));
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER protect_scoped_mandate_source BEFORE INSERT OR UPDATE OR DELETE ON mandate_document
      FOR EACH ROW EXECUTE FUNCTION protect_scoped_mandate_source();
    CREATE POLICY scoped_source_read ON mandate_document AS RESTRICTIVE FOR SELECT
      USING(mandate_source_access(organization_id,accounting_scope_id,'read'));
    CREATE POLICY scoped_source_insert ON mandate_document AS RESTRICTIVE FOR INSERT
      WITH CHECK(mandate_source_access(organization_id,accounting_scope_id,'prepare'));
    CREATE POLICY scoped_source_update ON mandate_document AS RESTRICTIVE FOR UPDATE
      USING(mandate_source_access(organization_id,accounting_scope_id,'read'))
      WITH CHECK(mandate_source_access(organization_id,accounting_scope_id,'prepare'));
    CREATE POLICY scoped_source_delete ON mandate_document AS RESTRICTIVE FOR DELETE
      USING(mandate_source_access(organization_id,accounting_scope_id,'read'));
    CREATE POLICY scoped_mandate_audit_read ON audit_event AS RESTRICTIVE FOR SELECT
      USING(entity_type<>'mandate_document' OR mandate_source_audit_access(organization_id,entity_id));
    REVOKE ALL ON FUNCTION mandate_source_access(uuid,uuid,text),mandate_source_audit_access(uuid,uuid),protect_scoped_mandate_source() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION mandate_source_access(uuid,uuid,text),mandate_source_audit_access(uuid,uuid) TO app_rw;
    ''')

    _scope_lookup(True)

def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM mandate_document WHERE accounting_scope_id IS NOT NULL) THEN
      RAISE EXCEPTION 'Preserve classified mandate originals; repair forward instead of downgrade';
      END IF; END $$""")
    _scope_lookup(False)
    op.execute('''
    DROP POLICY scoped_mandate_audit_read ON audit_event;
    DROP TRIGGER protect_scoped_mandate_source ON mandate_document;
    DROP POLICY scoped_source_read ON mandate_document;
    DROP POLICY scoped_source_insert ON mandate_document;
    DROP POLICY scoped_source_update ON mandate_document;
    DROP POLICY scoped_source_delete ON mandate_document;
    DROP FUNCTION protect_scoped_mandate_source(),mandate_source_audit_access(uuid,uuid),mandate_source_access(uuid,uuid,text);
    ALTER TABLE mandate_document DROP COLUMN accounting_scope_id, DROP COLUMN IF EXISTS scope_reason, DROP COLUMN archived_at, DROP COLUMN archived_by, DROP COLUMN archive_reason;
    ALTER TABLE accounting_scope DROP CONSTRAINT uq_scope_mandate_context;
    ''')
