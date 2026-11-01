# Documentation map

Start with the project overview, then choose a page for the task at hand.

| Start here | Purpose |
| --- | --- |
| [Project overview](../README.md) | Features, screenshots, quick start, and project status. |
| [Documentation map](README.md) | This index of maintained documentation. |
| [Roadmap](roadmap.md) | Milestones, issue-based work, and open domain questions. |
| [Feature status](feature-status.md) | Implemented features, partial support, gaps, and source locations. |

| Operate | Purpose |
| --- | --- |
| [Deployment](../DEPLOY.md) | Portable installation, hosted-server operation, worker setup, backup, and release boundaries. |
| [Declaration imports](operations/declaration-imports.md) | Reviewable extraction, building knowledge, workers, privacy, and local evaluation. |
| [Invoice extraction](operations/invoice-extraction.md) | Upload review, extraction queue, OCR limits, retry, and rollback. |
| [Invoice/cost linking](operations/invoice-cost-linking.md) | Source-link reconciliation, immutable originals, and rollout constraints. |

| Design documents (June 2026, pre-implementation) | Purpose |
| --- | --- |
| [Architecture](architecture.md) | M0 design: data model, organization isolation, and conventions. |
| [MVP specification](sdd-property-management-mvp.md) | MVP design: property-management scope, domain rules, and original acceptance requirements. |
| [Voucher flow](m2-gobd-belegfluss.md) | M2 design: costs, chart of accounts, GoBD immutability, and stored originals. |
| [Operating cost engine](m3-abrechnungs-engine.md) | M3 design: distribution keys, pro rata periods, rounding, snapshots, and tenant statements. |
| [WEG accounting](m4-weg-abrechnung.md) | M4 design: annual settlements, economic plans, owner balances, and asset reports. |
| [Payments and tenant ledger](m6-zahlungen-mietkonto.md) | M6 design: bookings, expected/actual charges, open items, and dunning. |

| ADRs | Purpose |
| --- | --- |
| [0001 Organization isolation](adr/0001-organization-isolation-rls.md) | Enforce organization isolation with row-level security. |
| [0002 Backend stack](adr/0002-backend-stack-fastapi.md) | Use FastAPI and the selected backend components. |
| [0003 GoBD immutability](adr/0003-gobd-immutability-mechanism.md) | Protect confirmed financial records and correct them without overwriting. |
| [0004 Daily allocation](adr/0004-pro-rata-daily-allocation.md) | Allocate periods by actual calendar days. |
| [0005 Largest-remainder rounding](adr/0005-rounding-largest-remainder.md) | Preserve exact totals when distributing cents. |
| [0006 End-date convention](adr/0006-valid-to-convention.md) | Define the shared valid-to interval convention. |
| [0007 WEG balances and plans](adr/0007-weg-settlement-balance-and-economic-plan.md) | Relate annual settlement balances to economic-plan expected amounts. |
| [0008 Asset-report scope](adr/0008-asset-report-mvp-scope.md) | Bound the MVP asset report. |
| [0009 Expected, actual, receipt](adr/0009-expected-actual-and-payment-receipt.md) | Distinguish expected charges, actual payments, and receipts. |
| [0010 Segregation of funds](adr/0010-segregation-of-funds-payments.md) | Keep payment flows separated by their accounting context. |
| [0011 Resolution register](adr/0011-resolution-register-immutability.md) | Preserve resolution history without destructive edits. |
| [0012 Privacy and retention](adr/0012-gdpr-erasure-vs-gobd-retention.md) | Reconcile GDPR erasure with GoBD retention. |

| Operations pages | Purpose |
| --- | --- |
| [Users and access](operations/users-and-access.md) | Change passwords, manage users, and invalidate sessions. |
| [Master data and selectors](operations/selector-pages.md) | Edit, end, delete, and search master records. |
| [Tenancies and rent](operations/tenancies-and-rent.md) | Rent history, graduated/index rent, deposits, and ownership changes. |
| [Bank and payments](operations/bank-and-payments.md) | Import and assign bank transactions, export CSV lists, dun arrears, and read rent figures. |
| [Statements](operations/statements.md) | Heating allocation, annual figures, BK batches, advance proposals, and prior-year plans. |
| [WEG operations](operations/weg.md) | Create special levies and export meeting invitations. |
| [Meters](operations/meters.md) | Record meters/readings and derive statement consumption. |
| [Tasks and documents](operations/tasks-and-documents.md) | Tasks, originals, correspondence, DATEV/SEPA files, and CSV master-data import. |
| [Accounting-scope invariants](operations/accounting-scope-invariants.md) | Staged scope and authorization rules. |
| [Accounting-scope setup](operations/accounting-scope-setup.md) | Review scope classification, grants, activation, and recovery. |
| [Financial input coverage](operations/financial-input-coverage.md) | Attest complete calculation inputs and preserve their provenance. |
| [Property-data audiences](operations/property-data-domains.md) | Prepare source audiences, grants, classification, and access boundaries. |

| Contributing and agents | Purpose |
| --- | --- |
| [Contributing](../CONTRIBUTING.md) | Development environment, checks, issues, commits, and pull requests. |
| [Repository rules](../AGENTS.md) | Workspace, safety, scope, and verification requirements. |
| [Frontend design contract](../DESIGN.md) | UI components, copy, layouts, formats, and screenshot references. |
| [Logo](assets/brand/README.md) | Wordmark, standalone mark, app icon, and their size and colour rules. |
| [Executable scenarios](../features/README.md) | Gherkin specifications, bindings, and scenario execution. |
| [Acceptance from the user's side](acceptance/README.md) | Core journeys with hand-worked expected values, sample data, and the user check. |

| Research and reference | Purpose |
| --- | --- |
| [Software trial evaluation](research/software-trials-2026-10-01/README.md) | Comparative trial observations, evidence, and limitations. |
