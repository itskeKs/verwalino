"""Fence the internal whole-source effect of exact completed inspection events."""
from alembic import op
from sqlalchemy import text

revision='0097'
down_revision='0096'
branch_labels=None
depends_on=None
BEFORE="UNION ALL SELECT 'field',id,to_jsonb(r) FROM public.field_evidence r WHERE organization_id=org) evidence"
AFTER="UNION ALL SELECT 'field',id,to_jsonb(r) FROM public.field_evidence r WHERE organization_id=org UNION ALL SELECT 'delivery',id,to_jsonb(r) FROM public.outbox_delivery r WHERE organization_id=org UNION ALL SELECT 'delivery_attempt',id,to_jsonb(r) FROM public.outbox_delivery_attempt r WHERE organization_id=org) evidence"


def upgrade():
    op.execute('''
    ALTER TABLE logical_document ADD COLUMN origin text NOT NULL DEFAULT 'manual' CHECK(origin IN ('manual','inspection'));
    CREATE TABLE outbox_delivery(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),event_id uuid NOT NULL,
      consumer text NOT NULL DEFAULT 'intake.logical_source' CHECK(consumer='intake.logical_source'),
      inspection_attempt_id uuid NOT NULL,initiated_by uuid NOT NULL,scope_hint_id uuid,
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','delivered','skipped','cancelled','blocked')),
      attempt_number integer NOT NULL DEFAULT 0 CHECK(attempt_number BETWEEN 0 AND 3),fence uuid,lease_expires_at timestamptz,
      error_code text,logical_document_id uuid,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,
      UNIQUE(organization_id,id),UNIQUE(organization_id,event_id,consumer),
      CHECK(status<>'running' OR (fence IS NOT NULL AND lease_expires_at IS NOT NULL)),
      FOREIGN KEY(organization_id,event_id) REFERENCES outbox_event(organization_id,id),
      FOREIGN KEY(organization_id,inspection_attempt_id) REFERENCES job_attempt(organization_id,id),
      FOREIGN KEY(organization_id,initiated_by) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,scope_hint_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,logical_document_id) REFERENCES logical_document(organization_id,id));
    CREATE INDEX ix_outbox_delivery_claim ON outbox_delivery(organization_id,status,created_at,id);
    CREATE TABLE outbox_delivery_attempt(
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL REFERENCES organization(id),delivery_id uuid NOT NULL,
      attempt_number integer NOT NULL CHECK(attempt_number BETWEEN 1 AND 3),fence uuid NOT NULL,
      started_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,result jsonb,
      UNIQUE(organization_id,id),UNIQUE(organization_id,delivery_id,attempt_number),UNIQUE(organization_id,delivery_id,fence),
      FOREIGN KEY(organization_id,delivery_id) REFERENCES outbox_delivery(organization_id,id));
    ''')
    for table in ('outbox_delivery','outbox_delivery_attempt'):
        op.execute(f'''ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY outbox_private ON {table} AS RESTRICTIVE FOR ALL USING(false) WITH CHECK(false);''')
    op.execute('''
    CREATE FUNCTION enqueue_inspected_event(event public.outbox_event) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE provenance record;
    BEGIN
      IF event.event_type<>'intake.inspected' THEN RETURN; END IF;
      SELECT a.id,b.initiated_by,b.scope_hint_id INTO provenance FROM public.job_attempt a
        JOIN public.workflow_job j ON j.organization_id=a.organization_id AND j.id=a.job_id
        JOIN public.intake_original o ON o.organization_id=j.organization_id AND o.id=j.original_id
        JOIN public.intake_item i ON i.organization_id=o.organization_id AND i.id=o.item_id
        JOIN public.intake_batch b ON b.organization_id=i.organization_id AND b.id=i.batch_id
        WHERE a.organization_id=event.organization_id AND o.id=event.resource_id
          AND event.effect_key='inspected:'||j.id::text||':'||a.fence::text AND a.finished_at IS NOT NULL
          AND a.result->>'status' IN ('clean','blocked','rejected');
      IF NOT FOUND THEN RAISE EXCEPTION 'Inspection event provenance missing' USING ERRCODE='23514'; END IF;
      INSERT INTO public.outbox_delivery(organization_id,event_id,inspection_attempt_id,initiated_by,scope_hint_id)
        VALUES(event.organization_id,event.id,provenance.id,provenance.initiated_by,provenance.scope_hint_id)
        ON CONFLICT(organization_id,event_id,consumer) DO NOTHING;
    END $$;
    CREATE FUNCTION enqueue_outbox_delivery() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN PERFORM public.enqueue_inspected_event(NEW); RETURN NEW; END $$;
    CREATE TRIGGER enqueue_outbox_delivery AFTER INSERT ON outbox_event FOR EACH ROW EXECUTE FUNCTION enqueue_outbox_delivery();
    SELECT enqueue_inspected_event(e) FROM outbox_event e WHERE event_type='intake.inspected' ORDER BY created_at,id;
    REVOKE ALL ON FUNCTION enqueue_inspected_event(public.outbox_event),enqueue_outbox_delivery() FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION claim_outbox_delivery(org uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.outbox_delivery; token uuid:=gen_random_uuid();
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Outbox runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.outbox_delivery WHERE organization_id=org AND
        (status='queued' OR status='running' AND lease_expires_at<=clock_timestamp()) ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED;
      IF NOT FOUND THEN RETURN NULL; END IF;
      IF work.status='running' THEN
        UPDATE public.outbox_delivery_attempt SET finished_at=clock_timestamp(),result=jsonb_build_object('status','blocked','error_code','lease_expired')
          WHERE organization_id=org AND delivery_id=work.id AND fence=work.fence AND finished_at IS NULL;
      END IF;
      IF work.attempt_number>=3 THEN
        UPDATE public.outbox_delivery SET status='blocked',error_code='attempt_limit',lease_expires_at=NULL,finished_at=clock_timestamp() WHERE id=work.id;
        RETURN NULL;
      END IF;
      UPDATE public.outbox_delivery SET status='running',attempt_number=attempt_number+1,fence=token,
        lease_expires_at=clock_timestamp()+interval '120 seconds',error_code=NULL WHERE id=work.id;
      INSERT INTO public.outbox_delivery_attempt(organization_id,delivery_id,attempt_number,fence) VALUES(org,work.id,work.attempt_number+1,token);
      RETURN jsonb_build_object('delivery_id',work.id,'fence',token);
    END $$;
    CREATE FUNCTION apply_intake_logical_delivery(org uuid,delivery uuid,token uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE work public.outbox_delivery; event public.outbox_event; inspection public.job_attempt; ref public.document_ref;
      child uuid; pages integer; state text; failure text;
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Outbox runtime required' USING ERRCODE='42501'; END IF;
      PERFORM public.lock_intake(org);
      SELECT * INTO work FROM public.outbox_delivery WHERE organization_id=org AND id=delivery FOR UPDATE;
      IF NOT FOUND OR work.status<>'running' OR token IS DISTINCT FROM work.fence OR work.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
      SELECT * INTO event FROM public.outbox_event WHERE organization_id=org AND id=work.event_id;
      SELECT * INTO inspection FROM public.job_attempt WHERE organization_id=org AND id=work.inspection_attempt_id;
      IF inspection.result->>'status'<>'clean' THEN state:='skipped';failure:='inspection_not_clean';
      ELSE
        PERFORM set_config('app.current_user_id',work.initiated_by::text,true);
        PERFORM set_config('app.current_scope_id',COALESCE(work.scope_hint_id::text,''),true);
        IF NOT public.intake_actor_access(org,work.initiated_by,work.scope_hint_id,true) OR NOT public.intake_original_access(org,event.resource_id,true) THEN
          state:='cancelled';failure:='initiator_permission_changed';
        ELSE
          SELECT * INTO ref FROM public.document_ref WHERE organization_id=org AND intake_original_id=event.resource_id;
          pages:=(inspection.result->>'page_count')::integer;
          SELECT id INTO child FROM public.logical_document WHERE organization_id=org AND document_ref_id=ref.id
            AND page_start IS NOT DISTINCT FROM CASE WHEN pages IS NULL THEN NULL ELSE 1 END AND page_end IS NOT DISTINCT FROM pages;
          IF child IS NULL AND (SELECT count(*) FROM public.logical_document WHERE organization_id=org AND document_ref_id=ref.id)>=200 THEN
            state:='blocked';failure:='logical_child_limit';
          ELSIF child IS NULL THEN
            INSERT INTO public.logical_document(organization_id,document_ref_id,verified_attempt_id,content_sha256,page_start,page_end,verified_page_count,
              created_by,request_key,request_digest,reason,origin)
              VALUES(org,ref.id,inspection.id,ref.content_sha256,CASE WHEN pages IS NULL THEN NULL ELSE 1 END,pages,pages,
                work.initiated_by,work.id,encode(sha256(convert_to('delivery:'||work.id::text,'UTF8')),'hex'),
                'Whole original proposed after successful inspection; completeness remains unreviewed','inspection') RETURNING id INTO child;
            INSERT INTO public.outbox_event(organization_id,resource_id,event_type,effect_key) VALUES(org,event.resource_id,'intake.logical_created','logical:'||child::text);
          END IF;
          IF state IS NULL THEN state:='delivered'; END IF;
        END IF;
      END IF;
      UPDATE public.outbox_delivery_attempt SET finished_at=clock_timestamp(),result=jsonb_build_object('status',state,'error_code',failure,'logical_document_id',child)
        WHERE organization_id=org AND delivery_id=delivery AND fence=token;
      UPDATE public.outbox_delivery SET status=state,error_code=failure,logical_document_id=child,finished_at=clock_timestamp(),lease_expires_at=NULL
        WHERE organization_id=org AND id=delivery;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION claim_outbox_delivery(uuid),apply_intake_logical_delivery(uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION claim_outbox_delivery(uuid),apply_intake_logical_delivery(uuid,uuid,uuid) TO app_worker;
    ''')
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(BEFORE)==1
    op.execute(definition.replace(BEFORE,AFTER))
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_disposition_work(uuid)'::regprocedure)"))
    op.execute(definition.replace('FUNCTION public.access_disposition_work(','FUNCTION public.access_disposition_work_pre_outbox(',1))
    op.execute('''REVOKE ALL ON FUNCTION access_disposition_work_pre_outbox(uuid) FROM PUBLIC,app_rw,app_worker,app_review;
      CREATE OR REPLACE FUNCTION access_disposition_work(org uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
        SELECT public.access_disposition_work_pre_outbox(org)||COALESCE(jsonb_agg(jsonb_build_object('kind','outbox_delivery','resource_id',id) ORDER BY id),'[]')
          FROM public.outbox_delivery WHERE organization_id=org AND (status IN ('queued','running') OR lease_expires_at>clock_timestamp());
      $$;
      REVOKE ALL ON FUNCTION access_disposition_work(uuid) FROM PUBLIC,app_rw,app_worker,app_review;''')


def downgrade():
    op.execute('LOCK TABLE outbox_delivery,outbox_delivery_attempt,logical_document,outbox_event IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM outbox_delivery) OR EXISTS(SELECT 1 FROM logical_document WHERE origin='inspection') THEN RAISE EXCEPTION 'Retain outbox delivery evidence; repair forward'; END IF; END $$")
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    assert definition.count(AFTER)==1
    op.execute(definition.replace(AFTER,BEFORE))
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('access_disposition_work_pre_outbox(uuid)'::regprocedure)"))
    op.execute(definition.replace('FUNCTION public.access_disposition_work_pre_outbox(','FUNCTION public.access_disposition_work(',1))
    op.execute('''DROP FUNCTION access_disposition_work_pre_outbox(uuid);
      DROP TRIGGER enqueue_outbox_delivery ON outbox_event;
      DROP FUNCTION enqueue_outbox_delivery(),enqueue_inspected_event(public.outbox_event),claim_outbox_delivery(uuid),apply_intake_logical_delivery(uuid,uuid,uuid);
      DROP TABLE outbox_delivery_attempt,outbox_delivery;
      ALTER TABLE logical_document DROP COLUMN origin;''')
