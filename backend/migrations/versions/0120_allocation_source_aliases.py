"""Recognize retained visual derivatives when checking physical consumption."""
from alembic import op
from sqlalchemy import text

revision = '0120'
down_revision = '0119'
branch_labels = None
depends_on = None


def patch_functions(reverse=False):
    grouped = {}
    for signature, before, after in reversed(PATCHES) if reverse else PATCHES:
        grouped.setdefault(signature, []).append((after, before) if reverse else (before, after))
    for signature, changes in grouped.items():
        definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'), {'signature': signature})
        for before, after in changes:
            assert definition.count(before) == 1, (signature, before)
            definition = definition.replace(before, after)
        op.execute(definition)


def upgrade():
    op.execute("""DO $$ DECLARE row record;BEGIN
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||row.id::text,0));END LOOP;
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||row.id::text,0));END LOOP;
      END $$;
      LOCK TABLE cost_entry,invoice_document,allocation_set,visual_extract_artifact IN SHARE ROW EXCLUSIVE MODE NOWAIT;""")
    op.execute('CREATE INDEX visual_extract_content_identity ON visual_extract_artifact(organization_id,content_sha256)')
    op.execute(FUNCTIONS)
    patch_functions()


def downgrade():
    # Removing recognition could permit a second financial effect through a
    # retained derivative. A populated system must repair forward.
    op.execute("""LOCK TABLE visual_extract_artifact,allocation_source_claim,allocation_set IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM visual_extract_artifact) AND EXISTS(SELECT 1 FROM allocation_set) THEN
        RAISE EXCEPTION 'Retain physical source alias protection; repair forward';END IF;END $$;""")
    patch_functions(reverse=True)
    op.execute('DROP FUNCTION source_physical_aliases(uuid,text,integer,integer),source_aliases_overlap(jsonb,jsonb),legacy_source_continuation_root(public.invoice_document); DROP INDEX visual_extract_content_identity')


