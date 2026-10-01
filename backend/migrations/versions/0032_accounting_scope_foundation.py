"""Immutable accounting spheres and exact, revocable capabilities.

This foundation does not switch existing resource policies to scoped enforcement.
The rollout migration must fail closed before team activation is exposed.
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None
RESOURCES = ("bank_account", "payment", "cost_entry", "settlement_run", "economic_plan")
TABLES = ("accounting_scope", "accounting_scope_unit", "accounting_scope_binding", "scope_migration_exception", "scope_grant")


def upgrade():
    op.execute("ALTER TABLE payment ADD COLUMN cancelled_at timestamptz")
    op.execute("ALTER TABLE payment ADD CONSTRAINT ck_payment_cancelled_draft CHECK(cancelled_at IS NULL OR status='draft')")
    for table in ("management_mandate", "owner_profile"):
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT uq_scope_{table}_org_id UNIQUE(organization_id,id)")
    for table in RESOURCES:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT uq_scope_{table}_property UNIQUE(organization_id,property_id,id)")
    op.execute("ALTER TABLE payment ADD CONSTRAINT uq_scope_payment_bank UNIQUE(organization_id,bank_account_id,id)")
    op.execute("""
    CREATE TABLE accounting_scope (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      property_id uuid NOT NULL, management_mandate_id uuid,
      scope_type varchar(4) NOT NULL CHECK(scope_type IN ('WEG','MIET','SEV')),
      label varchar(255) NOT NULL CHECK(length(trim(label))>0),
      valid_from date NOT NULL, valid_to date,
      whole_property boolean NOT NULL DEFAULT true,
      origin varchar(24) NOT NULL CHECK(origin IN ('REVIEWED','PROVEN_LEGACY')),
      created_by uuid, request_key uuid, created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(valid_to IS NULL OR valid_to>=valid_from),
      CHECK(origin='PROVEN_LEGACY' OR created_by IS NOT NULL),
      UNIQUE(organization_id,id), UNIQUE(organization_id,property_id,id),
      UNIQUE(organization_id,created_by,request_key),
      FOREIGN KEY(organization_id,property_id) REFERENCES property(organization_id,id),
      FOREIGN KEY(organization_id,management_mandate_id) REFERENCES management_mandate(organization_id,id),
      FOREIGN KEY(organization_id,created_by) REFERENCES "user"(organization_id,id)
    );
    CREATE TABLE accounting_scope_unit (
      organization_id uuid NOT NULL, scope_id uuid NOT NULL, unit_id uuid NOT NULL,
      property_id uuid NOT NULL, owner_profile_id uuid,
      PRIMARY KEY(organization_id,scope_id,unit_id),
      FOREIGN KEY(organization_id,property_id,scope_id) REFERENCES accounting_scope(organization_id,property_id,id),
      FOREIGN KEY(organization_id,property_id,unit_id) REFERENCES unit(organization_id,property_id,id),
      FOREIGN KEY(organization_id,owner_profile_id) REFERENCES owner_profile(organization_id,id)
    );
    CREATE TABLE accounting_scope_binding (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      property_id uuid NOT NULL, scope_id uuid NOT NULL,
      bank_account_id uuid, payment_id uuid, payment_bank_account_id uuid,
      cost_entry_id uuid, settlement_run_id uuid, economic_plan_id uuid,
      origin varchar(24) NOT NULL CHECK(origin IN ('REVIEWED','PROVEN_LEGACY')),
      reason text NOT NULL CHECK(length(trim(reason))>0), reviewed_by uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(num_nonnulls(bank_account_id,payment_id,cost_entry_id,settlement_run_id,economic_plan_id)=1),
      CHECK((payment_id IS NOT NULL)=(payment_bank_account_id IS NOT NULL)),
      CHECK(origin='PROVEN_LEGACY' OR reviewed_by IS NOT NULL),
      UNIQUE(organization_id,bank_account_id,scope_id),
      FOREIGN KEY(organization_id,property_id,scope_id) REFERENCES accounting_scope(organization_id,property_id,id),
      FOREIGN KEY(organization_id,reviewed_by) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,payment_bank_account_id,scope_id)
        REFERENCES accounting_scope_binding(organization_id,bank_account_id,scope_id),
      FOREIGN KEY(organization_id,payment_bank_account_id,payment_id)
        REFERENCES payment(organization_id,bank_account_id,id) DEFERRABLE INITIALLY DEFERRED
    );
    CREATE TABLE scope_migration_exception (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      property_id uuid NOT NULL, code varchar(64) NOT NULL, evidence jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,property_id,code),
      FOREIGN KEY(organization_id,property_id) REFERENCES property(organization_id,id)
    );
    CREATE TABLE scope_grant (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
      scope_id uuid NOT NULL, user_id uuid NOT NULL, capabilities varchar(16)[] NOT NULL,
      granted_by uuid NOT NULL, reason text NOT NULL CHECK(length(trim(reason))>0),
      expires_at timestamptz, revoked_at timestamptz, revoked_by uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(cardinality(capabilities)>0 AND array_position(capabilities,NULL) IS NULL
        AND capabilities <@ ARRAY['read','prepare','confirm','approve','send','export']::varchar[]
        AND capabilities @> ARRAY['read']::varchar[]),
      CHECK((revoked_at IS NULL)=(revoked_by IS NULL)),
      CHECK(expires_at IS NULL OR expires_at>created_at),
      FOREIGN KEY(organization_id,scope_id) REFERENCES accounting_scope(organization_id,id),
      FOREIGN KEY(organization_id,user_id) REFERENCES membership(organization_id,user_id),
      FOREIGN KEY(organization_id,granted_by) REFERENCES "user"(organization_id,id),
      FOREIGN KEY(organization_id,revoked_by) REFERENCES "user"(organization_id,id)
    );
    CREATE UNIQUE INDEX uq_scope_grant_active ON scope_grant(organization_id,scope_id,user_id) WHERE revoked_at IS NULL;
    CREATE INDEX ix_scope_grant_actor ON scope_grant(organization_id,user_id,scope_id) WHERE revoked_at IS NULL;
    CREATE INDEX ix_scope_property ON accounting_scope(organization_id,property_id,id);
    CREATE INDEX ix_scope_binding_scope ON accounting_scope_binding(organization_id,scope_id,id);
    """)
    for table in RESOURCES:
        column = f"{table}_id"
        op.execute(f"ALTER TABLE accounting_scope_binding ADD CONSTRAINT fk_binding_{table} FOREIGN KEY(organization_id,property_id,{column}) REFERENCES {table}(organization_id,property_id,id) DEFERRABLE INITIALLY DEFERRED")
        op.execute(f"CREATE UNIQUE INDEX uq_binding_{table} ON accounting_scope_binding(organization_id,{column}) WHERE {column} IS NOT NULL")
    op.execute("""
    CREATE FUNCTION scope_actor_role(org uuid) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT m.role FROM public.membership m JOIN public."user" u
        ON u.id=m.user_id AND u.organization_id=m.organization_id
      WHERE m.organization_id=org AND u.is_active
        AND org=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND m.user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
    $$;
    CREATE FUNCTION has_scope_capability(org uuid, sphere uuid, capability text) RETURNS boolean
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT COALESCE(public.scope_actor_role(org) IS NOT NULL
        AND (capability='read' OR public.scope_actor_role(org) IN ('OWNER_ADMIN','CLERK'))
        AND EXISTS(SELECT 1 FROM public.scope_grant g
          WHERE g.organization_id=org AND g.scope_id=sphere
            AND g.user_id=NULLIF(current_setting('app.current_user_id',true),'')::uuid
            AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp())
            AND capability=ANY(g.capabilities)),false)
    $$;
    REVOKE ALL ON FUNCTION scope_actor_role(uuid),has_scope_capability(uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION scope_actor_role(uuid),has_scope_capability(uuid,uuid,text) TO app_rw;
    CREATE FUNCTION protect_scope_evidence() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      RAISE EXCEPTION 'Accounting scope evidence is immutable; create a reviewed successor' USING ERRCODE='23514';
    END $$;
    CREATE FUNCTION protect_scope_grant() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid;
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Scope grants cannot be deleted' USING ERRCODE='23514'; END IF;
      org:=NEW.organization_id;
      PERFORM pg_advisory_xact_lock(hashtextextended('scope-acl:'||org::text,0));
      PERFORM 1 FROM public.accounting_scope WHERE organization_id=org AND id=NEW.scope_id FOR UPDATE;
      IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM public.accounting_scope s
        WHERE s.organization_id=org AND s.id=NEW.scope_id AND NOT s.whole_property
          AND NOT EXISTS(SELECT 1 FROM public.accounting_scope_unit u WHERE u.organization_id=org AND u.scope_id=s.id)) THEN
        RAISE EXCEPTION 'A scoped unit set must be reviewed before granting access' USING ERRCODE='23514';
      END IF;
      IF TG_OP='UPDATE' AND (
        OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL OR NEW.revoked_by IS NULL
        OR (to_jsonb(NEW)-'revoked_at'-'revoked_by') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at'-'revoked_by')
      ) THEN RAISE EXCEPTION 'Only one-way grant revocation is allowed' USING ERRCODE='23514'; END IF;
      IF TG_OP='UPDATE' THEN NEW.revoked_at:=clock_timestamp(); END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER scope_grant_evidence BEFORE INSERT OR UPDATE OR DELETE ON scope_grant
      FOR EACH ROW EXECUTE FUNCTION protect_scope_grant();
    """)
    op.execute("REVOKE ALL ON FUNCTION protect_scope_grant() FROM PUBLIC")
    for table in TABLES[:-1]:
        op.execute(f"CREATE TRIGGER scope_evidence BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence()")
    op.execute("""
    CREATE FUNCTION validate_scope_definition() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE m public.management_mandate;
    BEGIN
      IF NEW.management_mandate_id IS NOT NULL THEN
        SELECT * INTO m FROM public.management_mandate WHERE organization_id=NEW.organization_id AND id=NEW.management_mandate_id FOR SHARE;
        IF NOT FOUND OR m.mandate_type<>NEW.scope_type OR NEW.valid_from<m.start_date
           OR (m.end_date IS NOT NULL AND (NEW.valid_to IS NULL OR NEW.valid_to>m.end_date)) THEN
          RAISE EXCEPTION 'Scope must match mandate type and effective dates' USING ERRCODE='23514';
        END IF;
      END IF;
      IF NEW.scope_type='SEV' AND NEW.whole_property THEN
        RAISE EXCEPTION 'SEV requires an explicit unit set' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER validate_scope_definition BEFORE INSERT ON accounting_scope
      FOR EACH ROW EXECUTE FUNCTION validate_scope_definition();
    CREATE FUNCTION freeze_scope_unit_set() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere public.accounting_scope;
    BEGIN
      SELECT * INTO sphere FROM public.accounting_scope WHERE organization_id=NEW.organization_id AND id=NEW.scope_id FOR UPDATE;
      IF NOT FOUND OR sphere.whole_property
        OR EXISTS(SELECT 1 FROM public.scope_grant WHERE organization_id=NEW.organization_id AND scope_id=NEW.scope_id)
        OR EXISTS(SELECT 1 FROM public.accounting_scope_binding WHERE organization_id=NEW.organization_id AND scope_id=NEW.scope_id) THEN
        RAISE EXCEPTION 'Unit set is frozen when a scope is used' USING ERRCODE='23514';
      END IF;
      IF sphere.scope_type='SEV' AND NEW.owner_profile_id IS NULL THEN
        RAISE EXCEPTION 'SEV requires a reviewed owner for each unit' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION freeze_scope_unit_set() FROM PUBLIC;
    CREATE TRIGGER freeze_scope_unit_set BEFORE INSERT ON accounting_scope_unit
      FOR EACH ROW EXECUTE FUNCTION freeze_scope_unit_set();
    CREATE FUNCTION validate_accounting_binding(binding public.accounting_scope_binding) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere public.accounting_scope; resource jsonb; target text; target_id uuid; starts date; ends date;
    BEGIN
      SELECT * INTO sphere FROM public.accounting_scope WHERE organization_id=binding.organization_id AND id=binding.scope_id FOR SHARE;
      IF NOT sphere.whole_property AND NOT EXISTS(SELECT 1 FROM public.accounting_scope_unit WHERE organization_id=binding.organization_id AND scope_id=binding.scope_id) THEN
        RAISE EXCEPTION 'A binding requires the reviewed unit set' USING ERRCODE='23514';
      END IF;
      IF binding.bank_account_id IS NOT NULL THEN target:='bank_account'; target_id:=binding.bank_account_id;
      ELSIF binding.payment_id IS NOT NULL THEN target:='payment'; target_id:=binding.payment_id;
      ELSIF binding.cost_entry_id IS NOT NULL THEN target:='cost_entry'; target_id:=binding.cost_entry_id;
      ELSIF binding.settlement_run_id IS NOT NULL THEN target:='settlement_run'; target_id:=binding.settlement_run_id;
      ELSE target:='economic_plan'; target_id:=binding.economic_plan_id; END IF;
      EXECUTE format('SELECT to_jsonb(r) FROM public.%I r WHERE organization_id=$1 AND id=$2 FOR SHARE',target) INTO resource USING binding.organization_id,target_id;
      IF resource IS NULL THEN RAISE EXCEPTION 'Binding target missing' USING ERRCODE='23503'; END IF;
      IF target<>'bank_account' AND resource->>'management_mandate_id' IS NOT NULL
        AND (resource->>'management_mandate_id')::uuid IS DISTINCT FROM sphere.management_mandate_id THEN
        RAISE EXCEPTION 'Binding mandate mismatch' USING ERRCODE='23514';
      END IF;
      IF (target='bank_account' AND ((sphere.scope_type='WEG' AND resource->>'account_type'='KAUTION') OR (sphere.scope_type<>'WEG' AND resource->>'account_type'='RUECKLAGE')))
        OR (target='economic_plan' AND sphere.scope_type<>'WEG')
        OR (target='settlement_run' AND ((resource->>'run_type'='BK_MIETER')=(sphere.scope_type='WEG')))
        OR (target='payment' AND ((sphere.scope_type='WEG' AND resource->>'payment_type' IN ('KALTMIETE','NK_VORAUSZAHLUNG','KAUTION')) OR (sphere.scope_type<>'WEG' AND resource->>'payment_type' IN ('HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG')))) THEN
        RAISE EXCEPTION 'Binding accounting type mismatch' USING ERRCODE='23514';
      END IF;
      IF target='payment' THEN starts:=(resource->>'value_date')::date; ends:=starts;
      ELSIF target='cost_entry' THEN starts:=(resource->>'service_period_from')::date; ends:=(resource->>'service_period_to')::date;
      ELSIF target IN ('settlement_run','economic_plan') THEN starts:=(resource->>'period_start')::date; ends:=(resource->>'period_end')::date;
      END IF;
      IF starts<sphere.valid_from OR (sphere.valid_to IS NOT NULL AND ends>sphere.valid_to) THEN
        RAISE EXCEPTION 'Financial period is outside the accounting scope' USING ERRCODE='23514';
      END IF;
      IF target='settlement_run' AND NOT sphere.whole_property AND resource->>'unit_id' IS NOT NULL
        AND NOT EXISTS(SELECT 1 FROM public.accounting_scope_unit WHERE organization_id=binding.organization_id AND scope_id=binding.scope_id AND unit_id=(resource->>'unit_id')::uuid) THEN
        RAISE EXCEPTION 'Settlement unit is outside the accounting scope' USING ERRCODE='23514';
      END IF;
      RETURN;
    END $$;
    REVOKE ALL ON FUNCTION validate_accounting_binding(public.accounting_scope_binding) FROM PUBLIC;
    CREATE FUNCTION validate_binding_semantics() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN PERFORM public.validate_accounting_binding(NEW); RETURN NEW; END $$;
    REVOKE ALL ON FUNCTION validate_binding_semantics() FROM PUBLIC;
    CREATE FUNCTION validate_bound_resource() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE binding public.accounting_scope_binding;
    BEGIN
      EXECUTE format('SELECT * FROM public.accounting_scope_binding WHERE organization_id=$1 AND %I=$2',TG_TABLE_NAME||'_id') INTO binding USING NEW.organization_id,NEW.id;
      IF binding.id IS NOT NULL THEN PERFORM public.validate_accounting_binding(binding); END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION validate_bound_resource() FROM PUBLIC;
    CREATE CONSTRAINT TRIGGER validate_binding_semantics AFTER INSERT ON accounting_scope_binding
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_binding_semantics();
    """)
    op.execute("""
    CREATE FUNCTION protect_bound_resource_identity() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE bound boolean;
    BEGIN
      EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.accounting_scope_binding WHERE organization_id=$1 AND %I=$2)',TG_TABLE_NAME||'_id') INTO bound USING OLD.organization_id,OLD.id;
      IF bound AND (jsonb_build_array(to_jsonb(NEW)->'organization_id',to_jsonb(NEW)->'property_id',to_jsonb(NEW)->'management_mandate_id',to_jsonb(NEW)->'bank_account_id',to_jsonb(NEW)->'account_type',to_jsonb(NEW)->'payment_type',to_jsonb(NEW)->'run_type') IS DISTINCT FROM
                    jsonb_build_array(to_jsonb(OLD)->'organization_id',to_jsonb(OLD)->'property_id',to_jsonb(OLD)->'management_mandate_id',to_jsonb(OLD)->'bank_account_id',to_jsonb(OLD)->'account_type',to_jsonb(OLD)->'payment_type',to_jsonb(OLD)->'run_type')) THEN
        RAISE EXCEPTION 'Bound financial identity cannot be changed' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION protect_bound_resource_identity() FROM PUBLIC;
    CREATE FUNCTION protect_scoped_mandate_definition() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF (NEW.mandate_type,NEW.start_date,NEW.end_date) IS DISTINCT FROM (OLD.mandate_type,OLD.start_date,OLD.end_date)
        AND EXISTS(SELECT 1 FROM public.accounting_scope WHERE organization_id=OLD.organization_id AND management_mandate_id=OLD.id) THEN
        RAISE EXCEPTION 'A scoped mandate definition requires a reviewed successor' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION protect_scoped_mandate_definition() FROM PUBLIC;
    CREATE TRIGGER protect_scoped_mandate_definition BEFORE UPDATE ON management_mandate
      FOR EACH ROW EXECUTE FUNCTION protect_scoped_mandate_definition();
    """)
    for table in RESOURCES:
        op.execute(f"CREATE TRIGGER protect_bound_resource_identity BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_bound_resource_identity()")
        op.execute(f"CREATE CONSTRAINT TRIGGER validate_bound_resource AFTER UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_bound_resource()")
    op.execute("""
    CREATE FUNCTION bind_new_payment_to_account() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere public.accounting_scope; actor uuid;
    BEGIN
      SELECT s.* INTO sphere FROM public.accounting_scope_binding b
        JOIN public.accounting_scope s ON s.organization_id=b.organization_id AND s.id=b.scope_id
        WHERE b.organization_id=NEW.organization_id AND b.bank_account_id=NEW.bank_account_id;
      IF NOT FOUND THEN RETURN NEW; END IF; -- legacy compatibility until activation
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||NEW.organization_id::text,0));
      IF NOT public.has_scope_capability(NEW.organization_id,sphere.id,'prepare')
        OR (NEW.status='booked' AND NOT public.has_scope_capability(NEW.organization_id,sphere.id,'confirm')) THEN
        RAISE EXCEPTION 'Account scope permission required' USING ERRCODE='42501';
      END IF;
      IF NEW.management_mandate_id IS NOT NULL AND NEW.management_mandate_id IS DISTINCT FROM sphere.management_mandate_id THEN
        RAISE EXCEPTION 'Payment mandate must follow its account scope' USING ERRCODE='23514';
      END IF;
      NEW.management_mandate_id:=sphere.management_mandate_id;
      actor:=NULLIF(current_setting('app.current_user_id',true),'')::uuid;
      INSERT INTO public.accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by)
        VALUES(NEW.organization_id,NEW.property_id,sphere.id,NEW.id,NEW.bank_account_id,'REVIEWED','Inherited from the exact reviewed account scope',actor);
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION bind_new_payment_to_account() FROM PUBLIC;
    CREATE TRIGGER bind_new_payment_to_account BEFORE INSERT ON payment
      FOR EACH ROW EXECUTE FUNCTION bind_new_payment_to_account();
    """)
    op.execute("""
    CREATE FUNCTION authorize_bound_payment_write() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sphere uuid; financial_changed boolean;
    BEGIN
      IF OLD.cancelled_at IS NOT NULL THEN
        RAISE EXCEPTION 'Discarded payment drafts are immutable' USING ERRCODE='23514';
      END IF;
      IF NEW.cancelled_at IS NOT NULL THEN NEW.cancelled_at:=clock_timestamp(); END IF;
      SELECT scope_id INTO sphere FROM public.accounting_scope_binding
        WHERE organization_id=OLD.organization_id AND payment_id=OLD.id;
      IF NOT FOUND THEN
        IF EXISTS(SELECT 1 FROM public.accounting_scope_binding WHERE organization_id=NEW.organization_id AND bank_account_id=NEW.bank_account_id) THEN
          RAISE EXCEPTION 'Payment needs reviewed scope classification before changes' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||OLD.organization_id::text,0));
      financial_changed := (to_jsonb(NEW)-ARRAY['status','booked_at','booked_by','retention_until','reversed_by_id','updated_at'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','booked_at','booked_by','retention_until','reversed_by_id','updated_at']);
      IF (NEW.status='booked' AND NOT public.has_scope_capability(OLD.organization_id,sphere,'confirm'))
        OR ((NEW.status<>'booked' OR financial_changed) AND NOT public.has_scope_capability(OLD.organization_id,sphere,'prepare')) THEN
        RAISE EXCEPTION 'Payment scope capability required' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION authorize_bound_payment_write() FROM PUBLIC;
    CREATE TRIGGER authorize_bound_payment_write BEFORE UPDATE ON payment
      FOR EACH ROW EXECUTE FUNCTION authorize_bound_payment_write();
    """)
    tenant = "organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid"
    admin = "COALESCE(scope_actor_role(organization_id)='OWNER_ADMIN',false)"
    actor = "NULLIF(current_setting('app.current_user_id',true),'')::uuid"
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        sphere = "id" if table == "accounting_scope" else "scope_id"
        read = admin if table == "scope_migration_exception" else f"({admin} OR has_scope_capability(organization_id,{sphere},'read'))"
        if table == "scope_grant":
            read = f"({admin} OR user_id={actor})"
        insert = admin
        if table == "scope_grant":
            insert += f" AND granted_by={actor} AND revoked_at IS NULL"
        elif table == "accounting_scope":
            insert += f" AND origin='REVIEWED' AND created_by={actor}"
        elif table == "accounting_scope_binding":
            # Administration does not itself confer financial access.
            insert = f"has_scope_capability(organization_id,scope_id,'prepare') AND origin='REVIEWED' AND reviewed_by={actor}"
        op.execute(f"CREATE POLICY scope_select ON {table} FOR SELECT USING ({tenant} AND {read})")
        op.execute(f"CREATE POLICY scope_insert ON {table} FOR INSERT WITH CHECK ({tenant} AND {insert})")
        op.execute(f"REVOKE ALL ON {table} FROM app_rw")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO app_rw")
    op.execute(f"CREATE POLICY scope_revoke ON scope_grant FOR UPDATE USING ({tenant} AND {admin}) WITH CHECK ({tenant} AND {admin} AND revoked_by={actor})")
    op.execute("GRANT UPDATE(revoked_at,revoked_by) ON scope_grant TO app_rw")


def downgrade():
    # Disposable teardown only: production rollback must pause scoped writers.
    op.execute("DROP TRIGGER IF EXISTS authorize_bound_payment_write ON payment")
    op.execute("DROP FUNCTION IF EXISTS authorize_bound_payment_write()")
    op.execute("DROP TRIGGER IF EXISTS bind_new_payment_to_account ON payment")
    op.execute("DROP FUNCTION IF EXISTS bind_new_payment_to_account()")
    for table in RESOURCES:
        op.execute(f"DROP TRIGGER IF EXISTS protect_bound_resource_identity ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS validate_bound_resource ON {table}")
    op.execute("DROP TRIGGER IF EXISTS protect_scoped_mandate_definition ON management_mandate")
    op.execute("DROP FUNCTION IF EXISTS protect_scoped_mandate_definition(),protect_bound_resource_identity()")
    op.execute("DROP FUNCTION IF EXISTS validate_bound_resource(),validate_accounting_binding(public.accounting_scope_binding)")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS validate_binding_semantics(),freeze_scope_unit_set(),validate_scope_definition()")
    op.execute("DROP FUNCTION protect_scope_grant(),protect_scope_evidence(),has_scope_capability(uuid,uuid,text),scope_actor_role(uuid)")
    op.drop_constraint("uq_scope_payment_bank", "payment")
    for table in RESOURCES:
        op.drop_constraint(f"uq_scope_{table}_property", table)
    op.execute("ALTER TABLE payment DROP CONSTRAINT IF EXISTS ck_payment_cancelled_draft")
    op.execute("ALTER TABLE payment DROP COLUMN IF EXISTS cancelled_at")
    for table in ("management_mandate", "owner_profile"):
        op.drop_constraint(f"uq_scope_{table}_org_id", table)
