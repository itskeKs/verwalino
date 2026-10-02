"""Deny unknown mandate originals in strict source mode and pause their effects."""
from alembic import op

revision = '0077'
down_revision = '0076'
branch_labels = None
depends_on = None
TABLES = 'property_source_security,mandate_document,scope_grant,accounting_scope'

BASE = """
    CREATE OR REPLACE FUNCTION mandate_source_access(org uuid, sphere uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN false; END IF;
      IF capability NOT IN ('read','prepare','export') THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      IF sphere IS NULL THEN RETURN NULLIF(current_setting('app.current_scope_id',true),'') IS NULL; END IF;
      IF NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL
        AND sphere<>NULLIF(current_setting('app.current_scope_id',true),'')::uuid THEN RETURN false; END IF;
      RETURN public.has_scope_capability(org,sphere,'read') AND public.has_scope_capability(org,sphere,capability);
    END $$;
"""


def upgrade():
    op.execute('LOCK TABLE ' + TABLES + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    # Preserve the function OID referenced by RLS, original protection and audit.
    op.execute("""
    CREATE OR REPLACE FUNCTION mandate_source_access(org uuid, sphere uuid, capability text) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE mode text;
    BEGIN
      IF capability IS NULL OR capability NOT IN ('read','prepare','export') THEN RETURN false; END IF;
      mode:=public.property_source_security_mode(org);
      IF mode IS NULL THEN RETURN false; END IF;
      IF mode<>'PREPARATION' THEN
        IF sphere IS NULL OR public.scope_actor_role(org) IS NULL THEN RETURN false; END IF;
        IF mode='PAUSED' AND capability='prepare' THEN RETURN false; END IF;
      END IF;
      IF sphere IS NULL THEN RETURN NULLIF(current_setting('app.current_scope_id',true),'') IS NULL; END IF;
      IF NULLIF(current_setting('app.current_scope_id',true),'') IS NOT NULL
        AND sphere<>NULLIF(current_setting('app.current_scope_id',true),'')::uuid THEN RETURN false; END IF;
      RETURN public.has_scope_capability(org,sphere,'read') AND public.has_scope_capability(org,sphere,capability);
    END $$;
    """)


def downgrade():
    op.execute('LOCK TABLE ' + TABLES + ' IN ACCESS EXCLUSIVE MODE NOWAIT')
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM property_source_security WHERE mode IN ('ENFORCED','PAUSED')) THEN
      RAISE EXCEPTION 'Retain strict mandate source access; pause effects and repair forward';
    END IF; END $$""")
    op.execute(BASE)
