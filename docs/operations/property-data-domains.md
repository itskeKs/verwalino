# Prepared shared property-data audiences

Migration 0045 introduces explicit shared property-data domains and per-user grants.
This is configuration preparation only. Existing source and master-data policies
remain in compatibility mode. Every domain API response declares
`enforcement_status: PREPARATION`, and the administration screen states that these
entries do not yet restrict existing access. Do not advertise a source-security
rollout from these tables or grants.

Administrators open `/gebaeudedaten-rechte` from Users. Create one immutable domain
per property with a label and reviewed reason. No creator/admin/financial-scope
grant is added automatically. Prepare each team member's capabilities separately:
read, prepare, approve and export. Read is mandatory; only active writers can
exercise prepare/approve, while an explicitly granted active read-only member may
read/export. Financial confirm/send permissions are not part of this domain.

Configuration mutations revalidate the active administrator and hold the exclusive
organization ACL lock. Domain identity and original grant metadata are immutable.
Revocation is one-way, records the actual actor and database time, and retains the
grant. An expired but unrevoked grant must be explicitly revoked before replacement.
Every mutation writes its audit evidence in the same transaction, including raw
application-role writes. Tenant-safe FKs prevent cross-organization references.

## API and lost responses

- `GET /api/property-data-domains/page` supports bounded signed cursor pages and an
  optional property filter. Administrators see configuration; other active members
  see only explicitly granted domains. Read routes recheck the live principal in
  their data transaction.
- `POST /api/property-data-domains` requires a UUID `Idempotency-Key`. Same actor,
  key and normalized body return the original domain; changed input conflicts.
  One property cannot acquire a second domain with a fresh key.
- `GET /api/property-data-domains/{id}` returns the permitted domain metadata.
- `GET /api/property-data-domains/{id}/grants/page` is administrative grant history.
- `POST /api/property-data-domains/{id}/grants` requires a UUID key, active member,
  reviewed reason, capabilities and optional future timezone-aware expiry.
- `POST /api/property-data-domains/{id}/grants/{grant_id}/revoke` is idempotent and
  requires the exact domain/grant pair.

The UI retains a command key after an interrupted response. Grant replay returns
its current revoked state and never restores access; a replacement needs a new
explicit command. Authorization is checked before replay. Creation and grant keys
are namespaced by operation and initiating administrator.

The capability helper intentionally does not read financial grants. Conversely,
financial checks never read property-data grants. Complete source/derivative,
worker, audit, privacy-export and indirect-view coverage is required before a
future activation operation can be offered; see the property-data-domain design.
Populated downgrade refuses to erase prepared evidence. Repair forward. No live
provider or production activation is included.

Migration 0047 makes property/source row identity, creator and parent context
immutable before policy activation. An import's mode and target cannot be edited;
create a separate explicitly reviewed import for a different destination. An
applied import retains its original destination and approval receipt. Historical
null uploaders are not inferred from queue actors or approvers. This prerequisite
does not enable source permissions and does not certify cross-link consistency.
A populated downgrade refuses to remove these guards. Pause the relevant writers
and forward-repair through a reviewed migration; do not drop triggers to perform
an operational reparenting. Original bytes and source evidence remain preserved.
