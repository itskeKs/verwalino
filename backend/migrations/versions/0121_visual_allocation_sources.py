"""Retain exact visual derivatives as reviewed financial sources."""
from alembic import op
from sqlalchemy import text

revision = '0121'
down_revision = '0120'
branch_labels = None
depends_on = None


def definition(signature):
    return op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'), {'signature': signature})


def replace_exact(source, before, after):
    assert source.count(before) == 1, before
    return source.replace(before, after)


def clone(signature, old_name, new_name, changes=()):
    source = replace_exact(definition(signature), 'FUNCTION public.'+old_name+'(', 'FUNCTION public.'+new_name+'(')
    for before, after in changes:
        source = replace_exact(source, before, after)
    op.execute(source)


def private_copy(signature, old_name, new_name):
    clone(signature, old_name, new_name)
    arguments = signature[signature.index('('):]
    op.execute('REVOKE ALL ON FUNCTION '+new_name+arguments+' FROM PUBLIC,app_rw,app_worker,app_review')


def replace_function(signature, changes):
    source = definition(signature)
    for before, after in changes:
        source = replace_exact(source, before, after)
    op.execute(source)


def command_variant(signature, name, arguments, changes):
    source = definition(signature)
    source = 'CREATE OR REPLACE FUNCTION public.'+name+'('+arguments+')'+source[source.index('\n RETURNS'):]
    for before, after in changes:
        source = replace_exact(source, before, after)
    op.execute(source)


def upgrade():
    install_visual_sources()


ADAPTED = [
    'validate_allocation_set_membership()', 'seal_allocation_source_claim()', 'record_allocation_source_identity()',
    'seal_allocation_source_member()', 'seal_allocation_correction()', 'allocation_correction_preview(uuid,uuid,uuid)',
    'prepare_allocation_correction(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean)',
    'evaluate_exception_completion(uuid,uuid)', 'validate_exception_completion()',
]
ADDED = [
    'require_current_allocation_basis(uuid,uuid)', 'review_source_aliases(uuid,uuid)', 'review_physical_relation(uuid,uuid,uuid)',
    'current_physical_allocation(uuid,uuid)', 'visual_adoption_duplicate_snapshot(uuid,uuid)', 'visual_correction_duplicate_snapshot(uuid,uuid)',
    'require_visual_allocation_basis(uuid,uuid,uuid)', 'visual_allocation_preview(uuid,uuid,uuid)',
    'prepare_visual_allocation(uuid,uuid,uuid,uuid,uuid,text,uuid,text,text,boolean,boolean)',
    'require_retained_visual_correction_basis(uuid,uuid,uuid)', 'require_visual_financial_proof(uuid,uuid,uuid,uuid,text)',
    'original_adoption_bridge_admission(public.invoice_document)', 'visual_adoption_bridge_admission(public.invoice_document)',
    'visual_allocation_correction_preview(uuid,uuid,uuid)',
    'prepare_visual_allocation_correction(uuid,uuid,uuid,text,uuid,uuid,text,text,boolean,boolean,boolean)',
    'adopt_visual_allocation(uuid,uuid,uuid,uuid,uuid,text,uuid,text,text,boolean,boolean,uuid,uuid)',
    'correct_visual_allocation(uuid,uuid,uuid,text,uuid,uuid,text,text,boolean,boolean,boolean,uuid,uuid)',
]
INTERACTIVE = [ADDED[i] for i in (7,8,13,14,15,16)]


def install_visual_sources():
    """Install the coordinated source, membership and financial adapters."""
    op.execute("""DO $$ DECLARE row record;BEGIN
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||row.id::text,0));END LOOP;
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||row.id::text,0));END LOOP;
      END $$;
      LOCK TABLE allocation_set,allocation_correction,invoice_document,cost_entry,document_cost_link,visual_disclosure_event IN SHARE ROW EXCLUSIVE MODE NOWAIT;""")
    for signature in ADAPTED:
        name = signature.split('(')[0]
        private_copy(signature, name, 'pre_visual_'+name)
    op.execute(SCHEMA)
    constraint = op.get_bind().scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='allocation_set'::regclass AND conname='allocation_set_check'"))
    constraint = replace_exact(constraint, 'AND whole_original_disclosure_reviewed)', 'AND (whole_original_disclosure_reviewed OR retained_visual_source_reviewed))')
    op.execute('ALTER TABLE allocation_set DROP CONSTRAINT allocation_set_check, ADD CONSTRAINT allocation_set_check '+constraint)
    clone('require_current_adoption_basis(uuid,uuid)', 'require_current_adoption_basis', 'require_current_allocation_basis', [CURRENT_BASIS_CHANGE])
    op.execute(PHYSICAL_REVIEWS)
    visual_duplicate_functions()
    op.execute(VISUAL_BASIS)
    op.execute(VISUAL_RETENTION)
    visual_source_guards()
    visual_correction_preparation()
    visual_commands()
    visual_exception_outcomes()
    op.execute('REVOKE ALL ON FUNCTION '+','.join(ADDED)+' FROM PUBLIC,app_rw,app_worker,app_review')
    op.execute('GRANT EXECUTE ON FUNCTION '+','.join(INTERACTIVE)+' TO app_rw')


