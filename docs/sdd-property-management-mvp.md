# Software Design Document: Property Management Software MVP

As of: 2026-06-01 · Version 0.2

> Changes relative to v0.1 (2026-05-10): Delivery fixed as a pure **responsive web app** (native clients dropped). New Phase 1 building blocks: **e-invoice receipt**, **asset report (Vermögensbericht, § 28 IV WEG)**, **GoBD-compliant voucher (Beleg) storage**, **GDPR (DSGVO) concept**, **SEV (Sondereigentumsverwaltung, individual unit management) as a management type**, **resolution register (Beschluss-Sammlung) & owners' meeting**, **dunning / open items**, **base rent & tenant ledger (Mietkonto)**. Target users narrowed to **professional property managers with organization isolation**. Settlement engine extended with settlement balance (Abrechnungsspitze), vacancy and CO2 allocation.

## 1. Purpose

This document describes the MVP of a **responsive, web-based** property management software for rental management, homeowners' association (WEG) management, SEV, and mixed properties. The system is not intended to replace full financial accounting. Its purpose is to capture master data, costs, advance payments, incoming payments and settlements in a structured way and to generate traceable operating cost statements (Betriebskostenabrechnungen), Hausgeld statements (Hausgeld: monthly owner contribution), economic plans (Wirtschaftspläne) and asset reports from them.

The most important professional requirement is **settlement reliability** (Abrechnungssicherheit): automation may make suggestions, but legally relevant postings and settlements are adopted in Phase 1 only after manual confirmation.

The system is aimed at **professional property management companies that look after multiple mandates of third-party owners or owner communities**. Organization isolation and segregation of funds are therefore mandatory architectural features from the start.

## 2. MVP Goals

- Management of properties, units, owners and tenants.
- Mapping of pure rental properties, pure WEG properties, SEV and mixed properties.
- Multi-organization capability with segregation of funds per management entity and a simple role model.
- Creation of operating cost statements for tenants.
- Creation of Hausgeld/owner statements for WEG properties (WEMoG-compliant, as settlement balance).
- Creation of the **asset report** per § 28 Abs. 4 WEG.
- Creation of an economic plan with Hausgeld proposals calculated from it.
- Management of base rent, operating cost advance payments and a simple tenant ledger.
- Manual capture of costs, advance payments and incoming payments.
- **Open items overview and simple dunning** (expected/actual (Soll/Ist) per person, dunning levels).
- Assisted invoice import: **PDF with extraction suggestions** and **structured e-invoice receipt (XRechnung/ZUGFeRD)**.
- **GoBD-compliant, audit-proof voucher storage.**
- Maintenance of the **resolution register** and simple management of the **owners' meeting**.
- Fixed standard chart of accounts for Phase 1.
- Fixed, clean PDF output templates for settlements.
- Deadline and obligation monitoring (e.g., 12-month deadline for operating cost statements, WEG settlement deadlines).

## 3. Non-Goals for Phase 1

- **No native desktop or mobile apps.** Delivery is exclusively as a responsive web app. Mobile use happens through the browser.
- No full financial accounting with DATEV logic, chart-of-accounts frameworks, balance sheet or P&L.
- No self-service portal with login for owners or tenants (the manager generates and sends documents).
- No freely configurable, fine-grained permission system at the level of individual properties (there is organization isolation and a few fixed roles, see section 4).
- No bank import (CAMT/MT940) and no automatic payment matching.
- No freely configurable chart of accounts.
- No visual PDF template builder.
- No automatic posting of OCR/AI/e-invoice results without approval.
- No automatic e-invoice issuance (the B2B issuance obligation applies only in stages; the MVP covers receipt only).
- No online owners' meeting/voting; only capture and documentation.

## 4. Target Users, Multi-Organization Capability and Roles

The primary user is a **professional property management company** that looks after many properties of third-party owners and owner communities.

**Management entity (Organization):** Top-level organization/isolation boundary. All master data, vouchers, accounts and settlements are unambiguously assigned to one Organization. Data access is strictly limited to the Organization of the signed-in user.

**Mandate (ManagementMandate):** Business engagement within an Organization. A mandate links the management company to a managed context and has a term:

- WEG mandate: management of an entire owner community.
- Rental mandate: management on behalf of a single owner.
- SEV mandate: management of a single rented unit within a third-party WEG.

