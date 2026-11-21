"""The metering ledger of the model-assisted invoice path (issue #500, ADR 0017).

An unstructured invoice PDF is read page by page with the organization's document reading model and then
interpreted by its extraction model. Every one of those requests is reserved before it leaves and settled with the
usage it consumed. This revision holds everything the invoice path needs for that, so that nothing else has to
change the schema for it:

* ``invoice_model_call`` has one row per request: the attempt it belongs to, its stage (``read_page`` or
  ``extract_invoice``), the page it read, the model that served it, the reservation made before the request and,
  once it finished, the usage it consumed (``usage_complete`` is false when the provider could not report it and
  the reservation stands in for it) and the stable code of a failure. Forced row-level security, a composite
  foreign key to the attempt, rows inserted and settled only by the worker role (a trigger lets a RESERVED row
  become SETTLED once and nothing else), ``SELECT, INSERT, UPDATE, DELETE`` granted to ``app_rw`` and nothing to
  ``app_worker`` or ``app_review``; the retention catalog classifies the table ``delete`` and erasure removes it.
  It is working and accounting data of the runtime and not part of the organization export.
* ``organization_ai_monthly_usage`` adds the table as a third source: a RESERVED row counts at its reservation, a
  SETTLED row at its consumed cost, so the invoice path counts towards the monthly limit like the declaration
  import and the property questions do.
* The invoice proposals of a model-read invoice carry the processor version ``invoice-model-v1`` beside the
  ``invoice-local-v1`` of the deterministic parsers: the CHECK ``extraction_revision_typed_attempt`` and the
  function ``record_invoice_extraction_revision`` accept both for an invoice attempt (nothing else changes in
  either; the intake processor version stays as it was).

No row is read or rewritten by the upgrade and nothing is sent anywhere because of it: whether an organization's
invoices reach a provider stays governed by the organization's own AI import switch, confirmation, key and models.

``downgrade()`` refuses while a call record or a ``invoice-model-v1`` proposal exists, so that neither is discarded
silently: the older runtime cannot read a model-read proposal and a dropped ledger would reopen the monthly limit.

Revision ID: 0197
Revises: 0196
Create Date: 2026-11-21
"""
from alembic import op
import sqlalchemy as sa

revision = '0197'
down_revision = '0196'
branch_labels = None
depends_on = None

_MONTHLY_USAGE = 'organization_ai_monthly_usage(uuid,text[])'
_RECORD = 'record_invoice_extraction_revision(uuid,uuid,uuid,jsonb)'
# As installed by 0178 and left by 0179 and 0196: the closing of the union of charges.
_MONTHLY_BEFORE = "i.lease_expires_at>=month_start))\n        ) charges;"
_MONTHLY_AFTER = """i.lease_expires_at>=month_start))
          UNION ALL
          SELECT CASE WHEN c.status='SETTLED' THEN c.cost_usd ELSE c.reserved_cost_usd END cost
            FROM public.invoice_model_call c WHERE c.organization_id=org
              AND (c.created_at>=month_start OR c.settled_at>=month_start)
        ) charges;"""
_RECORD_BEFORE = "result->>'processor_version' IS DISTINCT FROM 'invoice-local-v1'"
_RECORD_AFTER = "result->>'processor_version' NOT IN ('invoice-local-v1','invoice-model-v1')"
_TYPED_BEFORE = "(invoice_attempt_id IS NOT NULL AND intake_attempt_id IS NULL AND logical_document_id IS NULL AND processor_version='invoice-local-v1')"
_TYPED_AFTER = "(invoice_attempt_id IS NOT NULL AND intake_attempt_id IS NULL AND logical_document_id IS NULL AND processor_version IN ('invoice-local-v1','invoice-model-v1'))"
_TYPED_INTAKE = "(invoice_attempt_id IS NULL AND intake_attempt_id IS NOT NULL AND logical_document_id IS NOT NULL AND processor_version='intake-local-v1')"


def _replace_definition(function, before, after):
    """Replace one fragment of an installed function body: the older definitions are not edited."""
    definition = op.get_bind().scalar(sa.text(f"SELECT pg_get_functiondef('public.{function}'::regprocedure)"))
    if definition.count(after) == 1:
        return
    if definition.count(before) != 1:
        raise RuntimeError(f'Unexpected {function}')
    op.execute(definition.replace(before, after))


def _typed_attempt(invoice_condition):
    op.execute('ALTER TABLE public.extraction_revision DROP CONSTRAINT extraction_revision_typed_attempt')
    op.execute(f"ALTER TABLE public.extraction_revision ADD CONSTRAINT extraction_revision_typed_attempt CHECK ({invoice_condition} OR {_TYPED_INTAKE})")


