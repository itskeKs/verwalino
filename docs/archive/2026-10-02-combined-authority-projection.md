# Combined authority projection

Migration 0086 extends the private property evaluator from 0085 to the contact,
financial, invoice, mandate, report, history and derived-row predicates needed by
B1 readiness. This is a shared authorization foundation, not activation readiness.
The activation function remains false; no API or UI can choose a runtime mode.

## Contract

Twenty-five accepted predicates retain their public signatures, owners, OIDs,
ACLs, volatility and security configuration. Their wrappers pass the actual actor
and actual property-source mode to private implementations. An exact SHA-256
check of every accepted body guards the mechanical transformation. Downgrade
restores each original body and checks its digest before removing private helpers.

Internal dependency calls carry the same target actor and supplied mode. Calls
into the seven 0085 graph predicates use their existing explicit-actor helpers.
The private implementations reject invalid modes and foreign tenant context. They
cannot be executed by PUBLIC, app_rw, app_worker or app_review. They neither set
session identity nor modify source security state.

Projection retains the selected financial scope and the live financial effect
control. Supplying ENFORCED for property-source evaluation does not resume paused
financial operations. Current membership, active-user state, grants, expiry,
revocation, complete source graph, exact recipient contacts, source classification
and invoice uploader/lineage checks remain the live predicates' responsibility.
Missing grants cannot be supplied collectively by different actors.

A report-source predicate is only its source gate. A future readiness command
must also compose financial scope, supported adapter, resource-specific lifecycle,
recipient and processing constraints. It must evaluate the complete expected
inventory, account for retained quarantines, and verify trusted deployment/budget
policy. Predicate parity alone does not prove an organization ready to activate.

## Validation

The initial four projection checks failed because the new functions did not yet
exist, then passed after implementation. The first expanded backend run passed
102 checks. Three comparison cases correctly encountered private helper ACLs;
the test now compares those helpers using the migration role with a fixed target
identity, while public predicates continue to run as app_rw. Two historical
migration probes needed to remove 0086 inside their rolled-back test savepoint
before testing the older function bodies. Existing runtime permissions are intact.

The focused follow-up covers all 25 predicates across PREPARATION, ENFORCED and
PAUSED; actor separation; contact revocation; invalid/foreign context; selected
scope; independent financial pause; invoice custody; function ACLs; dependency
closure; and exact migration restoration. The follow-up passed 51 checks, including
financial source/disclosure and mandate integration. The fake-Docker deployment
probe passed against the updated migration head.

Independent static review found one private-only NULL-tenant corner case. The
guard now explicitly requires a non-NULL organization, with absent-tenant tests
for both SQL and PL/pgSQL helpers. An added canonical-source fixture checks real
imports through the outer derived-row predicate for admitted and quarantined
originals. All 13 final projection tests passed after this delta, including
all 25 predicates across three modes and exact rollback. The disposable database
was restored from the earlier local prototype before applying the final guard;
no deployed migration or production database was modified.
