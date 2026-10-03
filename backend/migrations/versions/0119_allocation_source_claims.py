"""Permanent physical-source identities for existing allocation roots."""
from alembic import op
from sqlalchemy import text

revision = '0119'
down_revision = '0118'
branch_labels = None
depends_on = None
BEFORE = "UNION ALL SELECT 'visual_disclosure_attestation',id,to_jsonb(r) FROM public.visual_disclosure_attestation r WHERE organization_id=org) evidence"
AFTER = BEFORE.replace(') evidence', " UNION ALL SELECT 'allocation_source_claim',id,to_jsonb(r) FROM public.allocation_source_claim r WHERE organization_id=org UNION ALL SELECT 'allocation_source_member',id,to_jsonb(r) FROM public.allocation_source_member r WHERE organization_id=org) evidence")


def coverage(reverse=False):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    before, after = (AFTER, BEFORE) if reverse else (BEFORE, AFTER)
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def upgrade():
    op.execute("""DO $$ DECLARE row record;BEGIN
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||row.id::text,0));END LOOP;
      FOR row IN SELECT id FROM organization ORDER BY id LOOP PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||row.id::text,0));END LOOP;
      END $$;
      LOCK TABLE allocation_set,invoice_document,document_cost_link IN SHARE ROW EXCLUSIVE MODE NOWAIT;
      CREATE TABLE allocation_source_claim(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),organization_id uuid NOT NULL,root_allocation_id uuid NOT NULL,
        source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),page_start integer,page_end integer,
        migration_overlap boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((page_start IS NULL AND page_end IS NULL) OR (page_start IS NOT NULL AND page_end IS NOT NULL AND page_start>=1 AND page_end>=page_start)),
        UNIQUE(organization_id,id),UNIQUE(organization_id,root_allocation_id),
        FOREIGN KEY(organization_id,root_allocation_id) REFERENCES allocation_set(organization_id,id));
      CREATE INDEX allocation_source_claim_hash ON allocation_source_claim(organization_id,source_sha256);
      CREATE TABLE allocation_source_member(
        id uuid PRIMARY KEY,organization_id uuid NOT NULL,claim_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(organization_id,id),
        FOREIGN KEY(organization_id,id) REFERENCES allocation_set(organization_id,id),
        FOREIGN KEY(organization_id,claim_id) REFERENCES allocation_source_claim(organization_id,id));
      """)
    for table in ('allocation_source_claim', 'allocation_source_member'):
        op.execute(f"""ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON {table} FROM PUBLIC,app_rw,app_worker,app_review;
          CREATE POLICY allocation_source_private ON {table} USING(false) WITH CHECK(false);
          CREATE TRIGGER allocation_source_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence();""")
    # Migration-only backfill preserves all historical legacy collisions. Runtime
    # admission below never accepts a client-supplied exception to overlap checks.
    op.execute("""
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_set a LEFT JOIN invoice_document d ON d.organization_id=a.organization_id AND d.id=a.source_document_id
        WHERE d.id IS NULL OR d.content_sha256 IS DISTINCT FROM a.content_sha256 OR d.cost_entry_id IS DISTINCT FROM a.primary_cost_id OR d.released_at IS NULL
          OR (a.kind='intake_reviewed' AND (NOT a.whole_original_disclosure_reviewed OR NOT EXISTS(SELECT 1 FROM intake_original o
            WHERE o.organization_id=a.organization_id AND o.id=a.intake_original_id AND o.content_sha256=a.content_sha256)))) THEN
        RAISE EXCEPTION 'Unproven allocation source identity; repair before migration';END IF;END $$;
      INSERT INTO allocation_source_claim(organization_id,root_allocation_id,source_sha256,migration_overlap)
        SELECT organization_id,id,content_sha256,count(*) OVER(PARTITION BY organization_id,content_sha256)>1
        FROM allocation_set WHERE predecessor_id IS NULL;
      WITH RECURSIVE lineage AS (
        SELECT a.id,a.organization_id,a.id AS root_id,a.content_sha256,ARRAY[a.id] AS visited,1 AS depth FROM allocation_set a WHERE predecessor_id IS NULL
        UNION ALL SELECT a.id,a.organization_id,l.root_id,a.content_sha256,l.visited||a.id,l.depth+1
          FROM allocation_set a JOIN lineage l ON l.organization_id=a.organization_id AND l.id=a.predecessor_id
          WHERE l.depth<101 AND NOT a.id=ANY(l.visited) AND a.content_sha256=l.content_sha256
      ) INSERT INTO allocation_source_member(id,organization_id,claim_id)
        SELECT l.id,l.organization_id,c.id FROM lineage l JOIN allocation_source_claim c ON c.organization_id=l.organization_id AND c.root_allocation_id=l.root_id;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_set a WHERE NOT EXISTS(SELECT 1 FROM allocation_source_member m WHERE m.organization_id=a.organization_id AND m.id=a.id)) THEN
        RAISE EXCEPTION 'Incomplete or incoherent allocation source ancestry; repair before migration';END IF;END $$;
      """)
    op.execute(FUNCTIONS)
    coverage()


