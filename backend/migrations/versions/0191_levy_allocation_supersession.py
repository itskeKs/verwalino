"""Let a special levy's allocation follow the owners of its due date by superseding rows (issue #681).

A levy is allocated when it is created. A sale or a share correction recorded afterwards makes
other owners hold the unit on the due date, but allocation rows are immutable evidence: they are
never updated or deleted. The unit's amount therefore moves by superseding: the rows that
stop counting get a marker in the new append-only table ``special_levy_allocation_supersession``
(who and when), and new allocation rows carry the amount for the owners of the due date. A row
counts when it has no marker. The new rows may only be inserted in the transaction that
supersedes rows of the same unit and levy, and the deferred total check now sums the counting
rows only, so the levy total stays exact before and after every move.

An owner can hold a unit's share of a levy again after it was superseded (a sale and a
buy-back), so the unique key ``(levy, unit, owner)`` of 0127 cannot stay; the deferred check
refuses two counting rows of one owner and unit instead. The same check keeps, for every unit a
transaction supersedes rows of, the exact amount the unit had, and checks at commit that every
counting row written in the transaction names an owner of the due date (the row-level check of
0127 moves there, so an archive can restore a superseded row whose ownership row has ended). The
definer ``special_levy_unit_allocations`` shows the ownership routes which levies name a unit even
when the actor may not read them, so only a levy that would actually move through a write can
refuse it.

Revision ID: 0191
Revises: 0190
"""
from alembic import op
from sqlalchemy import text

revision = '0191'
down_revision = '0190'
branch_labels = None
depends_on = None

CREATION_ONLY = "IF l.creation_xid IS DISTINCT FROM pg_current_xact_id() THEN"
OR_MOVE = """IF l.creation_xid IS DISTINCT FROM pg_current_xact_id() AND NOT (
          EXISTS(SELECT 1 FROM public.special_levy_allocation_supersession sup
            JOIN public.special_levy_allocation prior ON prior.organization_id=sup.organization_id AND prior.id=sup.allocation_id
            WHERE prior.organization_id=NEW.organization_id AND prior.special_levy_id=NEW.special_levy_id
              AND prior.unit_id=NEW.unit_id AND sup.supersession_xid=pg_current_xact_id())
          AND EXISTS(SELECT 1 FROM public.ownership_history own WHERE own.id=NEW.ownership_history_id
            AND own.organization_id=NEW.organization_id AND own.corrected_at IS NULL)) THEN"""


ROW_OWNERSHIP = """        IF NOT EXISTS(SELECT 1 FROM public.ownership_history h WHERE h.id=NEW.ownership_history_id
          AND h.organization_id=NEW.organization_id AND h.unit_id=NEW.unit_id AND h.owner_profile_id=NEW.owner_profile_id
          AND h.valid_from<=l.due_date AND (h.valid_to IS NULL OR h.valid_to>l.due_date)) THEN
          RAISE EXCEPTION 'Allocation does not match due-date ownership' USING ERRCODE='23514'; END IF;"""
AT_COMMIT = """        NEW.creation_xid:=pg_current_xact_id(); -- a counting row is checked against the due-date ownership at commit (0191)"""


def patch_guard(reverse=False):
    definition = op.get_bind().scalar(text("SELECT pg_get_functiondef('guard_special_levy()'::regprocedure)"))
    for before, after in ((CREATION_ONLY, OR_MOVE), (ROW_OWNERSHIP, AT_COMMIT)):
        if reverse:
            before, after = after, before
        if definition.count(before) != 1:
            raise RuntimeError('Unexpected special levy guard; repair forward')
        definition = definition.replace(before, after, 1)
    op.execute(definition)


COUNTING = """NOT EXISTS(SELECT 1 FROM public.special_levy_allocation_supersession s WHERE s.allocation_id=a.id)"""


