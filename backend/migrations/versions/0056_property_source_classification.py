"""Review bound legacy originals without fabricating historical custody.

Revision ID: 0056
Revises: 0055
"""
from alembic import op

revision='0056'
down_revision='0055'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
    LOCK TABLE property_document,declaration_import,declaration_import_document,
      property_data_domain,property_data_grant IN ACCESS EXCLUSIVE MODE NOWAIT;
    CREATE TABLE property_source_classification(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,
      document_id uuid NOT NULL,property_id uuid NOT NULL,domain_id uuid NOT NULL,grant_id uuid NOT NULL,
      reviewed_by uuid NOT NULL,decision text NOT NULL CHECK(decision IN ('ADMITTED','QUARANTINED')),
      content_sha256 varchar(64) NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
      source_context_digest varchar(64) NOT NULL CHECK(source_context_digest ~ '^[0-9a-f]{64}$'),
      reason varchar(2000) NOT NULL CHECK(reason ~ '[^[:space:]]'),request_key uuid NOT NULL,
      request_digest varchar(64) NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT uq_source_classification_document UNIQUE(organization_id,document_id),
      CONSTRAINT uq_source_classification_request UNIQUE(organization_id,reviewed_by,request_key),
      CONSTRAINT fk_source_classification_document FOREIGN KEY(organization_id,document_id) REFERENCES property_document(organization_id,id),
      CONSTRAINT fk_source_classification_domain FOREIGN KEY(organization_id,property_id,domain_id) REFERENCES property_data_domain(organization_id,property_id,id),
      CONSTRAINT fk_source_classification_grant FOREIGN KEY(organization_id,domain_id,reviewed_by,grant_id) REFERENCES property_data_grant(organization_id,domain_id,user_id,id)
    );
    CREATE INDEX ix_source_classification_domain ON property_source_classification(organization_id,domain_id);
    CREATE INDEX ix_source_classification_grant ON property_source_classification(organization_id,grant_id);

    CREATE FUNCTION property_source_classification_context(org uuid,document uuid,lock_source boolean DEFAULT false)
      RETURNS TABLE(property_id uuid,domain_id uuid,grant_id uuid,content_sha256 text,source_context_digest text,
        source_uploaded_by uuid,linked_import_count bigint,can_admit boolean,quiescent boolean)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      source public.property_document; domain uuid; permitted_grant uuid; before_links uuid[]; after_links uuid[];
      imports jsonb; coherent boolean; quiet boolean;
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.property_source_actor_role(org,actor) IS DISTINCT FROM 'OWNER_ADMIN' THEN RETURN; END IF;
      SELECT * INTO source FROM public.property_document d WHERE d.organization_id=org AND d.id=document;
      IF NOT FOUND OR source.property_id IS NULL THEN RETURN; END IF;
      SELECT d.id,g.id INTO domain,permitted_grant FROM public.property_data_domain d JOIN public.property_data_grant g
        ON g.organization_id=d.organization_id AND g.domain_id=d.id
        WHERE d.organization_id=org AND d.property_id=source.property_id AND g.user_id=actor
          AND ARRAY['read','prepare','approve']::varchar[] <@ g.capabilities AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp());
      IF NOT FOUND THEN RETURN; END IF;
      IF lock_source THEN
        SELECT array_agg(l.import_id ORDER BY l.import_id) INTO before_links FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document;
        PERFORM 1 FROM public.declaration_import i WHERE i.organization_id=org AND i.id=ANY(before_links) ORDER BY i.id FOR UPDATE;
        SELECT * INTO source FROM public.property_document d WHERE d.organization_id=org AND d.id=document FOR UPDATE;
        IF NOT FOUND THEN RETURN; END IF;
        SELECT array_agg(l.import_id ORDER BY l.import_id) INTO after_links FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document;
        IF before_links IS DISTINCT FROM after_links THEN RAISE EXCEPTION 'Source links changed during review' USING ERRCODE='40001'; END IF;
      END IF;
      -- A grant can expire while waiting for a row even though ACL mutation is locked.
      IF NOT EXISTS(SELECT 1 FROM public.property_data_grant g WHERE g.organization_id=org AND g.id=permitted_grant
        AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp())) THEN RETURN; END IF;
      SELECT COALESCE(jsonb_agg(jsonb_build_object('id',i.id,'link_id',l.id,'document_role',l.document_role,'sequence_no',l.sequence_no,'mode',i.mode,'target',i.target_property_id,
          'applied',i.applied_property_id,'status',i.status,'lease_expires_at',i.lease_expires_at) ORDER BY i.id),'[]'::jsonb),
        COALESCE(bool_and(COALESCE(i.status='APPLIED' AND i.applied_property_id=source.property_id
          AND (i.mode='NEW_PROPERTY' OR i.target_property_id=source.property_id),false)),true),
        COALESCE(bool_and(i.status IN ('DRAFT','NEEDS_REVIEW','FAILED','CANCELLED','APPLIED')
          AND (i.lease_expires_at IS NULL OR i.lease_expires_at<=clock_timestamp())),true)
        INTO imports,coherent,quiet FROM public.declaration_import_document l JOIN public.declaration_import i
          ON i.organization_id=l.organization_id AND i.id=l.import_id WHERE l.organization_id=org AND l.document_id=document;
      RETURN QUERY SELECT source.property_id,domain,permitted_grant,source.content_sha256::text,
        encode(sha256(convert_to(jsonb_build_object('document_id',source.id,'property_id',source.property_id,
          'uploaded_by',source.uploaded_by,'content_sha256',source.content_sha256,'archived_at',source.archived_at,'imports',imports)::text,'UTF8')),'hex'),
        source.uploaded_by,jsonb_array_length(imports)::bigint,
        COALESCE(source.uploaded_by=actor AND source.archived_at IS NULL AND jsonb_array_length(imports)<=1 AND coherent AND quiet,false),quiet;
    END $$;
    CREATE FUNCTION property_source_classification_access(org uuid,document uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public.property_source_classification_context(org,document,false))
    $$;
    CREATE FUNCTION property_source_classification_content_access(org uuid,document uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public.property_source_classification_context(org,document,false) c
        JOIN public.property_document d ON d.organization_id=org AND d.id=document
        WHERE c.can_admit AND c.source_uploaded_by=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND d.archived_at IS NULL)
    $$;
    REVOKE ALL ON FUNCTION property_source_classification_context(uuid,uuid,boolean),property_source_classification_access(uuid,uuid),
      property_source_classification_content_access(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_classification_context(uuid,uuid,boolean),property_source_classification_access(uuid,uuid),
      property_source_classification_content_access(uuid,uuid) TO app_rw;

    CREATE FUNCTION property_source_classification_candidates(org uuid,after_id uuid,page_size integer,search_id text)
      RETURNS TABLE(document_id uuid,property_id uuid,content_sha256 text,source_context_digest text,source_uploaded_by uuid,
        linked_import_count bigint,can_admit boolean,quiescent boolean)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF page_size IS NULL OR page_size NOT BETWEEN 1 AND 201 OR search_id IS NULL OR length(search_id)>100 THEN
        RAISE EXCEPTION 'Invalid source classification page' USING ERRCODE='22023'; END IF;
      RETURN QUERY SELECT d.id,c.property_id,c.content_sha256,c.source_context_digest,c.source_uploaded_by,c.linked_import_count,c.can_admit,c.quiescent
        FROM public.property_document d CROSS JOIN LATERAL public.property_source_classification_context(org,d.id,false) c
        WHERE d.organization_id=org AND (after_id IS NULL OR d.id>after_id) AND position(lower(search_id) IN d.id::text)>0
          AND NOT EXISTS(SELECT 1 FROM public.property_source_classification r WHERE r.organization_id=org AND r.document_id=d.id)
        ORDER BY d.id LIMIT page_size;
    END $$;
    REVOKE ALL ON FUNCTION property_source_classification_candidates(uuid,uuid,integer,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_classification_candidates(uuid,uuid,integer,text) TO app_rw;

    ALTER TABLE property_source_classification ENABLE ROW LEVEL SECURITY;
    ALTER TABLE property_source_classification FORCE ROW LEVEL SECURITY;
    CREATE POLICY source_classification_read ON property_source_classification FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND has_property_data_capability(organization_id,domain_id,'read'));
    CREATE POLICY source_classification_insert ON property_source_classification FOR INSERT WITH CHECK(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND reviewed_by=NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    REVOKE ALL ON property_source_classification FROM PUBLIC,app_rw;
    GRANT SELECT,INSERT ON property_source_classification TO app_rw;

    CREATE FUNCTION guard_property_source_classification() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context record;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Source classification is immutable'; END IF;
      IF public.property_source_security_mode(NEW.organization_id)='PAUSED' THEN
        RAISE EXCEPTION 'Source classification requires active effects' USING ERRCODE='42501'; END IF;
      IF NEW.reviewed_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Source classification requires current reviewer' USING ERRCODE='42501'; END IF;
      SELECT * INTO context FROM public.property_source_classification_context(NEW.organization_id,NEW.document_id,true);
      IF NOT FOUND THEN RAISE EXCEPTION 'Source classification requires current authority' USING ERRCODE='42501'; END IF;
      IF ROW(NEW.property_id,NEW.domain_id,NEW.grant_id,NEW.content_sha256,NEW.source_context_digest) IS DISTINCT FROM
          ROW(context.property_id,context.domain_id,context.grant_id,context.content_sha256,context.source_context_digest)
        OR NOT context.quiescent THEN
        RAISE EXCEPTION 'Source classification context changed' USING ERRCODE='40001'; END IF;
      IF NEW.decision='ADMITTED' AND NOT context.can_admit THEN
        RAISE EXCEPTION 'Source custody or topology cannot be admitted' USING ERRCODE='42501'; END IF;
      NEW.reason:=btrim(NEW.reason);
      NEW.request_digest:=encode(sha256(convert_to(jsonb_build_object('document_id',NEW.document_id,'property_id',NEW.property_id,
        'content_sha256',NEW.content_sha256,'source_context_digest',NEW.source_context_digest,'decision',NEW.decision,'reason',NEW.reason)::text,'UTF8')),'hex');
      NEW.created_at:=clock_timestamp();
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,NEW.reviewed_by,'property_document',NEW.document_id,'property_document.classified',
          jsonb_build_object('classification_id',NEW.id,'property_id',NEW.property_id,'domain_id',NEW.domain_id,'grant_id',NEW.grant_id,
            'decision',NEW.decision,'content_sha256',NEW.content_sha256,'source_context_digest',NEW.source_context_digest,'reason',NEW.reason));
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_property_source_classification BEFORE INSERT OR UPDATE OR DELETE ON property_source_classification
      FOR EACH ROW EXECUTE FUNCTION guard_property_source_classification();

    CREATE FUNCTION guard_classified_source_links() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context record;
    BEGIN
      IF TG_OP='DELETE' THEN context:=OLD; ELSE context:=NEW; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||context.organization_id::text,0));
      PERFORM 1 FROM public.declaration_import i WHERE i.organization_id=context.organization_id AND i.id=context.import_id FOR UPDATE;
      PERFORM 1 FROM public.property_document d WHERE d.organization_id=context.organization_id AND d.id=context.document_id FOR UPDATE;
      IF EXISTS(SELECT 1 FROM public.property_source_classification c WHERE c.organization_id=context.organization_id AND c.document_id=context.document_id) THEN
        RAISE EXCEPTION 'Classified source links are immutable'; END IF;
      RETURN context;
    END $$;
    CREATE TRIGGER a_lock_classified_source_link BEFORE UPDATE OR DELETE ON declaration_import_document
      FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding();
    CREATE TRIGGER guard_classified_source_links BEFORE INSERT OR UPDATE OR DELETE ON declaration_import_document
      FOR EACH ROW EXECUTE FUNCTION guard_classified_source_links();
    REVOKE ALL ON FUNCTION guard_property_source_classification(),guard_classified_source_links() FROM PUBLIC,app_rw;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_classification) THEN
      RAISE EXCEPTION 'Retain populated source classification evidence'; END IF; END $$;
      DROP TRIGGER guard_classified_source_links ON declaration_import_document;
      DROP TRIGGER a_lock_classified_source_link ON declaration_import_document;
      DROP FUNCTION guard_classified_source_links();
      DROP TABLE property_source_classification;
      DROP FUNCTION property_source_classification_candidates(uuid,uuid,integer,text),guard_property_source_classification(),property_source_classification_content_access(uuid,uuid),
        property_source_classification_access(uuid,uuid),property_source_classification_context(uuid,uuid,boolean);
    """)
