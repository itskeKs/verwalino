# Strict financial scope closure

Status: verified local implementation at baseline ca67d5d; migration 0081.

The existing missing-binding branch still permitted ordinary financial reads and
first unclassified creations in an otherwise strict organization. ENFORCED and
PAUSED now deny unbound bank/payment/cost/run/plan and linked invoice access. The
same capability helper retains its OID and existing policy references. Unknown
nonfinancial audit kinds continue through their separate typed source boundary.
PREPARATION behavior remains available for reviewed legacy migration.

New strict money rows need an exact authorized scope. Existing creation triggers
establish the binding atomically before a final row guard checks it. Payments
inherit their reviewed bank scope. Every statement takes the shared ACL lock before
row triggers or financial-input locks. Source PAUSED permits granted READ/EXPORT
and denies new or changed financial effects; the HTTP creation adapter reports the
pause before writing. Neither original financial rows nor historical bindings change.

Linked invoice writes also require the current parent scope. Invoice processing
attempt RLS follows the parent document; ordinary callers cannot read or alter
hidden processing records. A narrow same-tenant, actual-app_worker scheduling helper
returns one attempt's four UUID fields (attempt, document, immutable actor, fence)
for the next due job or an exact fenced completion. No status, error, extraction,
filename or byte reference crosses this path. The worker sets the recorded actor,
checks current authority, then uses ordinary RLS; existing narrowly authorized
cancellation remains able to record denied jobs. Queue admission counts all pending
jobs through a separate aggregate helper, under ACL then queue locks, so hidden
jobs cannot free capacity. Invoice workers use the existing isolated app_worker connection; HTTP app_rw cannot
execute the metadata helper or assume that role. No new credentials are provisioned.

Standalone unattached invoice intake still has its prior boundary and lacks an
immutable uploader field. That remaining custody/strict intake issue is explicit
in the B1 closure assessment. This increment does not enable activation or complete
B1. Strict downgrade is refused; a preparation-mode roundtrip retains all money
and function identity. Production recovery is pause and repair forward.


## Verification

Two initial RED cases reproduced unbound bank disclosure and first unscoped raw
insertion. The first 33 checks passed after implementation. Expanded source tests
needed explicit synthetic finance and contact grants to keep testing source
revocation rather than the now-denied unclassified compatibility path. One new
payment fixture used an invalid enum and was corrected. The final source-focused
run passed 22 cases; financial/source/report/RBAC/tenant/RLS integration passed 108.

Independent review found and verified repairs for ACL/financial lock inversion,
linked invoice insertion, attempt metadata/effect visibility and worker-only
metadata access. The final 53-case strict finance/boundary/invoice-worker/pause run
passes through the isolated app_worker pool. It covers all five strict creation
paths, atomic bindings, ordinary child/audit/download hiding, raw attempt denial,
whole-tenant queue counts, lost authority before claim/finish, cancellation, bounded
OCR/timeouts/restarts, private helper role/tenant denial, two-connection payment lock
ordering, and migration preservation/strict rollback refusal. The full 52-case PDF suite also passes after both real upload/OCR journeys were
moved to the isolated worker pool. Independent final review accepted the repairs.
Earlier unchanged integration evidence is reused. No frontend changed or browser rerun is claimed.

Shell syntax and the fake-Docker deployment regression pass. Its first attempt
inherited a configured provider key and invalidated its intentional missing-key
scenario; rerunning with that variable unset restored the isolated fixture. The
uncommitted migration prototype was reset only on guarded disposable PostgreSQL
55432 before verifying the revised migration. No deployment or external changes.

Operational prerequisite: invoke `python -m app.workers.invoice_extraction` with
`HV_DATABASE_URL_WORKER` configured to the existing isolated worker login. There
is no fallback to the web connection. Reuse the existing worker secret delivery;
never expose this credential to the web service.