def downgrade():
    op.execute("""LOCK TABLE allocation_set,allocation_correction IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_set WHERE source_variant='visual') OR EXISTS(SELECT 1 FROM allocation_correction WHERE source_variant='visual') THEN
        RAISE EXCEPTION 'Retain visual financial sources; repair forward';END IF;END $$;""")
    for signature in reversed(ADAPTED):
        name = signature.split('(')[0]
        prior = 'pre_visual_'+signature
        clone(prior, 'pre_visual_'+name, name)
        op.execute('DROP FUNCTION '+prior)
    clone('original_adoption_bridge_admission(public.invoice_document)', 'original_adoption_bridge_admission', 'adoption_bridge_admission')
    op.execute('DROP FUNCTION '+','.join(reversed(ADDED)))
    constraint = op.get_bind().scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='allocation_set'::regclass AND conname='allocation_set_check'"))
    constraint = replace_exact(constraint, 'whole_original_disclosure_reviewed OR retained_visual_source_reviewed', 'whole_original_disclosure_reviewed')
    op.execute('ALTER TABLE allocation_set DROP CONSTRAINT allocation_set_check, ADD CONSTRAINT allocation_set_check '+constraint)
    op.execute("""DROP INDEX allocation_once_per_original_hash,allocation_initial_original;
      ALTER TABLE allocation_set DROP COLUMN source_variant,DROP COLUMN visual_artifact_id,DROP COLUMN visual_disclosure_id,
        DROP COLUMN visual_integrity_attestation_id,DROP COLUMN retained_visual_source_reviewed;
      ALTER TABLE allocation_correction DROP COLUMN source_variant,DROP COLUMN retained_visual_source_reviewed;
      ALTER TABLE allocation_correction ADD CONSTRAINT allocation_correction_whole_original_disclosure_reviewed_check CHECK(whole_original_disclosure_reviewed);
      CREATE UNIQUE INDEX allocation_once_per_original_hash ON allocation_set(organization_id,content_sha256) WHERE kind='intake_reviewed' AND predecessor_id IS NULL;
      CREATE UNIQUE INDEX allocation_initial_original ON allocation_set(organization_id,intake_original_id) WHERE predecessor_id IS NULL;
      GRANT SELECT(content_sha256) ON allocation_set TO app_rw;""")


SCHEMA = """
ALTER TABLE allocation_set ADD COLUMN source_variant text NOT NULL DEFAULT 'original' CHECK(source_variant IN ('original','visual')),
  ADD COLUMN visual_artifact_id uuid,ADD COLUMN visual_disclosure_id uuid,ADD COLUMN visual_integrity_attestation_id uuid,
  ADD COLUMN retained_visual_source_reviewed boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT allocation_visual_artifact_fk FOREIGN KEY(organization_id,visual_artifact_id) REFERENCES visual_extract_artifact(organization_id,id),
  ADD CONSTRAINT allocation_visual_disclosure_fk FOREIGN KEY(organization_id,visual_disclosure_id) REFERENCES visual_disclosure_event(organization_id,id),
  ADD CONSTRAINT allocation_visual_destination_fk FOREIGN KEY(organization_id,visual_artifact_id,scope_id,visual_disclosure_id)
    REFERENCES visual_disclosure_event(organization_id,artifact_id,scope_id,id),
  ADD CONSTRAINT allocation_visual_integrity_fk FOREIGN KEY(organization_id,visual_integrity_attestation_id) REFERENCES visual_disclosure_attestation(organization_id,id),
  ADD CONSTRAINT allocation_visual_proof_once UNIQUE(organization_id,visual_integrity_attestation_id),
  ADD CONSTRAINT allocation_exact_source_variant CHECK(
    (source_variant='original' AND visual_artifact_id IS NULL AND visual_disclosure_id IS NULL AND visual_integrity_attestation_id IS NULL AND NOT retained_visual_source_reviewed)
    OR (source_variant='visual' AND kind='intake_reviewed' AND visual_artifact_id IS NOT NULL AND visual_disclosure_id IS NOT NULL
      AND visual_integrity_attestation_id IS NOT NULL AND retained_visual_source_reviewed AND NOT whole_original_disclosure_reviewed));
REVOKE SELECT(content_sha256) ON allocation_set FROM app_rw;
GRANT SELECT(source_variant) ON allocation_set TO app_rw;
DROP INDEX allocation_once_per_original_hash,allocation_initial_original;
CREATE UNIQUE INDEX allocation_once_per_original_hash ON allocation_set(organization_id,content_sha256)
  WHERE kind='intake_reviewed' AND predecessor_id IS NULL AND source_variant='original';
CREATE UNIQUE INDEX allocation_initial_original ON allocation_set(organization_id,intake_original_id)
  WHERE predecessor_id IS NULL AND source_variant='original';
ALTER TABLE allocation_correction ADD COLUMN source_variant text NOT NULL DEFAULT 'original' CHECK(source_variant IN ('original','visual')),
  ADD COLUMN retained_visual_source_reviewed boolean NOT NULL DEFAULT false;
ALTER TABLE allocation_correction DROP CONSTRAINT allocation_correction_whole_original_disclosure_reviewed_check;
ALTER TABLE allocation_correction ADD CONSTRAINT correction_exact_source_ack CHECK(
  (source_variant='original' AND whole_original_disclosure_reviewed AND NOT retained_visual_source_reviewed)
  OR (source_variant='visual' AND NOT whole_original_disclosure_reviewed AND retained_visual_source_reviewed));
"""

