# Authenticated reference software review

Date: 2026-10-01. Repository baseline: `259ec0b`. Desktop browser, authenticated
trial account showing ten days remaining. This is a product-reference inspection,
not an implementation, security assessment, accounting certification or claim of
complete parity. It supplements the [design](2026-10-01-product-ux-technical-design.md)
and [code audit](2026-10-01-product-audit-and-parity-plan.md).

## Scope and evidence rules

The user authorized navigation and synthetic test records. All main navigation
areas were inspected. Representative creation journeys were exercised to a
verified saved result, a draft, a missing-data boundary or an explicit external
action boundary. Existing customer records were read only. No messages were sent,
no invitations activated, no contracts signed or purchased, no bank provider
connected, and no OCR consent or new integration permission accepted.

**Completed** means the saved result was visible in the UI. **Draft** means a
persisted incomplete record, without finalization. **Observed** means controls or
steps were inspected, without proving the downstream result. **Unverified** means
an attempted action did not yield sufficient evidence. UI claims remain claims;
they do not establish implementation, legal accuracy or provider behavior.

Sources below are the authenticated application
and its visible navigation. Routes identify entry points, not private record IDs.
Raw account details, customer addresses, internal IDs and screenshots are excluded
from the repository. Private screenshots of synthetic journeys remain in the
local task evidence directory. Public documentation in the design remains useful
for capabilities that this account or review could not exercise.

## Flow and feature matrix

