"""Project complete intake stage state into the existing durable batch contract."""
from alembic import op
from sqlalchemy import text

revision='0099'
down_revision='0098'
branch_labels=None
depends_on=None


def upgrade():
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('intake_batch_status(uuid,uuid,integer,integer)'::regprocedure)"))
    op.execute(definition.replace('FUNCTION public.intake_batch_status(','FUNCTION public.intake_batch_status_pre_extraction(',1))
    op.execute('''REVOKE ALL ON FUNCTION intake_batch_status_pre_extraction(uuid,uuid,integer,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE OR REPLACE FUNCTION intake_batch_status(org uuid,batch uuid,after_position integer,take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE prior jsonb; entry jsonb; entries jsonb:='[]'; counts jsonb; ref uuid; delivery public.outbox_delivery;
      total integer; active integer; failed integer; reviewable integer; failure text; stage text; state text;
    BEGIN
      IF after_position IS NULL OR after_position NOT BETWEEN 0 AND 100 OR take IS NULL OR take NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'Invalid intake page' USING ERRCODE='22023'; END IF;
      PERFORM public.lock_intake(org);
      prior:=public.intake_batch_status_pre_extraction(org,batch,0,200);
      FOR entry IN SELECT * FROM jsonb_array_elements(prior->'items') LOOP
        ref:=NULL;total:=0;active:=0;failed:=0;reviewable:=0;stage:='inspection';state:=entry->>'status';failure:=entry->>'error_code';
        IF state='review' THEN
          SELECT id INTO ref FROM public.document_ref WHERE organization_id=org AND intake_original_id=(entry->>'original_id')::uuid;
          SELECT d.* INTO delivery FROM public.outbox_delivery d JOIN public.outbox_event e ON e.organization_id=d.organization_id AND e.id=d.event_id
            WHERE e.organization_id=org AND e.effect_key='inspected:'||(entry->>'job_id')||':'||(entry->>'fence');
          SELECT count(*),count(*) FILTER(WHERE j.status IN ('queued','running')),
            count(*) FILTER(WHERE j.status IN ('succeeded','needs_review')),
            count(*) FILTER(WHERE j.status NOT IN ('queued','running','succeeded','needs_review'))
            INTO total,active,reviewable,failed FROM public.intake_extraction_job j JOIN public.logical_document l ON l.organization_id=j.organization_id AND l.id=j.logical_document_id
            WHERE l.organization_id=org AND l.document_ref_id=ref;
          IF delivery.id IS NULL OR delivery.status IN ('queued','running') THEN stage:='preparation';state:='processing';failure:=NULL;
          ELSIF delivery.status<>'delivered' THEN stage:='preparation';state:='failed';failure:=delivery.error_code;
          ELSIF active>0 THEN stage:='extraction';state:='processing';failure:=NULL;
          ELSIF failed>0 THEN
            stage:='extraction';state:='failed';
            SELECT j.error_code INTO failure FROM public.intake_extraction_job j JOIN public.logical_document l ON l.organization_id=j.organization_id AND l.id=j.logical_document_id
              WHERE l.organization_id=org AND l.document_ref_id=ref AND j.status NOT IN ('queued','running','succeeded','needs_review') ORDER BY j.created_at,j.id LIMIT 1;
          ELSIF total>0 THEN stage:='review';state:='review';failure:=NULL;
          ELSE stage:='preparation';state:='processing';failure:=NULL;
          END IF;
        END IF;
        entries:=entries||jsonb_build_array(entry||jsonb_build_object('status',state,'stage',stage,'error_code',failure,'document_ref_id',ref,
          'logical_counts',jsonb_build_object('total',total,'processing',active,'review',reviewable,'failed',failed)));
      END LOOP;
      SELECT jsonb_build_object('waiting',count(*) FILTER(WHERE e->>'status'='waiting'),'received',count(*) FILTER(WHERE e->>'status'='received'),
        'processing',count(*) FILTER(WHERE e->>'status'='processing'),'review',count(*) FILTER(WHERE e->>'status'='review'),
        'confirmed',0,'failed',count(*) FILTER(WHERE e->>'status'='failed')) INTO counts FROM jsonb_array_elements(entries) e;
      RETURN prior||jsonb_build_object('counts',counts,'revision',encode(sha256(convert_to(entries::text,'UTF8')),'hex'),
        'items',(SELECT COALESCE(jsonb_agg(e ORDER BY (e->>'position')::integer),'[]') FROM
          (SELECT e FROM jsonb_array_elements(entries) e WHERE (e->>'position')::integer>after_position ORDER BY (e->>'position')::integer LIMIT take+1) page));
    END $$;
    REVOKE ALL ON FUNCTION intake_batch_status(uuid,uuid,integer,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION intake_batch_status(uuid,uuid,integer,integer) TO app_rw;''')


def downgrade():
    definition=op.get_bind().scalar(text("SELECT pg_get_functiondef('intake_batch_status_pre_extraction(uuid,uuid,integer,integer)'::regprocedure)"))
    op.execute(definition.replace('FUNCTION public.intake_batch_status_pre_extraction(','FUNCTION public.intake_batch_status(',1))
    op.execute('DROP FUNCTION intake_batch_status_pre_extraction(uuid,uuid,integer,integer)')