PHYSICAL_REVIEWS = r"""
CREATE FUNCTION review_source_aliases(org uuid,target uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE review public.invoice_review_revision;logical public.logical_document;
BEGIN
  IF target IS NULL THEN RETURN '[]'::jsonb;END IF;
  SELECT * INTO STRICT review FROM public.invoice_review_revision WHERE organization_id=org AND id=target;
  SELECT * INTO STRICT logical FROM public.logical_document WHERE organization_id=org AND id=review.logical_document_id;
  RETURN public.source_physical_aliases(org,review.content_sha256,logical.page_start,logical.page_end);
END $$;

CREATE FUNCTION review_physical_relation(org uuid,first_review uuid,second_review uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE first_aliases jsonb;second_aliases jsonb;
BEGIN
  first_aliases:=public.review_source_aliases(org,first_review);
  second_aliases:=public.review_source_aliases(org,second_review);
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(first_aliases) a CROSS JOIN jsonb_array_elements(second_aliases) b WHERE a=b) THEN RETURN 'equal';END IF;
  IF public.source_aliases_overlap(first_aliases,second_aliases) THEN RETURN 'overlap';END IF;
  RETURN 'disjoint';
END $$;

CREATE FUNCTION current_physical_allocation(org uuid,target uuid) RETURNS public.allocation_set
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE ids uuid[];result public.allocation_set;
BEGIN
  IF target IS NULL THEN RETURN result;END IF;
  SELECT array_agg(id) INTO ids FROM (
    SELECT a.id FROM public.allocation_set a WHERE a.organization_id=org AND a.kind='intake_reviewed'
      AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=a.id)
      AND public.review_physical_relation(org,a.invoice_review_id,target)='equal' LIMIT 2
  ) heads;
  IF cardinality(ids)=1 THEN SELECT * INTO result FROM public.allocation_set WHERE organization_id=org AND id=ids[1];END IF;
  RETURN result;
END $$;
REVOKE ALL ON FUNCTION review_source_aliases(uuid,uuid),review_physical_relation(uuid,uuid,uuid),current_physical_allocation(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review;
"""


def visual_duplicate_functions():
    source = definition('adoption_duplicate_snapshot(uuid,uuid)')
    source = replace_exact(source, 'FUNCTION public.adoption_duplicate_snapshot(', 'FUNCTION public.visual_adoption_duplicate_snapshot(')
    source = replace_exact(source, 'same_bytes boolean;', 'same_bytes boolean;physical_relation text;')
    source = replace_exact(source, """WHERE r.document_ref_id<>subject.document_ref_id
      AND (r.content_sha256=subject.content_sha256 OR public.adoption_invoice_identity(r::public.invoice_review_revision)=public.adoption_invoice_identity(subject)) ORDER BY r.id""", """WHERE r.logical_document_id<>subject.logical_document_id
      AND (EXISTS(SELECT 1 FROM public.allocation_set retained WHERE retained.organization_id=org AND retained.invoice_review_id=r.id)
        OR (r.partition_revision_id=(SELECT id FROM public.source_partition_revision WHERE organization_id=org AND document_ref_id=r.document_ref_id ORDER BY revision_number DESC LIMIT 1)
          AND EXISTS(SELECT 1 FROM public.source_partition_member WHERE organization_id=org AND partition_revision_id=r.partition_revision_id AND logical_document_id=r.logical_document_id)))
      AND (public.review_physical_relation(org,r.id,subject.id)<>'disjoint'
        OR public.adoption_invoice_identity(r::public.invoice_review_revision)=public.adoption_invoice_identity(subject)) ORDER BY r.id""")
    source = replace_exact(source, 'same_bytes:=candidate.content_sha256=subject.content_sha256;',
        "physical_relation:=public.review_physical_relation(org,candidate.id,subject.id);same_bytes:=physical_relation='equal';")
    source = replace_exact(source, "consumed:=EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed' AND document_ref_id=candidate.document_ref_id);",
        """consumed:=EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed'
          AND logical_document_id=candidate.logical_document_id);""")
    source = replace_exact(source, 'allowed:=visible AND candidate.scope_id=subject.scope_id', "allowed:=physical_relation<>'overlap' AND visible AND candidate.scope_id=subject.scope_id")
    source = replace_exact(source, 'o.content_sha256=subject.content_sha256 AND o.id<>',
        'public.source_aliases_overlap(public.source_physical_aliases(org,o.content_sha256,NULL,NULL),public.review_source_aliases(org,subject.id)) AND o.id<>')
    source = replace_exact(source, 'd.content_sha256=subject.content_sha256 OR (c.status=',
        'public.source_aliases_overlap(public.source_physical_aliases(org,d.content_sha256,NULL,NULL),public.review_source_aliases(org,subject.id)) OR (c.status=')
    source = replace_exact(source, "IF public.allocation_source_overlap(org,subject.content_sha256,NULL,NULL,NULL) THEN blocked:=true;END IF;",
        """IF public.allocation_source_overlap(org,subject.content_sha256,
          (SELECT page_start FROM public.logical_document WHERE organization_id=org AND id=subject.logical_document_id),
          (SELECT page_end FROM public.logical_document WHERE organization_id=org AND id=subject.logical_document_id),NULL) THEN blocked:=true;END IF;""")
    source = replace_exact(source, "'policy','adoption-duplicates-v2','physical_sources',public.source_physical_aliases(org,subject.content_sha256,NULL,NULL)",
        "'policy','visual-adoption-duplicates-v1','physical_sources',public.review_source_aliases(org,subject.id)")
    op.execute(source)
    source = replace_exact(source, 'FUNCTION public.visual_adoption_duplicate_snapshot(', 'FUNCTION public.visual_correction_duplicate_snapshot(')
    source = replace_exact(source, '(SELECT page_end FROM public.logical_document WHERE organization_id=org AND id=subject.logical_document_id),NULL)',
        """(SELECT page_end FROM public.logical_document WHERE organization_id=org AND id=subject.logical_document_id),
          (SELECT id FROM public.allocation_set WHERE organization_id=org AND logical_document_id=subject.logical_document_id AND predecessor_id IS NULL))""")
    op.execute(source)
    op.execute('REVOKE ALL ON FUNCTION visual_adoption_duplicate_snapshot(uuid,uuid),visual_correction_duplicate_snapshot(uuid,uuid) FROM PUBLIC,app_rw,app_worker,app_review')