| ID / entry | Observed journey and required inputs | Result and boundaries | Design mapping |
| --- | --- | --- | --- |
| RF01 Dashboard `/` | Owner/property filters; monthly expected/received rent and occupancy; workflow counts for open, overdue, unassigned and due today; cashflow graph with SVG/PNG/CSV options | Observed; graph export not downloaded | P2.3, P4.1; distinguish forecast, obligations and received money |
| RF02 Properties `/objects` | Choose whole building, individual units or house; name and structured address; overview and accounting tabs; ownership shares/contact, service contacts, units, rent, finance, documents and notes | Completed synthetic property. Default ownership uses existing account owner. Migration notice requests verification of owner structure | P1.2, A05; inherited facts need provenance and review |
| RF03 Unit in property | Explicit apartment/commercial/garage type; label, living/use/heated areas, floor and optional different address; create tenancy from unit | Completed 50 m² apartment. Missing type produces inline validation; listing action disabled before integration | P1.2, P5.1, P5.7 |
| RF04 Ownership/accounting | Owners/shares and contact; period default calendar year; bank account; area/unit keys; individual and combined key controls; category-key mappings | Observed individual-key form with name/hint/total. Combined-key behavior not exercised; real owner unchanged | P3.2–3.3; defaults do not replace effective dated ownership |
| RF05 Tenant/contact creation | Existing person selector or inline new contact; multiple tenants and sublease option; inherited unit address; contact does not require email or bank for this path | Completed synthetic contact without email/phone/bank, attached to lease | P1.2, P5.1; reuse person identity, optional data collected when needed |
| RF06 Lease setup/lifecycle `/tenancies` | Parties → contract type/dates → rent → move-in details. Open-ended/fixed/step/index types; first/last partial rent, due day, interval, preceding-month option, occupancy changes, uploads, deposit/opening-balance/SEPA options | Completed lease starting **01.10.2026**, base 600 + advance 100 = 700. October obligation automatically visible as expected, unpaid. Attempted earlier date via automation did not persist; actual saved date governs evidence | P5.1–5.2, A23–A24 |
| RF07 Lease financial defaults | Setup offers a prefilled “mark rents paid through” date; future dated rent-change row; move-out readings/handover and missing-interim-reading notice | Paid-through field cleared before save. No future rent change saved. No move-out completed. UI mentions degree-day allocation when readings absent; legal/calculation correctness untested | P2.1, P3.3, P5.1; payment assertions and allocation methods require explicit evidence |
| RF08 Rent finance `/book-keeping` | Rent/obligation and receipt tabs; owner, date, status, property and type filters; bulk paid/lost/unassign; test obligation appears in scope | Observed expected 700; no payment posted or manually marked paid | P2.1, A06–A07 |
| RF09 Receipt capture | Local/existing-document upload → income/expense, supplier, description/number/date, paid/cashflow/due date → property/category/VAT/amount/period and splits. Category supplies defaults; recoverability, labor and exclusion controls | Synthetic PDF uploaded and previewed; OCR opens Beta consent, cancelled. Manual 100 EUR water entry attempted; save returned to enabled state without closing or captured success. No receipt in later list/property finance: persistence **unverified**, not booked evidence | P0.1–0.2, P1.1, A01–A04 |
| RF10 Document center `/documents` | Inbox, property folders, Other; search/date; max 25 MB; custom folders; select all, move/delete; personal email intake explained in settings | Completed upload of synthetic PDF from property, then verified same file in that property's document center. Object uploader accepts multiple files; receipt uploader accepted one. No automatic classification or property inference demonstrated | P1.1, P5.3, A25; contextual filing is distinct from inferred routing |
| RF11 Bank workspace `/banking` | All, suggestions, unassigned, assigned, automatically assigned, ignored and dismissed states; account/date filters; bank/BIC search and manual option; history default 365 days, range 14–3650 | Completed a renamed manual bookkeeping account, no real financial account. Account menu: property assignment, import, auto-assignment toggle, rename and deletion controls. Live connection untouched | P2.1–2.2, A07–A08 |
| RF12 Bank file import | Manual account → import opens single-file chooser; uploaded one synthetic semicolon CSV with a 700 EUR rent receipt | No mapping, success/error or transaction visible; counters remained zero. Import and matching **unverified**. No conclusion about supported format or underlying failure | P2.1; preview, format diagnostics and import receipt required |
| RF13 Meter `/meters` | Scope, number, type and measure unit required; replacement/submeter options; supplier/contract/location/reading interval/note. Type defaults measure unit; electricity adds market-location field | Completed property-level cold-water meter; 100 m³ reading on 01.10.2026 with recorder/note, success notice and list value. Reading upload offers up to two photos, 25 MB. Replacement, second reading/consumption and tenant-change calculations untested | P3.1, A11 |
| RF14 Settlement start `/operating-costs-v2` | Property → year/custom period → included advance types/units → sender → unit check → stationery → costs → preview | Initial pre-fixture attempt produced generic unexpected error. Later synthetic property/year 2026 successfully opened; no proven cause for the earlier error. Saved named draft | P3.2–3.3, A06/A13 |
| RF15 Settlement completeness | Steps show complete/needs-attention. Sender uses owner and flags missing address, account holder and IBAN. Unit check separates vacancy Jan–Sep and tenancy Oct–Dec; warns advances must be booked | Observed 0 paid advances, despite contractual 100 monthly advance. Owner record not changed. Preview reachable with unresolved sender/unit warnings; finalization gate **not tested** | P3.2–3.3; distinguish contractual, paid, evidenced zero and unknown coverage |
| RF16 Settlement costs/preview | Fifteen default categories, cost/date/note, distribution key, unit selection, distribution detail, consumption and labor controls. Zero rows prompt keep/remove. Receipt date/itemization switches and PDF/DOCX/XLSX/ZIP options | Draft manually contains 100 EUR water, by area. Preview: tenant 25.21, vacancy 74.79, total 100; prior monthly advance 100 and proposed 8.33 with explicit adoption switch. Proposal not adopted; settlement not finalized; output downloads not verified | P3.3, P5.2, A13/A16/A24 |
| RF17 Workflow `/workflows` | Own/predefined templates → title/description/assignee/context → To-Dos/history/comments; archive and labels tabs. Templates span onboarding, reletting, rent/payment issues, damage/maintenance, handover and purchasing | Completed synthetic V-0002, task creation/save and mark-done. History records creation/update/task addition. Completing its sole task changed task to COMPLETED and parent to DONE. No comment sent; global case had no property assignment | P4.1, A18; template/task states and audit history |
| RF18 Contract center `/rental-contracts` | Residential/garage areas; template-based wizard; unit/contact prefill → rooms/tenant/landlord/contract/bank/other data → signatures/attachments → review | Saved synthetic residential draft through review. Trial permits drafts but completion/purchase requires paid plan. No sign/send/purchase. Download attempted, no artifact obtained | P5.4, A26 |
| RF19 Contract details | Missing room count validated (minimum 0.5); explicit apply-to-master vs contract-only choice; salutation required; 600+100 rent produces editable 1800 deposit; transfer/SEPA account details required; attachment limit 20 files/10 MB, optional housing confirmation | Draft-only room count set to 2; unit master unchanged. Draft starts 02.10.2026; actual lease remains 01.10.2026. Bank details left blank; final Continue disabled. Additional agreement marks synthetic/no legal effect | P1.2, P5.1/P5.4; scope-aware prefill and reviewable required fields |
| RF20 Messages `/messaging` | New conversation selects contact; subject/rich text, templates, attachment, email/portal channels. Announcements separate, portal audience and active-only filter | Empty test-contact conversation opened; no message sent. Recipient empty/disabled and send unavailable for contact without email. Delivery/read evidence untested | P4.4, A21 |
| RF21 Tenant portal from property | Tenant selection → shared service contacts → optional first announcement → invitation body → activate | No eligible tenants shown in test context; reason not established. Reached activation boundary using skips; no invite/grant/activation | P4.3, A20; recipient eligibility and audience preflight |
| RF22 Profile `/settings` | Contact/address/bank/tax profile, personal document and message intake addresses; language, password/2FA and account-deletion controls | Read only; intake addresses and real profile not copied into repo. Security behavior not exercised | P5.3/P5.6, A28/A31 |
| RF23 Application settings | Archive hiding, rent-balance breakdown, creditor/debtor booking vs pooled contact; subuser manual-paid permission; daily bank retrieval, disconnected-bank alerts, ignored-transaction anonymization; monthly report, BCC, OCR and message/workflow notifications | Read only. Anonymization UI mentions irreversible action after 90 days; policy/legal validity not tested. UI options do not prove background jobs or delivery | P2.1/P2.2/P4.1/P4.4/P5.6 |
| RF24 User management `/settings/subuser` | Invite email, then manage permissions according to empty-state explanation | No invited users; rights matrix not available without invitation. No access expanded | P5.6, A31; authenticated UI evidence does not establish isolation |
| RF25 Category settings `/settings/account-charts` | SKR03/04 presets, account-length setting, CSV, category groups; custom category with tax/defaults/short code/account/recoverability | Creation form inspected and cancelled; account-wide settings unchanged. Recoverable flag described as automatic settlement inclusion | P1.1/P3.2/P5.5; effective category mapping and scope control |
| RF26 DATEV `/settings/datev` | Export period/owner/year, receipt images, ignored filtering, owner scope, property/unit cost centers, combined or monthly CSV; history; bank-account mapping and owner adviser/client tabs | Read only; manual account defaults to 1200. Explicit UI caveat: export currently lacks booking keys for separate VAT reporting. No target importer acceptance or export proven | P5.5, A27; supported tax profile must be explicit |
| RF27 SEPA `/settings/direct-debit` | Creditor ID per owner; export month, batch switch, XML creation/history | Form observed, empty history; no mandate/ID added or XML generated. Export is separate from bank execution | P2.4, A10 |
| RF28 Migration/export `/settings/data` | Contact/unit import with downloadable templates; full sample-data generator; export contacts/properties/finance/documents individually; legacy census prefill | Observed; no bulk sample generation, real-data export or master import performed | P1.2/P5.6, A05/A28 |
| RF29 Templates/stationery | Editable message templates with merge placeholders and preview; payment/settlement/adjustment/portal/handover categories; user stationery for settlement and post; downloadable Word landlord forms | Read only; no template contents copied wholesale, no output fidelity test | P4.4/P5.3/P5.4 |
| RF30 Listing/lab | Exposé permission action for listing provider. Lab exposes optional new document page with inbox-first, multi-select, breadcrumb/search and move-dialog description | No permission or account-wide flag changed. New-page behavior is a UI description only, not exercised | P5.7/P5.3; provider and rollout gates |
| RF31 Object reports | Object overview, rent overview, simple rent roll, Anlage V, EÜR, financial Excel; year selection | Test object overview generated through UI, dialog closed, but no download event/file received. Report contents/export integrity unverified | P5.5, A27 |

