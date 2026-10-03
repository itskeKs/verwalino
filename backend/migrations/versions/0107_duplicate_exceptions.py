"""Connect derived duplicate pairs to shared advisory cases without clearance."""
from alembic import op
from sqlalchemy import text

revision='0107'
down_revision='0106'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'duplicate_comparison',id,to_jsonb(r) FROM public.duplicate_comparison r WHERE organization_id=org) evidence"
AFTER=BEFORE.replace(') evidence'," UNION ALL SELECT 'duplicate_exception_dependency',id,to_jsonb(r) FROM public.duplicate_exception_dependency r WHERE organization_id=org) evidence")
ACCESS_BEFORE='AND public.assignment_proposal_access(org,c.document_ref_id,c.scope_id)'
ACCESS_AFTER=ACCESS_BEFORE+'''
        AND NOT EXISTS(SELECT 1 FROM public.duplicate_exception_dependency d WHERE d.organization_id=org AND d.case_id=c.id
          AND (NOT public.assignment_proposal_access(org,d.anchor_document_ref_id,d.scope_id)
            OR NOT public.assignment_proposal_access(org,d.reference_document_ref_id,d.scope_id)))'''
WORK_BEFORE="NOT public.exception_case_access(org,target) OR NOT public.source_partition_access(org,c.document_ref_id,true)\n        OR NOT public.has_scope_capability(org,c.scope_id,'prepare')"
WORK_AFTER='NOT public.exception_case_work_access(org,target)'
OLD_SHAPE="""CHECK((action='evidence_added' AND actor_id IS NULL AND dependency_id IS NOT NULL AND request_key IS NULL AND request_digest IS NULL)
         OR (action<>'evidence_added' AND actor_id IS NOT NULL AND dependency_id IS NULL AND request_key IS NOT NULL AND request_digest IS NOT NULL))"""


def replace_function(signature,before,after):
    definition=op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
    assert definition.count(before)==1,(signature,before)
    op.execute(definition.replace(before,after))