VISUAL_RETENTION = r"""
CREATE FUNCTION require_retained_visual_correction_basis(org uuid,predecessor uuid,target uuid) RETURNS public.visual_extract_artifact
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE old public.allocation_set;allocation public.allocation_proposal;artifact public.visual_extract_artifact;logical public.logical_document;
BEGIN
  old:=public.require_correction_authority(org,predecessor,target);
  allocation:=public.require_current_allocation_basis(org,target);
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=old.visual_artifact_id;
  SELECT * INTO STRICT logical FROM public.logical_document WHERE organization_id=org AND id=allocation.logical_document_id;
  IF old.source_variant<>'visual' OR artifact.id IS NULL OR artifact.source_sha256 IS DISTINCT FROM old.content_sha256
    OR (artifact.logical_document_id,artifact.document_ref_id,artifact.page_start,artifact.page_end)
      IS DISTINCT FROM (allocation.logical_document_id,allocation.document_ref_id,logical.page_start,logical.page_end) THEN
    RAISE EXCEPTION 'Exact retained visual financial source required' USING ERRCODE='23514';END IF;
  RETURN artifact;
END $$;

CREATE FUNCTION require_visual_financial_proof(org uuid,artifact_id uuid,proof_id uuid,request uuid,digest text) RETURNS public.visual_extract_artifact
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE artifact public.visual_extract_artifact;proof public.visual_disclosure_attestation;
BEGIN
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=artifact_id;
  SELECT * INTO proof FROM public.visual_disclosure_attestation WHERE organization_id=org AND id=proof_id;
  IF artifact.id IS NULL OR proof.id IS NULL OR proof.artifact_id IS DISTINCT FROM artifact.id
    OR proof.actor_id IS DISTINCT FROM NULLIF(current_setting('app.current_user_id',true),'')::uuid
    OR proof.request_key IS DISTINCT FROM request OR proof.request_digest IS DISTINCT FROM digest
    OR proof.backend_pid IS DISTINCT FROM pg_backend_pid() OR proof.transaction_id IS DISTINCT FROM pg_current_xact_id()
    OR proof.content_sha256 IS DISTINCT FROM artifact.content_sha256 OR proof.content_byte_size IS DISTINCT FROM artifact.content_byte_size THEN
    RAISE EXCEPTION 'Current transaction retained visual byte proof required' USING ERRCODE='42501';END IF;
  RETURN artifact;
END $$;
REVOKE ALL ON FUNCTION require_retained_visual_correction_basis(uuid,uuid,uuid),require_visual_financial_proof(uuid,uuid,uuid,uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
"""