FUNCTIONS = r"""
CREATE FUNCTION source_physical_aliases(org uuid,digest text,first_page integer,last_page integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE aliases jsonb;current_alias jsonb;candidate jsonb;artifact record;position integer:=0;edges integer:=0;first integer;last integer;
BEGIN
  IF org IS NULL OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Exact physical source identity required' USING ERRCODE='22023';END IF;
  PERFORM public.source_intervals_overlap(first_page,last_page,NULL,NULL);
  aliases:=jsonb_build_array(jsonb_build_object('hash',digest,'first',first_page,'last',last_page));
  WHILE position<jsonb_array_length(aliases) LOOP
    current_alias:=aliases->position;position:=position+1;
    first:=(current_alias->>'first')::integer;last:=(current_alias->>'last')::integer;
    -- Bound both retained identities and examined provenance records. Repeated
    -- renderings cannot turn a small result into an unbounded scan.
    FOR artifact IN SELECT source_sha256,page_start,page_end,output_page_count
      FROM public.visual_extract_artifact WHERE organization_id=org AND content_sha256=current_alias->>'hash'
      LIMIT 257
    LOOP
      edges:=edges+1;
      IF edges>256 THEN RAISE EXCEPTION 'Physical source identity requires further review' USING ERRCODE='23505';END IF;
      IF last>artifact.output_page_count THEN
        RAISE EXCEPTION 'Physical source identity requires further review' USING ERRCODE='23505';END IF;
      candidate:=jsonb_build_object('hash',artifact.source_sha256,
        'first',artifact.page_start+COALESCE(first,1)-1,
        'last',CASE WHEN last IS NULL THEN artifact.page_end ELSE artifact.page_start+last-1 END);
      IF NOT aliases @> jsonb_build_array(candidate) THEN
        IF jsonb_array_length(aliases)>=64 THEN
          RAISE EXCEPTION 'Physical source identity requires further review' USING ERRCODE='23505';END IF;
        aliases:=aliases||jsonb_build_array(candidate);
      END IF;
    END LOOP;
  END LOOP;
  RETURN (SELECT jsonb_agg(value ORDER BY value->>'hash',(value->>'first')::integer,(value->>'last')::integer)
    FROM jsonb_array_elements(aliases));
END $$;

CREATE FUNCTION source_aliases_overlap(first_aliases jsonb,second_aliases jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
  SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(first_aliases) a CROSS JOIN jsonb_array_elements(second_aliases) b
    WHERE a->>'hash'=b->>'hash' AND public.source_intervals_overlap((a->>'first')::integer,(a->>'last')::integer,(b->>'first')::integer,(b->>'last')::integer));
$$;

-- Ordinary legacy corrections already have paired cost and invoice version
-- chains. Exempt only their exact retained root, never an unrelated source with
-- the same hash. Reviewed allocations still require the atomic typed command.
CREATE FUNCTION legacy_source_continuation_root(source public.invoice_document) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
  WITH RECURSIVE chain AS (
    SELECT source.id,source.cost_entry_id,source.supersedes_id,source.chain_root_id,source.version,c.property_id,
      (SELECT scope_id FROM public.accounting_scope_binding WHERE organization_id=source.organization_id AND cost_entry_id=c.id) AS scope_id,
      ARRAY[source.id] AS visited,0 AS depth
    FROM public.cost_entry c WHERE c.organization_id=source.organization_id AND c.id=source.cost_entry_id
    UNION ALL
    SELECT d.id,d.cost_entry_id,d.supersedes_id,d.chain_root_id,d.version,c.property_id,p.scope_id,p.visited||d.id,p.depth+1
    FROM chain p JOIN public.invoice_document d ON d.organization_id=source.organization_id AND d.id=p.supersedes_id
      JOIN public.cost_entry c ON c.organization_id=d.organization_id AND c.id=d.cost_entry_id
    WHERE p.depth<100 AND NOT d.id=ANY(p.visited) AND d.chain_root_id=p.chain_root_id AND d.version=p.version-1
      AND d.released_at IS NOT NULL AND c.status='confirmed' AND c.superseded_by_id=p.cost_entry_id AND c.property_id=p.property_id
      AND (SELECT scope_id FROM public.accounting_scope_binding WHERE organization_id=c.organization_id AND cost_entry_id=c.id) IS NOT DISTINCT FROM p.scope_id
  ), roots AS (
    SELECT a.id FROM chain p JOIN public.allocation_set a ON a.organization_id=source.organization_id AND a.source_document_id=p.id
      WHERE p.depth>0 AND a.kind='proven_legacy' AND a.predecessor_id IS NULL
  ) SELECT CASE WHEN count(*)=1 THEN (array_agg(id))[1] END FROM roots;
$$;
REVOKE ALL ON FUNCTION source_physical_aliases(uuid,text,integer,integer),source_aliases_overlap(jsonb,jsonb),legacy_source_continuation_root(public.invoice_document) FROM PUBLIC,app_rw,app_worker,app_review;
"""


OVERLAP_BEFORE = """  RETURN EXISTS(SELECT 1 FROM public.allocation_source_claim c WHERE c.organization_id=org AND c.source_sha256=digest
    AND c.root_allocation_id IS DISTINCT FROM excluded_root AND public.source_intervals_overlap(c.page_start,c.page_end,first_page,last_page))
    OR EXISTS(SELECT 1 FROM public.allocation_set a WHERE a.organization_id=org AND a.predecessor_id IS NULL AND a.content_sha256=digest
      AND a.id IS DISTINCT FROM excluded_root AND NOT EXISTS(SELECT 1 FROM public.allocation_source_claim c WHERE c.organization_id=org AND c.root_allocation_id=a.id));"""
OVERLAP_AFTER = """  RETURN EXISTS(WITH subject AS MATERIALIZED (
      SELECT public.source_physical_aliases(org,digest,first_page,last_page) AS aliases
    ), roots AS (
      SELECT c.root_allocation_id AS id,c.source_sha256 AS hash,c.page_start,c.page_end
        FROM public.allocation_source_claim c WHERE c.organization_id=org
      UNION ALL SELECT a.id,a.content_sha256,NULL::integer,NULL::integer FROM public.allocation_set a
        WHERE a.organization_id=org AND a.predecessor_id IS NULL
          AND NOT EXISTS(SELECT 1 FROM public.allocation_source_claim c WHERE c.organization_id=org AND c.root_allocation_id=a.id)
      UNION ALL SELECT lineage.root_id,d.content_sha256,NULL::integer,NULL::integer
        FROM public.invoice_document d CROSS JOIN LATERAL (
          SELECT public.legacy_source_continuation_root(d) AS root_id OFFSET 0
        ) lineage WHERE d.organization_id=org AND d.released_at IS NOT NULL AND d.supersedes_id IS NOT NULL AND lineage.root_id IS NOT NULL
    ) SELECT 1 FROM roots r CROSS JOIN subject s WHERE r.id IS DISTINCT FROM excluded_root
      AND (EXISTS(SELECT 1 FROM jsonb_array_elements(s.aliases) e WHERE e->>'hash'=r.hash)
        OR EXISTS(SELECT 1 FROM public.visual_extract_artifact v WHERE v.organization_id=org AND v.content_sha256=r.hash))
      AND public.source_aliases_overlap(s.aliases,public.source_physical_aliases(org,r.hash,r.page_start,r.page_end)));"""

