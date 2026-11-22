"""The inbox reads and extracts through the organization's two models (issue #501, ADR 0017).

An unstructured PDF, PNG or JPEG of the inbox is read page by page with the organization's document reading model
(a page with a text layer keeps its text) and the organization's extraction model fills the invoice proposal, exactly
as for an invoice (issue #500). This revision holds everything the inbox path needs in the database, so that no later
part of the issue has to change the schema:

* The model call ledger ``invoice_model_call`` (0197) serves the inbox as well: a row belongs to an invoice attempt
  (``attempt_id``) or to an inbox extraction attempt (``intake_attempt_id``), exactly one of the two. The composite
  foreign key, forced row-level security, the worker-only insert and the settle-once guard stay as they are, the
  monthly total (``organization_ai_monthly_usage``) already sums the whole table, and no DELETE trigger is added
  (the retention catalog keeps classifying the ledger ``delete``).
* ``renew_intake_extraction`` extends the lease of a live claim before every paid request and re-checks the initiating
  person, as the invoice path does for its attempt: it is true only for the attempt that still holds the fence with an
  unexpired lease and an authorized initiator, so that a lost fence stops further spending. The inbox claim lasts
  three minutes, a request to a model can take longer.
* ``intake_model_usage`` lists the ledger rows of one logical document over all its extraction attempts, for the
  people who may read that document (the inbox tables are private; the interactive role reads them only through such
  functions, like ``intake_extraction_status``).
* ``complete_intake_extraction`` accepts the processor version ``intake-model-v1`` beside ``intake-local-v1`` (and
  records the one it was given), the optional tenth key ``vendor_iban`` of a proposal (a string of IBAN shape or null)
  and a certainty of that key, the page method ``reading_model`` beside ``text_layer`` and ``ocr``, and a page whose
  text is empty (a blank page reads to nothing). The key set of the evidence stays the nine fields. The CHECK
  ``extraction_revision_typed_attempt`` accepts both processor versions for an inbox attempt.
* ``field_evidence`` accepts the method ``reading_model`` for a located field (``page_quote``): the model that read a page
  states where it found a value (page and passage), the worker verifies both against the pages of that one logical
  document before it records the location, and the function above already requires the passage to occur in the stored text
  of that page and the method to be the page's own.

* A document of a multipage original is sent to a model only after a person has decided how the original divides (owner
  decision of 2026-11-22): the extraction job gets the status ``awaiting_split``, a state of the existing split workflow, not
  a new workflow. ``intake_split_state`` says for one logical document whether that decision is made: a single-page
  original needs none, a multipage one needs the latest confirmed source partition (0100; the partition that lists the
  whole original as one document is the confirmation "this is one document") to list the document. The claimed job's
  context carries that state; ``hold_intake_extraction`` parks the job when it is not made (``awaiting_split`` while no
  partition is confirmed, ``not_in_split`` for a document the latest partition leaves out, such as the unsplit whole after
  the split) and ``review_source_partition`` puts the parked documents the new partition lists back in the queue and marks
  the others ``not_in_split``. ``renew_intake_extraction`` refuses a claim whose document is no longer part of the
  decision, so no request is paid for after the partition changed. A parked job is not active and not failed in
  ``intake_batch_status``, which names the stage ``split``. A structured original needs no decision: the parking happens
  after its local parse, and only for an unstructured one. A parked job used one of its three attempts.

No row is read or rewritten and nothing is sent anywhere because of the upgrade: whether an organization's inbox
documents reach a provider stays governed by its own AI import switch, confirmation, key and models.

``downgrade()`` refuses while a ledger row of an inbox attempt, an ``intake-model-v1`` proposal or located evidence of a
model-read page exists, so that none is discarded silently: the older runtime cannot read a model-read proposal and a
dropped ledger row would reopen the monthly limit. It restores the older function text; the ``vendor_iban`` key of a stored proposal stays
where it is.

Revision ID: 0198
Revises: 0197
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0198'
down_revision = '0197'
branch_labels = None
depends_on = None

_COMPLETE = 'complete_intake_extraction(uuid,uuid,uuid,jsonb)'
# Fragments of the installed text of complete_intake_extraction (0098); the older definitions are not edited.
_VERSION_BEFORE = "OR result->>'processor_version' IS DISTINCT FROM 'intake-local-v1' OR state IS NULL OR"
_VERSION_AFTER = "OR COALESCE(result->>'processor_version','') NOT IN ('intake-local-v1','intake-model-v1') OR state IS NULL OR"
# The tenth key is optional, a string of IBAN shape or null (a JSON null leaves the condition unknown, which does not raise).
_KEYS_BEFORE = "(SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(proposal) k) IS DISTINCT FROM"
_KEYS_AFTER = """(jsonb_typeof(proposal->'vendor_iban') NOT IN ('null','string') OR proposal->>'vendor_iban' !~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$')
        OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(proposal) k WHERE k<>'vendor_iban') IS DISTINCT FROM"""
_CONFIDENCES_BEFORE = "IF field.key NOT IN ('vendor_name',"
_CONFIDENCES_AFTER = "IF field.key NOT IN ('vendor_iban','vendor_name',"
_METHOD_BEFORE = "page->>'method' NOT IN ('text_layer','ocr')"
_METHOD_AFTER = "page->>'method' NOT IN ('text_layer','ocr','reading_model')"
_LENGTH_BEFORE = "length(page->>'text') NOT BETWEEN 20 AND 1000000"
_LENGTH_AFTER = "length(page->>'text') NOT BETWEEN 0 AND 1000000"
_RECORDED_BEFORE = "context->>'content_sha256','intake-local-v1',state,result->>'error_code'"
_RECORDED_AFTER = "context->>'content_sha256',result->>'processor_version',state,result->>'error_code'"
_COMPLETE_CHANGES = (
    (_VERSION_BEFORE, _VERSION_AFTER), (_KEYS_BEFORE, _KEYS_AFTER), (_CONFIDENCES_BEFORE, _CONFIDENCES_AFTER),
    (_METHOD_BEFORE, _METHOD_AFTER), (_LENGTH_BEFORE, _LENGTH_AFTER), (_RECORDED_BEFORE, _RECORDED_AFTER),
)
_LOCATOR_UNLOCATED = "(locator_status='unlocated' AND page_number IS NULL AND quotation IS NULL AND xml_path IS NULL AND method='unlocated')"
_LOCATOR_XML = "(locator_status='xml_path' AND page_number IS NULL AND quotation IS NOT NULL AND length(quotation) BETWEEN 1 AND 4000 AND xml_path IS NOT NULL AND length(xml_path) BETWEEN 3 AND 16000 AND method='xml')"


def _locator_page(methods):
    return ("(locator_status='page_quote' AND page_number IS NOT NULL AND page_number BETWEEN 1 AND 200 AND quotation IS NOT NULL "
            f"AND length(quotation) BETWEEN 1 AND 4000 AND xml_path IS NULL AND method IN ({methods}))")


# The split gate (see the docstring). Fragments of the installed texts of the functions it extends (0098, 0099, 0100).
_CONTEXT = 'intake_extraction_context(uuid,uuid,uuid)'
_CONTEXT_BEFORE = "'verified_page_count',logical.verified_page_count);"
_CONTEXT_AFTER = "'verified_page_count',logical.verified_page_count,'split_state',public.intake_split_state(org,logical.id));"
_PARTITION = 'review_source_partition(uuid,uuid,uuid,uuid,uuid[],text,boolean)'
_PARTITION_BEFORE = "      RETURN public.source_partition_result(org,created);"
_PARTITION_AFTER = """      UPDATE public.intake_extraction_job j SET status='queued',error_code=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
        FROM public.logical_document l WHERE j.organization_id=org AND j.status='awaiting_split' AND l.organization_id=org
        AND l.id=j.logical_document_id AND l.document_ref_id=ref_id AND l.id=ANY(normalized);
      UPDATE public.intake_extraction_job j SET error_code='not_in_split',updated_at=clock_timestamp()
        FROM public.logical_document l WHERE j.organization_id=org AND j.status='awaiting_split' AND l.organization_id=org
        AND l.id=j.logical_document_id AND l.document_ref_id=ref_id AND NOT l.id=ANY(normalized);
      RETURN public.source_partition_result(org,created);"""
_BATCH = 'intake_batch_status(uuid,uuid,integer,integer)'
_BATCH_CHANGES = (
    ("total integer; active integer; failed integer; reviewable integer; failure text; stage text; state text;",
     "total integer; active integer; failed integer; reviewable integer; held integer; failure text; stage text; state text;"),
    ("ref:=NULL;total:=0;active:=0;failed:=0;reviewable:=0;", "ref:=NULL;total:=0;active:=0;failed:=0;reviewable:=0;held:=0;"),
    ("count(*) FILTER(WHERE j.status NOT IN ('queued','running','succeeded','needs_review'))\n            INTO total,active,reviewable,failed FROM",
     "count(*) FILTER(WHERE j.status NOT IN ('queued','running','succeeded','needs_review','awaiting_split')),\n"
     "            count(*) FILTER(WHERE j.status='awaiting_split')\n            INTO total,active,reviewable,failed,held FROM"),
    ("AND j.status NOT IN ('queued','running','succeeded','needs_review') ORDER BY j.created_at,j.id LIMIT 1;",
     "AND j.status NOT IN ('queued','running','succeeded','needs_review','awaiting_split') ORDER BY j.created_at,j.id LIMIT 1;"),
    ("          ELSIF total>0 THEN stage:='review';state:='review';failure:=NULL;",
     "          ELSIF reviewable=0 AND held>0 THEN\n            stage:='split';state:='review';\n"
     "            SELECT j.error_code INTO failure FROM public.intake_extraction_job j JOIN public.logical_document l ON l.organization_id=j.organization_id AND l.id=j.logical_document_id\n"
     "              WHERE l.organization_id=org AND l.document_ref_id=ref AND j.status='awaiting_split' ORDER BY j.created_at,j.id LIMIT 1;\n"
     "          ELSIF total>0 THEN stage:='review';state:='review';failure:=NULL;"),
)
_JOB_STATUS = "('queued','running','succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled','blocked')"
_JOB_STATUS_SPLIT = "('queued','running','succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled','blocked','awaiting_split')"

_TYPED_INVOICE = "(invoice_attempt_id IS NOT NULL AND intake_attempt_id IS NULL AND logical_document_id IS NULL AND processor_version IN ('invoice-local-v1','invoice-model-v1'))"
_TYPED_INTAKE_BEFORE = "(invoice_attempt_id IS NULL AND intake_attempt_id IS NOT NULL AND logical_document_id IS NOT NULL AND processor_version='intake-local-v1')"
_TYPED_INTAKE_AFTER = "(invoice_attempt_id IS NULL AND intake_attempt_id IS NOT NULL AND logical_document_id IS NOT NULL AND processor_version IN ('intake-local-v1','intake-model-v1'))"


def _replace_definition(function, before, after, *, reverting=False):
    """Replace one fragment of an installed function body: the older definitions are not edited.

    A fragment that already stands in the body is left alone, except when reverting: there the older text may be part of
    the newer one, and the newer one is what must go."""
    definition = op.get_bind().scalar(sa.text(f"SELECT pg_get_functiondef('public.{function}'::regprocedure)"))
    if not reverting and definition.count(after) == 1:
        return
    if definition.count(before) != 1:
        raise RuntimeError(f'Unexpected {function}')
    op.execute(definition.replace(before, after))


def _typed_attempt(intake_condition):
    op.execute('ALTER TABLE public.extraction_revision DROP CONSTRAINT extraction_revision_typed_attempt')
    op.execute(f"ALTER TABLE public.extraction_revision ADD CONSTRAINT extraction_revision_typed_attempt CHECK ({_TYPED_INVOICE} OR {intake_condition})")


def _evidence_locator(methods):
    op.execute('ALTER TABLE public.field_evidence DROP CONSTRAINT field_evidence_locator')
    op.execute(f"ALTER TABLE public.field_evidence ADD CONSTRAINT field_evidence_locator CHECK ({_LOCATOR_UNLOCATED} OR {_locator_page(methods)} OR {_LOCATOR_XML})")


def upgrade():
    op.execute("""
    ALTER TABLE public.invoice_model_call ALTER COLUMN attempt_id DROP NOT NULL;
    ALTER TABLE public.invoice_model_call ADD COLUMN intake_attempt_id uuid;
    ALTER TABLE public.invoice_model_call ADD CONSTRAINT fk_invoice_model_call_intake_attempt FOREIGN KEY (organization_id, intake_attempt_id)
      REFERENCES public.intake_extraction_attempt(organization_id, id) ON DELETE RESTRICT;
    ALTER TABLE public.invoice_model_call ADD CONSTRAINT ck_invoice_model_call_one_attempt CHECK ((attempt_id IS NULL) <> (intake_attempt_id IS NULL));
    CREATE INDEX ix_invoice_model_call_intake_attempt ON public.invoice_model_call (organization_id, intake_attempt_id, created_at)
      WHERE intake_attempt_id IS NOT NULL;

    CREATE FUNCTION public.renew_intake_extraction(org uuid, job uuid, token uuid, seconds integer) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context jsonb;
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Extraction runtime required' USING ERRCODE='42501'; END IF;
      IF seconds IS NULL OR seconds NOT BETWEEN 1 AND 3600 THEN RAISE EXCEPTION 'Invalid lease extension' USING ERRCODE='22023'; END IF;
      -- The one live-claim and initiator check of the inbox path: it also cancels the job of an initiator who lost the
      -- permission, which the caller commits instead of rolling it back.
      context:=public.intake_extraction_context(org,job,token);
      IF context IS NULL THEN RETURN false; END IF;
      -- A document that is no longer part of the decision how its original divides (a later partition left it out) is
      -- not paid for any more: the claim is refused like a lost fence.
      IF context->>'split_state' IS DISTINCT FROM 'decided' THEN RETURN false; END IF;
      UPDATE public.intake_extraction_job
        SET lease_expires_at=GREATEST(lease_expires_at,clock_timestamp()+make_interval(secs=>seconds)),updated_at=clock_timestamp()
        WHERE organization_id=org AND id=job;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION public.renew_intake_extraction(uuid,uuid,uuid,integer) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.renew_intake_extraction(uuid,uuid,uuid,integer) TO app_worker;

    CREATE FUNCTION public.intake_model_usage(org uuid, document uuid) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE original uuid;
    BEGIN
      PERFORM public.lock_intake(org);
      SELECT r.intake_original_id INTO original FROM public.logical_document l JOIN public.document_ref r ON r.organization_id=l.organization_id AND r.id=l.document_ref_id
        WHERE l.organization_id=org AND l.id=document;
      IF original IS NULL OR NOT public.intake_original_access(org,original,true) THEN RETURN NULL; END IF;
      RETURN COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt_number',a.attempt_number,'stage',c.stage,'pdf_page',c.pdf_page,
          'model_name',c.model_name,'status',c.status,'reserved_input_tokens',c.reserved_input_tokens,'reserved_output_tokens',c.reserved_output_tokens,
          'reserved_cost_usd',c.reserved_cost_usd::text,'input_tokens',c.input_tokens,'output_tokens',c.output_tokens,'cost_usd',c.cost_usd::text,
          'usage_complete',c.usage_complete,'error_code',c.error_code,'created_at',c.created_at) ORDER BY c.created_at,c.id)
        FROM public.invoice_model_call c
        JOIN public.intake_extraction_attempt a ON a.organization_id=c.organization_id AND a.id=c.intake_attempt_id
        JOIN public.intake_extraction_job j ON j.organization_id=a.organization_id AND j.id=a.job_id
        WHERE c.organization_id=org AND j.logical_document_id=document),'[]'::jsonb);
    END $$;
    REVOKE ALL ON FUNCTION public.intake_model_usage(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.intake_model_usage(uuid,uuid) TO app_rw;

    CREATE FUNCTION public.intake_split_state(org uuid, document uuid) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT CASE
        WHEN l.verified_page_count IS NULL OR l.verified_page_count<=1 THEN 'decided'
        WHEN NOT EXISTS(SELECT 1 FROM public.source_partition_revision r WHERE r.organization_id=org AND r.document_ref_id=l.document_ref_id) THEN 'awaiting'
        WHEN EXISTS(SELECT 1 FROM public.source_partition_member m WHERE m.organization_id=org AND m.logical_document_id=l.id
          AND m.partition_revision_id=(SELECT r.id FROM public.source_partition_revision r WHERE r.organization_id=org AND r.document_ref_id=l.document_ref_id
            ORDER BY r.revision_number DESC LIMIT 1)) THEN 'decided'
        ELSE 'excluded' END
      FROM public.logical_document l WHERE l.organization_id=org AND l.id=document;
    $$;
    REVOKE ALL ON FUNCTION public.intake_split_state(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION public.hold_intake_extraction(org uuid, job uuid, token uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE context jsonb; reason text;
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Extraction runtime required' USING ERRCODE='42501'; END IF;
      context:=public.intake_extraction_context(org,job,token);
      IF context IS NULL THEN RETURN false; END IF;
      -- Parked while nobody has decided how the original divides (or the latest decision leaves this document out); if the
      -- decision was made after the claim, the job is queued again instead.
      reason:=CASE context->>'split_state' WHEN 'awaiting' THEN 'awaiting_split' WHEN 'excluded' THEN 'not_in_split' END;
      UPDATE public.intake_extraction_attempt SET finished_at=clock_timestamp(),
        result=jsonb_build_object('status',CASE WHEN reason IS NULL THEN 'queued' ELSE 'awaiting_split' END,'error_code',reason)
        WHERE organization_id=org AND job_id=job AND fence=token AND finished_at IS NULL;
      UPDATE public.intake_extraction_job SET status=CASE WHEN reason IS NULL THEN 'queued' ELSE 'awaiting_split' END,error_code=reason,
        lease_expires_at=NULL,updated_at=clock_timestamp() WHERE organization_id=org AND id=job;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION public.hold_intake_extraction(uuid,uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT EXECUTE ON FUNCTION public.hold_intake_extraction(uuid,uuid,uuid) TO app_worker;
    ALTER TABLE public.intake_extraction_job DROP CONSTRAINT intake_extraction_job_status_check;
    ALTER TABLE public.intake_extraction_job ADD CONSTRAINT intake_extraction_job_status_check CHECK (status IN """ + _JOB_STATUS_SPLIT + """);
    """)
    _replace_definition(_CONTEXT, _CONTEXT_BEFORE, _CONTEXT_AFTER)
    _replace_definition(_PARTITION, _PARTITION_BEFORE, _PARTITION_AFTER)
    for before, after in _BATCH_CHANGES:
        _replace_definition(_BATCH, before, after)
    for before, after in _COMPLETE_CHANGES:
        _replace_definition(_COMPLETE, before, after)
    _typed_attempt(_TYPED_INTAKE_AFTER)
    _evidence_locator("'text_layer','ocr','reading_model'")


def downgrade():
    op.execute('LOCK TABLE public.invoice_model_call, public.intake_extraction_job, public.intake_extraction_attempt, public.extraction_revision, public.field_evidence IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.invoice_model_call WHERE intake_attempt_id IS NOT NULL)
      THEN RAISE EXCEPTION 'Inbox model call records require this release'; END IF; END $$""")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.extraction_revision WHERE processor_version='intake-model-v1')
      OR EXISTS(SELECT 1 FROM public.field_evidence WHERE method='reading_model')
      THEN RAISE EXCEPTION 'Model-read inbox proposals require this release'; END IF; END $$""")
    _evidence_locator("'text_layer','ocr'")
    _typed_attempt(_TYPED_INTAKE_BEFORE)
    for before, after in reversed(_COMPLETE_CHANGES):
        _replace_definition(_COMPLETE, after, before)
    # The older runtime sends a whole original: a parked job goes back in the queue, which no record is lost by.
    op.execute("""UPDATE public.intake_extraction_job SET status='queued',error_code=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
      WHERE status='awaiting_split'""")
    for before, after in reversed(_BATCH_CHANGES):
        _replace_definition(_BATCH, after, before, reverting=True)
    _replace_definition(_PARTITION, _PARTITION_AFTER, _PARTITION_BEFORE, reverting=True)
    _replace_definition(_CONTEXT, _CONTEXT_AFTER, _CONTEXT_BEFORE, reverting=True)
    op.execute("""
    ALTER TABLE public.intake_extraction_job DROP CONSTRAINT intake_extraction_job_status_check;
    ALTER TABLE public.intake_extraction_job ADD CONSTRAINT intake_extraction_job_status_check CHECK (status IN """ + _JOB_STATUS + """);
    DROP FUNCTION public.hold_intake_extraction(uuid,uuid,uuid);
    DROP FUNCTION public.intake_split_state(uuid,uuid);
    DROP FUNCTION public.intake_model_usage(uuid,uuid);
    DROP FUNCTION public.renew_intake_extraction(uuid,uuid,uuid,integer);
    DROP INDEX public.ix_invoice_model_call_intake_attempt;
    ALTER TABLE public.invoice_model_call DROP CONSTRAINT ck_invoice_model_call_one_attempt;
    ALTER TABLE public.invoice_model_call DROP CONSTRAINT fk_invoice_model_call_intake_attempt;
    ALTER TABLE public.invoice_model_call DROP COLUMN intake_attempt_id;
    ALTER TABLE public.invoice_model_call ALTER COLUMN attempt_id SET NOT NULL;
    """)