def upgrade():
    op.execute("""
    CREATE TABLE public.invoice_model_call (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES public.organization(id) ON DELETE RESTRICT,
      attempt_id uuid NOT NULL,
      stage text NOT NULL CHECK (stage IN ('read_page','extract_invoice')),
      pdf_page integer CHECK (pdf_page > 0),
      model_name varchar(255) NOT NULL CHECK (model_name <> ''),
      status text NOT NULL DEFAULT 'RESERVED' CHECK (status IN ('RESERVED','SETTLED')),
      reserved_input_tokens bigint NOT NULL CHECK (reserved_input_tokens >= 0),
      reserved_output_tokens bigint NOT NULL CHECK (reserved_output_tokens >= 0),
      reserved_cost_usd numeric(18,10) NOT NULL CHECK (reserved_cost_usd >= 0),
      input_tokens bigint CHECK (input_tokens >= 0),
      output_tokens bigint CHECK (output_tokens >= 0),
      cost_usd numeric(18,10) CHECK (cost_usd >= 0),
      usage_complete boolean,
      error_code varchar(64),
      created_at timestamptz NOT NULL DEFAULT now(),
      settled_at timestamptz,
      CONSTRAINT uq_invoice_model_call_org_id UNIQUE (organization_id, id),
      CONSTRAINT fk_invoice_model_call_attempt FOREIGN KEY (organization_id, attempt_id)
        REFERENCES public.invoice_processing_attempt(organization_id, id) ON DELETE RESTRICT,
      CONSTRAINT ck_invoice_model_call_stage_page CHECK ((stage = 'read_page') = (pdf_page IS NOT NULL)),
      CONSTRAINT ck_invoice_model_call_settled CHECK (
        (status = 'RESERVED' AND settled_at IS NULL AND input_tokens IS NULL AND output_tokens IS NULL
          AND cost_usd IS NULL AND usage_complete IS NULL AND error_code IS NULL)
        OR (status = 'SETTLED' AND settled_at IS NOT NULL AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL
          AND cost_usd IS NOT NULL AND usage_complete IS NOT NULL))
    );
    CREATE INDEX ix_invoice_model_call_attempt ON public.invoice_model_call (organization_id, attempt_id, created_at);
    ALTER TABLE public.invoice_model_call ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.invoice_model_call FORCE ROW LEVEL SECURITY;
    CREATE POLICY invoice_model_call_read ON public.invoice_model_call FOR SELECT TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid);
    CREATE POLICY invoice_model_call_insert ON public.invoice_model_call FOR INSERT TO app_rw
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid AND session_user = 'app_worker');
    CREATE POLICY invoice_model_call_update ON public.invoice_model_call FOR UPDATE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid AND session_user = 'app_worker')
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid AND session_user = 'app_worker');
    CREATE POLICY invoice_model_call_delete ON public.invoice_model_call FOR DELETE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid AND session_user = 'app_worker');
    REVOKE ALL ON public.invoice_model_call FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT,INSERT,UPDATE,DELETE ON public.invoice_model_call TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.invoice_model_call
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();
    -- UPDATE only: a RESERVED row becomes SETTLED once, with its usage, and nothing else changes. Deletes happen as the
    -- owner (erasure); a DELETE trigger of another function would make the retention catalog retain the ledger.
    CREATE FUNCTION public.guard_invoice_model_call() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE owner name;
    BEGIN
      SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.guard_invoice_model_call()'::regprocedure;
      IF current_user=owner THEN RETURN NEW; END IF;
      IF OLD.status<>'RESERVED' OR NEW.status<>'SETTLED'
        OR (to_jsonb(NEW)-ARRAY['status','input_tokens','output_tokens','cost_usd','usage_complete','error_code','settled_at'])
          IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','input_tokens','output_tokens','cost_usd','usage_complete','error_code','settled_at']) THEN
        RAISE EXCEPTION 'A model call is settled once and its reservation never changes' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION public.guard_invoice_model_call() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER g_guard_invoice_model_call BEFORE UPDATE ON public.invoice_model_call
      FOR EACH ROW EXECUTE FUNCTION public.guard_invoice_model_call();
    """)
    _replace_definition(_MONTHLY_USAGE, _MONTHLY_BEFORE, _MONTHLY_AFTER)
    _replace_definition(_RECORD, _RECORD_BEFORE, _RECORD_AFTER)
    _typed_attempt(_TYPED_AFTER)


def downgrade():
    op.execute('LOCK TABLE public.invoice_model_call, public.invoice_processing_attempt, public.extraction_revision IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.invoice_model_call)
      THEN RAISE EXCEPTION 'Invoice model call records require this release'; END IF; END $$""")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.extraction_revision WHERE processor_version='invoice-model-v1')
      OR EXISTS(SELECT 1 FROM public.invoice_processing_attempt WHERE processor_version='invoice-model-v1')
      THEN RAISE EXCEPTION 'Model-read invoice proposals require this release'; END IF; END $$""")
    _typed_attempt(_TYPED_BEFORE)
    _replace_definition(_RECORD, _RECORD_AFTER, _RECORD_BEFORE)
    _replace_definition(_MONTHLY_USAGE, _MONTHLY_AFTER, _MONTHLY_BEFORE)
    op.execute('DROP TABLE public.invoice_model_call')
    op.execute('DROP FUNCTION public.guard_invoice_model_call()')
