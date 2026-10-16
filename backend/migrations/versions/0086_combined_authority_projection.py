"""Share implicit-actor authority predicates with private strict projections.

Only property-source mode and target actor are explicit. Tenant and selected
financial scope remain caller boundaries; the financial effect switch stays live.
"""
import hashlib
import re
from alembic import op
from sqlalchemy import text

revision = '0086'
down_revision = '0085'
branch_labels = None
depends_on = None

EXPECTED = {
    'dunning_contact_access': ('dunning_contact_access(uuid,uuid,uuid,text)', 'eee17f82bded621105e5c723e534a0b58b28988df58dae2b92e9063d442d8bf7'),
    'dunning_source_access': ('dunning_source_access(uuid,uuid,text)', '95b2d013c90b427198fa9e12e80fb7eb18e7fc182034c823c5159d255e9deda3'),
    'financial_disclosure_before_contacts': ('financial_disclosure_before_contacts(uuid,uuid,text)', '3d1f8fddadbc083dc36c96ac203fc883490e7458843f59e12bd4e4335dcc11e9'),
    'financial_scope_access': ('financial_scope_access(uuid,text,uuid,text)', '006637c9494fed7e04bdb1edc7a5cf8f3b72cbf75a796f79d3dea4294aea5ec6'),
    'financial_sources_before_contacts': ('financial_sources_before_contacts(uuid,uuid)', '201cb61c212734391f29cfd98131c2bcf400a160ec029e47300b30fef33bed84'),
    'has_person_data_capability': ('has_person_data_capability(uuid,uuid,text)', '84bcee422768e1ea42705c616adc581e5a575c011f8c0a47fac2186612ce64a4'),
    'has_scope_capability': ('has_scope_capability(uuid,uuid,text)', '8260399dcf4ee9ac01e06a18e358b8430c76bd332d6389efd80d830f1de1a6d1'),
    'history_source_before_contacts': ('history_source_before_contacts(uuid,text,jsonb,text)', '9feed614ef3ed133c61c5f6a65f322047858cb3d0f8b50889d6644ec1d676550'),
    'invoice_source_access': ('invoice_source_access(uuid,uuid,text)', '8f41d83fab26e4d8421ab2254bfcd1086c8e375ff0e6eda73e29b5b023d1498d'),
    'invoice_source_row_access': ('invoice_source_row_access(invoice_document,text)', 'd333f4066220f910bb0fc9aa92fb374fa0bed0e64af93bd7b166357c716d71bf'),
    'knowledge_query_actor_access': ('knowledge_query_actor_access(uuid,uuid,uuid)', '961ec53a4e862b894470d04556c6154cc56868c7d16f3fd6d30989504c5f449b'),
    'mandate_source_access': ('mandate_source_access(uuid,uuid,text)', 'fd73352d6fa186d8b19a11446bb6f4e17e2d80bc2b806c8aab53c2a3ef8a6e23'),
    'person_source_access': ('person_source_access(uuid,uuid,text)', '881ce6553db26640ced9ecd10b5b1683537bf28c60dafba5a517a53443bd761c'),
    'person_source_row_access': ('person_source_row_access(uuid,uuid,uuid,text)', '7d570edf65de1d02916616f053f08eff024e41d8e10ff04a140421f452e23ddf'),
    'profile_reference_access': ('profile_reference_access(uuid,text,uuid)', 'd4977c1e901d6950155fbd641f929815e997dc3f519b21160e579d539b5935c5'),
    'profile_source_access': ('profile_source_access(uuid,text,uuid,text)', '95d68a85ed0b0c9a18cb5005fb0c774e56e3ca4ed13a0c26a1f785e2888a1841'),
    'property_derived_row_access': ('property_derived_row_access(uuid,text,jsonb,text)', '0f014860310d87844180a8b566aa839bc49e7557a947421e6eec15edb2313d8e'),
    'property_financial_contact_access': ('property_financial_contact_access(uuid,uuid,text)', '887f431d3df94697e67f69a79b0bbe1c931403f25a17e91157f9a2ab9af128d4'),
    'property_financial_disclosure_access': ('property_financial_disclosure_access(uuid,uuid,text)', '248a58a3874ddd293811556d44f693d5d322e4f0112ed904a0e509efea37bbfc'),
    'property_financial_source_access': ('property_financial_source_access(uuid,uuid)', '503ee92de34009cd02e9247c1e3e198488eaa1d69f8c6f7ca9c44da576370221'),
    'property_history_row_access': ('property_history_row_access(uuid,text,jsonb,text)', '29773432ef8002e75c29421f99e4be90f8973ad85af3d07254900211e7d3a95a'),
    'property_import_runtime_access': ('property_import_runtime_access(uuid,uuid,text)', '74c182027a6bbc0a66a093dd79708f13310038d4a7f753c1ece3432667138332'),
    'property_master_access': ('property_master_access(uuid,uuid,text)', '583977aa238d8000d5704c75f414a1bd331b6c0003cdde0ecd63bff06aad601a'),
    'property_source_permitted': ('property_source_permitted(uuid,text,uuid,text)', '9137c5b69269f0706fba0389c88c059e6b9fb149cfd6afbaae6693f80fdc5182'),
    'report_scope_source_access': ('report_scope_source_access(uuid,uuid,text)', '42bb226f69225371f5977324b153a84634c35dc360d956d51c64cab99089b51a'),
    # 0085 EXPECTED (inlined to avoid importlib):
    'property_import_context_actor_access': ('property_import_context_actor_access(uuid,uuid,uuid,text,uuid,uuid,uuid,text)', 'fdec0bf21d3536d2f23fdfed7753316508c0eb5048119636431c57ea3f851fc6'),
    'property_source_import_actor_access': ('property_source_import_actor_access(uuid,uuid,uuid,text)', '1bb69487ce610530353a1b78d3ab4c54917b07e91a2d79cc0d5a36fc531b9b9a'),
    'property_source_document_actor_access': ('property_source_document_actor_access(uuid,uuid,uuid,text)', '92cc0a9abdd3fea7fd8027e59d9af2fc58f3ea0f87ce005829ef82b2e6823cb8'),
    'property_source_actor_access': ('property_source_actor_access(uuid,uuid,text,uuid,text)', '7ade366cc570c40c1eedb8b61fef9ae88aa6d7c06e1fc785a989de3876d5cbbf'),
    'property_source_actor_permitted': ('property_source_actor_permitted(uuid,uuid,text,uuid,text)', '361fe4fb4741e25beceb62530aac37f75c97c5ccc30bf7d678551be571ab5ac3'),
    'property_source_graph_before_history': ('property_source_graph_before_history(uuid,uuid,uuid,text,boolean)', '93514a0cc2a0667ab508d42b550c78cba9ed0a12a5e2d7b9e1eb85d2d098ad71'),
    'property_complete_graph_actor_access': ('property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean)', 'd2c6114fc987f1c03ec6961180bd9f9fea0d3c02a52999cf87c5160aa02bc7fe'),
}
EXPLICIT_ACTOR = {'knowledge_query_actor_access'}
ACTOR = "NULLIF(current_setting('app.current_user_id',true),'')::uuid"
MODE = 'public.property_source_security_mode(org)'
VALID = "org IS NOT NULL AND security_mode IN ('PREPARATION','ENFORCED','PAUSED') AND org IS NOT DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid"
GUARD = '\n      IF ('+VALID+') IS NOT TRUE THEN RETURN false; END IF; -- explicit-audience-boundary\n'
SQL_PREFIX = ' SELECT CASE WHEN '+VALID+' THEN ('
SQL_SUFFIX = ') ELSE false END '