## Saved synthetic inventory and side effects

These records remain for reproducibility; deleting them was not part of the test.
They can affect dashboard counts and forecast totals. None represents a real debt.

| Fixture | Verified state |
| --- | --- |
| `TEST Codex Referenzprüfung 2026-10-01` | Synthetic property, fictitious street address, inherited account owner |
| `TEST Wohnung 01` | Apartment, 50 m² |
| `TEST Codex Mieter` | Contact without communication/bank details; active lease from 01.10.2026, 600 base +100 advance; expected 700 obligation |
| `TEST Codex - manuelles Konto` | Manual bookkeeping account, no verified imported transactions |
| Contract draft for test apartment | Synthetic parties, 02.10.2026 start, 700 monthly, 1800 deposit; incomplete and unsigned |
| `TEST Codex Referenzprüfung – Vorgang` | V-0002, sole test task completed; parent DONE |
| `TEST-WATER-001` / `TEST Wasserzähler` | Property cold-water meter, 100 m³ reading dated 01.10.2026 |
| `TEST Abrechnung 2026 – keine echte Forderung` | Saved draft, 100 water cost, incomplete sender/unit checks; no adopted advance or finalization |
| `TEST-invoice-001.pdf` | Explicit NOT PAYABLE synthetic fixture, verified in property document center |

An empty test conversation was opened; its persistence was not independently
checked. Receipt-dialog upload may have left an unlinked stored blob; no booked
receipt was verified. These uncertain effects must not be represented as either
confirmed records or confirmed cleanup. No destructive cleanup was attempted.

## Design consequences: reduce work without losing evidence

1. **Known data should prefill, with explicit change scope.** RF06/RF19 support
   reuse across unit, party, lease and contract. Show source and an explicit
   document-only/master-data diff; never silently overwrite historical facts.
