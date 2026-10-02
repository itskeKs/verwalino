# Reviewed runtime policy at strict Q&A admission

Migration 0089 requires an enabled, transaction-bound runtime policy matching the
latest explicit administrator review before `reserve_knowledge_query` admits any
strict Q&A work. A raw runtime command cannot reuse an earlier transaction's proof.
The application attests its current policy on the actual reservation connection;
missing registrar or mismatched review returns service-unavailable before the
provider's paid answer call.

Source authorization acquires the shared ACL lock before admission. Administrator
review and pause require the exclusive lock, so their changes cannot race between
policy comparison and reservation. The existing database policy still owns cap,
window, reservation expiration, hidden-property budget accounting and concurrent
admission. PREPARATION compatibility remains unchanged.

Already admitted work keeps its reservation and cost accounting if runtime policy
changes while an answer is in flight. Source revocation or pause still suppresses
the answer and durably accounts cancellation through the existing path. No new
proof is required merely to finalize admitted cost.

The migration checks the exact predecessor function body and preserves its OID,
owner, ACL and configuration. Empty rollback restores the exact original body;
rollback refuses ENFORCED or PAUSED organizations. Source activation readiness
remains literal false. Retained-disposition acceptance, bootstrap and audited
activation/pause/resume remain outstanding, as do B2–G.

## Verification

The three initial tests failed against migration 0088 and passed with 0089. The
expanded run passed 43 checks, including actual/expired transaction proof, cap,
window and expiration drift, disabled policy, unavailable registrar, in-flight
policy change, reservation concurrency, migration identity and strict rollback.
Two older cancellation assertions predated migration 0065's audit source RLS.
They now check that the runtime cannot read the inaccessible run's event and that
one durable audit event remains visible to the independent migrator inspection.
The compatibility run passed 53 checks and exposed an older migration test that
committed historical 0044 functions into the current test schema. Its roundtrip is
now savepoint-contained, exercises the legacy expiry as `app_rw`, and verifies exact
current function restoration. The ordered follow-up passed all five repaired
migration/cancellation cases. The fake-Docker deployment probe passed. No frontend
behavior changed; the preceding 0088 frontend/browser evidence remains applicable.

Independent static review found no production defect. It requested an explicit
legacy expiry result assertion so a hidden row cannot masquerade as a NULL actor;
the strengthened `UPDATE RETURNING` check passed separately. Logs:
`/tmp/hv-budget-reservation-{red,first,expanded,compat,repaired,legacy,deploy}.log`.
