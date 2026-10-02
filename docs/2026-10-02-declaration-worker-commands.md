# Declaration worker lifecycle commands

Status: accepted worker-lifecycle increment; source activation remains disabled.
Baseline fc47be7; migration 0070.

## Boundary

Strict source RLS grants permission to prepare an import, but does not prove a
current worker lease or a valid lifecycle transition. Raw row updates must not
invent leases, renew expired ownership, skip processing stages, change scheduling,
or rewrite protected budget/usage state. A caller-provided GUC is not command authority.

Database commands claim due work using database time, renew a matching live
lease, apply an allowed transition with a closed patch vocabulary, and cancel an
import under the current user. They acquire shared ACL before the import row,
resolve the immutable queue actor and current source rights, and recheck expiry
after waiting. Reclaim closes abandoned running attempts and increments the fence.
A trigger rejects direct strict worker-state changes from app_rw; privileged
commands carry authority through their database execution role. Creation starts
at DRAFT with empty lease/usage state. Existing audited publication remains a
separate approval operation. Ordinary review edits keep their existing boundary.

PREPARATION keeps existing compatibility while commands are integrated. Migration
uses NOWAIT dependency locks and retains strict protection during rollback. Tests
must exercise real strict mode, forged raw updates, invalid commands, stale and
expired tokens, revocation, concurrent claim/renew/cancel and normal pipeline flow.

This increment covers the import lifecycle row. Extraction-run/pass/candidate
writes must separately prove a live lease where they represent worker effects;
their source RLS alone is not sufficient. That closure and remaining indirect
consumers, remediation and activation evidence still block B1.

## Runtime authority

Independent review identified that typed commands alone do not distinguish a web
caller from a trusted worker. `app_worker` is now a separate login inheriting the
ordinary RLS-constrained runtime data privileges; the inheritance never runs in
reverse. Claim/renew/transition EXECUTE is granted only to app_worker. The web role
cannot impersonate that role. The worker's separate URL has no app_rw fallback and
its session factory verifies the actual database role and privilege flags. The
web container receives no worker credential. Development/test role bootstrap and
production provisioning instructions accompany this change; no deployment is run.

Strict raw updates to lifecycle, max budget and active processing revision are rejected
for both runtime roles. Existing NEEDS_REVIEW edits may increment draft_revision
by exactly one, preserving the existing review service contract. Contact privacy
triggers also recognize the new runtime role so role separation creates no new
anonymized-contact write exception.

The trusted extraction worker owns measured/reserved usage. Counters must be finite
and nonnegative, but are not monotonic: the pipeline persists an upper reservation
before a provider call and reconciles it downward afterwards. Actual over-budget
provider usage must remain recorded with the failure. Existing pipeline budget
checks enforce prospective spend; the DB must not discard the observed overrun.

## Explicit review residual

The NEEDS_REVIEW review service still writes candidates, revision and audit through
ordinary prepared transactions. Its +1 revision exception is compatibility, not
trusted proof that a review action occurred: a raw preparer can increment a review
revision without changing a candidate. This preexisting gap must be closed by an
atomic review command together with candidate/audit effects in the next increment.
0070 protects processing revisions and worker lifecycle, not complete review-effect
integrity. Activation and B1 remain blocked.

## Acceptance evidence

Six intended red cases reproduced raw worker-state forgery. Initial worker/principal
integration passed 50 cases. Expanded pipeline/source/API integration passed 161
cases; two fixture failures were repaired (set the actual caller for cancellation;
unwind the later contact-policy dependency before an older migration rehearsal),
and the isolated rerun passed both. Command/strict-pipeline/approval/review coverage
passed 120 cases. Final focused verification passed **35 cases**, including unsafe
membership/replication/NOINHERIT provisioning, runtime role drift and PostgreSQL 16
membership-edge INHERIT FALSE, strict end-to-end extraction, raw web attacks,
command graph/patch errors, six NOWAIT dependencies, safe round-trip, and strict
rollback refusal. Contact privacy guard inheritance is covered too.

Independent review accepted the bounded role/lifecycle boundary after membership
and role-attribute fixes, retaining the explicitly deferred review residual above.
The final role check also requires actual inherited privileges (`USAGE`), not just
membership existence. Rendered production Compose confirms credential separation.
Shell syntax checks and the fake-Docker deployment/restore regression passed;
the latter excludes inherited provider environment settings for deterministic tests.
The release gate matches 0070. No real deployment, provider call or external system
mutation was used. The disposable PostgreSQL role changes in negative tests are
reverted, and activation remains false.
