"""Record contemporary source-admission review without fabricating attachment history.

Revision ID: 0052
Revises: 0051
"""
from alembic import op

revision='0052'
down_revision='0051'
branch_labels=None
depends_on=None

PREVIOUS_IMPORT_ACCESS = """
CREATE OR REPLACE FUNCTION property_source_import_actor_access(org uuid, actor uuid, job uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text; destination uuid; creator uuid; mode text; publication uuid; allowed boolean;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL THEN RETURN false; END IF;
      SELECT COALESCE(i.applied_property_id,i.target_property_id),i.initiated_by,i.mode,i.publication_grant_id
        INTO destination,creator,mode,publication FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
      IF NOT FOUND THEN RETURN false; END IF;
      IF destination IS NULL THEN
        allowed:=mode='NEW_PROPERTY' AND creator=actor AND capability IN ('read','prepare','export')
          AND (capability<>'prepare' OR role IN ('OWNER_ADMIN','CLERK'));
      ELSE
        allowed:=public.property_data_property_actor_access(org,actor,destination,capability);
      END IF;
      IF NOT COALESCE(allowed,false) THEN RETURN false; END IF;
      -- No recursive call to document access: inspect only immutable local
      -- audience metadata and exact link topology, including archived sources.
      RETURN NOT EXISTS(SELECT 1 FROM public.declaration_import_document l
        JOIN public.property_document d ON d.organization_id=l.organization_id AND d.id=l.document_id
        WHERE l.organization_id=org AND l.import_id=job AND (
          (SELECT count(*) FROM public.declaration_import_document other
            WHERE other.organization_id=org AND other.document_id=d.id)<>1
          OR (d.property_id IS NOT NULL AND d.property_id IS DISTINCT FROM destination)
          OR (d.property_id IS NULL AND (d.uploaded_by IS NULL
            OR (destination IS NULL AND d.uploaded_by IS DISTINCT FROM creator)
            OR (destination IS NOT NULL AND NOT (
              (mode='NEW_PROPERTY' AND publication IS NOT NULL AND d.uploaded_by=creator)
              OR EXISTS(SELECT 1 FROM public.property_source_admission a
                WHERE a.organization_id=org AND a.import_id=job AND a.document_id=d.id AND a.property_id=destination)))))));
    END $$;
"""

IMPORT_ACCESS = """
CREATE OR REPLACE FUNCTION property_source_import_actor_access(org uuid, actor uuid, job uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text; destination uuid; creator uuid; mode text; publication uuid; allowed boolean;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL THEN RETURN false; END IF;
      SELECT COALESCE(i.applied_property_id,i.target_property_id),i.initiated_by,i.mode,i.publication_grant_id
        INTO destination,creator,mode,publication FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
      IF NOT FOUND THEN RETURN false; END IF;
      IF destination IS NULL THEN
        allowed:=mode='NEW_PROPERTY' AND creator=actor AND capability IN ('read','prepare','export')
          AND (capability<>'prepare' OR role IN ('OWNER_ADMIN','CLERK'));
      ELSE
        allowed:=public.property_data_property_actor_access(org,actor,destination,capability);
      END IF;
      IF NOT COALESCE(allowed,false) THEN RETURN false; END IF;
      -- No recursive call to document access: inspect only immutable local
      -- audience metadata and exact link topology, including archived sources.
      RETURN NOT EXISTS(SELECT 1 FROM public.declaration_import_document l
        JOIN public.property_document d ON d.organization_id=l.organization_id AND d.id=l.document_id
        WHERE l.organization_id=org AND l.import_id=job AND (
          (SELECT count(*) FROM public.declaration_import_document other
            WHERE other.organization_id=org AND other.document_id=d.id)<>1
          OR (d.property_id IS NOT NULL AND d.property_id IS DISTINCT FROM destination)
          OR (d.property_id IS NULL AND (d.uploaded_by IS NULL
            OR (destination IS NULL AND d.uploaded_by IS DISTINCT FROM creator)
            OR (destination IS NOT NULL AND NOT (
              (mode='NEW_PROPERTY' AND publication IS NOT NULL AND d.uploaded_by=creator)
              OR EXISTS(SELECT 1 FROM public.property_source_admission a
                WHERE a.organization_id=org AND a.import_id=job AND a.document_id=d.id AND a.property_id=destination)
              OR EXISTS(SELECT 1 FROM public.property_source_admission_review r
                WHERE r.organization_id=org AND r.import_id=job AND r.document_id=d.id AND r.property_id=destination)))))));
    END $$;
"""


