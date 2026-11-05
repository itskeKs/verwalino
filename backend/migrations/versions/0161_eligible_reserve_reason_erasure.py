"""Scrub eligible incidental reserve reasons after authoritative person retention."""
from alembic import op
from sqlalchemy import text

revision = '0161'
down_revision = '0160'
branch_labels = None
depends_on = None

RESERVE_EXCEPTION = """      IF TG_OP='UPDATE' AND pg_trigger_depth()>1
        AND OLD.organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND (to_jsonb(NEW)-'reason')=(to_jsonb(OLD)-'reason')
        AND NEW.reason IS DISTINCT FROM OLD.reason
        AND NEW.reason=public.person_reserve_erasure_text(OLD.organization_id,OLD.reason) THEN
        RETURN NEW;
      END IF;
"""

AUDIT_EXCEPTION = """      IF TG_OP='UPDATE' AND current_user='app_migrator' AND pg_trigger_depth()>1
        AND OLD.organization_id=NULLIF(current_setting('app.current_org_id',true),'')::uuid
        AND (to_jsonb(NEW)-'payload_diff')=(to_jsonb(OLD)-'payload_diff')
        AND OLD.entity_type='settlement_run'
        AND OLD.action IN ('reserve.override_added','reserve.contribution_reduced','reserve.contribution_restored')
        AND jsonb_typeof(OLD.payload_diff->'reason')='string'
        AND NEW.payload_diff IS DISTINCT FROM OLD.payload_diff
        AND NEW.payload_diff=jsonb_set(OLD.payload_diff,'{reason}',
          to_jsonb(public.person_reserve_erasure_text(OLD.organization_id,OLD.payload_diff->>'reason')))
        AND EXISTS(SELECT 1 FROM public.reserve_ledger_entry e
          WHERE e.organization_id=OLD.organization_id AND e.settlement_run_id=OLD.entity_id
            AND e.id::text=OLD.payload_diff->>'entry_id' AND e.reason=OLD.payload_diff->>'reason') THEN
        RETURN NEW;
      END IF;
"""


def change_guard(name, exception, *, reverse=False):
    definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(to_regprocedure(:name))'),
        {'name': f'public.{name}()'})
    if reverse:
        assert definition.count(exception) == 1
        definition = definition.replace(exception, '')
    else:
        assert definition.count('    BEGIN\n') == 1
        definition = definition.replace('    BEGIN\n', '    BEGIN\n' + exception)
    op.execute(definition)


def change_privacy_command(*, reverse=False):
    definition = op.get_bind().scalar(text(
        "SELECT pg_get_functiondef('public.apply_person_privacy(uuid,uuid,text)'::regprocedure)"))
    changes = [
        ('outcome text; now_utc timestamp;', "outcome text; now_utc timestamp; previous_person text:=current_setting('app.person_reserve_erasure',true);"),
        ("        UPDATE public.person SET first_name='',last_name='Anonymisiert'",
         "        PERFORM set_config('app.person_reserve_erasure',person::text,true);\n"
         "        UPDATE public.person SET first_name='',last_name='Anonymisiert'"),
        ("        outcome:='ANONYMIZED';", "        PERFORM set_config('app.person_reserve_erasure',COALESCE(previous_person,''),true);\n"
         "        outcome:='ANONYMIZED';"),
    ]
    for before, after in changes:
        if reverse:
            before, after = after, before
        assert definition.count(before) == 1
        definition = definition.replace(before, after)
    op.execute(definition)


