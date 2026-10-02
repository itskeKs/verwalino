"""Protect ordinary original/import rows and retain explicit review channels."""
import importlib
from alembic import op

revision = '0063'
down_revision = '0062'
branch_labels = None
depends_on = None

TABLES = ('property_document','declaration_import','declaration_import_document',
          'property_source_security','property_source_admission_review','property_source_classification')
# Preserve the exact previous resolver for rollback, including 0057's strict
# bound-proof addition. The new context variant shares this same source logic.
PREVIOUS_IMPORT = importlib.import_module('migrations.versions.0057_bound_source_access').PREVIOUS_IMPORT_ACCESS.replace(
    'SET search_path=pg_catalog,pg_temp AS', 'SET search_path=pg_catalog,pg_temp SET row_security=off AS'
).replace(
    'OR (d.property_id IS NOT NULL AND d.property_id IS DISTINCT FROM destination)',
    "OR (d.property_id IS NOT NULL AND (d.property_id IS DISTINCT FROM destination OR "
    "(public.property_source_security_mode(org) IS DISTINCT FROM 'PREPARATION' "
    "AND NOT public.property_source_bound_proven(org,d.id))))"
)
IMPORT_LOOKUP = '''      SELECT COALESCE(i.applied_property_id,i.target_property_id),i.initiated_by,i.mode,i.publication_grant_id
        INTO destination,creator,mode,publication FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
      IF NOT FOUND THEN RETURN false; END IF;
'''


def upgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute(PREVIOUS_IMPORT.replace(
        'property_source_import_actor_access(org uuid, actor uuid, job uuid, capability text)',
        'property_import_context_actor_access(org uuid,actor uuid,job uuid,mode text,creator uuid,destination uuid,publication uuid,capability text)'
    ).replace('DECLARE role text; destination uuid; creator uuid; mode text; publication uuid; allowed boolean;',
              'DECLARE role text; allowed boolean;').replace(IMPORT_LOOKUP,''))
    op.execute("""
    CREATE OR REPLACE FUNCTION property_source_import_actor_access(org uuid,actor uuid,job uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE source public.declaration_import;
    BEGIN
      SELECT * INTO source FROM public.declaration_import i WHERE i.organization_id=org AND i.id=job;
      IF NOT FOUND THEN RETURN false; END IF;
      RETURN public.property_import_context_actor_access(org,actor,job,source.mode,source.initiated_by,
        COALESCE(source.applied_property_id,source.target_property_id),source.publication_grant_id,capability);
    END $$;
    REVOKE ALL ON FUNCTION property_import_context_actor_access(uuid,uuid,uuid,text,uuid,uuid,uuid,text) FROM PUBLIC,app_rw;

    CREATE FUNCTION property_import_row_access(org uuid,context jsonb,capability text,creating boolean DEFAULT false) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; role text; destination uuid;
    BEGIN
      IF context->>'organization_id' IS DISTINCT FROM org::text OR context->>'id' IS NULL
        OR capability IS NULL OR capability NOT IN ('read','prepare','approve','export') OR creating IS NULL THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL OR (capability IN ('prepare','approve') AND (mode='PAUSED' OR role NOT IN ('OWNER_ADMIN','CLERK'))) THEN RETURN false; END IF;
      IF creating AND (context->>'status' IS DISTINCT FROM 'DRAFT' OR context->>'applied_property_id' IS NOT NULL
        OR (context->>'initiated_by')::uuid IS DISTINCT FROM actor) THEN RETURN false; END IF;
      destination:=COALESCE((context->>'applied_property_id')::uuid,(context->>'target_property_id')::uuid);
      IF capability='prepare' AND (context->>'applied_property_id' IS NOT NULL OR context->>'status'='APPLYING') THEN capability:='approve'; END IF;
      RETURN public.property_import_context_actor_access(org,actor,(context->>'id')::uuid,context->>'mode',
        (context->>'initiated_by')::uuid,destination,(context->>'publication_grant_id')::uuid,capability);
    END $$;

    CREATE FUNCTION property_original_row_access(org uuid,document uuid,property uuid,creator uuid,capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid; role text; stored_property uuid;
    BEGIN
      IF document IS NULL OR capability IS NULL OR capability NOT IN ('read','prepare','approve','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode='PREPARATION' THEN RETURN true; END IF;
      role:=public.property_source_actor_role(org,actor);
      IF role IS NULL OR (capability IN ('prepare','approve') AND (mode='PAUSED' OR role NOT IN ('OWNER_ADMIN','CLERK'))) THEN RETURN false; END IF;
      IF property IS NOT NULL THEN
        IF NOT public.property_data_property_actor_access(org,actor,property,capability) THEN RETURN false; END IF;
        SELECT d.property_id INTO stored_property FROM public.property_document d WHERE d.organization_id=org AND d.id=document;
        IF NOT FOUND THEN RETURN false; END IF;
        -- A legitimate first binding has an APPLIED receipt before this row
        -- changes. The independent 0055 trigger still requires current approve.
        IF stored_property IS NULL THEN RETURN public.property_source_binding_proven(org,document,property); END IF;
        RETURN stored_property=property AND public.property_source_document_actor_access(org,actor,document,capability);
      END IF;
      IF EXISTS(SELECT 1 FROM public.declaration_import_document l WHERE l.organization_id=org AND l.document_id=document) THEN
        RETURN public.property_source_document_actor_access(org,actor,document,capability);
      END IF;
      RETURN COALESCE(creator=actor AND capability IN ('read','prepare','export'),false);
    END $$;
    REVOKE ALL ON FUNCTION property_import_row_access(uuid,jsonb,text,boolean),property_original_row_access(uuid,uuid,uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_import_row_access(uuid,jsonb,text,boolean),property_original_row_access(uuid,uuid,uuid,uuid,text) TO app_rw;

    CREATE FUNCTION guard_original_source_attachment() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE mode text; source public.property_document;
    BEGIN
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      SELECT s.mode INTO mode FROM public.property_source_security s WHERE s.organization_id=NEW.organization_id;
      IF COALESCE(mode,'PREPARATION')='PREPARATION' THEN RETURN NEW; END IF;
      -- The link changes custody/topology. Destination prepare alone cannot
      -- claim an unrelated unlinked original, even if its UUID is known.
      PERFORM 1 FROM public.declaration_import i WHERE i.organization_id=NEW.organization_id AND i.id=NEW.import_id FOR UPDATE;
      IF NOT FOUND OR NOT public.property_import_runtime_access(NEW.organization_id,NEW.import_id,'prepare') THEN
        RAISE EXCEPTION 'Import source authority required' USING ERRCODE='42501'; END IF;
      SELECT * INTO source FROM public.property_document d WHERE d.organization_id=NEW.organization_id AND d.id=NEW.document_id FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Original source authority required' USING ERRCODE='42501'; END IF;
      -- Existing topology violations remain owned by the retained 0055 guard.
      IF source.property_id IS NOT NULL OR EXISTS(SELECT 1 FROM public.declaration_import_document l
        WHERE l.organization_id=NEW.organization_id AND l.document_id=NEW.document_id) THEN RETURN NEW; END IF;
      IF NOT public.property_original_row_access(NEW.organization_id,source.id,source.property_id,source.uploaded_by,'prepare') THEN
        RAISE EXCEPTION 'Original source authority required' USING ERRCODE='42501'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER b_guard_original_source_attachment BEFORE INSERT ON declaration_import_document
      FOR EACH ROW EXECUTE FUNCTION guard_original_source_attachment();
    REVOKE ALL ON FUNCTION guard_original_source_attachment() FROM PUBLIC,app_rw;

    -- Explicit review metadata is bounded and never returns ordinary source rows.
    CREATE FUNCTION property_source_admission_metadata(org uuid,document uuid)
      RETURNS TABLE(document_id uuid,title varchar,original_filename varchar,content_sha256 varchar)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF public.property_source_security_mode(org) IS NULL
        OR NOT EXISTS(SELECT 1 FROM public.property_source_review_context(org,document,false)) THEN RETURN; END IF;
      RETURN QUERY SELECT d.id,d.title,d.original_filename,d.content_sha256 FROM public.property_document d
        WHERE d.organization_id=org AND d.id=document;
    END $$;
    CREATE FUNCTION property_source_admission_candidates(org uuid,after_id uuid,page_limit integer,query text)
      RETURNS TABLE(document_id uuid,title varchar,original_filename varchar,content_sha256 varchar)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF page_limit IS NULL OR page_limit<1 OR page_limit>201 OR query IS NULL OR length(query)>100
        OR public.property_source_security_mode(org) IS NULL THEN RETURN; END IF;
      RETURN QUERY SELECT d.id,d.title,d.original_filename,d.content_sha256 FROM public.property_document d
        WHERE d.organization_id=org AND (after_id IS NULL OR d.id>after_id)
          AND public.property_source_review_pending(org,d.id)
          AND position(lower(query) IN lower(d.title))>0 ORDER BY d.id LIMIT page_limit;
    END $$;
    CREATE FUNCTION property_source_review_original(org uuid,document uuid,purpose text) RETURNS SETOF public.property_document
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF purpose IS NULL OR purpose NOT IN ('ADMISSION_REVIEW','CLASSIFICATION_REVIEW')
        OR NOT public.property_source_export_access(org,document,purpose) THEN RETURN; END IF;
      RETURN QUERY SELECT d.* FROM public.property_document d WHERE d.organization_id=org AND d.id=document;
    END $$;
    REVOKE ALL ON FUNCTION property_source_admission_metadata(uuid,uuid),property_source_admission_candidates(uuid,uuid,integer,text),
      property_source_review_original(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION property_source_admission_metadata(uuid,uuid),property_source_admission_candidates(uuid,uuid,integer,text),
      property_source_review_original(uuid,uuid,text) TO app_rw;
    """)
    context="jsonb_build_object('id',id,'organization_id',organization_id,'mode',mode,'initiated_by',initiated_by,'status',status," \
            "'target_property_id',target_property_id,'applied_property_id',applied_property_id,'publication_grant_id',publication_grant_id)"
    for table in ('property_document','declaration_import'):
        def predicate(capability,creating=False):
            if table=='property_document':
                return f"property_original_row_access(organization_id,id,property_id,uploaded_by,'{capability}')"
            return f"property_import_row_access(organization_id,{context},'{capability}',{'true' if creating else 'false'})"
        op.execute(f'CREATE POLICY original_source_read ON {table} AS RESTRICTIVE FOR SELECT USING ({predicate("read")})')
        op.execute(f'CREATE POLICY original_source_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({predicate("prepare",True)})')
        op.execute(f'CREATE POLICY original_source_update ON {table} AS RESTRICTIVE FOR UPDATE USING ({predicate("prepare")}) WITH CHECK ({predicate("prepare")})')
        op.execute(f'CREATE POLICY original_source_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({predicate("prepare")})')
        op.execute(f'CREATE TRIGGER a_lock_original_source BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION lock_strict_property_source_binding()')


def downgrade():
    op.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict original source authority'; END IF; END $$""")
    for table in ('property_document','declaration_import'):
        for action in ('read','insert','update','delete'):
            op.execute(f'DROP POLICY original_source_{action} ON {table}')
        op.execute(f'DROP TRIGGER a_lock_original_source ON {table}')
    op.execute('DROP TRIGGER b_guard_original_source_attachment ON declaration_import_document')
    op.execute(PREVIOUS_IMPORT)
    op.execute('DROP FUNCTION property_import_context_actor_access(uuid,uuid,uuid,text,uuid,uuid,uuid,text),'
               'property_import_row_access(uuid,jsonb,text,boolean),property_original_row_access(uuid,uuid,uuid,uuid,text),'
               'property_source_admission_metadata(uuid,uuid),property_source_admission_candidates(uuid,uuid,integer,text),'
               'property_source_review_original(uuid,uuid,text),guard_original_source_attachment()')