2. **Contextual upload is only the first level of assignment.** RF09/RF10 prove
   object-context filing, not inference. Our intake should infer scope/category
   with confidence/evidence, preserve the original, propose a linked cost and ask
   only about ambiguous facts. One document must not become a second cost when
   moved, retried or attached from another entry point.
3. **Money needs provenance, not convenient assumed payment.** RF07/RF15 require
   separate contractual advances, paid allocations, opening-balance assertions,
   evidence-backed zero and missing coverage. A paid-through import shortcut must
   show its impact and origin; defaulting to “today” must not create paid money.
4. **Draft operations need a visible receipt.** RF09/RF12/RF31 exposed uncertain
   outcomes in this review. Display stable job/record ID, saved/processing/failed
   state, retained input and actionable validation. Retry with an idempotency key;
   never require blind resubmission. Downloads need completion/error feedback.
5. **Preflight missing dependencies before the long wizard.** RF14/RF15/RF19/RF21
   suggest one shared readiness list with source links, affected outputs and an
   owner. Distinguish an editable incomplete draft from permitted finalization.
   Missing email blocks email delivery, not creation of a valid person record.
6. **Suggestions must explain their basis and impact.** RF16 shows proposed
   advances and explicit adoption. Show covered days/months, included costs,
   uncertainty, policy and resulting rent schedule. Do not adopt a numerically
   plausible suggestion solely because the UI calculated it.
7. **Templates should prepare accountable work.** RF17 gives useful task/history
   behavior. Model required vs optional tasks and outcomes; define whether all
   required tasks completing closes a case. Closing a case must not clear a
   financial exception or prove delivery.
8. **Keep exports/provider steps within declared support.** RF18/RF24/RF26/RF30
   leave material boundaries. Exact VAT/DATEV profiles, signature lifecycle,
   scoped permissions and public-listing disclosure need explicit gates. Trial
   labels and marketing descriptions cannot establish compatibility or safety.

## Acceptance deltas for the existing design

| Existing scenario | Additional concrete acceptance |
| --- | --- |
| A01/A03/A25 | Upload the same synthetic receipt through inbox, property and cost form: preserve one immutable source and one accepted cost; move folders without changing scope/ledger links; interrupted save resumes with explicit outcome |
| A05/A23/A26 | Prefill a contract from unit and parties; change rooms for contract only and prove master unchanged; show missing mandatory data before signature; draft contract must not activate another tenancy |
| A06/A07 | Import a lease with paid-through assertion vs independently imported bank proof; distinguish both origins and prevent double payment; contractual advance 100 plus zero posted receipts is not proof of complete zero coverage |
| A07/A28 | Bank CSV preview reports encoding/separator/date/decimal mapping, count, totals and unsupported columns; rejected/empty import has actionable outcome and produces no phantom transactions |
| A11 | Type-to-unit default, property vs unit scope, recorder/date/photo source; 100 m³ first reading creates no fabricated consumption; replacement and tenant-change boundaries remain explicit |
| A13/A24 | Reference observation fixture: 100.00 by area, 50 m² unit, 01.10–31.12.2026 occupancy (92/365) yields tenant 25.21 and vacancy 74.79. Independently validate calculation policy; any suggested advance shows basis and requires separate effective-date adoption |
| A18 | Required task completion, optional unfinished task, manual reopen and template revision preserve case history; completion does not resolve an unrelated settlement blocker |
| A20/A21 | Contact without email can exist; audience preflight names exclusion reason before portal/email release; no silent channel switch or invitation when recipient eligibility is unresolved |
| A27 | Warn/block unsupported VAT export combinations; supported generated files reconcile and open in target validator; failed download remains retriable without generating inconsistent exports |

## Remaining coverage and verification limits

No exhaustive combination coverage is claimed. The following remain unverified:
live banking/reconnection/matching, OCR output quality, successful receipt posting,
bank CSV interpretation, export file contents, authenticated tenant view and
delivery, actual signing, multi-user permissions/isolation, mobile/native apps,
large portfolios, WEG meetings/voting, loan/recurrence execution, ownership-change
accounting, meter replacements/consumption, finalized settlement/corrections and
specialist legal/tax accuracy. Subscription billing, support access, destructive
actions and live provider activation were outside the synthetic workflow scope.

Browser automation occasionally required keyboard/coordinate alternatives, and
immediate snapshots sometimes preceded UI updates. Date input and download-event
limitations are separated from product failures. The earlier generic settlement
error and uncertain receipt/import outcomes are observations, not root causes.

This reference evidence informs the target design only. Application code was not
changed or retested. Existing code-test receipts remain historical evidence for
their recorded baseline; they do not validate these new reference observations or
the proposed product behavior. Full parity still requires per-package implemented
journeys and the design's release checks.
