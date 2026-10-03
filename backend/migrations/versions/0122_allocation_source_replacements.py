"""Replace a retained whole-original allocation with a newly reviewed source."""
from alembic import op
from sqlalchemy import text

revision='0122'
down_revision='0121'
branch_labels=None
depends_on=None


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


ADAPTED=[
    'seal_allocation_source_claim()','record_allocation_source_identity()','seal_allocation_source_member()',
    'allocation_source_overlap(uuid,text,integer,integer,uuid)','seal_allocation_correction()',
    'allocation_correction_detail(uuid,uuid)','allocation_adoption_result(uuid,uuid)','correction_duplicate_snapshot(uuid,uuid)',
]
ADDED=[
    'allocation_ancestors(uuid,uuid)','require_replacement_authority(uuid,uuid,uuid)',
    'replacement_duplicate_snapshot(uuid,uuid,uuid)','allocation_replacement_preview(uuid,uuid,uuid)',
    'prepare_allocation_replacement(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean)',
    'replace_allocation_source(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean,uuid)',
    'allocation_replacement_candidates(uuid,uuid,uuid,integer)',
]


def upgrade():
    op.execute("""DO $$ DECLARE row record;BEGIN
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||row.id::text,0));END LOOP;
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||row.id::text,0));END LOOP;
      END $$;
      LOCK TABLE allocation_set,allocation_correction,allocation_source_claim,allocation_source_member,invoice_document,cost_entry IN SHARE ROW EXCLUSIVE MODE NOWAIT;
      ALTER TABLE allocation_set ADD COLUMN transition_kind text NOT NULL DEFAULT 'same_source'
        CHECK(transition_kind IN ('same_source','source_replacement')),
        ADD CONSTRAINT allocation_replacement_kind CHECK(transition_kind='same_source' OR (predecessor_id IS NOT NULL AND kind='intake_reviewed' AND source_variant='original'));
      GRANT SELECT(transition_kind) ON allocation_set TO app_rw;
      ALTER TABLE allocation_correction ADD COLUMN transition_kind text NOT NULL DEFAULT 'same_source' CHECK(transition_kind IN ('same_source','source_replacement')); """)
    for signature in ADAPTED:
        clone(signature,'pre_replacement_'+signature.split('(')[0])
        op.execute('REVOKE ALL ON FUNCTION pre_replacement_'+signature+' FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute(HELPERS)
    source_claims()
    financial_chain()
    commands()
    op.execute(CANDIDATES)
    op.execute('REVOKE ALL ON FUNCTION '+','.join(ADDED)+' FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute('GRANT EXECUTE ON FUNCTION '+','.join(ADDED[3:])+' TO app_rw')


def downgrade():
    op.execute("""LOCK TABLE allocation_set IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_set WHERE transition_kind='source_replacement') THEN
        RAISE EXCEPTION 'Retain changed-source replacement evidence; repair forward';END IF;END $$;""")
    for signature in reversed(ADAPTED):
        clone('pre_replacement_'+signature,signature.split('(')[0])
        op.execute('DROP FUNCTION pre_replacement_'+signature)
    op.execute('DROP FUNCTION '+','.join(reversed(ADDED)))
    op.execute('ALTER TABLE allocation_set DROP COLUMN transition_kind; ALTER TABLE allocation_correction DROP COLUMN transition_kind')


HELPERS=r"""
CREATE FUNCTION allocation_ancestors(org uuid,target uuid) RETURNS uuid[]
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE result uuid[];
BEGIN
  WITH RECURSIVE chain AS (
    SELECT id,predecessor_id,1 AS depth FROM public.allocation_set WHERE organization_id=org AND id=target
    UNION ALL SELECT a.id,a.predecessor_id,c.depth+1 FROM public.allocation_set a JOIN chain c ON c.predecessor_id=a.id
      WHERE a.organization_id=org AND c.depth<=100
  ) SELECT array_agg(id ORDER BY depth) INTO result FROM chain;
  IF cardinality(result)>100 THEN RAISE EXCEPTION 'Complete financial ancestry requires further review' USING ERRCODE='23514';END IF;
  RETURN COALESCE(result,ARRAY[]::uuid[]);
END $$;
CREATE FUNCTION require_replacement_authority(org uuid,predecessor uuid,target uuid) RETURNS public.allocation_set
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE old public.allocation_set;a public.allocation_proposal;
BEGIN
  a:=public.require_adoption_authority(org,target);
  SELECT * INTO old FROM public.allocation_set WHERE organization_id=org AND id=predecessor;
  IF old.id IS NULL OR old.kind<>'intake_reviewed' OR NOT public.allocation_set_access(org,old.id)
    OR NOT public.invoice_source_access(org,old.source_document_id,'prepare') OR NOT public.invoice_source_access(org,old.source_document_id,'export')
    OR NOT public.source_partition_access(org,old.document_ref_id,true) OR NOT public.intake_original_export_access(org,old.intake_original_id)
    OR (a.property_id,a.scope_id) IS DISTINCT FROM (old.property_id,old.scope_id) THEN
    RAISE EXCEPTION 'Replacement source inaccessible' USING ERRCODE='42501';END IF;
  RETURN old;
END $$;
"""


def source_claims():
    patch('seal_allocation_source_claim()',[
        ('root.predecessor_id IS NOT NULL',"(root.predecessor_id IS NOT NULL AND root.transition_kind<>'source_replacement')"),
    ])
    patch('record_allocation_source_identity()',[
        ('IF NEW.predecessor_id IS NULL THEN',"IF NEW.predecessor_id IS NULL OR NEW.transition_kind='source_replacement' THEN"),
    ])
    patch('seal_allocation_source_member()',[
        ('IF allocation.predecessor_id IS NOT NULL THEN',"IF allocation.predecessor_id IS NOT NULL AND allocation.transition_kind<>'source_replacement' THEN"),
        ('(allocation.predecessor_id IS NOT NULL AND NOT EXISTS',"(allocation.predecessor_id IS NOT NULL AND allocation.transition_kind<>'source_replacement' AND NOT EXISTS"),
        ('(allocation.predecessor_id IS NULL AND claim.root_allocation_id IS DISTINCT FROM allocation.id)',
         "((allocation.predecessor_id IS NULL OR allocation.transition_kind='source_replacement') AND claim.root_allocation_id IS DISTINCT FROM allocation.id)"),
    ])
    patch('allocation_source_overlap(uuid,text,integer,integer,uuid)',[
        ('WHERE a.organization_id=org AND a.predecessor_id IS NULL',"WHERE a.organization_id=org AND (a.predecessor_id IS NULL OR a.transition_kind='source_replacement')"),
    ])


ANCESTOR_FILTER="""AND NOT EXISTS(SELECT 1 FROM public.allocation_set retained WHERE retained.organization_id=org
        AND retained.id=ANY(public.allocation_ancestors(org,predecessor)) AND retained.invoice_review_id=r.id)
      AND (r.content_sha256=subject.content_sha256"""
CURRENT_SET="""(SELECT a.id FROM public.allocation_set a WHERE a.organization_id=org AND a.logical_document_id=subject.logical_document_id
          AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=a.id))"""


def financial_chain():
    patch('seal_allocation_correction()',[
        ("IF receipt.source_variant IS DISTINCT FROM successor.source_variant",
         "IF receipt.transition_kind IS DISTINCT FROM successor.transition_kind OR receipt.source_variant IS DISTINCT FROM successor.source_variant"),
        ("OR (old.source_variant,old.visual_artifact_id,old.visual_disclosure_id) IS DISTINCT FROM (successor.source_variant,successor.visual_artifact_id,successor.visual_disclosure_id)",
         "OR (successor.transition_kind='same_source' AND (old.source_variant,old.visual_artifact_id,old.visual_disclosure_id) IS DISTINCT FROM (successor.source_variant,successor.visual_artifact_id,successor.visual_disclosure_id))"),
        ("""OR (old.property_id,old.scope_id,old.document_ref_id,old.logical_document_id,old.intake_original_id,old.content_sha256)
      IS DISTINCT FROM (successor.property_id,successor.scope_id,successor.document_ref_id,successor.logical_document_id,successor.intake_original_id,successor.content_sha256)""",
         """OR (old.property_id,old.scope_id) IS DISTINCT FROM (successor.property_id,successor.scope_id)
    OR (successor.transition_kind='same_source' AND (old.document_ref_id,old.logical_document_id,old.intake_original_id,old.content_sha256)
      IS DISTINCT FROM (successor.document_ref_id,successor.logical_document_id,successor.intake_original_id,successor.content_sha256))
    OR (successor.transition_kind='source_replacement' AND (old.source_variant<>'original' OR successor.source_variant<>'original'
      OR old.document_ref_id=successor.document_ref_id OR old.logical_document_id=successor.logical_document_id
      OR old.intake_original_id=successor.intake_original_id OR old.content_sha256=successor.content_sha256))"""),
    ])
    patch('allocation_correction_detail(uuid,uuid)',[
        ('old:=public.require_correction_authority(org,receipt.predecessor_id,successor.treatment_review_id);',
         """IF successor.transition_kind='source_replacement' THEN old:=public.require_replacement_authority(org,receipt.predecessor_id,successor.treatment_review_id);
  ELSE old:=public.require_correction_authority(org,receipt.predecessor_id,successor.treatment_review_id);END IF;"""),
        ("RETURN jsonb_build_object('id',receipt.id", "RETURN jsonb_build_object('transition_kind',successor.transition_kind,'id',receipt.id"),
    ])
    patch('allocation_adoption_result(uuid,uuid)',[
        ('DECLARE ref uuid;result uuid;', 'DECLARE ref uuid;result uuid;ids uuid[];'),
        ("SELECT s.id INTO result FROM public.allocation_set s WHERE s.organization_id=org AND s.logical_document_id=logical_id AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=s.id);",
         """WITH RECURSIVE chain AS (
    SELECT id,1 AS depth FROM public.allocation_set WHERE organization_id=org AND logical_document_id=logical_id
      AND (predecessor_id IS NULL OR transition_kind='source_replacement')
    UNION ALL SELECT s.id,c.depth+1 FROM public.allocation_set s JOIN chain c ON s.predecessor_id=c.id WHERE s.organization_id=org AND c.depth<=100
  ) SELECT array_agg(id ORDER BY depth) INTO ids FROM chain;
  IF cardinality(ids)>100 THEN RAISE EXCEPTION 'Complete financial lineage requires further review' USING ERRCODE='23514';END IF;
  result:=ids[cardinality(ids)];"""),
    ])
    # Build replacement duplicate policy before changing ordinary correction policy.
    source=definition('adoption_duplicate_snapshot(uuid,uuid)')
    source=change(source,'FUNCTION public.adoption_duplicate_snapshot(org uuid, target uuid)',
        'FUNCTION public.replacement_duplicate_snapshot(org uuid, target uuid, predecessor uuid)')
    source=change(source,'AND (r.content_sha256=subject.content_sha256',ANCESTOR_FILTER)
    op.execute(source)
    patch('correction_duplicate_snapshot(uuid,uuid)',[
        ('AND (r.content_sha256=subject.content_sha256',ANCESTOR_FILTER.replace('predecessor',CURRENT_SET)),
        ('AND a.predecessor_id IS NULL)) THEN blocked:=true;',
         'AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=a.id))) THEN blocked:=true;'),
    ])


def commands():
    clone('allocation_correction_preview(uuid,uuid,uuid)','allocation_replacement_preview',[
        ('a:=public.require_adoption_authority(org,target);','old:=public.require_replacement_authority(org,predecessor,target);a:=public.require_adoption_authority(org,target);'),
        ("IF (a.document_ref_id,a.logical_document_id,a.property_id,a.scope_id) IS DISTINCT FROM (old.document_ref_id,old.logical_document_id,old.property_id,old.scope_id)",
         """IF (a.property_id,a.scope_id) IS DISTINCT FROM (old.property_id,old.scope_id)
      OR old.source_variant<>'original' OR a.document_ref_id=old.document_ref_id OR a.logical_document_id=old.logical_document_id
      OR (SELECT content_sha256 FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id)=old.content_sha256
      OR EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND logical_document_id=a.logical_document_id)
      OR (SELECT document_kind FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id)
        IS DISTINCT FROM (SELECT document_kind FROM public.invoice_review_revision WHERE organization_id=org AND id=old.invoice_review_id)"""),
        ('duplicates:=public.correction_duplicate_snapshot(org,a.invoice_review_id);','duplicates:=public.replacement_duplicate_snapshot(org,a.invoice_review_id,predecessor);'),
        ("'allocation-correction-preview-v1'","'allocation-source-replacement-preview-v1'"),
        ("RETURN jsonb_build_object('can_review',true,", "RETURN jsonb_build_object('transition_kind','source_replacement','replacement_document_ref_id',a.document_ref_id,'can_review',true,"),
    ])
    clone('prepare_allocation_correction(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean)','prepare_allocation_replacement',[
        ('old:=public.require_correction_authority(org,predecessor,target);','old:=public.require_replacement_authority(org,predecessor,target);'),
        ('jsonb_build_array(predecessor,target,expected,preview_digest,explanation,confirmed,disclosed,followups)',
         "jsonb_build_array('source-replacement-v1',predecessor,target,expected,preview_digest,explanation,confirmed,disclosed,followups)"),
        ('preview:=public.allocation_correction_preview(org,predecessor,target);','preview:=public.allocation_replacement_preview(org,predecessor,target);'),
        ('id=old.intake_original_id;',"id=(SELECT ref.intake_original_id FROM public.allocation_treatment_review t JOIN public.allocation_proposal a ON a.organization_id=t.organization_id AND a.id=t.allocation_proposal_id JOIN public.document_ref ref ON ref.organization_id=a.organization_id AND ref.id=a.document_ref_id WHERE t.organization_id=org AND t.id=target);"),
    ])
    clone('correct_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean,uuid)','replace_allocation_source',[
        ('public.prepare_allocation_correction(', 'public.prepare_allocation_replacement('),
        ('duplicates:=public.correction_duplicate_snapshot(org,r.id);','duplicates:=public.replacement_duplicate_snapshot(org,r.id,predecessor);'),
        ('reason,whole_original_disclosure_reviewed,confirmed_at)', 'reason,whole_original_disclosure_reviewed,confirmed_at,transition_kind)'),
        ('downstream_followups_reviewed,confirmed_at)', 'downstream_followups_reviewed,confirmed_at,transition_kind)'),
        ("impacts,explanation,true,true,true,moment);", "impacts,explanation,true,true,true,moment,'source_replacement');"),
        ("explanation,true,moment);", "explanation,true,moment,'source_replacement');"),
        ("'document.allocation_corrected'", "'document.allocation_source_replaced'"),
    ])


CANDIDATES=r"""
CREATE FUNCTION allocation_replacement_candidates(org uuid,target uuid,after_id uuid,take integer) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE a public.allocation_proposal;r public.invoice_review_revision;rows jsonb;
BEGIN
  a:=public.require_adoption_authority(org,target);
  IF take IS NULL OR take<1 OR take>50 THEN RAISE EXCEPTION 'Bounded replacement candidates required' USING ERRCODE='22023';END IF;
  SELECT * INTO STRICT r FROM public.invoice_review_revision WHERE organization_id=org AND id=a.invoice_review_id;
  SELECT COALESCE(jsonb_agg(to_jsonb(candidate) ORDER BY candidate.id),'[]') INTO rows FROM (
    SELECT s.id,s.source_document_id,prior.document_number,prior.seller_name,prior.gross_amount::text,s.confirmed_at
    FROM public.allocation_set s JOIN public.invoice_review_revision prior ON prior.organization_id=s.organization_id AND prior.id=s.invoice_review_id
    WHERE s.organization_id=org AND s.kind='intake_reviewed' AND s.source_variant='original' AND s.property_id=a.property_id AND s.scope_id=a.scope_id
      AND s.logical_document_id<>a.logical_document_id AND s.content_sha256<>r.content_sha256 AND (after_id IS NULL OR s.id>after_id)
      AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=s.id)
      AND lower(regexp_replace(btrim(prior.seller_name),'[[:space:]]+',' ','g'))=lower(regexp_replace(btrim(r.seller_name),'[[:space:]]+',' ','g'))
      AND btrim(prior.document_number)=btrim(r.document_number) AND prior.document_kind=r.document_kind AND prior.currency=r.currency
      AND public.allocation_set_access(org,s.id) AND public.invoice_source_access(org,s.source_document_id,'prepare')
      AND public.invoice_source_access(org,s.source_document_id,'export') AND public.source_partition_access(org,s.document_ref_id,true)
      AND public.intake_original_export_access(org,s.intake_original_id)
    ORDER BY s.id LIMIT take+1
  ) candidate;
  RETURN jsonb_build_object('items',(SELECT COALESCE(jsonb_agg(value ORDER BY ordinality),'[]') FROM jsonb_array_elements(rows) WITH ORDINALITY WHERE ordinality<=take),
    'next_after',CASE WHEN jsonb_array_length(rows)>take THEN rows->(take-1)->>'id' END);
END $$;
"""