**Segregation of funds:** Funds and vouchers of different mandates/WEGs are never mixed. Each WEG or client has its own accounts and its own settlement sphere (third-party funds/trust segregation).

**Roles (fixed, not freely configurable):**

| Role | Permissions |
| --- | --- |
| "Inhaber/Admin" (owner/admin) | Full access including user, role and mandate management |
| "Sachbearbeiter" (clerk) | Operational capture, settlement, voucher approval; no user management |
| "Nur-Lesen" (read-only) | Read-only access, e.g., for advisory board, auditor, deputy |

Finer, mandate-related access restrictions within an Organization are a deliberate later extension.

## 5. Property Types

In Phase 1, a property can have one of the following management types:

| Property type | Description | Settlements |
| --- | --- | --- |
| Rental property | A property owned by a landlord where only tenant settlements are relevant. | Operating cost statement |
| WEG property | A property with an owner structure, MEA (co-ownership share, Miteigentumsanteil), Hausgeld, economic plan and annual settlement. | Hausgeld statement, economic plan, asset report |
| SEV unit | A single rented condominium in a third-party WEG, managed on behalf of the individual owner. | Operating cost statement for the tenant; forwarding of the WEG Hausgeld statement |
| Mixed property | WEG property in which individual units are rented out. | Hausgeld statement and operating cost statement |

Important: A mixed property remains one property from a professional standpoint. It is not artificially split into a WEG property and a rental property. The unit connects both levels: it always has an owner assignment and optionally a lease assignment.

The SEV unit is the special case in which the own Organization does not manage the WEG, but only the individual ownership of a single owner. The WEG Hausgeld statement then arrives as an external document from the third-party WEG management and forms a cost basis for the tenant settlement.

## 6. Core Modules

### 6.1 Property and Unit Management

The system manages buildings and their units. A unit can be an apartment, a commercial unit, a parking space or another unit.

Important fields:

- Property name and address
- Management type: rental property, WEG property, SEV unit, mixed property
- Settlement period
- Units with number, location, area, heated area, type of use and MEA
- Optional distribution particularities per unit
- Optional meters per unit/property (see 6.2.1)

### 6.2 Person Management

The system manages owners, tenants and vendors/suppliers separately, because these roles follow different logic.

Owners:

- Contact data
- Bank data optional
- Ownership history per unit with start and end date
- MEA or share per unit

Tenants:

- Contact data
- Lease per unit
- Lease start and lease end
- Base rent and operating cost advance payment
- Optional security deposit and notes

Vendors/suppliers:

- Master contact (name, address, bank data, tax/VAT ID optional)
- Link to cost items and e-invoices (supplier recognition)

#### 6.2.1 Meters and Readings

For consumption-based distribution, the system optionally manages meters (e.g., cold water, general electricity) with readings (date, reading, period). Heating and hot water statements continue to be created externally in Phase 1 and imported as values/files (see 6.9 and 9).

### 6.3 Bank Accounts

The system manages the accounts belonging to each mandate or WEG as master data, without bank statement import in Phase 1:

- WEG/rental account (current account)
- Separate reserve account (maintenance reserve, Erhaltungsrücklage)
- Optional security deposit account

Accounts are unambiguously assigned to one mandate/WEG (segregation of funds). Incoming and outgoing payments are captured manually against these accounts in Phase 1.

### 6.4 Cost and Invoice Capture

Costs are captured as cost items. A cost item can be entered manually, suggested from an uploaded PDF, or taken from a structured e-invoice.

Important fields:

- Property (and mandate, if applicable)
- Category from the standard chart of accounts
- Supplier/invoice issuer (linked to vendor master data)
- Invoice date
- Service period from/to
- Gross amount (and VAT amount/rate, if available from the e-invoice)
- Optional voucher number
- Optional PDF file and/or e-invoice data record
- Distribution key for owner settlement
- Distribution key for tenant settlement
- Apportionability
- Status of voucher origin and approval (see 9)

### 6.5 E-Invoice Receipt

Since 01.01.2025, domestic B2B recipients must be able to receive and process e-invoices. The system accepts **XRechnung (XML)** and **ZUGFeRD (PDF with embedded XML, from profile EN-16931)**.

