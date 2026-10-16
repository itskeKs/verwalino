# Shared property authority evaluation

Baseline: 78f472a. Migration 0085 is a private evaluation foundation; acceptance
is accepted after focused checks and independent review. Source activation remains disabled.
This increment does not add a public projection API or change the current mode.

The seven existing explicit-actor property-source predicates now share private
implementations with an explicit security-mode argument. Existing function OIDs,
ownership, execution grants, volatility, definer context, search path and row-security
settings remain intact. The original functions forward the actual organization
mode. Private evaluation can supply ENFORCED without changing security state or
organization/user session settings. The import's NEW_PROPERTY/EXISTING_PROPERTY
mode remains a separate argument with its original meaning.

The shared call graph covers source import context, imports, originals, recursive
property-derived subjects, the permission entry point and the complete property
source/history graph. Every internal edge carries the same security mode and actor.
Invalid or absent modes fail closed. Runtime roles cannot call the explicit-mode
functions, including through inherited app_rw permissions. Live membership, domain
grants, expiry, immutable provenance and topology predicates remain shared leaves.
Low-level resolvers retain their original semantics; PAUSED effect denial still
belongs to the permission and complete-operation entry points.

The migration checks exact accepted predicate-body fingerprints, uses qualified
balanced-call transformations, rejects leftover calls through live wrappers, and
verifies its inverse restores the original definitions exactly. All definitions
are obtained from the installed migration chain; there is no temporary-file
runtime dependency. Installation is transactional. A changed upstream predicate
must be consciously reviewed instead of silently entering the new evaluator.

This is not complete readiness. Contact and financial authority, invoice/mandate/
dunning sources, combined recipients, processing principals and intended workflows
still need composition into the versioned checker. Budget/configuration correspondence,
bootstrap, atomic activation and pause/resume are separate remaining boundaries.

## Verification

Two initial tests failed because the explicit-mode functions did not exist, then
passed after implementation. The broader source/import/bound-proof/derived/history
run passed 96 cases and exposed two outdated test assumptions: the worker claim
fixture still used app_rw after worker-role isolation, and a historical downgrade
attempt omitted later publication-context policies. The former now uses app_worker;
the latter proves out-of-order downgrade is refused and original grants remain
intact. It no longer claims an obsolete historical roundtrip beneath active later
policies. The new migration has its own exact-body/OID/ACL roundtrip check.

The repaired targeted run passed 11 cases, including the two corrected historical
fixtures. The final 13 projection cases passed. They cover strict projection during actual
PREPARATION, preserved actor/mode settings, proof propagation through import children,
quarantine, expiry, changed roles, live ENFORCED/PAUSED parity, runtime execution
denial, complete graph access and roundtrip identity. An early run of the added
import fixtures needed the existing declaration test feature/store fixtures;
that setup failure is not a product regression or passing coverage.

Independent review found no implementation defect and requested a direct recursive
branch fixture. The final run includes a rule source and an approved fact with RULE
subject, separately admitted or quarantined originals, authorized clerk and denied
administrator targets, and runtime ENFORCED parity. Classifications stay immutable;
separate fixtures avoid rewriting an already-admitted original. Mixed creator
custody and malformed cross-tenant history are also rejected in projection.

Fake-Docker deployment checks passed with expected migration head 0085. No deployment
occurred. This increment changes no frontend code or public API, so the accepted
0084 browser/UI evidence is reused for the unchanged interface. Complete B1 readiness
and the full M-A–M-G plan remain outstanding.