def upgrade():
    # Runtime-owned nested triggers cannot receive authority to rewrite history.
    op.execute('REVOKE UPDATE ON public.reserve_ledger_entry FROM app_rw')
    op.execute(r"""
    CREATE FUNCTION reserve_erasure_name_pattern(identity text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE pattern text:=''; character text;
    BEGIN
      identity:=btrim(regexp_replace(identity,'[[:space:]]+',' ','g'));
      IF identity IS NULL OR identity='' THEN RETURN NULL; END IF;
      FOREACH character IN ARRAY string_to_array(identity,NULL) LOOP
        pattern:=pattern||CASE WHEN character=' ' THEN '[[:space:]]+'
          WHEN strpos(E'\\.^$|?*+()[]{}',character)>0 THEN E'\\'||character ELSE character END;
      END LOOP;
      RETURN '(?<![[:alnum:]_-])'||pattern||'(?![[:alnum:]_-])';
    END $$;
    REVOKE ALL ON FUNCTION reserve_erasure_name_pattern(text) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION prepare_person_reserve_erasure(org uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE target public.person; identities text[]; targets text; protected text;
    BEGIN
      SELECT * INTO target FROM public.person p WHERE p.organization_id=org
        AND p.id=NULLIF(current_setting('app.person_reserve_erasure',true),'')::uuid
        AND p.anonymized_at IS NULL;
      IF NOT FOUND THEN RETURN '{}'::jsonb; END IF;
      SELECT array_agg(DISTINCT lower(btrim(regexp_replace(name,'[[:space:]]+',' ','g')))),
        string_agg(DISTINCT public.reserve_erasure_name_pattern(name),'|') INTO identities,targets
        FROM unnest(ARRAY[CASE WHEN btrim(target.first_name)<>'' AND btrim(target.last_name)<>''
          THEN target.first_name||' '||target.last_name END,target.company_name]) name
        WHERE btrim(COALESCE(name,''))<>'';
      -- One organization-scoped lookup per actual erasure, not per ledger/audit
      -- row or guard invocation. Cache only longer identities containing a target.
      SELECT string_agg(DISTINCT public.reserve_erasure_name_pattern(name),'|') INTO protected
        FROM public.person p CROSS JOIN LATERAL (VALUES
          (CASE WHEN btrim(p.first_name)<>'' AND btrim(p.last_name)<>'' THEN p.first_name||' '||p.last_name END),
          (p.company_name)) names(name)
        WHERE p.organization_id=org AND p.id<>target.id AND p.anonymized_at IS NULL
          AND name ~* targets
          AND lower(btrim(regexp_replace(name,'[[:space:]]+',' ','g')))<>ALL(identities);
      RETURN jsonb_build_object('org',org,'person',target.id,'targets',targets,'protected',protected);
    END $$;
    REVOKE ALL ON FUNCTION prepare_person_reserve_erasure(uuid) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION person_reserve_erasure_text(org uuid,value text) RETURNS text
    LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE context jsonb; targets text; protected text; pattern text;
      position integer:=1; first integer; following integer; matched text; prefix text;
      result text:=''; compact text:='';
    BEGIN
      IF value IS NULL THEN RETURN value; END IF;
      context:=NULLIF(current_setting('app.person_reserve_erasure_names',true),'')::jsonb;
      IF context IS NULL OR context->>'org' IS DISTINCT FROM org::text
        OR context->>'person' IS DISTINCT FROM current_setting('app.person_reserve_erasure',true) THEN
        context:=public.prepare_person_reserve_erasure(org);
      END IF;
      targets:=context->>'targets'; protected:=context->>'protected';
      IF targets IS NULL THEN RETURN value; END IF;
      pattern:=CASE WHEN protected IS NULL THEN targets ELSE '(?:'||protected||')|(?:'||targets||')' END;
      -- Leftmost/longest matches consume an entire known longer identity before
      -- considering an embedded target name. All offsets refer to original text.
      LOOP
        first:=regexp_instr(value,pattern,position,1,0,'i');
        EXIT WHEN first=0;
        following:=regexp_instr(value,pattern,position,1,1,'i');
        matched:=substr(value,first,following-first);
        prefix:=substr(value,position,first-position);
        IF protected IS NOT NULL AND matched ~* ('^(?:'||protected||')$') THEN
          result:=result||prefix||matched; compact:=compact||prefix||matched;
        ELSE
          result:=result||prefix||'Gelöscht'; compact:=compact||prefix||'*';
        END IF;
        position:=following;
      END LOOP;
      result:=result||substr(value,position); compact:=compact||substr(value,position);
      -- Only the identifying spans change. A one-character marker cannot expand
      -- an accepted reason; no surrounding text is truncated to fit the column.
      RETURN CASE WHEN char_length(result)<=2000 THEN result ELSE compact END;
    END $$;
    REVOKE ALL ON FUNCTION person_reserve_erasure_text(uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE FUNCTION erase_person_reserve_reasons() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    DECLARE previous_names text:=current_setting('app.person_reserve_erasure_names',true);
    BEGIN
      -- Invoker rights require the protected privacy command's effective role.
      -- Runtime has no column grant to set anonymized_at, and cannot call this trigger.
      IF current_user<>'app_migrator' OR OLD.organization_id IS DISTINCT FROM
        NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Use the person privacy command' USING ERRCODE='42501';
      END IF;
      PERFORM set_config('app.person_reserve_erasure_names',public.prepare_person_reserve_erasure(OLD.organization_id)::text,true);
      -- Audit first: both guard validation and selection use the original ledger reason.
      UPDATE public.audit_event a SET payload_diff=jsonb_set(a.payload_diff,'{reason}',
          to_jsonb(public.person_reserve_erasure_text(OLD.organization_id,a.payload_diff->>'reason')))
        FROM public.reserve_ledger_entry e
        WHERE a.organization_id=OLD.organization_id AND e.organization_id=a.organization_id
          AND a.entity_type='settlement_run' AND a.entity_id=e.settlement_run_id
          AND a.action IN ('reserve.override_added','reserve.contribution_reduced','reserve.contribution_restored')
          AND a.payload_diff->>'entry_id'=e.id::text AND a.payload_diff->>'reason'=e.reason
          AND jsonb_typeof(a.payload_diff->'reason')='string'
          AND public.person_reserve_erasure_text(OLD.organization_id,e.reason) IS DISTINCT FROM e.reason;
      UPDATE public.reserve_ledger_entry e SET reason=public.person_reserve_erasure_text(OLD.organization_id,e.reason)
        WHERE e.organization_id=OLD.organization_id
          AND public.person_reserve_erasure_text(OLD.organization_id,e.reason) IS DISTINCT FROM e.reason;
      PERFORM set_config('app.person_reserve_erasure_names',COALESCE(previous_names,''),true);
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION erase_person_reserve_reasons() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER z_erase_person_reserve_reasons BEFORE UPDATE ON public.person FOR EACH ROW
      WHEN (OLD.anonymized_at IS NULL AND NEW.anonymized_at IS NOT NULL
        AND current_setting('app.person_reserve_erasure',true)=OLD.id::text)
      EXECUTE FUNCTION erase_person_reserve_reasons();

    -- Existing a_lock_contact_source acquires the shared ACL lock first. This
    -- statement trigger adds only synchronization, never a new contact policy.
    CREATE FUNCTION lock_person_reserve_names() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE org uuid:=NULLIF(current_setting('app.current_org_id',true),'')::uuid;
    BEGIN
      IF org IS NOT NULL AND (current_user='app_migrator'
        OR public.property_source_security_mode(org)='PREPARATION'
        OR public.scope_actor_role(org) IS NOT NULL) THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
      END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION lock_person_reserve_names() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER b_lock_person_reserve_names BEFORE INSERT OR UPDATE OR DELETE ON public.person
      FOR EACH STATEMENT EXECUTE FUNCTION lock_person_reserve_names();
""")
    change_guard('guard_reserve_ledger', RESERVE_EXCEPTION)
    change_guard('guard_audit_event_payload', AUDIT_EXCEPTION)
    # Existing apply_person_privacy gates still use the shared retention facts.
    change_privacy_command()


def downgrade():
    change_privacy_command(reverse=True)
    op.execute('DROP TRIGGER z_erase_person_reserve_reasons ON public.person')
    op.execute('DROP TRIGGER b_lock_person_reserve_names ON public.person')
    change_guard('guard_audit_event_payload', AUDIT_EXCEPTION, reverse=True)
    change_guard('guard_reserve_ledger', RESERVE_EXCEPTION, reverse=True)
    op.execute('DROP FUNCTION erase_person_reserve_reasons(),person_reserve_erasure_text(uuid,text),'
        'prepare_person_reserve_erasure(uuid),reserve_erasure_name_pattern(text),lock_person_reserve_names()')
    op.execute('GRANT UPDATE ON public.reserve_ledger_entry TO app_rw')
