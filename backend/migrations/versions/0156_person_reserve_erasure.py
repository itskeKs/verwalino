"""Scrub an anonymized person's complete names in linked reserve reasons."""
from alembic import op
from sqlalchemy import text

revision = '0156'
down_revision = '0155'
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
    # Nested triggers can be runtime-owned temporary objects. Privileges, not
    # trigger depth or caller-controlled context, exclude that untrusted writer.
    op.execute('REVOKE UPDATE ON public.reserve_ledger_entry FROM app_rw')
    op.execute(r"""
    CREATE FUNCTION person_reserve_erasure_text(org uuid,value text) RETURNS text
    LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE target public.person; identity text; pattern text; character text;
      result text:=value;
    BEGIN
      SELECT * INTO target FROM public.person p WHERE p.organization_id=org
        AND p.id=NULLIF(current_setting('app.person_reserve_erasure',true),'')::uuid
        AND p.anonymized_at IS NULL;
      IF NOT FOUND THEN RETURN value; END IF;
      -- Like the organization-erasure matcher, use complete normalized names,
      -- never individual name fragments. Only this reason scope matches embedded names.
      FOREACH identity IN ARRAY ARRAY[
        CASE WHEN btrim(target.first_name)<>'' AND btrim(target.last_name)<>''
          THEN target.first_name||' '||target.last_name END,target.company_name]
      LOOP
        identity:=btrim(regexp_replace(identity,'[[:space:]]+',' ','g'));
        IF identity IS NULL OR identity='' THEN CONTINUE; END IF;
        pattern:='';
        FOREACH character IN ARRAY string_to_array(identity,NULL) LOOP
          pattern:=pattern||CASE WHEN character=' ' THEN '[[:space:]]+'
            WHEN strpos(E'\\.^$|?*+()[]{}',character)>0 THEN E'\\'||character ELSE character END;
        END LOOP;
        -- Hyphenated/longer names belong to another identity; retain them verbatim.
        result:=regexp_replace(result,'(?<![[:alnum:]_-])'||pattern||'(?![[:alnum:]_-])','Gelöscht','gi');
      END LOOP;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION person_reserve_erasure_text(uuid,text) FROM PUBLIC,app_rw,app_worker,app_review;

    CREATE FUNCTION erase_person_reserve_reasons() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
    BEGIN
      -- Invoker rights require the protected privacy command's effective role.
      -- Runtime has no column grant to set anonymized_at, and cannot call this trigger.
      IF current_user<>'app_migrator' OR OLD.organization_id IS DISTINCT FROM
        NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'Use the person privacy command' USING ERRCODE='42501';
      END IF;
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
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION erase_person_reserve_reasons() FROM PUBLIC,app_rw,app_worker,app_review;
    CREATE TRIGGER z_erase_person_reserve_reasons BEFORE UPDATE ON public.person FOR EACH ROW
      WHEN (OLD.anonymized_at IS NULL AND NEW.anonymized_at IS NOT NULL
        AND current_setting('app.person_reserve_erasure',true)=OLD.id::text)
      EXECUTE FUNCTION erase_person_reserve_reasons();
    """)
    change_guard('guard_reserve_ledger', RESERVE_EXCEPTION)
    change_guard('guard_audit_event_payload', AUDIT_EXCEPTION)
    change_privacy_command()


def downgrade():
    change_privacy_command(reverse=True)
    op.execute('DROP TRIGGER z_erase_person_reserve_reasons ON public.person')
    change_guard('guard_audit_event_payload', AUDIT_EXCEPTION, reverse=True)
    change_guard('guard_reserve_ledger', RESERVE_EXCEPTION, reverse=True)
    op.execute('DROP FUNCTION erase_person_reserve_reasons(),person_reserve_erasure_text(uuid,text)')
    op.execute('GRANT UPDATE ON public.reserve_ledger_entry TO app_rw')
