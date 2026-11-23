"""One read of what the files of a user's Posteingang batches are doing, for the Meldungen of the application frame (issue #872).

The intake tables are private: a role of the application reads them only through the commands that check the actor, so
``GET /api/meldungen`` cannot select from them. The batch page asks ``intake_batch_status`` for one batch, which walks the
files of the batch one by one and queries three tables for each file that passed the inspection. The frame asks on every
navigation of every user, so it cannot call that function for every batch of a user either.

``intake_batch_progress(org, take)`` answers for the caller's newest ``take`` batches in one statement: for each batch the
number of its files in each state, ``status`` and ``stage`` being the words ``intake_batch_status`` gives a file (the state of
a file is derived from the inspection job, the delivery of the inspected original and the extraction jobs of its documents,
and nothing is stored). It follows that function's rules and nothing else: ``tests/test_meldungen_intake.py`` takes a batch
through every stage with the real workers and compares the two answers at each step. The function decides nothing: which of
these states is a Meldung is decided in one place, ``INTAKE_FILE_STATES`` in ``services/meldungen_service.py``.

It names no file, no error text and no document: a state, a stage, the reason code of a failure (the frame needs it to tell
a file that waits for the organization's setup from one that failed), counts and, for a failed state, when a file of it last
failed (the latest ``updated_at`` of its failed inspection or extraction job, ``finished_at`` of its delivery: columns of rows
the statement reads anyway; the frame lists a batch that only failed for a limited time). Like the Posteingang's own list it returns
the batches the caller uploaded and may read: another user of the organization (an administrator included) gets none of
them, a batch of a scope the caller may no longer read is left out, and a caller without a membership gets an empty answer.
The organization and the user come from the session's tenant context, and a call that names another organization raises.

No index is added: the newest batches of the caller come from ``ix_intake_batch_owner (organization_id, initiated_by,
created_at, id)``, their files from the key of ``intake_item (organization_id, batch_id, position)`` and the rest from the keys
of ``intake_original``, ``workflow_job``, ``document_ref``, ``outbox_event``, ``outbox_delivery``, ``logical_document`` and
``intake_extraction_job`` that ``intake_batch_status`` uses too. ``tests/test_meldungen_intake.py`` asserts that no sequential
scan of these tables happens on a database that holds thousands of batches of a colleague. No table, column or row changes
and no data can be lost: ``downgrade()`` drops the function.

Revision ID: 0205
Revises: 0204
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0205"
down_revision: Union[str, None] = "0204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION public.intake_batch_progress(org uuid, take integer) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE actor uuid:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
    BEGIN
      IF org IS NULL OR org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid OR actor IS NULL THEN
        RAISE EXCEPTION 'Intake organization required' USING ERRCODE='42501'; END IF;
      IF take IS NULL OR take NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'Invalid intake page' USING ERRCODE='22023'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF public.scope_actor_role(org) IS NULL THEN RETURN '[]'::jsonb; END IF;
      RETURN COALESCE((
        WITH mine AS (
          SELECT b.id,b.created_at,jsonb_array_length(b.manifest) AS item_count FROM public.intake_batch b
            WHERE b.organization_id=org AND b.initiated_by=actor AND public.intake_actor_access(org,actor,b.scope_hint_id,false)
            ORDER BY b.created_at DESC,b.id DESC LIMIT take),
        file AS (
          SELECT m.id AS batch_id,o.id AS original_id,j.id AS job_id,j.fence,j.status AS job_status,j.error_code AS job_error,j.updated_at AS job_changed
            FROM mine m JOIN public.intake_item i ON i.organization_id=org AND i.batch_id=m.id
            LEFT JOIN public.intake_original o ON o.organization_id=org AND o.item_id=i.id
            LEFT JOIN public.workflow_job j ON j.organization_id=org AND j.original_id=o.id),
        inspected AS (
          SELECT f.*,r.id AS ref_id,d.status AS delivery_status,d.error_code AS delivery_error,d.changed AS delivery_changed
            FROM file f
            LEFT JOIN public.document_ref r ON r.organization_id=org AND r.intake_original_id=f.original_id AND f.job_status='clean'
            LEFT JOIN LATERAL (
              SELECT dl.status,dl.error_code,COALESCE(dl.finished_at,dl.created_at) AS changed FROM public.outbox_event e
                JOIN public.outbox_delivery dl ON dl.organization_id=e.organization_id AND dl.event_id=e.id
                WHERE f.job_status='clean' AND e.organization_id=org AND e.effect_key='inspected:'||f.job_id||':'||f.fence LIMIT 1) d ON true),
        counted AS (
          SELECT p.*,x.total,x.active,x.reviewable,x.failed,x.held,x.failure,x.hold_reason,x.failed_at FROM inspected p
            LEFT JOIN LATERAL (
              SELECT count(*) AS total,
                count(*) FILTER(WHERE j.status IN ('queued','running')) AS active,
                count(*) FILTER(WHERE j.status IN ('succeeded','needs_review')) AS reviewable,
                count(*) FILTER(WHERE j.status NOT IN ('queued','running','succeeded','needs_review','awaiting_split')) AS failed,
                count(*) FILTER(WHERE j.status='awaiting_split') AS held,
                max(j.updated_at) FILTER(WHERE j.status NOT IN ('queued','running','succeeded','needs_review','awaiting_split')) AS failed_at,
                (array_agg(j.error_code ORDER BY j.created_at,j.id) FILTER(WHERE j.status NOT IN ('queued','running','succeeded','needs_review','awaiting_split')))[1] AS failure,
                (array_agg(j.error_code ORDER BY j.created_at,j.id) FILTER(WHERE j.status='awaiting_split'))[1] AS hold_reason
              FROM public.intake_extraction_job j
                JOIN public.logical_document l ON l.organization_id=j.organization_id AND l.id=j.logical_document_id
              WHERE p.ref_id IS NOT NULL AND l.organization_id=org AND l.document_ref_id=p.ref_id) x ON true),
        classified AS (
          SELECT c.batch_id,CASE
              WHEN c.original_id IS NULL THEN jsonb_build_object('status','waiting','stage','inspection','error_code',NULL::text)
              WHEN c.job_status='queued' THEN jsonb_build_object('status','received','stage','inspection','error_code',c.job_error)
              WHEN c.job_status='running' THEN jsonb_build_object('status','processing','stage','inspection','error_code',c.job_error)
              WHEN c.job_status IS DISTINCT FROM 'clean' THEN jsonb_build_object('status','failed','stage','inspection','error_code',c.job_error,'failed_at',c.job_changed)
              WHEN c.delivery_status IS NULL OR c.delivery_status IN ('queued','running') THEN jsonb_build_object('status','processing','stage','preparation','error_code',NULL::text)
              WHEN c.delivery_status<>'delivered' THEN jsonb_build_object('status','failed','stage','preparation','error_code',c.delivery_error,'failed_at',c.delivery_changed)
              WHEN c.active>0 THEN jsonb_build_object('status','processing','stage','extraction','error_code',NULL::text)
              WHEN c.failed>0 THEN jsonb_build_object('status','failed','stage','extraction','error_code',c.failure,'failed_at',c.failed_at)
              WHEN c.reviewable=0 AND c.held>0 THEN jsonb_build_object('status','review','stage','split','error_code',c.hold_reason)
              WHEN c.total>0 THEN jsonb_build_object('status','review','stage','review','error_code',NULL::text)
              ELSE jsonb_build_object('status','processing','stage','preparation','error_code',NULL::text) END AS state
            FROM counted c),
        grouped AS (SELECT batch_id,state-'failed_at' AS state,max((state->>'failed_at')::timestamptz) AS failed_at,count(*) AS n FROM classified GROUP BY batch_id,state-'failed_at')
        SELECT jsonb_agg(jsonb_build_object('id',m.id,'created_at',m.created_at,'item_count',m.item_count,
            'groups',COALESCE((SELECT jsonb_agg(g.state||jsonb_build_object('count',g.n,'failed_at',g.failed_at) ORDER BY g.state::text) FROM grouped g WHERE g.batch_id=m.id),'[]'::jsonb))
            ORDER BY m.created_at DESC,m.id DESC) FROM mine m),'[]'::jsonb);
    END $$;
    REVOKE ALL ON FUNCTION public.intake_batch_progress(uuid,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.intake_batch_progress(uuid,integer) TO app_rw;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION public.intake_batch_progress(uuid,integer)")