def _parts(definition):
    header,body,end = definition.split('$function$')
    assert end.strip() == ''
    match = re.search(r'FUNCTION public\.([a-z_]+)\(([^\n]*)\)\n',header)
    assert match
    return header,body,match


def _calls(body,forward):
    names = set(EXPECTED) | set(PROPERTY)
    pattern = re.compile(r'public\.('+'|'.join(sorted(names))+')'+('' if forward else '_at_mode')+r'\(')
    for match in reversed(list(pattern.finditer(body))):
        pos = match.end(); depth = 1; quoted = False
        while depth:
            char = body[pos]
            if char == "'":
                if quoted and pos+1 < len(body) and body[pos+1] == "'":
                    pos += 2
                    continue
                quoted = not quoted
            if not quoted:
                if char == '(': depth += 1
                elif char == ')': depth -= 1
            pos += 1
        name = match.group(1)
        args = body[match.end():pos-1]
        extra = ',security_mode' if name in PROPERTY or name in EXPLICIT_ACTOR else ',evaluation_actor,security_mode'
        if forward:
            args += extra
        else:
            assert args.endswith(extra)
            args = args[:-len(extra)]
        body = body[:match.start()]+'public.'+name+('_at_mode' if forward else '')+'('+args+')'+body[pos:]
    return body


