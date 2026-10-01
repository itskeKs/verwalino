# Invoice/cost linking rollout

Migration 0029 checks legacy links before adding tenant and uniqueness constraints.
Run these read-only queries with migration privileges on the intended rollout copy
and retain their output as the reconciliation report. Empty results mean there are
no ambiguous candidates. Any rows block rollout and need an evidenced correction;
do not guess by amount/vendor, reparent foreign rows or modify released originals.

```sql
SELECT d.id AS document_id, d.organization_id AS document_org,
       c.id AS cost_id, c.organization_id AS cost_org
FROM invoice_document d JOIN cost_entry c ON c.id = d.cost_entry_id
WHERE d.organization_id <> c.organization_id;

SELECT organization_id, cost_entry_id, array_agg(id ORDER BY created_at) AS documents
FROM invoice_document
WHERE status = 'active' AND cost_entry_id IS NOT NULL
GROUP BY organization_id, cost_entry_id HAVING count(*) > 1;
```

A new cost from an invoice carries `source_document_id`. Creation, attachment,
audit and optional operation receipt share the request transaction. A repeated
`Idempotency-Key` is scoped to organization, actor and operation. A changed payload
returns 409. Replays check current authentication and source/result access. Source
uniqueness remains enforced without a key. An imported cost without an active
source cannot be confirmed. Manual drafts without a source remain supported.

Cost-first upload locks the destination and rejects a second primary source or a
confirmed cost. Replacement supersedes the old version before inserting the next
one in the same transaction. Released source bytes and links stay frozen. A
replacement can follow the original cost's explicit correction link only.

Rollback: stop application writers (or disable the create/upload routes at the
application boundary), preserve migration 0029, operation receipts, originals and
link reads. Do not downgrade a populated database or re-enable the old imported
cost writer. Alembic downgrade exists for disposable test teardown only. Forward
repair is required for production data. No production rollout has been performed.