Advantage over the PDF/OCR route: fields such as supplier, invoice number, date, amounts, VAT and service period come in structured form from the data record; no extraction guessing step is needed. The system maps these fields to a cost item in status `draft_extracted` or `needs_review` and, as with the PDF import, suggests category, apportionability and distribution key. The same applies here: no automatic, final posting without approval.

### 6.6 Voucher Archive and GoBD Compliance

All vouchers (PDF, e-invoice XML, imported external statements) are stored in an audit-proof manner:

- Immutability: An approved voucher is not overwritten; corrections create a new version referencing the original.
- Integrity: Checksum (e.g., SHA-256) per voucher.
- Traceability: Change/approval history (who, when, what) per voucher and cost item.
- Retention: at least **8 years** (as of BEG IV from 2025), machine-readable and retrievable.
- Procedural documentation (Verfahrensdokumentation): The voucher flow (Belegfluss: receipt → suggestion → approval → settlement) is documented.

### 6.7 Rent and Payment Management

For each tenancy, the system captures base rent and operating cost advance payment and maintains a simple tenant ledger (expected charge per period, captured payments, balance).

Incoming payments are captured manually in Phase 1 and can be assigned to:

- Tenant: base rent
- Tenant: operating cost advance payment
- Tenant: security deposit
- Owner: Hausgeld advance payment
- Owner: reserve share
- Owner: special assessment (Sonderumlage) / special payment / supplementary payment

For settlements, the system calculates the difference between the expected share and the payments already captured, and maintains an **open items list** per person.

### 6.8 Dunning

Based on the open items, the system supports simple, multi-level dunning:

- Detection of overdue expected charges (rent, Hausgeld, supplementary payment)
- Dunning levels (e.g., payment reminder, 1st/2nd dunning notice)
- Generation of dunning letters as PDF
- Recording of dunning history and status per open item

No automatic bank matching; payments are posted manually and clear open items.

### 6.9 Settlement Engine

The settlement engine processes costs, periods, person histories, distribution keys and payments.

It produces:

- Operating cost statement for tenants
- Hausgeld/owner statement for WEG properties
- Economic plan with Hausgeld proposals
- Data basis for the asset report (see 11)

Binding rules:

- **Chronological integrity:** Old ownership and tenancy relationships must not be overwritten. In the event of an owner or tenant change within a settlement period, pro rata periods must be calculated.
- **Settlement balance (Abrechnungsspitze, WEMoG):** The WEG annual settlement shows the difference between the expected advance payments resolved in the economic plan and the actual costs as the settlement balance. The subject of the resolution is the adjustment (additional payment/credit), not the entire settlement.
- **Vacancy:** The owner bears the shares of vacant units; the distribution must map vacancy cleanly.
- **CO2 cost allocation:** For the allocation under CO2KostAufG (10-level model, depending on kg CO₂/m²·a), the tenant/landlord share is either taken from the external heating cost statement or calculated from entered parameters. Only the tenant share is apportionable.
- **Heating costs:** In Phase 1, heating/hot water costs are taken over as externally created values per unit (HeizkostenV-compliant through the metering service); the system shows these as an external statement.

### 6.10 Owners' Meeting and Resolution Register

For WEG mandates, the system maintains:

- **Owners' meeting:** Invitation with agenda, participants/voting rights (by MEA or per-capita principle), minutes, resolutions passed with voting result.
- **Resolution register (Beschluss-Sammlung, § 24 Abs. 7 WEG):** consecutively numbered, dated collection of resolutions with place/date of the meeting and a note on contestation/annulment. Mandatory for every WEG.

Online voting or a virtual meeting is not part of Phase 1.

## 7. Standard Chart of Accounts, Phase 1

The MVP uses a fixed standard chart of accounts. Custom categories are not created in Phase 1. Each category brings defaults for apportionability and distribution key. These defaults can be adjusted manually per cost item if a special case applies.

### 7.1 Apportionable Operating Costs

These costs are in principle intended for operating cost statements, provided they are agreed in the lease and are recurring operating costs in the specific case.