def upgrade():
    op.execute("""
    ALTER TABLE special_levy_allocation
      ADD COLUMN creation_xid xid8,
      DROP CONSTRAINT uq_special_levy_owner_unit,
      ADD CONSTRAINT uq_special_levy_allocation_context UNIQUE(organization_id,id);
    CREATE INDEX ix_special_levy_allocation_unit ON special_levy_allocation(organization_id,unit_id);

    CREATE TABLE special_levy_allocation_supersession (
      allocation_id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
      superseded_by uuid NOT NULL, superseded_at timestamptz NOT NULL DEFAULT now(),
      supersession_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
      FOREIGN KEY(organization_id,allocation_id) REFERENCES special_levy_allocation(organization_id,id),
      FOREIGN KEY(organization_id,superseded_by) REFERENCES "user"(organization_id,id)
    );

    CREATE FUNCTION guard_special_levy_supersession() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE restoring boolean;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Special levy supersessions are append-only' USING ERRCODE='23514'; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||NEW.organization_id::text,0));
      NEW.supersession_xid:=pg_current_xact_id();
      -- The time is the transaction's, except while an archive restores the levy with its archived markers.
      SELECT l.creation_xid=pg_current_xact_id() INTO restoring FROM public.special_levy_allocation a
        JOIN public.special_levy l ON l.organization_id=a.organization_id AND l.id=a.special_levy_id
        WHERE a.organization_id=NEW.organization_id AND a.id=NEW.allocation_id;
      IF restoring IS DISTINCT FROM true THEN NEW.superseded_at:=now(); END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION guard_special_levy_supersession() FROM PUBLIC;
    ALTER TABLE special_levy_allocation_supersession ENABLE ROW LEVEL SECURITY;
    ALTER TABLE special_levy_allocation_supersession FORCE ROW LEVEL SECURITY;
    GRANT SELECT,INSERT ON special_levy_allocation_supersession TO app_rw;
    CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON special_levy_allocation_supersession
      FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();
    CREATE TRIGGER guard_special_levy_supersession BEFORE INSERT OR UPDATE OR DELETE ON special_levy_allocation_supersession
      FOR EACH ROW EXECUTE FUNCTION guard_special_levy_supersession();
    CREATE POLICY special_levy_supersession_read ON special_levy_allocation_supersession FOR SELECT USING (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND EXISTS(SELECT 1 FROM special_levy_allocation a
        WHERE a.organization_id=special_levy_allocation_supersession.organization_id AND a.id=allocation_id));
    CREATE POLICY special_levy_supersession_insert ON special_levy_allocation_supersession FOR INSERT WITH CHECK (
      organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
      AND scope_actor_role(organization_id) IN ('OWNER_ADMIN','CLERK')
      AND EXISTS(SELECT 1 FROM special_levy_allocation a JOIN special_levy l
          ON l.organization_id=a.organization_id AND l.id=a.special_levy_id
        WHERE a.organization_id=special_levy_allocation_supersession.organization_id AND a.id=allocation_id
          AND has_scope_capability(l.organization_id,l.accounting_scope_id,'prepare')
          AND (superseded_by=NULLIF(current_setting('app.current_user_id',true),'')::uuid
            OR l.creation_xid=pg_current_xact_id())));

    CREATE OR REPLACE FUNCTION validate_special_levy_total() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target uuid; total numeric; created boolean;
    BEGIN
      IF TG_TABLE_NAME='special_levy' THEN target:=NEW.id;
      ELSIF TG_TABLE_NAME='special_levy_allocation' THEN target:=NEW.special_levy_id;
      ELSE SELECT a.special_levy_id INTO target FROM public.special_levy_allocation a WHERE a.id=NEW.allocation_id; END IF;
      SELECT amount,creation_xid=pg_current_xact_id() INTO total,created FROM public.special_levy WHERE id=target;
      IF NOT EXISTS(SELECT 1 FROM public.special_levy_allocation a WHERE a.special_levy_id=target AND """ + COUNTING + """)
        OR total IS DISTINCT FROM (SELECT sum(a.amount) FROM public.special_levy_allocation a
          WHERE a.special_levy_id=target AND """ + COUNTING + """) THEN
        RAISE EXCEPTION 'Special levy allocations must conserve the exact total' USING ERRCODE='23514'; END IF;
      IF EXISTS(SELECT 1 FROM public.special_levy_allocation a WHERE a.special_levy_id=target AND """ + COUNTING + """
          GROUP BY a.unit_id,a.owner_profile_id HAVING count(*)>1) THEN
        RAISE EXCEPTION 'An owner holds one counting allocation per unit' USING ERRCODE='23514'; END IF;
      -- Every row written in this transaction names an owner of the due date (a moved row an uncorrected ownership
      -- row), also one superseded at once. Only while the levy itself is being created or restored may a superseded
      -- row keep the ownership row it was calculated from, which may have ended.
      IF EXISTS(SELECT 1 FROM public.special_levy_allocation a JOIN public.special_levy l ON l.id=a.special_levy_id
          WHERE a.special_levy_id=target AND a.creation_xid=pg_current_xact_id() AND (NOT created OR """ + COUNTING + """)
            AND NOT EXISTS(SELECT 1 FROM public.ownership_history h WHERE h.id=a.ownership_history_id
              AND h.organization_id=a.organization_id AND h.unit_id=a.unit_id AND h.owner_profile_id=a.owner_profile_id
              AND h.valid_from<=l.due_date AND (h.valid_to IS NULL OR h.valid_to>l.due_date)
              AND (created OR h.corrected_at IS NULL))) THEN
        RAISE EXCEPTION 'Allocation does not match due-date ownership' USING ERRCODE='23514'; END IF;
      -- A move keeps the exact amount of every unit it supersedes rows of (rows written in this transaction
      -- do not count as the amount the unit had).
      IF NOT created AND EXISTS(SELECT 1 FROM (SELECT o.unit_id,sum(o.amount) was FROM public.special_levy_allocation o
            JOIN public.special_levy_allocation_supersession s ON s.allocation_id=o.id
            WHERE o.special_levy_id=target AND s.supersession_xid=pg_current_xact_id()
              AND o.creation_xid IS DISTINCT FROM pg_current_xact_id() GROUP BY o.unit_id) p
          WHERE p.was IS DISTINCT FROM COALESCE((SELECT sum(a.amount) FROM public.special_levy_allocation a
            WHERE a.special_levy_id=target AND a.unit_id=p.unit_id AND """ + COUNTING + """),0)) THEN
        RAISE EXCEPTION 'Special levy allocations must conserve the exact amount of each unit' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE CONSTRAINT TRIGGER special_levy_supersession_total AFTER INSERT ON special_levy_allocation_supersession
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_special_levy_total();

    CREATE FUNCTION special_levy_unit_allocations(org uuid, unit uuid)
      RETURNS TABLE(levy_id uuid, scope_id uuid, due date, owner_id uuid, history_id uuid)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
      SELECT l.id,l.accounting_scope_id,l.due_date,a.owner_profile_id,a.ownership_history_id
      FROM public.special_levy_allocation a JOIN public.special_levy l ON l.organization_id=a.organization_id AND l.id=a.special_levy_id
      WHERE org=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND public.scope_actor_role(org) IS NOT NULL AND a.organization_id=org AND a.unit_id=unit AND """ + COUNTING + """
      ORDER BY l.due_date,l.id,a.owner_profile_id
    $$;
    REVOKE ALL ON FUNCTION special_levy_unit_allocations(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION special_levy_unit_allocations(uuid,uuid) TO app_rw;
    """)
    patch_guard()


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM special_levy_allocation_supersession)
      THEN RAISE EXCEPTION 'Cannot discard superseded special levy allocations'; END IF; END $$""")
    patch_guard(reverse=True)
    op.execute("""
    DROP FUNCTION special_levy_unit_allocations(uuid,uuid);
    DROP TRIGGER special_levy_supersession_total ON special_levy_allocation_supersession;
    CREATE OR REPLACE FUNCTION validate_special_levy_total() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target uuid; total numeric;
    BEGIN
      IF TG_TABLE_NAME='special_levy' THEN target:=NEW.id; ELSE target:=NEW.special_levy_id; END IF;
      SELECT amount INTO total FROM public.special_levy WHERE id=target;
      IF NOT EXISTS(SELECT 1 FROM public.special_levy_allocation a WHERE a.special_levy_id=target)
        OR total IS DISTINCT FROM (SELECT sum(a.amount) FROM public.special_levy_allocation a WHERE a.special_levy_id=target) THEN
        RAISE EXCEPTION 'Special levy allocations must conserve the exact total' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    DROP TABLE special_levy_allocation_supersession;
    DROP FUNCTION guard_special_levy_supersession();
    DROP INDEX ix_special_levy_allocation_unit;
    ALTER TABLE special_levy_allocation
      DROP COLUMN creation_xid,
      DROP CONSTRAINT uq_special_levy_allocation_context,
      ADD CONSTRAINT uq_special_levy_owner_unit UNIQUE(special_levy_id,unit_id,owner_profile_id);
    """)