def visual_source_guards():
    signature = 'adoption_bridge_admission(public.invoice_document)'
    private_copy(signature, 'adoption_bridge_admission', 'original_adoption_bridge_admission')
    source = replace_exact(definition(signature), 'FUNCTION public.adoption_bridge_admission(', 'FUNCTION public.visual_adoption_bridge_admission(')
    source = replace_exact(source, "WHERE s.organization_id=source.organization_id AND s.source_document_id=source.id AND s.kind='intake_reviewed'",
        """JOIN public.visual_extract_artifact v ON v.organization_id=s.organization_id AND v.id=s.visual_artifact_id
    JOIN public.visual_disclosure_attestation vp ON vp.organization_id=s.organization_id AND vp.id=s.visual_integrity_attestation_id
    WHERE s.organization_id=source.organization_id AND s.source_document_id=source.id AND s.kind='intake_reviewed' AND s.source_variant='visual'
      AND vp.artifact_id=v.id AND vp.actor_id=s.actor_id AND vp.request_key=s.request_key AND vp.request_digest=s.request_digest
      AND vp.backend_pid=pg_backend_pid() AND vp.transaction_id=pg_current_xact_id()
      AND vp.content_sha256=v.content_sha256 AND vp.content_byte_size=v.content_byte_size""")
    source = replace_exact(source, """source.storage_uri=o.storage_uri AND source.storage_backend=o.storage_backend AND source.storage_version IS NOT DISTINCT FROM o.storage_version
      AND source.content_sha256=o.content_sha256 AND source.content_byte_size=o.content_byte_size AND source.mime_type=o.mime_type AND source.original_filename=i.filename""",
        """source.storage_uri=v.storage_uri AND source.storage_backend=v.storage_backend AND source.storage_version IS NOT DISTINCT FROM v.storage_version
      AND source.content_sha256=v.content_sha256 AND source.content_byte_size=v.content_byte_size AND source.mime_type='application/pdf'
      AND source.original_filename='reviewed-visual-source.pdf' AND v.source_sha256=o.content_sha256""")
    op.execute(source)
    op.execute("""CREATE OR REPLACE FUNCTION adoption_bridge_admission(source public.invoice_document) RETURNS boolean
      LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT CASE WHEN EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=source.organization_id AND source_document_id=source.id AND source_variant='visual')
        THEN public.visual_adoption_bridge_admission(source) ELSE public.original_adoption_bridge_admission(source) END;
      $$;
      REVOKE ALL ON FUNCTION visual_adoption_bridge_admission(public.invoice_document) FROM PUBLIC,app_rw,app_worker,app_review;""")
    replace_function('validate_allocation_set_membership()', [
        ('d.content_sha256 IS DISTINCT FROM s.content_sha256', "d.content_sha256 IS DISTINCT FROM (CASE WHEN s.source_variant='original' THEN s.content_sha256 ELSE (SELECT content_sha256 FROM public.visual_extract_artifact WHERE organization_id=s.organization_id AND id=s.visual_artifact_id) END)"),
        ("""IF NOT EXISTS(SELECT 1 FROM public.intake_original o WHERE o.organization_id=s.organization_id AND o.id=s.intake_original_id
    AND (d.storage_uri,d.storage_backend,d.storage_version,d.content_sha256,d.content_byte_size,d.mime_type,d.uploaded_by)
      IS NOT DISTINCT FROM (o.storage_uri,o.storage_backend,o.storage_version,o.content_sha256,o.content_byte_size,o.mime_type,o.uploaded_by))""",
         """IF (s.source_variant='original' AND NOT EXISTS(SELECT 1 FROM public.intake_original o WHERE o.organization_id=s.organization_id AND o.id=s.intake_original_id
    AND (d.storage_uri,d.storage_backend,d.storage_version,d.content_sha256,d.content_byte_size,d.mime_type,d.uploaded_by)
      IS NOT DISTINCT FROM (o.storage_uri,o.storage_backend,o.storage_version,o.content_sha256,o.content_byte_size,o.mime_type,o.uploaded_by)))
    OR (s.source_variant='visual' AND NOT EXISTS(SELECT 1 FROM public.visual_extract_artifact v JOIN public.intake_original o ON o.organization_id=v.organization_id AND o.id=s.intake_original_id
      JOIN public.logical_document logical ON logical.organization_id=v.organization_id AND logical.id=v.logical_document_id
      WHERE v.organization_id=s.organization_id AND v.id=s.visual_artifact_id AND v.source_sha256=s.content_sha256 AND o.content_sha256=s.content_sha256
        AND v.logical_document_id=s.logical_document_id AND v.document_ref_id=s.document_ref_id AND v.page_start=logical.page_start AND v.page_end=logical.page_end
        AND (s.predecessor_id IS NOT NULL OR v.partition_revision_id=r.partition_revision_id)
        AND EXISTS(SELECT 1 FROM public.visual_disclosure_event WHERE organization_id=s.organization_id AND id=s.visual_disclosure_id
          AND artifact_id=v.id AND scope_id=s.scope_id AND kind='published')
        AND (d.storage_uri,d.storage_backend,d.storage_version,d.content_sha256,d.content_byte_size,d.mime_type,d.uploaded_by,d.original_filename)
          IS NOT DISTINCT FROM (v.storage_uri,v.storage_backend,v.storage_version,v.content_sha256,v.content_byte_size,'application/pdf',o.uploaded_by,'reviewed-visual-source.pdf')))"""),
    ])
    replace_function('seal_allocation_source_claim()', [
        ('OR NEW.page_start IS NOT NULL OR NEW.page_end IS NOT NULL OR NEW.migration_overlap', """OR NEW.migration_overlap
    OR (root.source_variant='original' AND (NEW.page_start IS NOT NULL OR NEW.page_end IS NOT NULL))
    OR (root.source_variant='visual' AND NOT EXISTS(SELECT 1 FROM public.visual_extract_artifact WHERE organization_id=NEW.organization_id AND id=root.visual_artifact_id
      AND source_sha256=NEW.source_sha256 AND page_start=NEW.page_start AND page_end=NEW.page_end))"""),
        ('Exact whole-original allocation root required', 'Exact retained allocation source root required'),
    ])
    replace_function('record_allocation_source_identity()', [
        ("INSERT INTO public.allocation_source_claim(organization_id,root_allocation_id,source_sha256)\n      VALUES(NEW.organization_id,NEW.id,NEW.content_sha256)",
         """INSERT INTO public.allocation_source_claim(organization_id,root_allocation_id,source_sha256,page_start,page_end)
      VALUES(NEW.organization_id,NEW.id,NEW.content_sha256,
        (SELECT page_start FROM public.visual_extract_artifact WHERE organization_id=NEW.organization_id AND id=NEW.visual_artifact_id),
        (SELECT page_end FROM public.visual_extract_artifact WHERE organization_id=NEW.organization_id AND id=NEW.visual_artifact_id))"""),
    ])
    replace_function('seal_allocation_source_member()', [
        ("OR parent_claim IS DISTINCT FROM NEW.claim_id", """OR (allocation.source_variant='original' AND (claim.page_start IS NOT NULL OR claim.page_end IS NOT NULL))
    OR (allocation.source_variant='visual' AND NOT EXISTS(SELECT 1 FROM public.visual_extract_artifact WHERE organization_id=NEW.organization_id AND id=allocation.visual_artifact_id
      AND source_sha256=claim.source_sha256 AND page_start=claim.page_start AND page_end=claim.page_end))
    OR (allocation.predecessor_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=NEW.organization_id AND id=allocation.predecessor_id
      AND source_variant=allocation.source_variant AND visual_artifact_id IS NOT DISTINCT FROM allocation.visual_artifact_id))
    OR parent_claim IS DISTINCT FROM NEW.claim_id"""),
    ])
    replace_function('seal_allocation_correction()', [
        ("IF old.kind<>'intake_reviewed'", """IF receipt.source_variant IS DISTINCT FROM successor.source_variant
    OR receipt.retained_visual_source_reviewed IS DISTINCT FROM successor.retained_visual_source_reviewed
    OR receipt.whole_original_disclosure_reviewed IS DISTINCT FROM successor.whole_original_disclosure_reviewed
    OR (old.source_variant,old.visual_artifact_id,old.visual_disclosure_id) IS DISTINCT FROM (successor.source_variant,successor.visual_artifact_id,successor.visual_disclosure_id)
    OR old.kind<>'intake_reviewed'"""),
    ])