def upgrade():
    op.execute('''
    -- Live commands lock organization inputs before inserting reviews/comparisons.
    -- Acquire that same order before table/DDL locks and deterministic backfill.
    DO $$ DECLARE org uuid; BEGIN
      FOR org IN SELECT id FROM organization ORDER BY id LOOP
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      END LOOP;
    END $$;
    LOCK TABLE invoice_review_revision,duplicate_comparison IN SHARE ROW EXCLUSIVE MODE;
    ALTER TABLE exception_case DROP CONSTRAINT exception_case_cause_check;
    ALTER TABLE exception_case ADD CONSTRAINT exception_case_cause_check CHECK(cause IN ('scope_hint_conflict','unsupported_scope_type','duplicate_candidate'));
    ALTER TABLE invoice_review_revision ADD CONSTRAINT duplicate_exception_review_basis UNIQUE(organization_id,scope_id,document_ref_id,logical_document_id,extraction_revision_id,id);
    CREATE TABLE duplicate_exception_dependency(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,case_id uuid NOT NULL,scope_id uuid NOT NULL,
      cause text NOT NULL DEFAULT 'duplicate_candidate' CHECK(cause='duplicate_candidate'),
      anchor_document_ref_id uuid NOT NULL,anchor_logical_id uuid NOT NULL,anchor_extraction_id uuid NOT NULL,anchor_review_id uuid NOT NULL,
      reference_document_ref_id uuid NOT NULL,reference_logical_id uuid NOT NULL,reference_extraction_id uuid NOT NULL,reference_review_id uuid NOT NULL,
      evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(anchor_logical_id<reference_logical_id),UNIQUE(organization_id,case_id,id),UNIQUE(organization_id,anchor_review_id,reference_review_id),
      FOREIGN KEY(organization_id,case_id,scope_id,cause,anchor_extraction_id) REFERENCES exception_case(organization_id,id,scope_id,cause,extraction_revision_id),
      FOREIGN KEY(organization_id,scope_id,anchor_document_ref_id,anchor_logical_id,anchor_extraction_id,anchor_review_id)
        REFERENCES invoice_review_revision(organization_id,scope_id,document_ref_id,logical_document_id,extraction_revision_id,id),
      FOREIGN KEY(organization_id,scope_id,reference_document_ref_id,reference_logical_id,reference_extraction_id,reference_review_id)
        REFERENCES invoice_review_revision(organization_id,scope_id,document_ref_id,logical_document_id,extraction_revision_id,id));
    CREATE FUNCTION validate_duplicate_exception_dependency() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE facts jsonb;
    BEGIN
      facts:=public.duplicate_pair_evidence(NEW.organization_id,NEW.anchor_review_id,NEW.reference_review_id);
      IF facts IS NULL OR jsonb_array_length(facts->'match_reasons')=0 OR NEW.evidence IS DISTINCT FROM facts
        OR NOT EXISTS(SELECT 1 FROM public.exception_case WHERE organization_id=NEW.organization_id AND id=NEW.case_id
          AND cause='duplicate_candidate' AND scope_id=NEW.scope_id AND document_ref_id=NEW.anchor_document_ref_id
          AND logical_document_id=NEW.anchor_logical_id AND extraction_revision_id=NEW.anchor_extraction_id) THEN
        RAISE EXCEPTION 'Exact duplicate exception provenance required' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION validate_duplicate_exception_dependency() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER validate_duplicate_exception_dependency BEFORE INSERT ON duplicate_exception_dependency FOR EACH ROW EXECUTE FUNCTION validate_duplicate_exception_dependency();
    ALTER TABLE duplicate_exception_dependency ENABLE ROW LEVEL SECURITY; ALTER TABLE duplicate_exception_dependency FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON duplicate_exception_dependency FROM PUBLIC,app_rw,app_worker,app_review; GRANT SELECT ON duplicate_exception_dependency TO app_rw;
    CREATE POLICY duplicate_exception_read ON duplicate_exception_dependency FOR SELECT USING(exception_case_access(organization_id,case_id));
    CREATE POLICY duplicate_exception_no_insert ON duplicate_exception_dependency FOR INSERT WITH CHECK(false);
    CREATE TRIGGER protect_duplicate_dependency BEFORE UPDATE OR DELETE ON duplicate_exception_dependency FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();

    ALTER TABLE exception_case_event ADD COLUMN duplicate_dependency_id uuid;
    ALTER TABLE exception_case_event ADD COLUMN duplicate_comparison_id uuid;
    ALTER TABLE exception_case_event ADD CONSTRAINT duplicate_event_dependency FOREIGN KEY(organization_id,case_id,duplicate_dependency_id) REFERENCES duplicate_exception_dependency(organization_id,case_id,id);
    ALTER TABLE exception_case_event ADD CONSTRAINT duplicate_event_comparison FOREIGN KEY(organization_id,duplicate_comparison_id) REFERENCES duplicate_comparison(organization_id,id);
    CREATE UNIQUE INDEX duplicate_evidence_event_once ON exception_case_event(organization_id,duplicate_dependency_id) WHERE action='duplicate_evidence_added';
    CREATE UNIQUE INDEX duplicate_comparison_event_once ON exception_case_event(organization_id,duplicate_comparison_id) WHERE duplicate_comparison_id IS NOT NULL;
    ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_action_check;
    ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_action_check CHECK(action IN ('evidence_added','claim','release','snooze','reopen','context_reviewed','duplicate_evidence_added','duplicate_comparison_recorded'));
    ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_check1;
    ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_check1 CHECK(
      (action IN ('duplicate_evidence_added','duplicate_comparison_recorded') AND duplicate_dependency_id IS NOT NULL
        AND ((action='duplicate_comparison_recorded')=(duplicate_comparison_id IS NOT NULL))
        AND actor_id IS NULL AND request_key IS NULL AND request_digest IS NULL AND dependency_id IS NULL AND context_decision_id IS NULL
        AND workflow_state='open' AND snoozed_until IS NULL)
      OR (action='evidence_added' AND actor_id IS NULL AND dependency_id IS NOT NULL AND request_key IS NULL AND request_digest IS NULL
        AND duplicate_dependency_id IS NULL AND duplicate_comparison_id IS NULL)
      OR (action IN ('claim','release','snooze','reopen','context_reviewed') AND actor_id IS NOT NULL AND dependency_id IS NULL AND request_key IS NOT NULL AND request_digest IS NOT NULL
        AND duplicate_dependency_id IS NULL AND duplicate_comparison_id IS NULL));
    CREATE FUNCTION validate_duplicate_exception_event() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      IF NEW.action='duplicate_comparison_recorded' AND NOT EXISTS(
        SELECT 1 FROM public.duplicate_exception_dependency d JOIN public.duplicate_comparison c ON c.organization_id=d.organization_id AND c.scope_id=d.scope_id
        WHERE d.organization_id=NEW.organization_id AND d.case_id=NEW.case_id AND d.id=NEW.duplicate_dependency_id AND c.id=NEW.duplicate_comparison_id
          AND ((c.subject_review_id=d.anchor_review_id AND c.reference_review_id=d.reference_review_id)
            OR (c.subject_review_id=d.reference_review_id AND c.reference_review_id=d.anchor_review_id))) THEN
        RAISE EXCEPTION 'Exact duplicate observation provenance required' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION validate_duplicate_exception_event() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER validate_duplicate_exception_event BEFORE INSERT ON exception_case_event FOR EACH ROW EXECUTE FUNCTION validate_duplicate_exception_event();
    ''')
    replace_function('exception_case_access(uuid,uuid)',ACCESS_BEFORE,ACCESS_AFTER)
    op.execute('''
    CREATE FUNCTION exception_case_work_access(org uuid,target uuid) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT EXISTS(SELECT 1 FROM public.exception_case c WHERE c.organization_id=org AND c.id=target
        AND public.exception_case_access(org,target) AND public.source_partition_access(org,c.document_ref_id,true)
        AND public.has_scope_capability(org,c.scope_id,'prepare')
        AND NOT EXISTS(SELECT 1 FROM public.duplicate_exception_dependency d WHERE d.organization_id=org AND d.case_id=c.id
          AND (NOT public.source_partition_access(org,d.anchor_document_ref_id,true) OR NOT public.source_partition_access(org,d.reference_document_ref_id,true))));
    $$;
    REVOKE ALL ON FUNCTION exception_case_work_access(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION exception_case_work_access(uuid,uuid) TO app_rw;

    CREATE FUNCTION record_duplicate_exception_dependencies(org uuid,target uuid) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE r public.invoice_review_revision; other public.invoice_review_revision; a public.invoice_review_revision; b public.invoice_review_revision;
      facts jsonb; target_case uuid; dependency uuid; prior public.exception_case_event;
    BEGIN
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=org AND id=target;
      IF r.id IS DISTINCT FROM (SELECT id FROM public.invoice_review_revision WHERE organization_id=org AND logical_document_id=r.logical_document_id ORDER BY revision_number DESC LIMIT 1) THEN RETURN; END IF;
      FOR other IN SELECT h.* FROM (SELECT DISTINCT ON(logical_document_id) * FROM public.invoice_review_revision WHERE organization_id=org ORDER BY logical_document_id,revision_number DESC) h
        WHERE h.scope_id=r.scope_id AND h.logical_document_id<>r.logical_document_id ORDER BY h.logical_document_id LOOP
        IF r.logical_document_id<other.logical_document_id THEN a:=r;b:=other; ELSE a:=other;b:=r; END IF;
        facts:=public.duplicate_pair_evidence(org,a.id,b.id);
        IF facts IS NULL OR jsonb_array_length(facts->'match_reasons')=0 THEN CONTINUE; END IF;
        INSERT INTO public.exception_case(organization_id,document_ref_id,logical_document_id,extraction_revision_id,scope_id,cause)
          VALUES(org,a.document_ref_id,a.logical_document_id,a.extraction_revision_id,a.scope_id,'duplicate_candidate')
          ON CONFLICT(organization_id,scope_id,cause,extraction_revision_id) DO NOTHING;
        SELECT id INTO STRICT target_case FROM public.exception_case WHERE organization_id=org AND scope_id=a.scope_id AND cause='duplicate_candidate' AND extraction_revision_id=a.extraction_revision_id;
        INSERT INTO public.duplicate_exception_dependency(organization_id,case_id,scope_id,anchor_document_ref_id,anchor_logical_id,anchor_extraction_id,anchor_review_id,
          reference_document_ref_id,reference_logical_id,reference_extraction_id,reference_review_id,evidence)
        VALUES(org,target_case,a.scope_id,a.document_ref_id,a.logical_document_id,a.extraction_revision_id,a.id,b.document_ref_id,b.logical_document_id,b.extraction_revision_id,b.id,facts)
          ON CONFLICT(organization_id,anchor_review_id,reference_review_id) DO NOTHING RETURNING id INTO dependency;
        IF dependency IS NOT NULL THEN
          SELECT * INTO prior FROM public.exception_case_event WHERE organization_id=org AND case_id=target_case ORDER BY revision_number DESC LIMIT 1;
          INSERT INTO public.exception_case_event(organization_id,case_id,previous_event_id,revision_number,action,workflow_state,claimant_id,reason,duplicate_dependency_id)
            VALUES(org,target_case,prior.id,COALESCE(prior.revision_number,0)+1,'duplicate_evidence_added','open',prior.claimant_id,'Derived duplicate evidence linked at this time',dependency);
        END IF;
      END LOOP;
    END $$;
    CREATE FUNCTION record_duplicate_comparison_event(org uuid,target uuid,required boolean) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE c public.duplicate_comparison; d public.duplicate_exception_dependency; prior public.exception_case_event;
    BEGIN
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      SELECT * INTO STRICT c FROM public.duplicate_comparison WHERE organization_id=org AND id=target;
      IF EXISTS(SELECT 1 FROM public.exception_case_event WHERE organization_id=org AND duplicate_comparison_id=target) THEN RETURN; END IF;
      SELECT * INTO d FROM public.duplicate_exception_dependency WHERE organization_id=org AND scope_id=c.scope_id
        AND anchor_review_id=CASE WHEN c.subject_logical_id<c.reference_logical_id THEN c.subject_review_id ELSE c.reference_review_id END
        AND reference_review_id=CASE WHEN c.subject_logical_id<c.reference_logical_id THEN c.reference_review_id ELSE c.subject_review_id END;
      IF d.id IS NULL THEN
        IF required THEN RAISE EXCEPTION 'Comparison evidence not linked' USING ERRCODE='23514'; END IF;
        RETURN;
      END IF;
      SELECT * INTO STRICT prior FROM public.exception_case_event WHERE organization_id=org AND case_id=d.case_id ORDER BY revision_number DESC LIMIT 1;
      INSERT INTO public.exception_case_event(organization_id,case_id,previous_event_id,revision_number,action,workflow_state,claimant_id,reason,duplicate_dependency_id,duplicate_comparison_id)
        VALUES(org,d.case_id,prior.id,prior.revision_number+1,'duplicate_comparison_recorded','open',prior.claimant_id,'Retained human pair comparison linked at this time',d.id,c.id);
    END $$;
    CREATE FUNCTION invoice_duplicate_exception_trigger() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$ BEGIN
      PERFORM public.record_duplicate_exception_dependencies(NEW.organization_id,NEW.id); RETURN NEW;
    END $$;
    CREATE FUNCTION comparison_exception_trigger() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$ BEGIN
      PERFORM public.record_duplicate_comparison_event(NEW.organization_id,NEW.id,true); RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION record_duplicate_exception_dependencies(uuid,uuid),record_duplicate_comparison_event(uuid,uuid,boolean),invoice_duplicate_exception_trigger(),comparison_exception_trigger() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER invoice_duplicate_exceptions AFTER INSERT ON invoice_review_revision FOR EACH ROW EXECUTE FUNCTION invoice_duplicate_exception_trigger();
    CREATE TRIGGER comparison_exception AFTER INSERT ON duplicate_comparison FOR EACH ROW EXECUTE FUNCTION comparison_exception_trigger();
    DO $$ DECLARE row record; BEGIN
      FOR row IN SELECT DISTINCT ON(organization_id,logical_document_id) organization_id,logical_document_id,id FROM public.invoice_review_revision ORDER BY organization_id,logical_document_id,revision_number DESC LOOP
        PERFORM public.record_duplicate_exception_dependencies(row.organization_id,row.id);
      END LOOP;
      FOR row IN SELECT organization_id,id FROM public.duplicate_comparison ORDER BY organization_id,created_at,id LOOP
        PERFORM public.record_duplicate_comparison_event(row.organization_id,row.id,false);
      END LOOP;
    END $$;
    ''')
    replace_function('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',WORK_BEFORE,WORK_AFTER)
    replace_function('access_coverage_manifest(uuid,jsonb)',BEFORE,AFTER)