| Category | Tenant operating cost default | Owner statement | Default distribution |
| --- | --- | --- | --- |
| Grundsteuer / öffentliche Lasten (property tax / public charges) | apportionable | yes | area or MEA |
| Kaltwasser (cold water) | apportionable | yes | consumption or area |
| Entwässerung / Abwasser (drainage / wastewater) | apportionable | yes | consumption or area |
| Heizung zentral (central heating) | apportionable external/review | yes | external statement or heating cost logic |
| Warmwasser zentral (central hot water) | apportionable external/review | yes | external statement or heating cost logic |
| Müllbeseitigung (waste disposal) | apportionable | yes | unit, persons or area |
| Straßenreinigung (street cleaning) | apportionable | yes | area or MEA |
| Winterdienst (winter maintenance) | apportionable | yes | area or MEA |
| Gebäudereinigung (building cleaning) | apportionable | yes | area |
| Gartenpflege (garden maintenance) | apportionable | yes | area or MEA |
| Allgemeinstrom / Beleuchtung (common-area electricity / lighting) | apportionable | yes | area or MEA |
| Schornsteinreinigung (chimney sweeping) | apportionable | yes | unit, area or external statement |
| Gebäudeversicherung (building insurance) | apportionable | yes | area or MEA |
| Haftpflichtversicherung (liability insurance) | apportionable | yes | area or MEA |
| Aufzug Betrieb/Wartung (elevator operation/maintenance) | apportionable | yes | unit or area |
| Waschraum / Gemeinschaftswascheinrichtung (laundry room / shared laundry facility) | apportionable | yes | consumption, unit or area |

### 7.2 Mixed or Review-Required Costs

These categories must not be blindly allocated in full to tenants.

| Category | Tenant operating cost default | Owner statement | Note |
| --- | --- | --- | --- |
| Hausmeister / Hauswart (caretaker / janitor) | mixed_review | yes | Exclude administration, repair and maintenance shares |
| Sonstige Betriebskosten (other operating costs) | manual_review | yes | only with a specific basis in the lease and for recurring costs |
| Kabel / Breitband / Gemeinschaftsantenne (cable / broadband / shared antenna) | manual_review | yes | apportionability restricted since 2024 |
| Messdienst / Abrechnungsdienst (metering service / billing service) | apportionable_external/review | yes | e.g., heating costs/water, no double billing |
| CO2-Kosten (CO2 costs) | partial_review | yes | only tenant share apportionable (10-level model) |

### 7.3 Non-Apportionable Owner/WEG Costs

These costs appear in the owner/Hausgeld statement, but not in the operating cost statement for tenants.

