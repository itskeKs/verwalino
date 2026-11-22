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


_TYPED_INVOICE = "(invoice_attempt_id IS NOT NULL AND intake_attempt_id IS NULL AND logical_document_id IS NULL AND processor_version IN ('invoice-local-v1','invoice-model-v1'))"
_TYPED_INTAKE_BEFORE = "(invoice_attempt_id IS NULL AND intake_attempt_id IS NOT NULL AND logical_document_id IS NOT NULL AND processor_version='intake-local-v1')"
_TYPED_INTAKE_AFTER = "(invoice_attempt_id IS NULL AND intake_attempt_id IS NOT NULL AND logical_document_id IS NOT NULL AND processor_version IN ('intake-local-v1','intake-model-v1'))"


def _replace_definition(function, before, after):
    """Replace one fragment of an installed function body: the older definitions are not edited."""
    definition = op.get_bind().scalar(sa.text(f"SELECT pg_get_functiondef('public.{function}'::regprocedure)"))
    if definition.count(after) == 1:
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
    BEGIN
      IF session_user<>'app_worker' THEN RAISE EXCEPTION 'Extraction runtime required' USING ERRCODE='42501'; END IF;
      IF seconds IS NULL OR seconds NOT BETWEEN 1 AND 3600 THEN RAISE EXCEPTION 'Invalid lease extension' USING ERRCODE='22023'; END IF;
      -- The one live-claim and initiator check of the inbox path: it also cancels the job of an initiator who lost the
      -- permission, which the caller commits instead of rolling it back.
      IF public.intake_extraction_context(org,job,token) IS NULL THEN RETURN false; END IF;
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
    """)
    for before, after in _COMPLETE_CHANGES:
        _replace_definition(_COMPLETE, before, after)
    _typed_attempt(_TYPED_INTAKE_AFTER)
    _evidence_locator("'text_layer','ocr','reading_model'")


def downgrade():
    op.execute('LOCK TABLE public.invoice_model_call, public.intake_extraction_attempt, public.extraction_revision, public.field_evidence IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.invoice_model_call WHERE intake_attempt_id IS NOT NULL)
      THEN RAISE EXCEPTION 'Inbox model call records require this release'; END IF; END $$""")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.extraction_revision WHERE processor_version='intake-model-v1')
      OR EXISTS(SELECT 1 FROM public.field_evidence WHERE method='reading_model')
      THEN RAISE EXCEPTION 'Model-read inbox proposals require this release'; END IF; END $$""")
    _evidence_locator("'text_layer','ocr'")
    _typed_attempt(_TYPED_INTAKE_BEFORE)
    for before, after in reversed(_COMPLETE_CHANGES):
        _replace_definition(_COMPLETE, after, before)
    op.execute("""
    DROP FUNCTION public.intake_model_usage(uuid,uuid);
    DROP FUNCTION public.renew_intake_extraction(uuid,uuid,uuid,integer);
    DROP INDEX public.ix_invoice_model_call_intake_attempt;
    ALTER TABLE public.invoice_model_call DROP CONSTRAINT ck_invoice_model_call_one_attempt;
    ALTER TABLE public.invoice_model_call DROP CONSTRAINT fk_invoice_model_call_intake_attempt;
    ALTER TABLE public.invoice_model_call DROP COLUMN intake_attempt_id;
    ALTER TABLE public.invoice_model_call ALTER COLUMN attempt_id SET NOT NULL;
    """)
