"""Operational tasks with live-role, source and canonical-parent boundaries."""
from alembic import op

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE task (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
        created_by uuid NOT NULL REFERENCES "user"(id) ON DELETE RESTRICT,
        assigned_user_id uuid NOT NULL REFERENCES "user"(id) ON DELETE RESTRICT,
        property_id uuid REFERENCES property(id) ON DELETE RESTRICT,
        unit_id uuid REFERENCES unit(id) ON DELETE RESTRICT,
        lease_agreement_id uuid REFERENCES lease_agreement(id) ON DELETE RESTRICT,
        title varchar(255) NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 255),
        note text NOT NULL DEFAULT '' CHECK(length(note)<=10000),
        due_date date,
        completed boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_task_org_created ON task(organization_id,created_at,id);
    CREATE INDEX ix_task_org_due ON task(organization_id,due_date);
    CREATE INDEX ix_task_property ON task(property_id);
    CREATE INDEX ix_task_unit ON task(unit_id);
    CREATE INDEX ix_task_lease ON task(lease_agreement_id);
    CREATE INDEX ix_task_assignee ON task(assigned_user_id);

    -- Invoker adapter intentionally avoids policy dependencies on old source DDL.
    CREATE FUNCTION task_source_access(p_org uuid,p_property uuid,p_unit uuid,
        p_lease uuid,p_capability text) RETURNS boolean
        LANGUAGE plpgsql STABLE SECURITY INVOKER
        SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE actor_role text; permitted boolean;
    BEGIN
        actor_role := scope_actor_role(p_org);
        IF actor_role IS NULL OR actor_role NOT IN ('OWNER_ADMIN','CLERK','READ_ONLY')
           OR (p_capability<>'read' AND actor_role NOT IN ('OWNER_ADMIN','CLERK')) THEN
            RETURN false;
        END IF;
        IF p_property IS NOT NULL AND NOT coalesce(property_master_access(p_org,p_property,p_capability),false) THEN
            RETURN false;
        END IF;
        IF p_unit IS NOT NULL AND NOT EXISTS(SELECT 1 FROM unit u
            WHERE u.id=p_unit AND u.organization_id=p_org AND u.property_id=p_property) THEN
            RETURN false;
        END IF;
        IF p_lease IS NOT NULL THEN
            SELECT property_history_row_access(l.organization_id,'lease_agreement',to_jsonb(l),p_capability)
              INTO permitted FROM lease_agreement l JOIN unit u
                ON u.id=l.unit_id AND u.organization_id=l.organization_id
              WHERE l.id=p_lease AND l.organization_id=p_org AND l.unit_id=p_unit
                AND u.property_id=p_property;
            IF NOT coalesce(permitted,false) THEN RETURN false; END IF;
        END IF;
        RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION task_source_access(uuid,uuid,uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION task_source_access(uuid,uuid,uuid,uuid,text) TO app_rw;

    CREATE FUNCTION task_acl_lock() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
        PERFORM pg_advisory_xact_lock_shared(hashtextextended(
            'scope-acl:' || NULLIF(current_setting('app.current_org_id',true),''),0));
        RETURN NULL;
    END $$;
    CREATE TRIGGER a_task_acl_lock BEFORE INSERT OR UPDATE OR DELETE ON task
        FOR EACH STATEMENT EXECUTE FUNCTION task_acl_lock();

    CREATE FUNCTION task_parent_guard() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
        IF TG_OP='UPDATE' AND ROW(NEW.id,NEW.organization_id,NEW.created_by,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.organization_id,OLD.created_by,OLD.created_at) THEN
            RAISE EXCEPTION 'Task identity is immutable' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM "user" u WHERE u.id=NEW.created_by AND u.organization_id=NEW.organization_id)
           OR NOT EXISTS(SELECT 1 FROM "user" u WHERE u.id=NEW.assigned_user_id AND u.organization_id=NEW.organization_id)
           OR (NEW.property_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM property p
               WHERE p.id=NEW.property_id AND p.organization_id=NEW.organization_id))
           OR (NEW.unit_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM unit u WHERE u.id=NEW.unit_id
               AND u.organization_id=NEW.organization_id AND u.property_id=NEW.property_id))
           OR (NEW.lease_agreement_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM lease_agreement l
               WHERE l.id=NEW.lease_agreement_id AND l.organization_id=NEW.organization_id AND l.unit_id=NEW.unit_id)) THEN
            RAISE EXCEPTION 'Task parent coherence violation' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' OR NEW.assigned_user_id IS DISTINCT FROM OLD.assigned_user_id THEN
            IF NOT EXISTS(SELECT 1 FROM "user" u JOIN membership m
                ON m.user_id=u.id AND m.organization_id=u.organization_id
                WHERE u.id=NEW.assigned_user_id AND u.organization_id=NEW.organization_id
                  AND u.is_active AND m.role IN ('OWNER_ADMIN','CLERK','READ_ONLY')) THEN
                RAISE EXCEPTION 'Task assignee must be an active organization member' USING ERRCODE='23514';
            END IF;
        END IF;
        IF TG_OP='UPDATE' THEN NEW.updated_at := clock_timestamp(); END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER task_parent_guard BEFORE INSERT OR UPDATE ON task
        FOR EACH ROW EXECUTE FUNCTION task_parent_guard();

    ALTER TABLE task ENABLE ROW LEVEL SECURITY;
    ALTER TABLE task FORCE ROW LEVEL SECURITY;
    CREATE POLICY task_read ON task FOR SELECT USING (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND task_source_access(organization_id,property_id,unit_id,lease_agreement_id,'read'));
    CREATE POLICY task_insert ON task FOR INSERT WITH CHECK (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND created_by=NULLIF(current_setting('app.current_user_id',true),'')::uuid
        AND task_source_access(organization_id,property_id,unit_id,lease_agreement_id,'prepare'));
    CREATE POLICY task_update ON task FOR UPDATE USING (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND task_source_access(organization_id,property_id,unit_id,lease_agreement_id,'prepare'))
        WITH CHECK (organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND task_source_access(organization_id,property_id,unit_id,lease_agreement_id,'prepare'));
    CREATE POLICY task_delete ON task FOR DELETE USING (
        organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND task_source_access(organization_id,property_id,unit_id,lease_agreement_id,'prepare'));
    GRANT SELECT,INSERT,UPDATE,DELETE ON task TO app_rw;
    """)


def downgrade():
    op.execute("""
    DROP TABLE task;
    DROP FUNCTION task_parent_guard();
    DROP FUNCTION task_acl_lock();
    DROP FUNCTION task_source_access(uuid,uuid,uuid,uuid,text);
    """)
