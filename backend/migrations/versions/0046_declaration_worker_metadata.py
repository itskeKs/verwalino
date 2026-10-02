"""Resolve worker principals from bounded scheduling metadata before source reads."""
from alembic import op

revision = '0046'
down_revision = '0045'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE FUNCTION next_declaration_queue_metadata(org uuid, due_at timestamptz)
      RETURNS TABLE(import_id uuid,queue_request_id uuid,lease_generation bigint,lease_token uuid)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid THEN RETURN; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      RETURN QUERY SELECT i.id,i.queue_request_id,i.lease_generation,i.lease_token
        FROM public.declaration_import i WHERE i.organization_id=org AND (
          (i.status='QUEUED' AND (i.lease_expires_at IS NULL OR i.lease_expires_at<=due_at))
          OR (i.status='RETRY_WAIT' AND (i.lease_expires_at IS NULL OR i.lease_expires_at<=due_at)
            AND (i.next_attempt_at IS NULL OR i.next_attempt_at<=due_at))
          OR (i.status IN ('PREFLIGHT','EXTRACTING','RECONCILING') AND i.lease_expires_at<=due_at))
        ORDER BY i.created_at,i.id LIMIT 1 FOR UPDATE SKIP LOCKED;
    END $$;

    CREATE FUNCTION lock_declaration_lease_metadata(org uuid, job uuid, request uuid, generation bigint, token uuid, statuses text[])
      RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE expiry timestamptz;
    BEGIN
      IF org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id',true),'')::uuid
        OR token IS NULL OR cardinality(statuses)=0 THEN RETURN false; END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||org::text,0));
      SELECT i.lease_expires_at INTO expiry FROM public.declaration_import i
        WHERE i.organization_id=org AND i.id=job AND i.queue_request_id IS NOT DISTINCT FROM request
          AND i.lease_generation=generation AND i.lease_token=token AND i.status=ANY(statuses)
          AND i.lease_expires_at>clock_timestamp() FOR UPDATE;
      -- The lock may have waited past the deadline. Check again after acquiring it.
      RETURN FOUND AND expiry>clock_timestamp();
    END $$;
    REVOKE ALL ON FUNCTION next_declaration_queue_metadata(uuid,timestamptz),
      lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[]) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION next_declaration_queue_metadata(uuid,timestamptz),
      lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[]) TO app_rw;
    ''')


def downgrade():
    op.execute('''DROP FUNCTION next_declaration_queue_metadata(uuid,timestamptz),
      lock_declaration_lease_metadata(uuid,uuid,uuid,bigint,uuid,text[])''')