def upgrade():
    op.execute("""
    LOCK TABLE declaration_import, declaration_import_document, property_document,
      property_data_domain, property_data_grant IN ACCESS EXCLUSIVE MODE NOWAIT;
    CREATE TABLE property_source_admission_review(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      import_id uuid NOT NULL, document_id uuid NOT NULL, property_id uuid NOT NULL,
      domain_id uuid NOT NULL, grant_id uuid NOT NULL, reviewed_by uuid NOT NULL,
      draft_revision integer NOT NULL CHECK(draft_revision>=0), content_sha256 varchar(64) NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
      reason varchar(2000) NOT NULL CHECK(reason ~ '[^[:space:]]'), request_key uuid NOT NULL,
      request_digest varchar(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT uq_property_source_review_document UNIQUE(organization_id,document_id),
      CONSTRAINT uq_property_source_review_request UNIQUE(organization_id,reviewed_by,request_key),
      CONSTRAINT fk_property_source_review_link FOREIGN KEY(organization_id,import_id,document_id)
        REFERENCES declaration_import_document(organization_id,import_id,document_id),
      CONSTRAINT fk_property_source_review_domain FOREIGN KEY(organization_id,property_id,domain_id)
        REFERENCES property_data_domain(organization_id,property_id,id),
      CONSTRAINT fk_property_source_review_grant FOREIGN KEY(organization_id,domain_id,reviewed_by,grant_id)
        REFERENCES property_data_grant(organization_id,domain_id,user_id,id)
    );
    CREATE INDEX ix_property_source_review_import ON property_source_admission_review(organization_id,import_id);
    CREATE INDEX ix_property_source_review_domain ON property_source_admission_review(organization_id,domain_id);
    CREATE INDEX ix_property_source_review_grant ON property_source_admission_review(organization_id,grant_id);

    CREATE FUNCTION property_source_review_access(org uuid,document uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF NOT COALESCE(public.property_source_actor_role(org,actor) IN ('OWNER_ADMIN','CLERK'),false) THEN RETURN false; END IF;
      RETURN EXISTS(SELECT 1 FROM public.property_document d
        JOIN public.declaration_import_document l ON l.organization_id=d.organization_id AND l.document_id=d.id
        JOIN public.declaration_import i ON i.organization_id=l.organization_id AND i.id=l.import_id
        JOIN public.property_data_domain domain ON domain.organization_id=i.organization_id AND domain.property_id=i.target_property_id
        JOIN public.property_data_grant g ON g.organization_id=domain.organization_id AND g.domain_id=domain.id AND g.user_id=actor
        WHERE d.organization_id=org AND d.id=document AND d.uploaded_by=actor AND d.property_id IS NULL AND d.archived_at IS NULL
          AND i.mode='EXISTING_PROPERTY' AND i.applied_property_id IS NULL
          AND (SELECT count(*) FROM public.declaration_import_document other WHERE other.organization_id=org AND other.document_id=document)=1
          AND ARRAY['read','prepare','approve']::varchar[] <@ g.capabilities AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp()));
    END $$;

    CREATE FUNCTION property_source_review_context(org uuid,document uuid,lock_import boolean DEFAULT false)
    RETURNS TABLE(import_id uuid,property_id uuid,domain_id uuid,grant_id uuid,draft_revision integer,status text,lease_expires_at timestamptz)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF NOT public.property_source_review_access(org,document) THEN RETURN; END IF;
      IF lock_import THEN
        PERFORM 1 FROM public.declaration_import i JOIN public.declaration_import_document l
          ON l.organization_id=i.organization_id AND l.import_id=i.id
          WHERE l.organization_id=org AND l.document_id=document FOR UPDATE OF i;
        PERFORM 1 FROM public.property_document d WHERE d.organization_id=org AND d.id=document FOR UPDATE;
        IF NOT public.property_source_review_access(org,document) THEN RETURN; END IF;
      END IF;
      RETURN QUERY SELECT i.id,i.target_property_id,domain.id,g.id,i.draft_revision,i.status::text,i.lease_expires_at
        FROM public.declaration_import_document l JOIN public.declaration_import i ON i.organization_id=l.organization_id AND i.id=l.import_id
        JOIN public.property_data_domain domain ON domain.organization_id=i.organization_id AND domain.property_id=i.target_property_id
        JOIN public.property_data_grant g ON g.organization_id=domain.organization_id AND g.domain_id=domain.id
        WHERE l.organization_id=org AND l.document_id=document AND g.user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
          AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp());
    END $$;
    CREATE FUNCTION property_source_review_pending(org uuid,document uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT EXISTS(SELECT 1 FROM public.property_source_review_context(org,document,false) c
        WHERE c.status IN ('DRAFT','NEEDS_REVIEW','FAILED','CANCELLED')
          AND (c.lease_expires_at IS NULL OR c.lease_expires_at<=clock_timestamp()))
        AND NOT EXISTS(SELECT 1 FROM public.property_source_admission a WHERE a.organization_id=org AND a.document_id=document)
        AND NOT EXISTS(SELECT 1 FROM public.property_source_admission_review r WHERE r.organization_id=org AND r.document_id=document)
    $$;
    REVOKE ALL ON FUNCTION property_source_review_access(uuid,uuid),property_source_review_context(uuid,uuid,boolean) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_review_access(uuid,uuid),property_source_review_context(uuid,uuid,boolean) TO app_rw;
    REVOKE ALL ON FUNCTION property_source_review_pending(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_review_pending(uuid,uuid) TO app_rw;

    ALTER TABLE property_source_admission_review ENABLE ROW LEVEL SECURITY;
    ALTER TABLE property_source_admission_review FORCE ROW LEVEL SECURITY;
    CREATE POLICY property_source_review_read ON property_source_admission_review FOR SELECT USING(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND
      (property_source_access(organization_id,'property_document',document_id,'read') OR property_source_review_access(organization_id,document_id)));
    CREATE POLICY property_source_review_insert ON property_source_admission_review FOR INSERT WITH CHECK(
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid AND reviewed_by=NULLIF(current_setting('app.current_user_id',true),'')::uuid);
    REVOKE ALL ON property_source_admission_review FROM PUBLIC,app_rw;
    GRANT SELECT,INSERT ON property_source_admission_review TO app_rw;

    CREATE FUNCTION guard_property_source_admission_review() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE context record; source_hash text;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Source admission review is immutable'; END IF;
      IF NEW.reviewed_by IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Source admission review requires current authority' USING ERRCODE='42501'; END IF;
      SELECT * INTO context FROM public.property_source_review_context(NEW.organization_id,NEW.document_id,true);
      IF NOT FOUND THEN RAISE EXCEPTION 'Source admission review requires current authority' USING ERRCODE='42501'; END IF;
      SELECT d.content_sha256 INTO source_hash FROM public.property_document d WHERE d.organization_id=NEW.organization_id AND d.id=NEW.document_id;
      IF ROW(NEW.import_id,NEW.property_id,NEW.domain_id,NEW.grant_id,NEW.draft_revision) IS DISTINCT FROM
          ROW(context.import_id,context.property_id,context.domain_id,context.grant_id,context.draft_revision)
          OR NEW.content_sha256 IS DISTINCT FROM source_hash THEN
        RAISE EXCEPTION 'Source admission review context is stale' USING ERRCODE='40001'; END IF;
      IF context.status NOT IN ('DRAFT','NEEDS_REVIEW','FAILED','CANCELLED')
          OR context.lease_expires_at>clock_timestamp() THEN
        RAISE EXCEPTION 'Source admission review requires a quiescent import' USING ERRCODE='40001'; END IF;
      IF EXISTS(SELECT 1 FROM public.property_source_admission a WHERE a.organization_id=NEW.organization_id AND a.document_id=NEW.document_id) THEN
        RAISE EXCEPTION 'Original already has attachment admission proof' USING ERRCODE='23505'; END IF;
      NEW.reason:=btrim(NEW.reason);
      NEW.request_digest:=encode(sha256(convert_to(jsonb_build_object('document_id',NEW.document_id,'import_id',NEW.import_id,
        'draft_revision',NEW.draft_revision,'content_sha256',NEW.content_sha256,'reason',NEW.reason)::text,'UTF8')),'hex');
      NEW.created_at:=clock_timestamp();
      INSERT INTO public.audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES(NEW.organization_id,NEW.reviewed_by,'property_document',NEW.document_id,'property_document.admission_reviewed',
          jsonb_build_object('review_id',NEW.id,'import_id',NEW.import_id,'property_id',NEW.property_id,'domain_id',NEW.domain_id,
            'grant_id',NEW.grant_id,'draft_revision',NEW.draft_revision,'content_sha256',NEW.content_sha256,'reason',NEW.reason));
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_property_source_admission_review BEFORE INSERT OR UPDATE OR DELETE ON property_source_admission_review
      FOR EACH ROW EXECUTE FUNCTION guard_property_source_admission_review();

    CREATE FUNCTION guard_reviewed_source_destination() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_TABLE_NAME='property_document' THEN
        IF NEW.property_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.property_source_admission_review r
          WHERE r.organization_id=NEW.organization_id AND r.document_id=NEW.id AND r.property_id<>NEW.property_id) THEN
          RAISE EXCEPTION 'Original binding differs from its reviewed property'; END IF;
      ELSE
        -- Serialize with review and binding before checking the retained proof;
        -- checking before the FK's later row lock would permit a stale snapshot.
        PERFORM 1 FROM public.property_document d WHERE d.organization_id=NEW.organization_id AND d.id=NEW.document_id FOR UPDATE;
        IF EXISTS(SELECT 1 FROM public.property_source_admission_review r WHERE r.organization_id=NEW.organization_id
          AND r.document_id=NEW.document_id AND r.import_id<>NEW.import_id) THEN
          RAISE EXCEPTION 'Reviewed original cannot enter another import'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_reviewed_source_destination BEFORE UPDATE OF property_id ON property_document
      FOR EACH ROW EXECUTE FUNCTION guard_reviewed_source_destination();
    CREATE TRIGGER guard_reviewed_source_link BEFORE INSERT ON declaration_import_document
      FOR EACH ROW EXECUTE FUNCTION guard_reviewed_source_destination();
    REVOKE ALL ON FUNCTION guard_property_source_admission_review(),guard_reviewed_source_destination() FROM PUBLIC,app_rw;

    -- Prepared proof check only. Activation must attach the rejecting binding
    -- guard and classify already-bound legacy sources before enforcing policies.
    CREATE FUNCTION property_source_binding_proven(org uuid,document uuid,target uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT COALESCE(org=NULLIF(current_setting('app.current_org_id',true),'')::uuid,false) AND EXISTS(
        SELECT 1 FROM public.property_document d JOIN public.declaration_import_document l
          ON l.organization_id=d.organization_id AND l.document_id=d.id
        JOIN public.declaration_import i ON i.organization_id=l.organization_id AND i.id=l.import_id
        WHERE d.organization_id=org AND d.id=document AND d.property_id IS NULL
          AND COALESCE(i.applied_property_id,i.target_property_id)=target
          AND (SELECT count(*) FROM public.declaration_import_document other WHERE other.organization_id=org AND other.document_id=document)=1
          AND ((i.mode='NEW_PROPERTY' AND i.publication_grant_id IS NOT NULL AND d.uploaded_by=i.initiated_by)
            OR (i.mode='EXISTING_PROPERTY' AND i.target_property_id=target AND
              (EXISTS(SELECT 1 FROM public.property_source_admission a WHERE a.organization_id=org AND a.import_id=i.id AND a.document_id=document AND a.property_id=target)
               OR EXISTS(SELECT 1 FROM public.property_source_admission_review r WHERE r.organization_id=org AND r.import_id=i.id AND r.document_id=document AND r.property_id=target)))))
    $$;
    REVOKE ALL ON FUNCTION property_source_binding_proven(uuid,uuid,uuid) FROM PUBLIC,app_rw;
    """)
    op.execute(IMPORT_ACCESS)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_admission_review) THEN
      RAISE EXCEPTION 'Retain populated source admission reviews'; END IF; END $$""")
    op.execute(PREVIOUS_IMPORT_ACCESS)
    op.execute("""
      DROP TRIGGER guard_reviewed_source_destination ON property_document;
      DROP TRIGGER guard_reviewed_source_link ON declaration_import_document;
      DROP FUNCTION guard_reviewed_source_destination(),property_source_binding_proven(uuid,uuid,uuid);
      DROP TABLE property_source_admission_review;
      DROP FUNCTION guard_property_source_admission_review(),property_source_review_pending(uuid,uuid),property_source_review_context(uuid,uuid,boolean),property_source_review_access(uuid,uuid);
    """)
