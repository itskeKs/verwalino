"""A retained source replacement is a typed exception outcome, not a copy."""
from alembic import op
from sqlalchemy import text

revision='0123'
down_revision='0122'
branch_labels=None
depends_on=None

# 0122 helper functions (inlined to avoid importlib):
def definition(signature):
    return op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:s AS regprocedure))'),{'s':signature})

def change(source,before,after):
    assert source.count(before)==1,(before,source.count(before))
    return source.replace(before,after)

def clone(signature,name,changes=()):
    source=change(definition(signature),'FUNCTION public.'+signature.split('(')[0]+'(','FUNCTION public.'+name+'(')
    for before,after in changes:source=change(source,before,after)
    op.execute(source)

def patch(signature,changes):
    source=definition(signature)
    for before,after in changes:source=change(source,before,after)
    op.execute(source)

ADAPTED=['evaluate_exception_completion(uuid,uuid)','validate_exception_completion()',
    'create_exception_completion(uuid,uuid,uuid,uuid,text,text,boolean)','completion_case_audience(uuid,uuid,boolean)']
ADDED=['replacement_source_outcome(uuid,uuid)','allocation_replacement_relation(uuid,uuid,uuid)']


def upgrade():
    op.execute("""DO $$ DECLARE row record;BEGIN
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||row.id::text,0));END LOOP;
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||row.id::text,0));END LOOP;
      END $$;
      LOCK TABLE exception_completion,exception_completion_member,exception_case_event,allocation_set IN SHARE ROW EXCLUSIVE MODE NOWAIT;
      ALTER TABLE exception_completion_member DROP CONSTRAINT exception_completion_member_disposition_check,
        ADD CONSTRAINT exception_completion_member_disposition_check CHECK(disposition IN ('adopted_document','covered_copy','replaced_document')),
        ADD COLUMN replacement_head_id uuid, ADD COLUMN replacement_receipt_id uuid,
        ADD CONSTRAINT completion_replacement_head_fk FOREIGN KEY(organization_id,replacement_head_id) REFERENCES allocation_set(organization_id,id),
        ADD CONSTRAINT completion_replacement_receipt_fk FOREIGN KEY(organization_id,replacement_receipt_id) REFERENCES allocation_correction(organization_id,id),
        ADD CONSTRAINT completion_replacement_shape CHECK(
          (disposition='replaced_document' AND replacement_head_id IS NOT NULL AND replacement_receipt_id IS NOT NULL)
          OR (disposition<>'replaced_document' AND replacement_head_id IS NULL AND replacement_receipt_id IS NULL));""")
    for signature in ADAPTED:
        h.clone(signature,'pre_replaced_outcome_'+signature.split('(')[0])
        op.execute('REVOKE ALL ON FUNCTION pre_replaced_outcome_'+signature+' FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute(HELPERS)
    evaluator()
    seal()
    h.patch('create_exception_completion(uuid,uuid,uuid,uuid,text,text,boolean)',[
        ('property_id,allocation_set_id,disposition)', 'property_id,allocation_set_id,disposition,replacement_head_id,replacement_receipt_id)'),
        ("(m->>'property_id')::uuid,(m->>'allocation_set_id')::uuid,m->>'disposition' FROM", "(m->>'property_id')::uuid,(m->>'allocation_set_id')::uuid,m->>'disposition',(m->'replacement'->>'allocation_head_id')::uuid,(m->'replacement'->>'replacement_receipt_id')::uuid FROM"),
    ])
    h.patch('completion_case_audience(uuid,uuid,boolean)',[
        ('OR NOT public.allocation_set_access(org,m.allocation_set_id)',
         'OR NOT public.allocation_set_access(org,m.allocation_set_id) OR (m.replacement_head_id IS NOT NULL AND NOT public.allocation_set_access(org,m.replacement_head_id))'),
    ])
    op.execute('REVOKE ALL ON FUNCTION '+','.join(ADDED)+' FROM PUBLIC,app_rw,app_worker,app_review')


def downgrade():
    op.execute("""LOCK TABLE exception_completion,exception_completion_member IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM exception_completion_member WHERE disposition='replaced_document') THEN
        RAISE EXCEPTION 'Retain replacement completion evidence; repair forward';END IF;END $$;""")
    for signature in reversed(ADAPTED):
        h.clone('pre_replaced_outcome_'+signature,signature.split('(')[0])
        op.execute('DROP FUNCTION pre_replaced_outcome_'+signature)
    op.execute('DROP FUNCTION '+','.join(reversed(ADDED)))
    op.execute("""ALTER TABLE exception_completion_member DROP COLUMN replacement_head_id,DROP COLUMN replacement_receipt_id,
      DROP CONSTRAINT exception_completion_member_disposition_check,
      ADD CONSTRAINT exception_completion_member_disposition_check CHECK(disposition IN ('adopted_document','covered_copy'));""")


HELPERS=r"""
CREATE FUNCTION allocation_replacement_relation(org uuid,left_set uuid,right_set uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE ancestry uuid[];path uuid[];earlier uuid;later uuid;receipts jsonb;n integer;
BEGIN
  IF left_set IS NULL OR right_set IS NULL OR left_set=right_set THEN RETURN NULL;END IF;
  ancestry:=public.allocation_ancestors(org,right_set);
  IF left_set=ANY(ancestry) THEN earlier:=left_set;later:=right_set;
  ELSE
    ancestry:=public.allocation_ancestors(org,left_set);
    IF NOT right_set=ANY(ancestry) THEN RETURN NULL;END IF;
    earlier:=right_set;later:=left_set;
  END IF;
  path:=ancestry[1:array_position(ancestry,earlier)-1];
  IF NOT EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND id=ANY(path) AND transition_kind='source_replacement') THEN RETURN NULL;END IF;
  SELECT jsonb_agg(id ORDER BY array_position(path,successor_id)),count(*) INTO receipts,n
    FROM public.allocation_correction WHERE organization_id=org AND successor_id=ANY(path);
  IF n<>cardinality(path) THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('earlier_allocation_id',earlier,'later_allocation_id',later,'correction_ids',receipts);
END $$;

CREATE FUNCTION replacement_source_outcome(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE candidates uuid[];old public.allocation_set;head public.allocation_set;path uuid[];proof jsonb;receipt uuid;
BEGIN
  -- Find the unique physical source at its final retained allocation before a
  -- changed-source transition. Do not filter by audience before ambiguity.
  SELECT array_agg(id) INTO candidates FROM (
    SELECT s.id FROM public.allocation_set s JOIN public.allocation_set successor
      ON successor.organization_id=s.organization_id AND successor.predecessor_id=s.id AND successor.transition_kind='source_replacement'
    JOIN public.invoice_review_revision r ON r.organization_id=s.organization_id AND r.id=target
    JOIN public.invoice_review_revision retained ON retained.organization_id=s.organization_id AND retained.id=s.invoice_review_id
    WHERE s.organization_id=org AND s.kind='intake_reviewed' AND s.scope_id=r.scope_id
      AND public.review_physical_relation(org,s.invoice_review_id,r.id)='equal'
      AND public.adoption_invoice_identity(r)=public.adoption_invoice_identity(retained) LIMIT 2
  ) origins;
  IF cardinality(candidates) IS DISTINCT FROM 1 THEN RETURN NULL;END IF;
  SELECT * INTO STRICT old FROM public.allocation_set WHERE organization_id=org AND id=candidates[1];
  WITH RECURSIVE chain AS (
    SELECT id,1 AS depth FROM public.allocation_set WHERE organization_id=org AND id=old.id
    UNION ALL SELECT a.id,c.depth+1 FROM public.allocation_set a JOIN chain c ON a.predecessor_id=c.id WHERE a.organization_id=org AND c.depth<=100
  ) SELECT array_agg(id ORDER BY depth) INTO path FROM chain;
  IF cardinality(path)>100 THEN RAISE EXCEPTION 'Complete replacement lineage requires further review' USING ERRCODE='23514';END IF;
  SELECT * INTO STRICT head FROM public.allocation_set WHERE organization_id=org AND id=path[cardinality(path)];
  proof:=public.allocation_replacement_relation(org,old.id,head.id);
  SELECT id INTO receipt FROM public.allocation_correction WHERE organization_id=org AND predecessor_id=old.id AND transition_kind='source_replacement';
  IF proof IS NULL OR receipt IS NULL OR head.scope_id<>old.scope_id OR head.property_id<>old.property_id
    OR NOT EXISTS(SELECT 1 FROM public.invoice_document WHERE organization_id=org AND id=head.source_document_id AND status='active') THEN RETURN NULL;END IF;
  RETURN jsonb_build_object('retained_allocation_id',old.id,'allocation_head_id',head.id,'replacement_receipt_id',receipt,
    'source_document_id',head.source_document_id,'primary_cost_id',head.primary_cost_id,'cost_count',head.cost_count,
    'gross_amount',(SELECT gross_amount::text FROM public.invoice_review_revision WHERE organization_id=org AND id=head.invoice_review_id),'lineage',proof);
END $$;
"""


def evaluator():
    h.patch('evaluate_exception_completion(uuid,uuid)',[
        ('logical uuid;component uuid[];', 'replacement jsonb;relation jsonb;logical uuid;component uuid[];'),
        ('OR public.adoption_invoice_identity(right_review::public.invoice_review_revision)=public.adoption_invoice_identity(left_review::public.invoice_review_revision))',
         """OR public.adoption_invoice_identity(right_review::public.invoice_review_revision)=public.adoption_invoice_identity(left_review::public.invoice_review_revision)
        OR EXISTS(SELECT 1 FROM public.allocation_set old_set JOIN public.allocation_set next_set ON next_set.organization_id=old_set.organization_id
          AND next_set.predecessor_id=old_set.id AND next_set.transition_kind='source_replacement' WHERE old_set.organization_id=org
          AND ((old_set.logical_document_id=left_review.logical_document_id AND next_set.logical_document_id=right_review.logical_document_id)
            OR (next_set.logical_document_id=left_review.logical_document_id AND old_set.logical_document_id=right_review.logical_document_id))))"""),
        ('s:=public.current_physical_allocation(org,r.id);',
         """replacement:=NULL;s:=public.current_physical_allocation(org,r.id);
    IF s.id IS NULL THEN
      replacement:=public.replacement_source_outcome(org,r.id);
      SELECT * INTO s FROM public.allocation_set WHERE organization_id=org AND id=(replacement->>'retained_allocation_id')::uuid;
      IF replacement IS NOT NULL AND NOT public.allocation_set_access(org,(replacement->>'allocation_head_id')::uuid) THEN readable:=false;END IF;
    END IF;"""),
        ("'allocation_set_id',s.id,'disposition',CASE WHEN", "'allocation_set_id',s.id,'disposition',CASE WHEN replacement IS NOT NULL THEN 'replaced_document' WHEN"),
        ('members:=members||jsonb_build_array(member);', "IF replacement IS NOT NULL THEN member:=member||jsonb_build_object('replacement',replacement);END IF;\n    members:=members||jsonb_build_array(member);"),
        ('IF NOT needed THEN CONTINUE;END IF;',
         """relation:=public.allocation_replacement_relation(org,(left_member->>'allocation_set_id')::uuid,(right_member->>'allocation_set_id')::uuid);
    IF relation IS NOT NULL THEN
      -- A sealed financial transition supplies the oriented outcome. Retain the
      -- latest advisory comparison in the basis, without treating it as money.
      SELECT * INTO pair FROM public.duplicate_comparison WHERE organization_id=org AND scope_id=c.scope_id
        AND pair_low=(left_member->>'logical_document_id')::uuid AND pair_high=(right_member->>'logical_document_id')::uuid ORDER BY revision_number DESC LIMIT 1;
      comparisons:=comparisons||jsonb_build_array(jsonb_build_object('left_logical_id',left_member->>'logical_document_id','right_logical_id',right_member->>'logical_document_id',
        'comparison_id',NULL,'verdict','confirmed_replacement','replacement',relation,'advisory_comparison_id',pair.id,'advisory_verdict',pair.verdict,
        'subject_review_id',CASE WHEN relation->>'later_allocation_id'=left_member->>'allocation_set_id' THEN left_member->>'invoice_review_id' ELSE right_member->>'invoice_review_id' END,
        'reference_review_id',CASE WHEN relation->>'earlier_allocation_id'=left_member->>'allocation_set_id' THEN left_member->>'invoice_review_id' ELSE right_member->>'invoice_review_id' END));
      CONTINUE;
    END IF;
    IF NOT needed THEN CONTINUE;END IF;"""),
        ("member->>'disposition'<>'adopted_document'", "member->>'disposition' NOT IN ('adopted_document','replaced_document')"),
        ("'disposition',CASE WHEN dep.assignment_proposal_id=ctx.assignment_proposal_id", "'disposition',CASE WHEN member->>'disposition'='replaced_document' THEN 'fulfilled_by_source_replacement' WHEN dep.assignment_proposal_id=ctx.assignment_proposal_id"),
        ("IF facts->>'comparison_id' IS NULL THEN eligible:=false;END IF;", "IF facts->>'comparison_id' IS NULL AND facts->'replacement' IS NULL THEN eligible:=false;END IF;"),
        ("'disposition',CASE WHEN dep.anchor_review_id=", "'disposition',CASE WHEN facts->>'verdict'='confirmed_replacement' THEN 'fulfilled_by_source_replacement' WHEN dep.anchor_review_id="),
        ("'verdict',facts->>'verdict'));\n  END LOOP;", "'verdict',facts->>'verdict'));\n    IF facts->'replacement' IS NOT NULL THEN dispositions:=jsonb_set(dispositions,ARRAY[(jsonb_array_length(dispositions)-1)::text,'replacement'],facts->'replacement');END IF;\n  END LOOP;"),
    ])


def seal():
    h.patch('validate_exception_completion()',[
        ("OR recorded->>'allocation_set_id' IS DISTINCT FROM member.allocation_set_id::text", """OR recorded->>'allocation_set_id' IS DISTINCT FROM member.allocation_set_id::text
      OR (recorded->'replacement'->>'allocation_head_id')::uuid IS DISTINCT FROM member.replacement_head_id
      OR (recorded->'replacement'->>'replacement_receipt_id')::uuid IS DISTINCT FROM member.replacement_receipt_id
      OR (member.disposition='replaced_document' AND recorded->'replacement' IS DISTINCT FROM public.replacement_source_outcome(member.organization_id,member.invoice_review_id))"""),
        ("OR (member.disposition='covered_copy' AND s.logical_document_id<>member.logical_document_id)))", """OR (member.disposition='covered_copy' AND s.logical_document_id<>member.logical_document_id)
            OR (member.disposition='replaced_document' AND s.id=(recorded->'replacement'->>'retained_allocation_id')::uuid)))"""),
    ])
