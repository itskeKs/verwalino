"""Two model roles for the organization AI settings and the reading stage's persistence (issue #499, ADR 0017).

The organization has two model roles with one provider, endpoint, timeout and credential: the existing ``model``
(extraction, questions) and the new ``document_model`` (reading documents). The column is nullable and the
migration writes no row: ``model`` and every other column stay as they are, and no organization gets a reading
model until an administrator chooses one. A paid model is never selected on the organization's behalf.

The reading stage of the declaration import needs three more things, all in this one revision:

* ``declaration_extraction_run.document_model_name`` names the reading model a run used beside ``model_name``;
  a job keeps both identities across its resumes.
* ``declaration_page_reading`` keeps the transcript of every page the reading model has finished, so that a
  resumed job neither asks again nor pays again. Nothing held per-page text before (the local reader's text was
  recomputed on every run), hence the table. Only the worker writes it, and a guard fences each insert to the
  live lease with the existing worker effect receipt, like the other import outputs. Reading follows the access to the import's
  other source data (``property_import_runtime_access``), so the recorded evaluation can re-check the quotes
  of a finished import against the text they were taken from. The transcripts are kept as long as the import's
  documents, until the organization is erased; nothing cascades them away earlier.
* the pass-ledger validator accepts the pass id ``read_page``.
* ``organization_ai_monthly_usage`` holds a RUNNING run at its cap (``greatest(max_cost_usd, estimated)``) when
  its extraction model or its reading model is priced, with the same provider guard as before for both; it
  asked only for the extraction model, so a priced reading model beside an unpriced extraction model left a
  running import counted at what was already charged.

``downgrade()`` refuses while a reading model or a reading-pass record is stored, so that neither is discarded
silently; the older runtime has no reading role and would run the import without it. The page transcripts of
every import, finished ones included, are dropped with their table: they are working data copied from the
originals, which stay, and a resumed job reads them again.

Revision ID: 0196
Revises: 0195
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = '0196'
down_revision = '0195'
branch_labels = None
depends_on = None

_PASS_IDS_OLD = "NOT IN ('inventory', 'extract_core', 'extract_rules', 'reconcile', 'recheck')"
_PASS_IDS_NEW = "NOT IN ('inventory', 'extract_core', 'extract_rules', 'reconcile', 'recheck', 'read_page')"
_ANTHROPIC_PRICED = "('claude-haiku-4-5','claude-haiku-4-5-20251001')"
_MONTHLY_USAGE = 'organization_ai_monthly_usage(uuid,text[])'
# As patched in 0179: a RUNNING run is held at its cap only when its extraction model is priced.
_MONTHLY_OLD = (
    "r.status='RUNNING' AND ((r.provider='OpenAiDeclarationProvider' AND r.model_name=ANY(priced_models)) "
    f"OR (r.provider='AnthropicDeclarationProvider' AND r.model_name IN {_ANTHROPIC_PRICED}))"
)
# The reading model counts like the extraction model, under the same provider guard.
_MONTHLY_NEW = (
    "r.status='RUNNING' AND ((r.provider='OpenAiDeclarationProvider' AND (r.model_name=ANY(priced_models) "
    "OR r.document_model_name=ANY(priced_models))) "
    f"OR (r.provider='AnthropicDeclarationProvider' AND (r.model_name IN {_ANTHROPIC_PRICED} "
    f"OR r.document_model_name IN {_ANTHROPIC_PRICED})))"
)


def _replace_definition(function, before, after):
    """Replace one fragment of an installed function body: the 0028 and 0071 definitions are not edited."""
    definition = op.get_bind().scalar(sa.text(f"SELECT pg_get_functiondef('public.{function}'::regprocedure)"))
    if definition.count(after) == 1:
        return
    if definition.count(before) != 1:
        raise RuntimeError(f'Unexpected {function}')
    op.execute(definition.replace(before, after))


def upgrade():
    op.add_column('organization_ai_settings', sa.Column('document_model', sa.String(255)))
    op.add_column('declaration_extraction_run', sa.Column('document_model_name', sa.String(255)))
    _replace_definition('declaration_import_pass_safe_metadata_is_valid(jsonb,integer)', _PASS_IDS_OLD, _PASS_IDS_NEW)
    _replace_definition(_MONTHLY_USAGE, _MONTHLY_OLD, _MONTHLY_NEW)
    op.execute("""
    CREATE TABLE public.declaration_page_reading (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL REFERENCES public.organization(id) ON DELETE RESTRICT,
      import_id uuid NOT NULL,
      document_id uuid NOT NULL,
      pdf_page integer NOT NULL CHECK (pdf_page > 0),
      model_name varchar(255) NOT NULL CHECK (model_name <> ''),
      blocks jsonb NOT NULL CHECK (jsonb_typeof(blocks) = 'array'),
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_declaration_page_reading_page UNIQUE (organization_id, import_id, document_id, pdf_page),
      CONSTRAINT fk_declaration_page_reading_document FOREIGN KEY (organization_id, import_id, document_id)
        REFERENCES public.declaration_import_document(organization_id, import_id, document_id) ON DELETE CASCADE
    );
    ALTER TABLE public.declaration_page_reading ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.declaration_page_reading FORCE ROW LEVEL SECURITY;
    -- The access rule of the import's other source data, called by name inside a function body so that this table
    -- does not hold the older revisions' functions in place.
    CREATE FUNCTION public.declaration_page_reading_readable(org uuid, job uuid) RETURNS boolean
      LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      RETURN session_user = 'app_worker' OR public.property_import_runtime_access(org, job, 'read');
    END $$;
    REVOKE ALL ON FUNCTION public.declaration_page_reading_readable(uuid,uuid) FROM PUBLIC,app_review;
    GRANT EXECUTE ON FUNCTION public.declaration_page_reading_readable(uuid,uuid) TO app_rw;
    CREATE POLICY declaration_page_reading_read ON public.declaration_page_reading FOR SELECT TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.declaration_page_reading_readable(organization_id, import_id));
    CREATE POLICY declaration_page_reading_insert ON public.declaration_page_reading FOR INSERT TO app_rw
      WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid AND session_user = 'app_worker');
    CREATE POLICY declaration_page_reading_delete ON public.declaration_page_reading FOR DELETE TO app_rw
      USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid AND session_user = 'app_worker');
    REVOKE ALL ON public.declaration_page_reading FROM PUBLIC,app_rw,app_worker,app_review;
    GRANT SELECT,INSERT,DELETE ON public.declaration_page_reading TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.declaration_page_reading
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();
    -- Inserts only: the runtime holds no UPDATE and deletes happen as the owner (erasure, the import's cascade). A
    -- DELETE trigger of another function would also make the retention catalog retain the page transcripts.
    CREATE FUNCTION public.guard_declaration_page_reading() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE owner name;
    BEGIN
      SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.guard_declaration_page_reading()'::regprocedure;
      IF current_user=owner THEN RETURN NEW; END IF;
      IF NOT COALESCE(public.declaration_worker_effect_access(NEW.organization_id,NEW.import_id,
          ARRAY['PREFLIGHT','EXTRACTING','RECONCILING']),false) THEN
        RAISE EXCEPTION 'Current worker effect receipt required' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION public.guard_declaration_page_reading() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER g_guard_page_reading BEFORE INSERT ON public.declaration_page_reading
      FOR EACH ROW EXECUTE FUNCTION public.guard_declaration_page_reading();
    """)


def downgrade():
    op.execute('LOCK TABLE organization_ai_settings, declaration_extraction_run, declaration_import_pass IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM organization_ai_settings WHERE document_model IS NOT NULL)
      THEN RAISE EXCEPTION 'Configured document reading models require this release'; END IF; END $$""")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM declaration_import_pass WHERE safe_metadata->>'pass_id'='read_page')
      THEN RAISE EXCEPTION 'Reading pass records require this release'; END IF; END $$""")
    # The page transcripts of every import, finished ones included, are working data copied from the originals,
    # which stay; a resumed job reads them again.
    op.execute('DROP TABLE public.declaration_page_reading')
    op.execute('DROP FUNCTION public.guard_declaration_page_reading()')
    op.execute('DROP FUNCTION public.declaration_page_reading_readable(uuid,uuid)')
    _replace_definition('declaration_import_pass_safe_metadata_is_valid(jsonb,integer)', _PASS_IDS_NEW, _PASS_IDS_OLD)
    _replace_definition(_MONTHLY_USAGE, _MONTHLY_NEW, _MONTHLY_OLD)
    op.drop_column('declaration_extraction_run', 'document_model_name')
    op.drop_column('organization_ai_settings', 'document_model')