def _transform(definition,forward):
    header,body,match = _parts(definition)
    name = match.group(1) if forward else match.group(1).removesuffix('_at_mode')
    extra = ', security_mode text' if name in EXPLICIT_ACTOR else ', evaluation_actor uuid, security_mode text'
    if forward:
        assert hashlib.sha256(body.encode()).hexdigest() == EXPECTED[name][1]
        assert 'evaluation_actor' not in body and 'security_mode' not in body.replace(MODE,'')
        body = _calls(body,True).replace(MODE,'security_mode')
        body = body.replace('public.scope_actor_role(org)','public.property_source_actor_role(org,evaluation_actor)')
        body = body.replace(ACTOR,'evaluation_actor')
        if 'LANGUAGE plpgsql' in header:
            assert body.count('    BEGIN\n') == 1
            body = body.replace('    BEGIN\n','    BEGIN\n'+GUARD,1)
        else:
            assert 'LANGUAGE sql' in header
            body = SQL_PREFIX+body+SQL_SUFFIX
        args = match.group(2)+extra
    else:
        assert match.group(2).endswith(extra)
        args = match.group(2)[:-len(extra)]
        if 'LANGUAGE plpgsql' in header:
            assert body.count(GUARD) == 1
            body = body.replace(GUARD,'')
        else:
            assert body.startswith(SQL_PREFIX) and body.endswith(SQL_SUFFIX)
            body = body[len(SQL_PREFIX):-len(SQL_SUFFIX)]
        body = _calls(body,False).replace('security_mode',MODE)
        body = body.replace('public.property_source_actor_role(org,evaluation_actor)','public.scope_actor_role(org)')
        body = body.replace('evaluation_actor',ACTOR)
        assert hashlib.sha256(body.encode()).hexdigest() == EXPECTED[name][1]
    header = header[:match.start(1)]+name+('_at_mode' if forward else '')+'('+args+')\n'+header[match.end():]
    return header+'$function$'+body+'$function$\n'


def _internal(name,signature):
    return signature.replace(name+'(',name+'_at_mode(')[:-1]+(',text)' if name in EXPLICIT_ACTOR else ',uuid,text)')


def upgrade():
    db = op.get_bind()
    definitions = {name:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
        for name,(signature,_) in EXPECTED.items()}
    for name,definition in definitions.items():
        assert db.scalar(text('SELECT pg_get_userbyid(proowner)=current_user FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':EXPECTED[name][0]})
        evaluator = _transform(definition,True)
        assert _transform(evaluator,False) == definition
        op.execute(evaluator)
        op.execute(f'REVOKE ALL ON FUNCTION {_internal(name,EXPECTED[name][0])} FROM PUBLIC,app_rw,app_worker,app_review')
    for name,definition in definitions.items():
        header,_,match = _parts(definition)
        args = ','.join(arg.strip().split(' ')[0] for arg in match.group(2).split(','))
        extra = '' if name in EXPLICIT_ACTOR else ','+ACTOR
        mode = MODE.replace('(org)','((source).organization_id)') if name == 'invoice_source_row_access' else MODE
        header = header.replace('LANGUAGE plpgsql','LANGUAGE sql')
        op.execute(header+f'$function$ SELECT public.{name}_at_mode({args}{extra},{mode}) $function$')


def downgrade():
    db = op.get_bind()
    for name,(signature,_) in EXPECTED.items():
        definition = db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':_internal(name,signature)})
        op.execute(_transform(definition,False))
    for name,(signature,_) in reversed(list(EXPECTED.items())):
        op.execute(f'DROP FUNCTION {_internal(name,signature)}')