| Category | Tenant operating cost default | Owner statement | Note |
| --- | --- | --- | --- |
| WEG-Verwalterkosten (WEG administrator costs) | not_apportionable | yes | administration costs |
| Buchhaltung / Jahresabrechnung (bookkeeping / annual settlement) | not_apportionable | yes | administration costs |
| Eigentümerversammlung (owners' meeting) | not_apportionable | yes | administration costs |
| Bankgebühren WEG-Konto (bank fees, WEG account) | not_apportionable | yes | administration costs/admin costs |
| Rechtsberatung / Prozesskosten (legal advice / litigation costs) | not_apportionable | yes | regularly owner/WEG costs |
| Instandhaltung (maintenance) | not_apportionable | yes | not operating costs |
| Instandsetzung / Reparaturen (repair / repairs) | not_apportionable | yes | not operating costs |
| Erhaltungsrücklage / Instandhaltungsrücklage (maintenance reserve) | not_apportionable | yes | show separately |
| Sonderumlage (special assessment) | not_apportionable | yes | check purpose, default owner-only |

### 7.4 Pure Rental Properties

For pure rental properties, Phase 1 captures only costs that are relevant for the operating cost statement. Non-apportionable internal landlord costs such as repairs, administration or maintenance are not captured for pure rental properties in the MVP.

## 8. Distribution Keys

In Phase 1, the system supports the following distribution keys:

- MEA
- Residential/usable area
- Heated area
- Unit
- Number of persons
- Consumption
- External statement per unit
- Manual amount per unit

Owner statement and tenant statement can have different distribution keys for the same cost item.

Example: A WEG invoice is distributed by MEA in the owner statement, but for tenants by residential area or by external statement.

## 9. Assisted Invoice Import

In Phase 1, the invoice import is a suggestion tool. It must not automatically post a cost item as final. There are two input channels:

- **PDF invoice:** Extraction via OCR/parsing (less reliable).
- **E-invoice (XRechnung/ZUGFeRD):** Structured fields directly from the data record (more reliable, see 6.5).

Flow:

1. The user uploads a PDF or e-invoice (or forwards it to an input channel).
2. The system extracts or takes over supplier, invoice date, voucher number, amount, VAT, service period and text features.
3. The system suggests a category from the standard chart of accounts.
4. Based on the category, the system suggests apportionability and distribution key.
5. The user reviews and confirms or corrects the values.
6. Only after confirmation does the cost item become active for settlements and the voucher is locked in an audit-proof manner (see 6.6).

Every imported suggestion must have a status:

- `draft_extracted`: detected from PDF/e-invoice, not yet confirmed
- `needs_review`: detection uncertain or category critical
- `confirmed`: confirmed by the user and relevant for settlement
- `rejected`: suggestion discarded

## 10. Economic Plan

The economic plan is used to calculate Hausgeld advance payments for owners.

Inputs:

- Prior-year costs or manual plan values per category
- Expected reserve contribution
- Distribution key per cost category
- Optional rounding rules and monthly payment schedule

Outputs:

- Plan amount per category
- Share per unit/owner
- Monthly Hausgeld proposal
- Share of apportionable and non-apportionable costs
- Reserve share shown separately

The economic plan is not a substitute for a formal WEG resolution. The system generates proposals and documents for the resolution; the resolution passed is documented in the resolution register (see 6.10).

## 11. Document and PDF Output

Phase 1 uses fixed PDF templates. The following are generated:

- Operating cost statement for tenants
- Hausgeld/owner statement
- Economic plan
- **Asset report (§ 28 Abs. 4 WEG)**
- Dunning letters
- Meeting invitation and minutes

The settlement PDFs should contain (formal minimum requirements under Federal Court of Justice (BGH) case law for tenant settlements):

- Property and settlement period
- Addressee and unit reference
- Cost overview by category (total costs per cost category)
- Distribution key applied per item
- Calculation of the addressee's share
- Deduction of advance payments
- Supplementary payment or credit
- For owner statements, separation of apportionable/non-apportionable/reserve as well as disclosure of the settlement balance
- Notes on manual external settlement values, if used

The asset report contains at least the status of the maintenance reserve and a statement of the essential community assets (receivables/liabilities of the community).

## 12. Deadline and Obligation Monitoring

The system monitors and reminds of essential deadlines:

- 12-month deadline for the operating cost statement (§ 556 Abs. 3 BGB).
- WEG annual settlement/asset report after the end of the calendar year.
- Upcoming owners' meeting / due economic plans.
- Overdue open items (trigger for dunning).

## 13. High-Level Data Model

Central entities:

- `Organization` (organization/isolation boundary)
- `User`, `Role`
- `ManagementMandate`
- `Property`
- `Unit`
- `Meter`, `MeterReading`
- `Person`
- `OwnerProfile`
- `TenantProfile`
- `Vendor` (vendor/supplier)
- `OwnershipHistory`
- `LeaseAgreement`
- `BankAccount`
- `CostCategory`
- `CostEntry`
- `InvoiceDocument` (PDF and/or e-invoice data record, with checksum/version)
- `Payment`
- `OpenItem` (open item) and `DunningNotice` (dunning notice)
- `SettlementRun`
- `SettlementLine`
- `EconomicPlan`
- `EconomicPlanLine`
- `AssetReport` (asset report)
- `OwnersMeeting`, `Resolution` (resolution), `ResolutionRegister` (resolution register)

Relationships (excerpt):

- `Organization` encloses all other entities (strict organization isolation).
- `Property` has many `Unit`.
- `Unit` has many `OwnershipHistory` and many `LeaseAgreement`.
- `Property`/`Mandate` has one or more `BankAccount`.
- `Property` has many `CostEntry`; `CostEntry` can have an `InvoiceDocument`.
- `Payment` is assigned to tenant, owner, unit, account and period and clears `OpenItem`.
- `SettlementRun` generates `SettlementLine` per addressee and category.
- `OwnersMeeting` generates `Resolution`; confirmed `Resolution` entries are carried forward in the `ResolutionRegister`.

## 14. Chronological Rules

- Ownership changes and tenant changes are stored as history.
- Historical data is not overwritten.
- Settlements access the state in the respective settlement period.
- Cost items have service periods, not just invoice dates.
- In the event of a change during the year, it must be possible to calculate pro rata.
- Approved vouchers and finalized settlements are immutable; corrections are made through new versions referencing the original.

## 15. Data Protection (GDPR)

The system processes personal data of tenants, owners and vendors. Phase 1 takes the following into account:

- Legal basis and purpose limitation of the stored data.
- Retention and erasure concept (interlocked with the 8-year tax retention periods for vouchers).
- Data access only within the own Organization and according to role.
- Data hosting in the EU; for SaaS operation, commissioned processing (Art. 28 GDPR) is planned.
- Access and erasure requests must be technically operable (export/anonymization) without violating audit-proof voucher retention.

## 16. Technical Architecture

Recommended MVP stack:

- Frontend: **Responsive web app** with React and TypeScript (desktop-first, fully usable on tablet and smartphone). No native app; mobile use via the browser. File upload/voucher photo via the browser file/camera API.
- Backend: Python FastAPI or Node.js/NestJS.
- Database: PostgreSQL with a multi-organization schema (Organization scoping on every relevant table).
- E-invoice: Parser/validator for XRechnung and ZUGFeRD (EN-16931) as a modular service.
- PDF generation: HTML-to-PDF rendering, e.g., Playwright/Puppeteer, or a server-side PDF library.
- File storage: audit-proof with checksums; local in the MVP, later S3-compatible with object lock/versioning.
- OCR/PDF extraction: modular service, so that another provider or a local model can be connected later.
- AuthN/AuthZ: Login with Organization binding and fixed roles.

Architecture principles:

- **API-first**, cleanly separated from the web UI, so that integrations (e.g., later bank import, DATEV export) remain possible. The API serves integrations, not native clients.
- Organization isolation as an invariant property of every data access layer (no cross-org access).
- Settlement logic separated from UI and PDF layout.
- Cost categories as versioned system data.
- All final settlement results and approved vouchers are stored as immutable snapshots, so that later master data changes do not alter old documents.

## 17. Legal Bases for Defaults

The software maps professional defaults based on the following bases:

- BGB § 556: Agreements on operating costs
- BGB § 556 Abs. 3: 12-month settlement deadline and objection period
- BGB § 556a: Allocation standard for operating costs
- BetrKV § 1: Definition and exclusion of administration/maintenance costs
- BetrKV § 2: Operating cost catalog
- WEG § 16: Benefits and costs
- WEG § 24 Abs. 7: Resolution register
- WEG § 28: Economic plan, annual settlement, asset report (incl. Abs. 4 asset report; settlement balance under WEMoG)
- HeizkostenV, in particular distribution of heating and hot water costs
- CO2KostAufG for allocation of CO2 costs (10-level model)
- TKG / Nebenkostenprivileg changes for cable/broadband since 2024
- UStG § 14 / e-invoice obligation (receipt since 01.01.2025; XRechnung/ZUGFeRD)
- GoBD: immutability, traceability, machine evaluability
- Retention periods (8 years for accounting vouchers from 2025, BEG IV)
- GDPR: processing of personal data, retention/erasure, commissioned processing

Note: The system can map legal rules technically and provide plausibility checks. It does not replace legal or tax review in special cases.

## 18. Open Points for the Next Specification Round

- Exact PDF structure for operating cost statement, Hausgeld statement and asset report.
- Which rounding rules apply per settlement.
- Exact dunning levels, deadlines and fee logic in dunning.
- How external heating/water statements are entered in detail: amount per unit, PDF attachment, total and plausibility check; takeover of the CO2 allocation value.
- Level of detail of the tenant ledger (expected/actual only vs. individual expected charges per month) and handling of security deposit/security deposit account.
- Whether and how commercial units need an upfront deduction (Vorwegabzug) (different allocation logic, VAT option).
- Whether owner statements should include tax disclosures (e.g., § 35a EStG household-related services/craftsman services).
- Input channels for e-invoices (upload, e-mail inbox per mandate) and validation strictness.
- Exact scope of meeting management (voting-right models, proxies, written-circulation resolution (Umlaufbeschluss)).
- Level of detail of the erasure/anonymization concept in tension with audit-proof retention.