def visual_correction_preparation():
    clone('allocation_correction_preview(uuid,uuid,uuid)', 'allocation_correction_preview', 'visual_allocation_correction_preview', [
        ('a:=public.require_current_adoption_basis(org,target);',
         'PERFORM public.require_retained_visual_correction_basis(org,predecessor,target);a:=public.require_current_allocation_basis(org,target);'),
        ('duplicates:=public.correction_duplicate_snapshot(org,a.invoice_review_id);','duplicates:=public.visual_correction_duplicate_snapshot(org,a.invoice_review_id);'),
        ("'allocation-correction-preview-v1',old.id,target,t.snapshot_digest,", "'visual-allocation-correction-preview-v1',old.id,old.visual_artifact_id,old.visual_disclosure_id,target,t.snapshot_digest,"),
        ("RETURN jsonb_build_object('can_review',true,", "RETURN jsonb_build_object('source_variant','visual','visual_content_sha256',(SELECT content_sha256 FROM public.visual_extract_artifact WHERE organization_id=org AND id=old.visual_artifact_id),'can_review',true,"),
    ])
    command_variant('prepare_allocation_correction(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean)',
        'prepare_visual_allocation_correction',
        'org uuid,predecessor uuid,target uuid,observed_hash text,expected uuid,request uuid,preview_digest text,explanation text,confirmed boolean,retained boolean,followups boolean', [
        ('original public.intake_original;preview jsonb;digest text;', 'original public.intake_original;artifact public.visual_extract_artifact;preview jsonb;digest text;'),
        ('old:=public.require_correction_authority(org,predecessor,target);', """old:=public.require_correction_authority(org,predecessor,target);
  SELECT * INTO artifact FROM public.visual_extract_artifact WHERE organization_id=org AND id=old.visual_artifact_id;
  IF old.source_variant<>'visual' OR artifact.id IS NULL THEN RAISE EXCEPTION 'Retained visual correction required' USING ERRCODE='23514';END IF;"""),
        ("IF request IS NULL OR expected IS DISTINCT FROM predecessor", "IF request IS NULL OR observed_hash IS DISTINCT FROM artifact.content_sha256 OR expected IS DISTINCT FROM predecessor"),
        ('OR disclosed IS DISTINCT FROM true', 'OR retained IS DISTINCT FROM true'),
        ('jsonb_build_array(predecessor,target,expected,preview_digest,explanation,confirmed,disclosed,followups)', "jsonb_build_array('visual-allocation-correction-v1',predecessor,target,observed_hash,expected,preview_digest,explanation,confirmed,retained,followups)"),
        ('preview:=public.allocation_correction_preview(org,predecessor,target);','preview:=public.visual_allocation_correction_preview(org,predecessor,target);'),
        ("""RETURN jsonb_build_object('replay_id',NULL,'original_id',original.id,'storage_uri',original.storage_uri,'storage_backend',original.storage_backend,
    'storage_version',original.storage_version,'content_sha256',original.content_sha256,'content_byte_size',original.content_byte_size,""",
         """RETURN jsonb_build_object('replay_id',NULL,'original_id',original.id,'artifact_id',artifact.id,
    'original',jsonb_build_object('storage_uri',original.storage_uri,'content_sha256',original.content_sha256,'content_byte_size',original.content_byte_size),
    'visual',jsonb_build_object('storage_uri',artifact.storage_uri,'content_sha256',artifact.content_sha256,'content_byte_size',artifact.content_byte_size),"""),
    ])
    replace_function('allocation_correction_preview(uuid,uuid,uuid)', [
        ('a:=public.require_current_adoption_basis(org,target);', "IF old.source_variant<>'original' THEN RAISE EXCEPTION 'Retained visual correction required' USING ERRCODE='23514';END IF;\n    a:=public.require_current_adoption_basis(org,target);"),
    ])
    replace_function('prepare_allocation_correction(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean)', [
        ('old:=public.require_correction_authority(org,predecessor,target);', "old:=public.require_correction_authority(org,predecessor,target);\n  IF old.source_variant<>'original' THEN RAISE EXCEPTION 'Retained visual correction required' USING ERRCODE='23514';END IF;"),
    ])


def visual_commands():
    # Preserve the proven atomic membership, monetary, audit, impact and source
    # version operations; replace only their exact source admission and bindings.
    common = [
        ('original public.intake_original;basis record;', 'original public.intake_original;artifact public.visual_extract_artifact;basis record;'),
        ("prepared->>'content_sha256'", "prepared->'original'->>'content_sha256'"),
        ("prepared->>'content_byte_size'", "prepared->'original'->>'content_byte_size'"),
        ('SELECT p.* INTO STRICT a FROM public.allocation_treatment_review',
         "artifact:=public.require_visual_financial_proof(org,(prepared->>'artifact_id')::uuid,visual_attestation,request,prepared->>'request_digest');\n  SELECT p.* INTO STRICT a FROM public.allocation_treatment_review"),
        ('reason,whole_original_disclosure_reviewed,confirmed_at)',
         'reason,whole_original_disclosure_reviewed,confirmed_at,source_variant,visual_artifact_id,visual_disclosure_id,visual_integrity_attestation_id,retained_visual_source_reviewed)'),
        ("CASE WHEN original.mime_type='application/xml' THEN 'E_INVOICE' ELSE 'PDF_IMPORT' END", "'PDF_IMPORT'"),
        ("""original.uploaded_by,original.storage_uri,original.storage_backend,original.storage_version,
      (SELECT filename FROM public.intake_item WHERE organization_id=org AND id=original.item_id),original.mime_type,original.content_byte_size,original.content_sha256,""",
         """original.uploaded_by,artifact.storage_uri,artifact.storage_backend,artifact.storage_version,
      'reviewed-visual-source.pdf','application/pdf',artifact.content_byte_size,artifact.content_sha256,"""),
        ("'content_sha256',original.content_sha256", "'source_variant','visual','content_sha256',artifact.content_sha256"),
    ]
    command_variant('adopt_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,uuid)', 'adopt_visual_allocation',
        'org uuid,logical_id uuid,target uuid,disclosure_id uuid,expected uuid,observed_hash text,request uuid,preview_digest text,explanation text,confirmed boolean,retained boolean,attestation uuid,visual_attestation uuid', [
        ('prepared:=public.prepare_allocation_adoption(org,logical_id,target,expected,request,preview_digest,explanation,confirmed,disclosed);',
         'prepared:=public.prepare_visual_allocation(org,logical_id,target,disclosure_id,expected,observed_hash,request,preview_digest,explanation,confirmed,retained);'),
        ('duplicates:=public.adoption_duplicate_snapshot(org,r.id);','duplicates:=public.visual_adoption_duplicate_snapshot(org,r.id);'),
        ("original.content_sha256,duplicates,actor,request,prepared->>'request_digest',explanation,true,moment);",
         "original.content_sha256,duplicates,actor,request,prepared->>'request_digest',explanation,false,moment,'visual',artifact.id,disclosure_id,visual_attestation,true);"),
        *common,
    ])

    command_variant('correct_allocation(uuid,uuid,uuid,uuid,uuid,text,text,boolean,boolean,boolean,uuid)', 'correct_visual_allocation',
        'org uuid,predecessor uuid,target uuid,observed_hash text,expected uuid,request uuid,preview_digest text,explanation text,confirmed boolean,retained boolean,followups boolean,attestation uuid,visual_attestation uuid', [
        ('prepared:=public.prepare_allocation_correction(org,predecessor,target,expected,request,preview_digest,explanation,confirmed,disclosed,followups);',
         'prepared:=public.prepare_visual_allocation_correction(org,predecessor,target,observed_hash,expected,request,preview_digest,explanation,confirmed,retained,followups);'),
        ('duplicates:=public.correction_duplicate_snapshot(org,r.id);','duplicates:=public.visual_correction_duplicate_snapshot(org,r.id);'),
        ("original.content_sha256,duplicates,actor,request,prepared->>'request_digest',explanation,true,moment);",
         "original.content_sha256,duplicates,actor,request,prepared->>'request_digest',explanation,false,moment,'visual',artifact.id,old.visual_disclosure_id,visual_attestation,true);"),
        ('impact_snapshot,reason,costs_confirmed,whole_original_disclosure_reviewed,downstream_followups_reviewed,confirmed_at)',
         'impact_snapshot,reason,costs_confirmed,whole_original_disclosure_reviewed,downstream_followups_reviewed,confirmed_at,source_variant,retained_visual_source_reviewed)'),
        ("preview_digest,impacts,explanation,true,true,true,moment);", "preview_digest,impacts,explanation,true,false,true,moment,'visual',true);"),
        *common,
    ])


