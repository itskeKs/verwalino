# Property management software trial evaluation

Test dates: 2026-10-01–02 (Europe/Berlin). Both immoGenio and CasaManager trials are activated and authenticated. Rental payments, a WEG budget, exports and a repair workflow have execution evidence; detailed coverage and limitations appear below.

## Scope and evidence rules

Evaluate software for professional German property managers: rental management, condominium management (WEG), and individual unit management (SEV). The user authorized free trial accounts and fictional operational data. The user subsequently requested replacing vendors whose accounts cannot be created directly, rather than pursuing sales-assisted access.

Use the supplied registration identity only in the vendor registration UI. Do not store credentials in this repository. Use clearly fictional portfolio data. Test payment preparation and document drafts without connecting a real bank or sending tenant/vendor communications.

Evidence statuses:

- **Observed**: directly seen in the live UI, with the exact action and resulting state recorded.
- **Passed**: performed with test data and checked against the fixture's expected result.
- **Failed**: an attempted supported workflow produced a reproducible incorrect result.
- **Blocked**: access, trial entitlement, activation, or a dependency prevented execution.
- **Vendor claim**: described by the vendor but not executed.
- **Not tested**: no execution evidence. A missing visible menu alone is not proof that a feature is absent.

## Access findings

| Vendor | Observed entry route | Status | Next step / limitation |
|---|---|---|---|
| Immoware24 | [Trial form](https://www.immoware24.de/testzugang/) requests company, phone, address and callback availability; vendor states access follows a call. | Skipped under revised scope | No request submitted; not a self-service account. |
| Win-CASA | [Trial download request](https://www.software24.com/kontakt/kostenlos-testen/) sends an email confirmation followed by a download link. | Skipped under revised scope | Form partly prepared but not submitted. Browser testing is preferred; desktop execution has not been established. |
| Verwalterware24 | [Trial buttons](https://verwalterware24.de/) lead to an offer-request form requiring a phone and a minimum selectable range of 51–75 units. | Skipped for account creation | No request submitted. Public portal demo is available, but is not evidence of a functional administrator account. |
| abrechnung360 | [Request trial](https://www.abrechnung360.de/) opens a contact form labelled “Unverbindlichen Testzugang beantragen”. | Skipped under revised scope | No request submitted. No direct account creation observed. |
| haussoft | [Contact](https://www.haussoft.de/kontakt/) offers a free demo request and requires postal address and phone. | Skipped under revised scope | No request submitted. |
| immoGenio | [Registration](https://app.immogenio.de/register) has account, role, company details, team and first-property stages. Phone and marketing opt-in are optional. | Activated; 45-day trial visible | Verified with the user-provided code; professional workspace and fictional clients created. |
| CasaManager | [Registration tab](https://app.casamanager.de/login) requires email, first/last name, password and terms acceptance; phone optional. | Activated; 29 trial days remaining visible | User confirmed verification; authenticated rental and WEG workflows executed. |
| SCALARA | [Registration](https://app.scalara.de/sign-up) exists and asks for account type, identity, address, credentials and terms. | Candidate; not submitted | Free registration is advertised, but trial entitlement and duration have not been confirmed. Do not count as a usable trial yet. |

Both accounts were activated using user-provided verification and both authenticated UIs were reached. No sales inquiry or paid order has been submitted.

The user explicitly authorized generating and saving the trial passwords. Unique random initial passwords were saved outside the repository in a local owner-readable/writable file (permissions `0600`). Native Mail control is unavailable in this session; the iCloud Mail browser fallback requires sign-in. The user supplied the immoGenio code and confirmed CasaManager verification. No browser or app session credentials were copied.

### Trial terms checked

- immoGenio's [terms, dated 2026-09-30](https://www.immogenio.de/agb/), specify 45 free days of Professional; expiry is automatic and payment details are required only after choosing a paid plan. They describe one month of read-only access after trial expiry, followed by suspension. This differs from the earlier marketing description of continuing in a Starter scope.
- CasaManager's [terms, version 2026-09-v1](https://app.casamanager.de/agb), require an explicit paid order to continue paid functionality after a trial. The [vendor product page](https://wp-workers.de/produkte/casamanager/) advertises 30 days without a credit card. Terms distinguish bank-data retrieval and SEPA-file generation from initiating bank payments directly.
- SCALARA's [pricing](https://www.scalara.de/preise) describes subscriptions; the existence of a free registration form alone does not establish a free full-feature trial.

## Shared execution protocol

Use `fixtures.json` across providers. Amounts are EUR. All identities, addresses, entities and transactions are fictional. Run scenarios independently where their accounting years or purposes differ.

For each workflow record: product/tier/version if visible, timestamp, start state, navigation labels, required fields, number of user actions, unexpected detours, persistence after reopening, output/export, expected versus actual result, and screenshot reference. Measure active interaction time only; exclude time waiting for the user or vendor. Never estimate click counts or time retrospectively.

| ID | Workflow | Acceptance evidence |
|---|---|---|
| ONB-01 | Register and onboard a professional management workspace | Dashboard reachable; correct trial entitlement and expiry visible; no paid order |
| MD-01 | Create rental property, units, owner, tenants and rental contracts | Reopened records preserve links, areas, amounts, dates and vacancy |
| MD-02 | Create WEG, four units, owners and ownership shares | Shares sum to 1000; rental tenant and unit owner are distinct |
| MD-03 | Set up SEV for one WEG unit | Separate management mandate, fee, housegeld and owner payout settings |
| RENT-01 | Generate September rent charges and record full/partial receipts | Rent due 1850; receipts 1700; outstanding balance 150; vacancy not charged |
| RENT-02 | Prepare arrears reminder | Correct tenant and 150 balance; preview generated without external dispatch |
| DOC-01 | Attach a fictional repair document; inspect search and linkage | Document traceable from unit, expense and case; stored data persist |
| SEV-01 | Record rent, housegeld, management fee and repair; prepare owner statement | 1100 income minus 300, 35 and 119 = 646 owner balance, with itemized entries |
| SEV-02 | Prepare owner payout | Correct 646 amount and owner; preview/file only; no real bank submission |
| NKA-01 | Allocate one annual rental operating-cost category by area | Shares 720/960/720; tenant credits 1080/1440; vacant share remains owner cost |
| WEG-01 | Allocate 6000 annual cost by ownership shares | Unit costs 1200/1602/1200/1998; sum 6000 |
| WEG-02 | Record reserve opening, additions and expenditure | 10000 + 2400 - 1800 = 10600 closing reserve; not treated as ordinary operating income |
| WEG-03 | Draft next-year budget and an owners' meeting/resolution | Budget allocation, agenda, draft resolution and document outputs inspectable |
| CASE-01 | Log repair, assign internal responsibility, track status and close | Case remains linked to the correct unit; status history and attachments available |
| PORTAL-01 | Inspect tenant/owner views where a preview or controlled test login exists | Appropriate documents and balances visible; no invitations to unrelated recipients |
| ROLE-01 | Inspect role and property access settings | Document only visible configuration; do not claim isolation without a second controlled login |
| EXIT-01 | Export master data, transactions and statements | Download succeeds; inspect contents, totals and usable file formats |
| CORR-01 | Correct a draft or reverse a posted test entry | Audit trail and balances remain consistent; inspect correction mechanism |

## Current limitations

Coverage differs between products because of account blockers. Successful budget previews do not establish correct annual closing, bank execution or SEV owner statements. Access findings are bounded to the inspected entry routes.

## Supplementary observation: Verwalterware24 public portal demo

Source: [public interactive portal demo](https://verwalterware24.de/portal-demo), inspected 2026-10-01. This is separate from the authenticated administrator evaluation and uses the vendor's fictional portfolio.

| Check | Actions and observed result | Interpretation |
|---|---|---|
| Tenant navigation | Dashboard exposes rental contract/payments, requests/damage, documents, operating-cost statement and meter readings. | Navigation observed; underlying accounting not tested. |
| Damage submission | Opened “Schaden oder Anliegen melden”, selected “Wasser / Sanitär”, entered `TEST-CASE-01 – Wasserhahn Küche tropft` and an explicitly fictional description, then submitted. The UI displayed ticket `T-2026-0146`, status “Neu”, the entered description and an open-request count increasing from 2 to 3. | Interactive demo submission observed. No claim of delivery to a real management system. |
| Within-page persistence | Navigated to the request list; the new test ticket appeared there. | State preserved during navigation. |
| Reload persistence | Reloaded the page; the dashboard reverted to two open requests and original fixture content. | The demo did not preserve the created case across page reload. This is a demo limitation, not evidence of data loss in the actual product. |
| Date behavior | New demo ticket displayed 27.09.2026 although the inspection date was 01.10.2026. | The demo uses scenario dates; it is unsuitable for validating real timestamps. |
| Owner navigation | Switched to “Vermieterportal”. Visible sections: properties, rent receipts, cases, reports and documents. Dashboard showed vacancy, rent arrears, monthly expected/received chart, repair approval and owner statement entries. | Owner workflow presentation observed; no real approval, export, access-control or arithmetic test executed. |

Local evidence (kept outside Git because registration screenshots contain the supplied email): `immogenio-registration.png`, `casamanager-registration.png`, `immogenio-email-verification.png`, `casamanager-registration-success.png`, `verwalterware24-demo-request-result.png`, `verwalterware24-demo-owner.png`.

## Authenticated testing results

### immoGenio

- ONB-01 **Passed (activation)**: six-digit verification succeeded; optional appointment and team invitations skipped. Selected professional management, external clients, rental/WEG/SEV and accounting. UI displays 45 trial days.
- Company accepts a fictional 00000 Teststadt address. First client `TEST Eigentümer Alpha` created with SKR03 accounting.
- MD-01 **Observed, partial**: created `TEST-MV-01 / TEST Mietshaus Lindenhof`, Fiktive Teststraße 10, 00000 Teststadt, 200 m². Created three units (60/80/60 m²). Their persistence and refreshed header count were subsequently verified.
- Object creation explicitly distinguishes whole-house rental management from individual-unit SEV with an externally managed WEG. Object sections: overview, units, people, cases, technology, money, contracts, documents.

### CasaManager

- ONB-01 **Passed (activation)**: password login after user-confirmed email verification succeeded. UI displays 29 remaining trial days.
- MD-01 **Passed for the rental fixture**: building wizard follows ownership type → chart of accounts → building data → review. Selected whole building and `Vermietung Standard – Mietverwaltung` (58 accounts). Created owner inline, automatically selected in the still-populated building form.
- Created and reopened `TEST Mietshaus Lindenhof` at the exact fictional address. Description and owner persisted. A dedicated accounting scope and two distribution keys appeared automatically.
- Created 60 m² unit `MV-01`, type apartment; chose follow-up rental contract creation. Building and owner carried into the lease form. Lease form supports multiple tenants, separate original/charge start dates, basic rent, operating/heating advances, extras, deposit, index/stepped rent, and internal notes. These capabilities are UI observations, not executed calculations.
- Address suggestions allow free-text fictional address. No real contact emails or bank details added.

### Rental/accounting execution evidence

**CasaManager RENT-01: Passed for September arithmetic and allocation.** Created all three units (60/80/60 m²), two leases starting 2025-01-01 (650+150 and 850+200), deposits1950/2550, third unit vacant. Reopened linked building/unit/lease pages. Created a fictional manual ledger account without IBAN or bank connection. Uploaded `test-rent-receipts.csv`: 2 imported, 0 duplicates, 0 invalid rows. Preview matched each tenant by name (85% confidence) and September period. Confirmed fictional postings: Anna650+150; Ben850+50. Reloaded report and set 01–30 September2026: Soll1850, Ist1700; Anna difference0, Ben−150, vacancy0. Evidence: `casamanager-september-rent-verified.png`.

Friction/interpretation: the report's overdue total remains all-period (7550) despite a September filter, because older test charges were not paid. It is not a September150 reminder. The explanation defines difference as Soll−Ist but the table renders a shortfall as negative (−150); this sign/documentation inconsistency is directly observed. Lease creation generated historical charges; a full historic reconciliation was not performed.

**CasaManager banking limitation (Observed):** live UI explicitly says direct online-bank connectivity is in preparation and unavailable; file import is available. Manual account creation does not require IBAN. CSV importer reports LLM mapping active and lists mapped fields.

**immoGenio MD-01: partial, persisted.** Three apartments survive reload with correct areas; header updates to3 after reload. First tenant/lease saved with650+150 and1950 deposit. A native date input ignored automation `fill` at save; keyboard segment entry corrected the start to2025-01-01 and header confirmed it. Treat as automation limitation, not established product defect. The new lease remains `Qualifiziert`, not running, and does not contribute to occupancy or monthly charges.

**immoGenio RENT-01: Blocked in this account.** Sollstellung page reports no permission despite enabled accounting and tenant-admin role. Own user initially had `mietverwaltung`/`tenant_admin`; added `buchhalter` for the fictional test client through normal role UI and saved. The same denial remained after navigation and full reload. No role-isolation claim. Screenshot: `immogenio-sollstellung-access-blocked.png`. No posted rent calculations verified.


### WEG, workflow and export evidence (2026-10-01–02)

**CasaManager MD-02: Passed for master data.** Created WEG property `TEST WEG Birkenhof` with a 65-account SKR03 property chart, four apartments (60/80/60/100 m²), four distinct fictional owners and MEA 200/267/200/333 out of 1000. The inline owner dialog preserves the underlying unit form. Live MEA progress warns about the missing balance and confirms 1000/1000. WEG-02 additionally has active lease MV-2026-003, tenant TEST Clara Demo, landlord TEST Eigentümer Gamma, start 2026-09-01, 900+200 EUR. Owner and tenant remain distinct; this alone is not proof of a complete SEV mandate/payout workflow.

**immoGenio MD-02: Partial.** Separate WEG client and object2004 created; four units with the same areas and shares persist after reload. Units can be created without owners and visibly flag the missing assignment. The owner Beta creation form was submitted; the object owner list remained empty, and ownership assignment was not completed. A client-selection detour required reloading and reselecting the new client after a blank view.

**CasaManager WEG-03: Passed for budget preview and PDF, not approval.** Building → Wirtschaftspläne → Neuer Wirtschaftsplan → empty2027 draft → add operating-cost position (account850000 building cleaning,6000,MEA) → reserve position (2400,MEA) → Hausgeld-Vorschau. Monthly contributions are140/186.90/140/233.10; annual totals1680/2242.80/1680/2797.20; total8400. Costs6000 and reserve2400 remain separate. No approval or owner dispatch. A warning about missing management costs is expected for this deliberately minimal fixture. Evidence `casamanager-weg-budget-verified.png`.

**immoGenio WEG-03: Calculation failed for this fixture.** Created2027 draft, reserve2400 and one6000MEA cost position. The data persist after full reload, but Berechnen returns `Interner Serverfehler`; reproduced after reopening. Unit shares remain uncalculated. The fixture has unassigned owners, which is an unresolved precondition; the UI returned a server error rather than a specific validation message. Do not infer all WEG calculations fail. Evidence `immogenio-budget-calculation-error.png`.

**immoGenio CASE-01: Passed for simple request lifecycle.** Object2004 → Vorgänge → Anfragen → new `TEST-CASE-01`, linked to WEG-02, fictional description. Request ANF-2026-000001 progressed from pending through Bearbeitung starten (note entered) to Erledigen (result required). Completed status and dated start/processing/completion history appeared; completed requests can be displayed with a filter. Documents, messages and PDF preview controls observed; no supplier order, invoice posting or external message. Evidence `immogenio-case-completed.png`.

**immoGenio MD-03: SEV object concept observed.** Created separate synthetic object2005 `TEST-SEV-EXT`, typeETW,80m², external WEG manager. UI explicitly labels Sondereigentumsverwaltung and records external manager name/address/email/phone fields. No unit, lease, management fee or owner payout attached to this additional object. The shared four-unit WEG fixture remains separate.

**EXIT-01: Partial passed.** CasaManager downloaded a3-page overall2027 budget and a1-page WEG-02 individual plan; PDF text checked. Individual plan names Gamma and shows1602 operating costs +640.80 reserve =2242.80/year =186.90/month, explicitly draft. Overall PDF includes four owners, shares and all totals. Company letterhead remains a setup placeholder. Files are in `exports/`. Browser download-event tracking timed out, but actual files were saved in Downloads and read successfully; this is an automation limitation, not a failed product export. immoGenio XLSX downloaded with nine structured sheets; the first export was deliberately scoped to the additional SEV object, and the object row preserves SEV/external-manager fields.

**CasaManager WEG-02: Arithmetic passed; metadata mismatch.** Created an internal reserve ledger without bank details, opening10000, contribution2400, maintenance1800. Balance10600 is correct. The submitted forms displayed2025 dates and types Hausgeld-Rücklage/Instandhaltung, but saved table and CSV show current creation date02.10.2026 and both movements as Manuelle Anpassung. The edit dialog also confirms the latter. Therefore the intended2025 reserve scenario is not passed end-to-end. Initial CSV preserved as evidence. Subsequent edits corrected both movement types to Hausgeld-Rücklage and Instandhaltung; reopening confirmed both types and the unchanged EUR 10,600 balance (evidence `casamanager-reserve-corrected.png`); no editable date field is exposed in that edit dialog. Audit history was not established. This is a materially relevant mismatch requiring vendor explanation before use with real accounts.


### Rental operating-cost statement (NKA-01)

**CasaManager: statements created; exact fixture allocation differs by one cent.** Entered a 2025 building-cleaning expense of EUR 2,400 gross, 19% VAT (net 2,016.81; VAT 383.19), account 4030, service period 2025-01-01 to 2025-12-31, booking date 2025-12-31, distribution by 200 m² total area. The cost date persisted correctly, unlike the separate reserve-entry issue.

The five-stage wizard covers property/year, expense review with inline correction, units/advances, sender/recipient addresses, and preview. Adding the missing owner's sender address inline preserved the wizard. All three units were included. Preview assigns EUR 720 to vacancy at the owner's expense and creates no fictitious tenant statement for that unit. Two tenant statements were created and reopened:

| Unit | Area | Expected cost | Actual cost | Annual advance | Actual credit |
|---|---:|---:|---:|---:|---:|
| MV-01 / Anna | 60 m² | 720.00 | 719.99 | 1,800.00 | 1,080.01 |
| MV-02 / Ben | 80 m² | 960.00 | 960.01 | 2,400.00 | 1,439.99 |
| MV-03 / vacant | 60 m² | 720.00 | 720.00 | — | — |

Total allocation remains EUR 2,400. The one-cent movement may arise from net/VAT rounding, but the cause was not established. This is a deviation from the simple gross-allocation fixture, not a legal assessment. Advances were taken from scheduled contracts; no 2025 receipt reconciliation was entered. The resulting two-page Anna PDF nevertheless labels them “Geleistete Vorauszahlungen” and states that the credit will be transferred shortly. No transfer was prepared or executed, and no recipient bank account was added. Review these template assumptions before sending a real statement.

The PDF was generated, downloaded and its text inspected; its cost/advance/credit values match the saved statement. No email, sharing or portal release was performed. Evidence: `casamanager-nka-preview-rounding.png`, `casamanager-nka-saved.png`, and the PDF/text export in `exports/`. The PDF is dated 2026-10-01 while local testing had crossed into 2026-10-02; timezone handling was not investigated.

### Coverage at handoff

This table describes execution, not the entire marketed feature set. No retrospective timing or click-count estimates were made. No product version/build identifier was captured.

| Scenario | CasaManager | immoGenio |
|---|---|---|
| ONB-01 | Activated; dashboard and remaining trial days observed | Activated; 45-day entitlement observed |
| MD-01 | Rental property, three units, owner, two active leases persisted | Three units persisted; first lease remains Qualified |
| MD-02 | Four owners/units; MEA 1000/1000; distinct owner and tenant | Four units and MEA persisted; owner assignment incomplete |
| MD-03 | WEG unit rented to separate tenant; full mandate not established | Explicit SEV/external-manager object created; full mandate not established |
| RENT-01 | September due 1,850 / received 1,700 / shortfall 150 verified | Blocked by Sollstellung access denial |
| RENT-02 | Not executed; all-period arrears differ from September shortfall | Not executed |
| DOC-01 | No uploaded repair-document linkage test | No uploaded repair-document linkage test |
| SEV-01 | Full owner statement/fee accounting not executed | Full owner statement/fee accounting not executed |
| SEV-02 | Owner payout not executed | Owner payout not executed |
| NKA-01 | Saved statements and PDF; one-cent allocation deviation | Not executed |
| WEG-01 | Cost allocation checked in budget only, not annual actual closing | Budget calculation failed; actual closing not executed |
| WEG-02 | Balance correct; original type/date persistence mismatch | Not executed |
| WEG-03 | Budget and individual/overall PDFs verified; meeting/resolution untested | Budget persisted; calculation returns server error |
| CASE-01 | Not executed | Basic linked request lifecycle completed; assignment/attachments untested |
| PORTAL-01 | Authenticated recipient view not tested | Authenticated recipient view not tested |
| ROLE-01 | No second-user isolation test | Own role settings observed; no second-user isolation test |
| EXIT-01 | Budget PDFs, tenant statement PDF and reserve CSV read | Nine-sheet XLSX read; second export contains two WEG-client objects and four units |
| CORR-01 | Reserve type edit observed; audit trail/reversal not established | No posting correction test |

### Assessment for a professional manager

CasaManager allowed more of this accounting fixture to be completed: linked owners/tenants, CSV rent reconciliation, a WEG budget, a reserve ledger and rental operating-cost statements. Inline contact creation and the step-by-step statement flow are useful. The reserve metadata mismatch, unavailable direct banking, all-period arrears display and statement-template assumptions require follow-up before operational use.

immoGenio exposes clearer explicit client separation and a dedicated SEV object with external WEG manager details. Its basic case lifecycle and structured Excel export worked. The lease remaining Qualified, denied Sollstellung and budget server error prevented a comparable accounting assessment in this account. The unassigned WEG owners remain a fixture limitation.

Neither product has been validated here as a complete professional SEV solution. Management mandates, fee posting, owner statements, payouts, full annual WEG closing and independent role isolation remain unverified. These are material open requirements for the user's intended selection; the current evidence does not justify selecting either as a fully qualified replacement.

### Saved artifacts

- `fixtures.json`: planned synthetic portfolio and expected arithmetic.
- `test-rent-receipts.csv`: two synthetic September receipts imported into CasaManager.
- `exports/`: inspected budget PDFs, rental statement PDF, reserve CSV and immoGenio Excel exports; PDF text extractions aid review.
- Credentials and screenshots are stored outside Git at `/Users/keks/.codex/workflow-state/artifacts/hausverwaltung-software-trials-2026-10-01/`; credential file `trial-credentials.json` has mode `0600`.
- Both authenticated browser tabs are retained for inspection. Test data and draft documents remain in the accounts. No subscriptions, paid orders, live bank connections or external tenant/vendor dispatches were initiated.
