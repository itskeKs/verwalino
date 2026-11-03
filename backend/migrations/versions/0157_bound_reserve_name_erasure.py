"""Preserve known longer names and bound reserve erasure replacements."""
from alembic import op
from sqlalchemy import text

revision = '0157'
down_revision = '0156'
branch_labels = None
depends_on = None


MATCH_BEFORE = r"""    DECLARE target public.person; identity text; pattern text; character text;
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
"""

MATCH_AFTER = r"""    DECLARE context jsonb; targets text; protected text; pattern text;
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
"""

LOCK_BEFORE = "        PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));\n"
LOCK_AFTER = LOCK_BEFORE + """        IF TG_TABLE_SCHEMA='public' AND TG_TABLE_NAME='person' THEN
          IF current_user<>'app_migrator' AND public.scope_actor_role(org) IS NULL THEN
            RAISE EXCEPTION 'A valid contact principal is required' USING ERRCODE='42501';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('financial-inputs:'||org::text,0));
        END IF;
"""

PREPARE = """      PERFORM set_config('app.person_reserve_erasure_names',public.prepare_person_reserve_erasure(OLD.organization_id)::text,true);
"""
RESTORE = """      PERFORM set_config('app.person_reserve_erasure_names',COALESCE(previous_names,''),true);
"""


def replace_definition(signature, before, after, *, reverse=False):
    definition = op.get_bind().scalar(text('SELECT pg_get_functiondef(to_regprocedure(:signature))'),
        {'signature': 'public.' + signature})
    if reverse:
        before, after = after, before
    assert definition.count(before) == 1
    op.execute(definition.replace(before, after))


def change_erasure_trigger(*, reverse=False):
    replace_definition('erase_person_reserve_reasons()', '    BEGIN\n',
        "    DECLARE previous_names text:=current_setting('app.person_reserve_erasure_names',true);\n    BEGIN\n", reverse=reverse)
    replace_definition('erase_person_reserve_reasons()',
        '      -- Audit first:', PREPARE + '      -- Audit first:', reverse=reverse)
    replace_definition('erase_person_reserve_reasons()',
        '      RETURN NEW;', RESTORE + '      RETURN NEW;', reverse=reverse)


def upgrade():
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
    """)
    replace_definition('person_reserve_erasure_text(uuid,text)', MATCH_BEFORE, MATCH_AFTER)
    change_erasure_trigger()
    replace_definition('lock_strict_property_source_binding()', LOCK_BEFORE, LOCK_AFTER)


def downgrade():
    replace_definition('lock_strict_property_source_binding()', LOCK_BEFORE, LOCK_AFTER, reverse=True)
    change_erasure_trigger(reverse=True)
    replace_definition('person_reserve_erasure_text(uuid,text)', MATCH_BEFORE, MATCH_AFTER, reverse=True)
    op.execute('DROP FUNCTION prepare_person_reserve_erasure(uuid),reserve_erasure_name_pattern(text)')