def downgrade():
    op.execute("""LOCK TABLE allocation_source_claim,allocation_source_member,allocation_set IN ACCESS EXCLUSIVE MODE NOWAIT;
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM allocation_source_claim) OR EXISTS(SELECT 1 FROM allocation_source_member) THEN
        RAISE EXCEPTION 'Retain allocation source identities; repair forward';END IF;END $$;""")
    coverage(reverse=True)
    op.execute("""DROP TRIGGER record_allocation_source_identity ON allocation_set;
      DROP TABLE allocation_source_member,allocation_source_claim;
      DROP FUNCTION record_allocation_source_identity(),seal_allocation_source_claim(),seal_allocation_source_member(),
        allocation_source_overlap(uuid,text,integer,integer,uuid),source_intervals_overlap(integer,integer,integer,integer),lock_allocation_source(uuid);""")


FUNCTIONS = r"""
CREATE FUNCTION lock_allocation_source(org uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF org IS NULL THEN RAISE EXCEPTION 'Source organization required' USING ERRCODE='22023';END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
  PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
END $$;

CREATE FUNCTION source_intervals_overlap(first_start integer,first_end integer,second_start integer,second_end integer) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF (first_start IS NULL)<>(first_end IS NULL) OR (second_start IS NULL)<>(second_end IS NULL)
    OR first_start<1 OR first_end<first_start OR second_start<1 OR second_end<second_start THEN
    RAISE EXCEPTION 'Exact inclusive source interval required' USING ERRCODE='22023';END IF;
  RETURN first_start IS NULL OR second_start IS NULL OR (first_start<=second_end AND second_start<=first_end);
END $$;

CREATE FUNCTION allocation_source_overlap(org uuid,digest text,first_page integer,last_page integer,excluded_root uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
BEGIN
  PERFORM public.lock_allocation_source(org);
  IF digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Exact canonical source hash required' USING ERRCODE='22023';END IF;
  PERFORM public.source_intervals_overlap(first_page,last_page,NULL,NULL);
  RETURN EXISTS(SELECT 1 FROM public.allocation_source_claim c WHERE c.organization_id=org AND c.source_sha256=digest
    AND c.root_allocation_id IS DISTINCT FROM excluded_root AND public.source_intervals_overlap(c.page_start,c.page_end,first_page,last_page))
    OR EXISTS(SELECT 1 FROM public.allocation_set a WHERE a.organization_id=org AND a.predecessor_id IS NULL AND a.content_sha256=digest
      AND a.id IS DISTINCT FROM excluded_root AND NOT EXISTS(SELECT 1 FROM public.allocation_source_claim c WHERE c.organization_id=org AND c.root_allocation_id=a.id));
END $$;

CREATE FUNCTION seal_allocation_source_claim() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE root public.allocation_set;
BEGIN
  PERFORM public.lock_allocation_source(NEW.organization_id);
  SELECT * INTO root FROM public.allocation_set WHERE organization_id=NEW.organization_id AND id=NEW.root_allocation_id;
  IF root.id IS NULL OR root.predecessor_id IS NOT NULL OR NEW.source_sha256 IS DISTINCT FROM root.content_sha256
    OR NEW.page_start IS NOT NULL OR NEW.page_end IS NOT NULL OR NEW.migration_overlap THEN
    RAISE EXCEPTION 'Exact whole-original allocation root required' USING ERRCODE='23514';END IF;
  IF public.allocation_source_overlap(NEW.organization_id,NEW.source_sha256,NEW.page_start,NEW.page_end,root.id) THEN
    RAISE EXCEPTION 'Physical source already consumed' USING ERRCODE='23505';END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION seal_allocation_source_member() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE allocation public.allocation_set;claim public.allocation_source_claim;parent_claim uuid;
BEGIN
  PERFORM public.lock_allocation_source(NEW.organization_id);
  SELECT * INTO allocation FROM public.allocation_set WHERE organization_id=NEW.organization_id AND id=NEW.id;
  SELECT * INTO claim FROM public.allocation_source_claim WHERE organization_id=NEW.organization_id AND id=NEW.claim_id;
  IF allocation.predecessor_id IS NOT NULL THEN
    SELECT claim_id INTO parent_claim FROM public.allocation_source_member WHERE organization_id=NEW.organization_id AND id=allocation.predecessor_id;
  ELSE parent_claim:=NEW.claim_id;END IF;
  IF allocation.id IS NULL OR claim.id IS NULL OR claim.source_sha256 IS DISTINCT FROM allocation.content_sha256
    OR parent_claim IS DISTINCT FROM NEW.claim_id OR (allocation.predecessor_id IS NULL AND claim.root_allocation_id IS DISTINCT FROM allocation.id) THEN
    RAISE EXCEPTION 'Exact permanent source lineage required' USING ERRCODE='23514';END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION record_allocation_source_identity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
DECLARE claim uuid;
BEGIN
  PERFORM public.lock_allocation_source(NEW.organization_id);
  IF NEW.predecessor_id IS NULL THEN
    INSERT INTO public.allocation_source_claim(organization_id,root_allocation_id,source_sha256)
      VALUES(NEW.organization_id,NEW.id,NEW.content_sha256) RETURNING id INTO claim;
  ELSE
    SELECT claim_id INTO claim FROM public.allocation_source_member WHERE organization_id=NEW.organization_id AND id=NEW.predecessor_id;
    IF claim IS NULL THEN RAISE EXCEPTION 'Allocation predecessor identity missing' USING ERRCODE='23514';END IF;
  END IF;
  INSERT INTO public.allocation_source_member(id,organization_id,claim_id) VALUES(NEW.id,NEW.organization_id,claim);
  RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION lock_allocation_source(uuid),source_intervals_overlap(integer,integer,integer,integer),allocation_source_overlap(uuid,text,integer,integer,uuid),
  seal_allocation_source_claim(),seal_allocation_source_member(),record_allocation_source_identity() FROM PUBLIC,app_rw,app_worker,app_review;
CREATE TRIGGER seal_allocation_source_claim BEFORE INSERT ON allocation_source_claim FOR EACH ROW EXECUTE FUNCTION seal_allocation_source_claim();
CREATE TRIGGER seal_allocation_source_member BEFORE INSERT ON allocation_source_member FOR EACH ROW EXECUTE FUNCTION seal_allocation_source_member();
CREATE TRIGGER record_allocation_source_identity AFTER INSERT ON allocation_set FOR EACH ROW EXECUTE FUNCTION record_allocation_source_identity();
"""
