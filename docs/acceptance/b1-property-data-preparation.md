# B1 shared property-data preparation

Base: `95bf32f`. Migration 0045, explicit setup API and administrative UI. This
increment prepares one shared-data domain per property and explicit team grants;
it does not activate source/master-data restrictions or complete B1.

The initial domain-creation test failed because the new route was absent. Backend
checks use disposable PostgreSQL 16 and Python 3.13. They cover no implicit admin
grant, no cross-inheritance with financial grants, same-key replay/concurrency,
revocation without resurrection, replacement, correct domain/grant pairing,
read-only/deactivated/expired roles, invalid capabilities, tenant fences,
immutable database evidence, canonical audit actor, populated downgrade refusal
and empty downgrade/upgrade with rollback restoration. Read-snapshot regressions
cover demotion/deactivation on both detail and paged metadata.

The RLS inventory was expanded for the two new tenant tables. Existing accounting
scope API cases passed and remain valid after changes restricted to the new read
routes/tests. UI checks cover lost create/grant replies and durable revocation.
Frontend lint and production build passed; the existing bundle-size warning now
reports approximately 603 KB. Final **17 domain tests**, **7 RLS inventory tests**,
and **3 UI tests** passed. Two test assumptions were repaired: host/database clock
skew required waiting against the stored database expiry, and a demoted admin
retained a legitimate explicit grant from another case. The tested inaccessible
domain remains absent; authorized domains must not disappear.

Independent review accepted the increment after page/detail routes adopted the
live-member transaction guard. Local Chromium acceptance passed Users → domain
setup → committed response lost → identical retry → explicit grant → durable
revocation. The page states preparation and separate financial rights, has no page
errors/framework overlay, and fits a 390px viewport (visible/content width 390px).
The mobile screenshot was visually inspected. Browser plugin was unavailable;
regular Playwright was used. Reproducible script:
`scripts/acceptance/b1_property_data_domains_browser.cjs`; sanitized result:
`docs/acceptance/b1-property-data-browser.json`. API/Vite processes were stopped
after verification. No live providers or external systems are used.

## Worker scheduling prerequisite (0046)

On base `b869a2e`, two red cases added a temporary test-only source RLS policy.
They reproduced an actorless claim silently missing eligible work and a hidden
revoked lease returning no result instead of committing cancellation. The worker
now obtains only a single tenant-local queue candidate's four fence fields from a
narrow definer helper, resolves the immutable queue actor, then reads the import
through ordinary RLS. Lease checks likewise verify metadata before actor/source
access, including a database-clock check after row-lock waits. No original bytes,
questions, candidates, approval results or error messages cross the metadata path.

**37 principal/worker cases passed** after implementation. **79 metadata/pipeline
cases passed** after adding bounded/foreign-tenant metadata, observed row-lock
expiry, migration round-trip and readiness coverage. Existing due-time selection,
concurrent claims, lease recovery, stale fences, conservative budgets and provider
publication checks remain covered. Independent read-only review accepted the
increment. There are no frontend changes or browser reruns for this prerequisite.

This does not enable source RLS. Future domain policy activation must add the exact
property-data permission to `declaration_queue_actor` and cancellation semantics;
an otherwise live writer must not bypass a source policy. The metadata helper
retains the existing trusted worker-clock due-selection input; durable effects
remain fenced using the database clock. Startup now requires both metadata helper
functions as well as lease/principal columns.
