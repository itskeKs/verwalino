"""Share property-source predicates between live enforcement and private projection.

No activation or caller-controlled runtime mode is introduced.
"""
import hashlib
import re
from alembic import op
from sqlalchemy import text

revision = '0085'
down_revision = '0084'
branch_labels = None
depends_on = None

# Guard the exact accepted predicate bodies before the mechanical refactor.
EXPECTED = {
    'property_import_context_actor_access': ('property_import_context_actor_access(uuid,uuid,uuid,text,uuid,uuid,uuid,text)', 'fdec0bf21d3536d2f23fdfed7753316508c0eb5048119636431c57ea3f851fc6'),
    'property_source_import_actor_access': ('property_source_import_actor_access(uuid,uuid,uuid,text)', '1bb69487ce610530353a1b78d3ab4c54917b07e91a2d79cc0d5a36fc531b9b9a'),
    'property_source_document_actor_access': ('property_source_document_actor_access(uuid,uuid,uuid,text)', '92cc0a9abdd3fea7fd8027e59d9af2fc58f3ea0f87ce005829ef82b2e6823cb8'),
    'property_source_actor_access': ('property_source_actor_access(uuid,uuid,text,uuid,text)', '7ade366cc570c40c1eedb8b61fef9ae88aa6d7c06e1fc785a989de3876d5cbbf'),
    'property_source_actor_permitted': ('property_source_actor_permitted(uuid,uuid,text,uuid,text)', '361fe4fb4741e25beceb62530aac37f75c97c5ccc30bf7d678551be571ab5ac3'),
    'property_source_graph_before_history': ('property_source_graph_before_history(uuid,uuid,uuid,text,boolean)', '93514a0cc2a0667ab508d42b550c78cba9ed0a12a5e2d7b9e1eb85d2d098ad71'),
    'property_complete_graph_actor_access': ('property_complete_graph_actor_access(uuid,uuid,uuid,text,boolean)', 'd2c6114fc987f1c03ec6961180bd9f9fea0d3c02a52999cf87c5160aa02bc7fe'),
}
GUARD = "\n      IF security_mode IS NULL OR security_mode NOT IN ('PREPARATION','ENFORCED','PAUSED') THEN RETURN false; END IF; -- explicit-mode-boundary\n"
MODE_CALL = 'public.property_source_security_mode(org)'


def _calls(source, *, forward):
    """Rewrite only balanced, qualified calls in these checked predicate bodies."""
    suffix = '' if forward else '_at_mode'
    pattern = re.compile(r'public\.('+'|'.join(EXPECTED)+')'+suffix+r'\(')
    matches = list(pattern.finditer(source))
    # Process rightmost calls first so recursive/nested arguments stay balanced.
    for match in reversed(matches):
        start=match.end(); pos=start; depth=1; quoted=False
        while depth:
            char=source[pos]
            if char=="'":
                if quoted and pos+1<len(source) and source[pos+1]=="'": pos+=2; continue
                quoted=not quoted
            if not quoted:
                if char=='(': depth+=1
                elif char==')': depth-=1
            pos+=1
        arguments=source[start:pos-1]
        if forward: arguments+=',security_mode'
        else:
            assert arguments.endswith(',security_mode')
            arguments=arguments[:-len(',security_mode')]
        source=source[:match.start()]+'public.'+match.group(1)+('_at_mode' if forward else '')+'('+arguments+')'+source[pos:]
    return source


def _parts(definition):
    header,body,end=definition.split('$function$')
    assert end.strip()==''
    match=re.search(r'FUNCTION public\.([a-z_]+)\(([^\n]*)\)\n',header)
    assert match and 'LANGUAGE plpgsql' in header
    return header,body,match


def _evaluator(definition):
    header,body,match=_parts(definition)
    name=match.group(1)
    assert hashlib.sha256(body.encode()).hexdigest()==EXPECTED[name][1]
    assert 'security_mode' not in body.replace(MODE_CALL,'')
    body=_calls(body,forward=True).replace(MODE_CALL,'security_mode')
    assert body.count('    BEGIN\n')==1
    body=body.replace('    BEGIN\n','    BEGIN\n'+GUARD,1)
    assert MODE_CALL not in body
    assert not re.search(r'public\.('+'|'.join(EXPECTED)+r')\(',body)
    header=header[:match.start(1)]+name+'_at_mode('+match.group(2)+', security_mode text)\n'+header[match.end():]
    return header+'$function$'+body+'$function$\n'


def _restore(definition):
    header,body,match=_parts(definition)
    name=match.group(1).removesuffix('_at_mode')
    assert match.group(2).endswith(', security_mode text') and body.count(GUARD)==1
    args=match.group(2)[:-len(', security_mode text')]
    body=_calls(body.replace(GUARD,''),forward=False).replace('security_mode',MODE_CALL)
    assert hashlib.sha256(body.encode()).hexdigest()==EXPECTED[name][1]
    header=header[:match.start(1)]+name+'('+args+')\n'+header[match.end():]
    return header+'$function$'+body+'$function$\n'


def upgrade():
    connection=op.get_bind()
    definitions={name:connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
                 for name,(signature,_) in EXPECTED.items()}
    for name,definition in definitions.items():
        # All source predicates are owned by this migration role. Do not silently
        # replace another owner's authorization implementation.
        owner=connection.scalar(text('SELECT pg_get_userbyid(proowner)=current_user FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':EXPECTED[name][0]})
        assert owner
        evaluator=_evaluator(definition)
        assert _restore(evaluator)==definition
        op.execute(evaluator)
        signature=EXPECTED[name][0].replace(name+'(',name+'_at_mode(')[:-1]+',text)'
        op.execute(f'REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,app_rw,app_worker,app_review')
    for name,definition in definitions.items():
        header,_,match=_parts(definition)
        args=','.join(arg.strip().split(' ')[0] for arg in match.group(2).split(','))
        # CREATE OR REPLACE retains OID, owner and ACL; header retains volatility,
        # definer/search-path/row-security attributes from the checked definition.
        header=header.replace('LANGUAGE plpgsql','LANGUAGE sql')
        op.execute(header+f'$function$ SELECT public.{name}_at_mode({args},{MODE_CALL}) $function$')


def downgrade():
    connection=op.get_bind()
    for name,(signature,_) in EXPECTED.items():
        internal=signature.replace(name+'(',name+'_at_mode(')[:-1]+',text)'
        definition=connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':internal})
        op.execute(_restore(definition))
    for name,(signature,_) in reversed(list(EXPECTED.items())):
        internal=signature.replace(name+'(',name+'_at_mode(')[:-1]+',text)'
        op.execute(f'DROP FUNCTION {internal}')
