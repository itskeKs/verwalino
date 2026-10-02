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
