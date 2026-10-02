"""Resolve strict bound originals from retained proof and current classification.

Revision ID: 0057
Revises: 0056
"""
from alembic import op

revision = '0057'
down_revision = '0056'
branch_labels = None
depends_on = None

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
                WHERE a.organization_id=org AND a.import_id=job AND a.document_id=d.id AND a.property_id=destination)
              OR EXISTS(SELECT 1 FROM public.property_source_admission_review r
                WHERE r.organization_id=org AND r.import_id=job AND r.document_id=d.id AND r.property_id=destination)))))));
    END $$;
"""

PREVIOUS_DOCUMENT_ACCESS = """    CREATE OR REPLACE FUNCTION property_source_document_actor_access(org uuid, actor uuid, document uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE role text; property uuid; creator uuid; job uuid; links bigint;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL THEN RETURN false; END IF;
      SELECT d.property_id,d.uploaded_by INTO property,creator FROM public.property_document d
        WHERE d.organization_id=org AND d.id=document;
      IF NOT FOUND THEN RETURN false; END IF;
      SELECT count(*) INTO links FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document;
      IF links>1 THEN RETURN false; END IF;
      IF links=1 THEN
        SELECT l.import_id INTO job FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document;
        RETURN public.property_source_import_actor_access(org,actor,job,capability);
      ELSIF property IS NOT NULL THEN
        RETURN public.property_data_property_actor_access(org,actor,property,capability);
      END IF;
      RETURN COALESCE(creator=actor AND capability IN ('read','prepare','export')
        AND (capability<>'prepare' OR role IN ('OWNER_ADMIN','CLERK')),false);
    END $$;

"""


def upgrade():
    op.execute("""
    LOCK TABLE property_document,declaration_import,declaration_import_document,
      property_source_admission,property_source_admission_review,property_source_classification,
      property_source_security IN ACCESS EXCLUSIVE MODE NOWAIT;

    -- A private metadata predicate, independent of the current reviewer's role or
    -- grant. Historic proof persists; the consuming actor's rights are separate.
    CREATE FUNCTION property_source_bound_proven(org uuid,document uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source public.property_document; classification public.property_source_classification;
      links bigint; imports jsonb; context_digest text;
    BEGIN
      SELECT * INTO source FROM public.property_document d WHERE d.organization_id=org AND d.id=document;
      IF NOT FOUND OR source.property_id IS NULL THEN RETURN false; END IF;
      SELECT * INTO classification FROM public.property_source_classification c WHERE c.organization_id=org AND c.document_id=document;
      IF FOUND AND classification.decision='QUARANTINED' THEN RETURN false; END IF;
      SELECT count(*) INTO links FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document;
      IF links>1 THEN RETURN false; END IF;
      IF links=1 AND NOT EXISTS(SELECT 1 FROM public.declaration_import_document l JOIN public.declaration_import i
        ON i.organization_id=l.organization_id AND i.id=l.import_id WHERE l.organization_id=org AND l.document_id=document
          AND i.status='APPLIED' AND i.applied_property_id=source.property_id
          AND (i.mode='NEW_PROPERTY' OR i.target_property_id=source.property_id)) THEN RETURN false; END IF;
      IF classification.decision='ADMITTED' AND classification.property_id=source.property_id
        AND classification.content_sha256=source.content_sha256 AND classification.reviewed_by=source.uploaded_by THEN
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id',i.id,'link_id',l.id,'document_role',l.document_role,'sequence_no',l.sequence_no,
          'mode',i.mode,'target',i.target_property_id,'applied',i.applied_property_id,'status',i.status,
          'lease_expires_at',i.lease_expires_at) ORDER BY i.id),'[]'::jsonb)
          INTO imports FROM public.declaration_import_document l JOIN public.declaration_import i
          ON i.organization_id=l.organization_id AND i.id=l.import_id WHERE l.organization_id=org AND l.document_id=document;
        context_digest:=encode(sha256(convert_to(jsonb_build_object('document_id',source.id,'property_id',source.property_id,
          'uploaded_by',source.uploaded_by,'content_sha256',source.content_sha256,'archived_at',source.archived_at,'imports',imports)::text,'UTF8')),'hex');
        IF classification.source_context_digest=context_digest THEN RETURN true; END IF;
      END IF;
      RETURN EXISTS(SELECT 1 FROM public.declaration_import_document l JOIN public.declaration_import i
        ON i.organization_id=l.organization_id AND i.id=l.import_id WHERE l.organization_id=org AND l.document_id=document
          AND i.applied_property_id=source.property_id AND i.status='APPLIED' AND (
            (i.mode='NEW_PROPERTY' AND i.publication_grant_id IS NOT NULL AND i.publication_domain_id IS NOT NULL
              AND i.approved_by=source.uploaded_by AND i.initiated_by=source.uploaded_by)
            OR (i.mode='EXISTING_PROPERTY' AND i.target_property_id=source.property_id AND (
              EXISTS(SELECT 1 FROM public.property_source_admission a WHERE a.organization_id=org AND a.import_id=i.id
                AND a.document_id=document AND a.property_id=source.property_id AND a.admitted_by=source.uploaded_by)
              OR EXISTS(SELECT 1 FROM public.property_source_admission_review r WHERE r.organization_id=org AND r.import_id=i.id
                AND r.document_id=document AND r.property_id=source.property_id AND r.reviewed_by=source.uploaded_by
                AND r.content_sha256=source.content_sha256)))));
    END $$;
    REVOKE ALL ON FUNCTION property_source_bound_proven(uuid,uuid) FROM PUBLIC,app_rw;

    -- Classifications now change strict authority. Serialize their commit with
    -- all in-flight source checks, before inspecting or locking source rows.
    CREATE FUNCTION lock_source_classification_authority() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NOT NULL THEN PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0)); END IF;
      RETURN NULL;
    END $$;
    CREATE TRIGGER a_lock_source_classification_authority BEFORE INSERT ON property_source_classification
      FOR EACH STATEMENT EXECUTE FUNCTION lock_source_classification_authority();
    REVOKE ALL ON FUNCTION lock_source_classification_authority() FROM PUBLIC,app_rw;
    """)
    op.execute(PREVIOUS_IMPORT_ACCESS.replace(
        'SET search_path=pg_catalog,pg_temp AS', 'SET search_path=pg_catalog,pg_temp SET row_security=off AS'
    ).replace(
        'OR (d.property_id IS NOT NULL AND d.property_id IS DISTINCT FROM destination)',
        "OR (d.property_id IS NOT NULL AND (d.property_id IS DISTINCT FROM destination OR "
        "(public.property_source_security_mode(org) IS DISTINCT FROM 'PREPARATION' "
        "AND NOT public.property_source_bound_proven(org,d.id))))"
    ))
    op.execute(PREVIOUS_DOCUMENT_ACCESS.replace(
        'SET search_path=pg_catalog,pg_temp AS', 'SET search_path=pg_catalog,pg_temp SET row_security=off AS'
    ).replace(
        'IF NOT FOUND THEN RETURN false; END IF;',
        "IF NOT FOUND THEN RETURN false; END IF;\n"
        "      IF property IS NOT NULL AND public.property_source_security_mode(org) IS DISTINCT FROM 'PREPARATION' "
        "AND NOT public.property_source_bound_proven(org,document) THEN RETURN false; END IF;"
    ))


def downgrade():
    op.execute("""
    LOCK TABLE property_document,declaration_import,declaration_import_document,
      property_source_admission,property_source_admission_review,property_source_classification,
      property_source_security IN ACCESS EXCLUSIVE MODE NOWAIT;
    DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict bound source resolution'; END IF; END $$;
    """)
    op.execute(PREVIOUS_IMPORT_ACCESS)
    op.execute(PREVIOUS_DOCUMENT_ACCESS)
    op.execute("""
    DROP TRIGGER a_lock_source_classification_authority ON property_source_classification;
    DROP FUNCTION lock_source_classification_authority(),property_source_bound_proven(uuid,uuid);
    """)
