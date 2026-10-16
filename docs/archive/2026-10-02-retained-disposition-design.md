# Snapshot-bound retained disposition

Status: locally verified 0090 increment following accepted 0089 (`536ceb6`).
Source activation remains disabled; lifecycle integration is still outstanding.

## Decision

Administrative acceptance may retain blocked legacy resources, but cannot turn a
coverage warning into permission. Bind the decision to a current checker-v3
snapshot, its existing review, trusted current budget evidence, a deterministic
assessment digest, explicit team/retention acknowledgments and a reason. Recompute
under exclusive ACL then financial-input locking. Store immutable evidence and
return original receipts on replay without changing source data or permissions.

Do not infer inaccessibility from an empty combined audience. The 0087 preview
models complete report/operation authority and can be stronger than ordinary row
access: a financial reader may still read a bank account while missing property or
contact rights makes the combined audience empty. Independent review identified
this counterexample before implementation.

Initial retention eligibility must use family-specific conditions enforced by the
actual strict runtime access boundary:

- Reviewed quarantine for property originals, mandate documents and dunning notices,
  with the current exact classification/disposition proof. Existing restricted
  administrative quarantine inspection remains available where already supported.
- Financial roots with no binding, only where strict row and effect gates actually
  reject the missing scope. Verify each eligible family through runtime sessions.
- Historical standalone invoices with unknown custody, only when the actual strict
  original-access gate denies ordinary reads/exports/effects.

Other finding-bearing resources remain blocked, including readable records with
incomplete combined authority. Unknown kinds/codes, budget findings and unsupported
SEV operations cannot be waived. Active and inactive member assignments require an
explicit team review; acceptance itself creates no grants. Existing ready entries
need no additional source disposition.

Global active-work checks must include declaration states and live leases,
extraction runs, invoice documents/attempts and RESERVED knowledge queries. An
active operation cannot be hidden by a filtered source list or waived as retained.
Current assessment and its exact retained identities must be reviewable through
bounded pages. The UI must distinguish retained restrictions from resolved findings
and avoid implying activation. Later activation must independently recheck the same
conditions and current receipt under its own atomic authorization boundary.

## Verification contract

Prove the bank-row counterexample remains blocked by assessment. Pair each eligible
family with actual strict read/export/effect denial and a non-eligible readable
case. Cover wrong tenant/current role, stale snapshot or budget policy, unresolved
active work, response loss, changed-key payload, concurrent acceptance, atomic audit
failure, private table ACLs and downgrade retention. Activation remains literal
false throughout this increment.

## Acceptance evidence

The implementation stores private immutable acceptance and audit evidence without
changing grants, resources or security mode. Only narrow recognized denial cases
are eligible. The paginated UI displays exact identities, requires both explicit
acknowledgments and a reason, preserves the original request after response loss,
and separates historical receipts from current eligibility.

Backend verification: 48 tests passed across disposition, coverage, projected
coverage and grant proposals; five additional active-work/current-evidence tests
passed. These cover the readable-bank counterexample, strict unbound-bank and all
three quarantine-family denials, immutable/replay-safe acceptance, concurrent keys,
atomic audit rollback, private ACLs, bounded paging, unknown/mixed/SEV rejection,
and downgrade refusal/empty round trip. Live declaration leases, extraction runs,
invoice attempts and reserved Q&A work each block acceptance. Existing 0081/0082
strict finance and invoice boundary evidence remains applicable.

Frontend verification: 19 component tests, lint and production build passed. The
existing large-bundle warning remains. The fake-Docker deployment contract passed
at head 0090. Local browser verification passed for explicit acknowledgments,
response-loss replay, retained reason, reloading, 390px layout without horizontal
overflow, and absence of an activation button or JavaScript errors. Desktop and
mobile screenshots were inspected. Independent read-only backend/UI review found
no remaining production defect after the empty-audience counterexample was fixed.

Evidence logs: `/tmp/hv-access-disposition-{expanded,work,ui,lint,build,deploy,browser}.log`.
B1 is incomplete; B2–G remain outstanding. No rollout or activation is authorized
by this acceptance.
