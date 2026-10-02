"""Require reviewed, enabled runtime policy at strict Q&A reservation admission."""
import hashlib
from alembic import op
from sqlalchemy import text

revision = '0089'
down_revision = '0088'
branch_labels = None
depends_on = None
SIGNATURE = 'reserve_knowledge_query(uuid,uuid,text,text,numeric)'
EXPECTED = 'e21d75da6b66b5bb0d8a4101ec00968cbfab17bca053546640ac52bd64df5c6e'
BEFORE = '      SELECT * INTO policy FROM public.knowledge_budget_policy WHERE organization_id=org;'
AFTER = '''      -- Source checks hold shared ACL before inspecting current review evidence.
      -- The proof is minted by app_review for this actual app_rw transaction.
      runtime_policy:=(public.current_runtime_budget_attestation(org)).policy;
      IF runtime_policy IS NULL OR (runtime_policy->>'enabled')::boolean IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Reviewed runtime budget required' USING ERRCODE='55000'; END IF;
      IF NOT public.runtime_budget_matches_review(org,runtime_policy) THEN
        RAISE EXCEPTION 'Reviewed runtime budget required' USING ERRCODE='55000'; END IF;
      SELECT * INTO policy FROM public.knowledge_budget_policy WHERE organization_id=org;'''
DECLARATION = '    DECLARE policy public.knowledge_budget_policy;'
REPLACEMENT = '    DECLARE runtime_policy jsonb; policy public.knowledge_budget_policy;'


def upgrade():
    db = op.get_bind()
    body = db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':SIGNATURE})
    assert hashlib.sha256(body.encode()).hexdigest()==EXPECTED
    definition = db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':SIGNATURE})
    assert definition.count(BEFORE)==1 and definition.count(DECLARATION)==1
    op.execute(definition.replace(BEFORE,AFTER).replace(DECLARATION,REPLACEMENT))


def downgrade():
    op.execute('LOCK TABLE property_source_security IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode<>'PREPARATION') THEN
      RAISE EXCEPTION 'Retain strict runtime budget enforcement; repair forward'; END IF; END $$""")
    db = op.get_bind()
    body = db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':SIGNATURE})
    assert body.count(AFTER)==1 and body.count(REPLACEMENT)==1
    restored = body.replace(AFTER,BEFORE).replace(REPLACEMENT,DECLARATION)
    assert hashlib.sha256(restored.encode()).hexdigest()==EXPECTED
    definition = db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':SIGNATURE})
    op.execute(definition.replace(AFTER,BEFORE).replace(REPLACEMENT,DECLARATION))