def visual_exception_outcomes():
    relation = "public.review_physical_relation(org,(left_member->>'invoice_review_id')::uuid,(right_member->>'invoice_review_id')::uuid)"
    replace_function('evaluate_exception_completion(uuid,uuid)', [
        ('right_review.content_sha256=left_review.content_sha256 OR', "public.review_physical_relation(org,right_review.id,left_review.id)<>'disjoint' OR"),
        ('AND part.member_count=1 AND m.logical_document_id=logical', 'AND m.logical_document_id=logical'),
        ("SELECT head_set.* INTO s FROM public.allocation_set head_set WHERE head_set.organization_id=org AND head_set.kind='intake_reviewed' AND head_set.content_sha256=r.content_sha256 AND NOT EXISTS(SELECT 1 FROM public.allocation_set successor WHERE successor.organization_id=org AND successor.predecessor_id=head_set.id);",
         's:=public.current_physical_allocation(org,r.id);'),
        ('facts:=public.adoption_duplicate_snapshot(org,r.id);', "facts:=CASE WHEN s.source_variant='visual' THEN public.visual_adoption_duplicate_snapshot(org,r.id) ELSE public.adoption_duplicate_snapshot(org,r.id) END;"),
        ("matches:=left_member->>'content_sha256'=right_member->>'content_sha256' OR", "matches:="+relation+"<>'disjoint' OR"),
        ("ELSIF left_member->>'content_sha256'=right_member->>'content_sha256' THEN", "ELSIF "+relation+"='overlap' THEN eligible:=false;\n    ELSIF "+relation+"='equal' THEN"),
        ("o.content_sha256 IN(SELECT value->>'content_sha256' FROM jsonb_array_elements(members))", "EXISTS(SELECT 1 FROM jsonb_array_elements(members) m WHERE public.source_aliases_overlap(public.source_physical_aliases(org,o.content_sha256,NULL,NULL),public.review_source_aliases(org,(m->>'invoice_review_id')::uuid)))"),
    ])
    replace_function('validate_exception_completion()', [
        ('s.content_sha256=r.content_sha256 AND s.scope_id=member.scope_id', "public.review_physical_relation(member.organization_id,s.invoice_review_id,r.id)='equal' AND s.scope_id=member.scope_id"),
    ])

# Clone require_current_adoption_basis as require_current_allocation_basis,
# removing only its p.member_count<>1 restriction. The original function stays
# unchanged, so original adoption can never release a segmented container.
CURRENT_BASIS_CHANGE = (
    "IF p.member_count<>1 OR NOT EXISTS(SELECT 1 FROM public.source_partition_member",
    "IF NOT EXISTS(SELECT 1 FROM public.source_partition_member",
)

