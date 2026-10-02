# Shared property-data authorization boundary

Status: architecture accepted after independent read-only consultation on `95bf32f`;
the preparation unit is implemented and independently accepted. Enforcement
remains staged and does not authorize team rollout.

## Decision

Create a distinct immutable `property_data_domain` for a physical property and
explicit per-user grants. It is neither an accounting sphere nor a presentation
filter. Financial scope grants never confer property-data capabilities, and
property-data grants never confer financial capabilities. An administrator can
configure grants without receiving an implicit data entitlement. The capabilities
are read, prepare, approve and export; a live read-only membership cannot exercise
prepare/approve even if an old grant contains those capabilities.

A domain includes shared property/unit facts and property-wide legal sources.
It is not a way to publish a private financial original across WEG, rental and SEV
spheres. Such originals retain their exact financial source authorization; sharing
reviewed extracts is a separate disclosure operation. No automatic scope, role,
mandate or property-name matching creates a data grant.

## Required closure before enforcement

| Group | Paths that must change together |
| --- | --- |
| Canonical facts | Property, Unit, legal references, physical assignments, document relations, direct APIs and selectors |
| Derived knowledge | Approved fact sources, rules, rule relations/sources, search projections, overview/search/Q&A and query-run accounting |
| Original source | Property-document metadata, list/detail/export, integrity checks and storage access |
| Draft/worker | Imports, document links, candidates/evidence/issues, extraction runs/passes, immutable queue commands, review/approval and every worker source/effect phase |
| Indirect disclosure | Audit entities/payloads, privacy export/retention, existing snapshots and linked views containing copied values |

Review financial computations and party/history joins as dependencies: RLS filtering
must never silently turn inaccessible units or source inputs into zero or partial
calculations. Existing exact financial grants remain necessary; neither domain
access nor configuration authority can substitute for them.

Use one database capability helper with the shared organization ACL lock and
current membership/grants. A property-bound row resolves exactly one domain via its
tenant-safe property relationship. Storage reads require an imperative export
check. Approval locks the destination domain and publishes source, canonical facts,
provenance and rules in one transaction. Revocation is rechecked in each bounded
worker/provider phase; no transaction spans external I/O.

Unassigned new imports and originals need immutable creator-only ownership. An
existing-property draft requires destination prepare; a new-property approval
atomically creates/assigns its reviewed destination and explicit grants. Do not
infer a historical draft owner from a worker or approver. The existing creator and
queue initiator retain separate meanings.

## Delivery and activation

1. Add immutable domain identity, explicit grant/revocation records and idempotent
   administrative setup with bounded selectors. No legacy role backfill or policy
   activation is included in this preparation unit.
2. Implement and verify the complete read/effect graph above, creator-only intake,
   destination approval, worker and Q&A cancellation, and audit/export closure.
   Do not enable a source-only restriction before this graph is closed.
3. Add a reviewed coverage report and explicit organization activation. Activation
   requires complete domain/reference classification and live reviewed grants.
   Unknown enforced rows fail closed; narrowly scoped, audited remediation remains
   possible without granting broad source access. Compatibility history remains
   explicit until reviewed migration. New enforced organizations have an atomic
   administrator bootstrap and creator-only intake path.
4. Rollback pauses effects while retaining restricted reads, original bytes and
   grant evidence. Never revert an enforced organization to broad legacy access.

V05/V06 remain release gates. No source or property-data enforcement claim follows
from the presence of preparation tables or configuration APIs.