def downgrade():
    op.execute('LOCK TABLE invoice_review_revision,duplicate_comparison,duplicate_exception_dependency,exception_case,exception_case_event IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM duplicate_exception_dependency) OR EXISTS(SELECT 1 FROM exception_case WHERE cause='duplicate_candidate') THEN RAISE EXCEPTION 'Retain duplicate exception evidence; repair forward'; END IF; END $$")
    replace_function('access_coverage_manifest(uuid,jsonb)',AFTER,BEFORE)
    replace_function('append_exception_event(uuid,uuid,uuid,uuid,text,text,timestamptz)',WORK_AFTER,WORK_BEFORE)
    op.execute('DROP FUNCTION exception_case_work_access(uuid,uuid)')
    replace_function('exception_case_access(uuid,uuid)',ACCESS_AFTER,ACCESS_BEFORE)
    op.execute('''DROP TRIGGER invoice_duplicate_exceptions ON invoice_review_revision; DROP TRIGGER comparison_exception ON duplicate_comparison;
      DROP TRIGGER validate_duplicate_exception_event ON exception_case_event;
      DROP FUNCTION invoice_duplicate_exception_trigger(),comparison_exception_trigger(),record_duplicate_exception_dependencies(uuid,uuid),record_duplicate_comparison_event(uuid,uuid,boolean),validate_duplicate_exception_event();
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_check1;
      ALTER TABLE exception_case_event DROP COLUMN duplicate_dependency_id; ALTER TABLE exception_case_event DROP COLUMN duplicate_comparison_id;
      ALTER TABLE exception_case_event DROP CONSTRAINT exception_case_event_action_check;
      ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_action_check CHECK(action IN ('evidence_added','claim','release','snooze','reopen','context_reviewed'));
      DROP TABLE duplicate_exception_dependency; DROP FUNCTION validate_duplicate_exception_dependency();
      ALTER TABLE invoice_review_revision DROP CONSTRAINT duplicate_exception_review_basis;
      ALTER TABLE exception_case DROP CONSTRAINT exception_case_cause_check;
      ALTER TABLE exception_case ADD CONSTRAINT exception_case_cause_check CHECK(cause IN ('scope_hint_conflict','unsupported_scope_type'));''')
    op.execute('ALTER TABLE exception_case_event ADD CONSTRAINT exception_case_event_check1 '+OLD_SHAPE)