PATCHES = [
    ('allocation_source_overlap(uuid,text,integer,integer,uuid)', OVERLAP_BEFORE, OVERLAP_AFTER),
    ('adoption_duplicate_snapshot(uuid,uuid)',
     "IF EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed' AND content_sha256=subject.content_sha256) THEN blocked:=true;END IF;",
     "IF public.allocation_source_overlap(org,subject.content_sha256,NULL,NULL,NULL) THEN blocked:=true;END IF;"),
    ('correction_duplicate_snapshot(uuid,uuid)',
     "IF EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed' AND content_sha256=subject.content_sha256 AND document_ref_id<>subject.document_ref_id) THEN blocked:=true;END IF;",
     """IF public.allocation_source_overlap(org,subject.content_sha256,NULL,NULL,
       (SELECT c.root_allocation_id FROM public.allocation_set a JOIN public.allocation_source_member m ON m.organization_id=a.organization_id AND m.id=a.id
        JOIN public.allocation_source_claim c ON c.organization_id=m.organization_id AND c.id=m.claim_id
        WHERE a.organization_id=org AND a.logical_document_id=subject.logical_document_id AND a.predecessor_id IS NULL)) THEN blocked:=true;END IF;"""),
    *[(signature,
       "'policy','adoption-duplicates-v1','blocked',blocked",
       "'policy','adoption-duplicates-v2','physical_sources',public.source_physical_aliases(org,subject.content_sha256,NULL,NULL),'blocked',blocked")
      for signature in ('adoption_duplicate_snapshot(uuid,uuid)', 'correction_duplicate_snapshot(uuid,uuid)')],
    ('protect_adopted_cost()',
     "IF TG_OP<>'INSERT' AND EXISTS(SELECT 1 FROM public.document_cost_link",
     """IF TG_OP='UPDATE' AND OLD.status='confirmed' AND OLD.superseded_by_id IS NOT NULL
      AND NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id THEN
      RAISE EXCEPTION 'Established cost successor is immutable' USING ERRCODE='23514';END IF;
  IF TG_OP<>'INSERT' AND EXISTS(SELECT 1 FROM public.document_cost_link"""),
    ('protect_adopted_cost()',
     "IF TG_OP<>'DELETE' AND NEW.status='confirmed' AND NOT EXISTS",
     "IF TG_OP<>'DELETE' AND NEW.status='confirmed' AND (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'confirmed') AND NOT EXISTS"),
    ('protect_adopted_cost()',
     """OR EXISTS(SELECT 1 FROM public.invoice_document d WHERE d.organization_id=org AND d.cost_entry_id=target AND d.content_sha256=s.content_sha256))) THEN""",
     """OR false)) OR EXISTS(SELECT 1 FROM public.invoice_document d WHERE d.organization_id=org AND d.cost_entry_id=target
        AND public.allocation_source_overlap(org,d.content_sha256,NULL,NULL,
          CASE WHEN TG_OP='UPDATE' AND NEW.property_id=OLD.property_id THEN public.legacy_source_continuation_root(d) END)) THEN"""),
    ('protect_adopted_invoice()',
     """IF NEW.released_at IS NOT NULL AND EXISTS(SELECT 1 FROM public.allocation_set WHERE organization_id=org AND kind='intake_reviewed'
      AND content_sha256=NEW.content_sha256 AND source_document_id<>NEW.id) AND NOT public.adoption_bridge_admission(NEW) THEN""",
     """IF NEW.released_at IS NOT NULL AND (TG_OP='INSERT' OR OLD.released_at IS NULL) AND NOT public.adoption_bridge_admission(NEW)
      AND public.allocation_source_overlap(org,NEW.content_sha256,NULL,NULL,public.legacy_source_continuation_root(NEW)) THEN"""),
]