VISUAL_BASIS = r"""
CREATE FUNCTION require_visual_allocation_basis(org uuid,target uuid,disclosure_id uuid) RETURNS public.visual_extract_artifact
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE allocation public.allocation_proposal;review public.invoice_review_revision;publication public.visual_disclosure_event;artifact public.visual_extract_artifact;
BEGIN
  allocation:=public.require_current_allocation_basis(org,target);
  SELECT * INTO publication FROM public.visual_disclosure_event WHERE organization_id=org AND id=disclosure_id;
  IF publication.id IS NULL OR publication.scope_id IS DISTINCT FROM allocation.scope_id
    OR publication.kind<>'published' OR EXISTS(SELECT 1 FROM public.visual_disclosure_event WHERE organization_id=org AND previous_event_id=publication.id) THEN
    RAISE EXCEPTION 'Current exact visual disclosure required' USING ERRCODE='23505';END IF;
  artifact:=public.require_visual_disclosure_publish(org,publication.artifact_id,allocation.scope_id,true);
  SELECT * INTO STRICT review FROM public.invoice_review_revision WHERE organization_id=org AND id=allocation.invoice_review_id;
  IF artifact.logical_document_id IS DISTINCT FROM allocation.logical_document_id OR artifact.document_ref_id IS DISTINCT FROM allocation.document_ref_id
    OR artifact.partition_revision_id IS DISTINCT FROM review.partition_revision_id OR artifact.source_sha256 IS DISTINCT FROM review.content_sha256 THEN
    RAISE EXCEPTION 'Exact logical visual source required' USING ERRCODE='23514';END IF;
  RETURN artifact;
END $$;

CREATE FUNCTION visual_allocation_preview(org uuid,target uuid,disclosure_id uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE allocation public.allocation_proposal;treatment public.allocation_treatment_review;artifact public.visual_extract_artifact;duplicates jsonb;digest text;
BEGIN
  allocation:=public.require_adoption_authority(org,target);
  BEGIN
    artifact:=public.require_visual_allocation_basis(org,target,disclosure_id);
  EXCEPTION WHEN insufficient_privilege OR unique_violation OR check_violation THEN
    RETURN jsonb_build_object('source_variant','visual','can_adopt',false,'preview_digest',NULL,'blocker','Current reviewed visual source and financial authority required');
  END;
  SELECT * INTO STRICT treatment FROM public.allocation_treatment_review WHERE organization_id=org AND id=target;
  duplicates:=public.visual_adoption_duplicate_snapshot(org,allocation.invoice_review_id);
  digest:=encode(sha256(convert_to(jsonb_build_array('visual-adoption-preview-v1',target,treatment.snapshot_digest,disclosure_id,artifact.id,
    artifact.content_sha256,artifact.content_byte_size,artifact.partition_revision_id,duplicates)::text,'UTF8')),'hex');
  RETURN jsonb_build_object('source_variant','visual','can_adopt',NOT (duplicates->>'blocked')::boolean,'preview_digest',digest,
    'blocker',CASE WHEN (duplicates->>'blocked')::boolean THEN 'Source conflict requires further review' END,
    'scope_id',allocation.scope_id,'scope_label',(SELECT snapshot->'scope'->>'label' FROM public.context_decision WHERE organization_id=org AND id=allocation.context_decision_id),
    'cost_count',allocation.line_count,'gross_amount',(SELECT gross_amount::text FROM public.invoice_review_revision WHERE organization_id=org AND id=allocation.invoice_review_id),
    'visual_content_sha256',artifact.content_sha256,'output_page_count',artifact.output_page_count);
END $$;

CREATE FUNCTION prepare_visual_allocation(org uuid,logical_id uuid,target uuid,disclosure_id uuid,expected uuid,observed_hash text,request uuid,preview_digest text,
  explanation text,confirmed boolean,retained boolean) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE allocation public.allocation_proposal;prior public.allocation_set;artifact public.visual_extract_artifact;original public.intake_original;preview jsonb;digest text;
BEGIN
  IF session_user<>'app_rw' THEN RAISE EXCEPTION 'Interactive runtime required' USING ERRCODE='42501';END IF;
  allocation:=public.require_adoption_authority(org,target);
  IF allocation.logical_document_id IS DISTINCT FROM logical_id THEN RAISE EXCEPTION 'Logical allocation inaccessible' USING ERRCODE='42501';END IF;
  IF request IS NULL OR disclosure_id IS NULL OR expected IS DISTINCT FROM target OR observed_hash IS NULL OR observed_hash !~ '^[0-9a-f]{64}$'
    OR preview_digest IS NULL OR preview_digest !~ '^[0-9a-f]{64}$' OR explanation IS NULL OR length(btrim(explanation)) NOT BETWEEN 1 AND 2000
    OR confirmed IS DISTINCT FROM true OR retained IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Exact financial and retained visual review required' USING ERRCODE='22023';END IF;
  digest:=encode(sha256(convert_to(jsonb_build_array('visual-adoption-v1',logical_id,target,disclosure_id,expected,observed_hash,preview_digest,explanation,confirmed,retained)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM public.allocation_set WHERE organization_id=org AND actor_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid AND request_key=request;
  IF FOUND THEN
    IF prior.source_variant<>'visual' OR prior.request_digest IS DISTINCT FROM digest THEN RAISE EXCEPTION 'Financial request changed' USING ERRCODE='23505';END IF;
    IF NOT public.invoice_source_access(org,prior.source_document_id,'read') OR NOT public.invoice_source_access(org,prior.source_document_id,'export') THEN
      RAISE EXCEPTION 'Retained visual source inaccessible' USING ERRCODE='42501';END IF;
    RETURN jsonb_build_object('replay_id',prior.id);
  END IF;
  artifact:=public.require_visual_allocation_basis(org,target,disclosure_id);
  IF observed_hash IS DISTINCT FROM artifact.content_sha256 THEN RAISE EXCEPTION 'Exact reviewed visual bytes required' USING ERRCODE='22023';END IF;
  preview:=public.visual_allocation_preview(org,target,disclosure_id);
  IF NOT (preview->>'can_adopt')::boolean OR preview->>'preview_digest' IS DISTINCT FROM preview_digest THEN
    RAISE EXCEPTION 'Visual financial basis changed or blocked' USING ERRCODE='23505';END IF;
  SELECT o.* INTO STRICT original FROM public.intake_original o JOIN public.document_ref r ON r.organization_id=o.organization_id AND r.intake_original_id=o.id
    WHERE r.organization_id=org AND r.id=allocation.document_ref_id;
  RETURN jsonb_build_object('replay_id',NULL,'original_id',original.id,'artifact_id',artifact.id,
    'original',jsonb_build_object('storage_uri',original.storage_uri,'content_sha256',original.content_sha256,'content_byte_size',original.content_byte_size),
    'visual',jsonb_build_object('storage_uri',artifact.storage_uri,'content_sha256',artifact.content_sha256,'content_byte_size',artifact.content_byte_size),
    'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text,'request_digest',digest);
END $$;
"""
