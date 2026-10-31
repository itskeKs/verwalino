# Feature status

Current feature catalog of Verwalino by area, compared with the reference product described in
[domain-reference.md](domain-reference.md). Status as of 2026-10-31, migration head `0142`.
Status: ✅ implemented and reachable in the running app, 🟡 partial (reason given), ❌ not implemented.
Reference product: whether the reference software offers the feature (yes/partial/no, n/a = not a
reference feature). Short paths: `api/ services/ engine/ models/ workers/ core/` are under
`backend/app/`, `pages/ components/ lib/` under `frontend/src/`; other paths are relative to the
repository root. Task IDs (`T…`) are the historical task numbers of the former MVP task list (see git
history). Update this page in the same PR whenever a feature's status changes.

The hosted instance is being set up on the hosted server (#6) through `scripts/self-host.sh`
and the [hosted deploy workflow](../DEPLOY.md#hosted-server). It shares the portable
stack's limits: declaration import/Q&A, SMTP and automatic invoice OCR are disabled.

## Access and organization

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Deployment mode | n/a | ✅ validated selfhost/hosted policy, backend-owned setup gate; hosted features remain deferred | `core/config.py`, `core/deployment_policy.py`, issue #18 |
| Browser first-run setup | n/a | ✅ selfhost-only, one-time installer token, 24-hour expiry, atomic first organization/admin; real-stack acceptance pending | `api/setup.py`, `pages/SetupPage.tsx`, `scripts/self-host.sh`, issue #8 |
| Manual diagnostics reports | n/a | ✅ admin download and installer bundle with structural redacted logs; manual attachment only, fake-Docker acceptance | `diagnostics.py`, `api/diagnostics.py`, `pages/UsersPage.tsx`, `scripts/self-host.sh`, issue #17 |
| One-command release installation | n/a | 🟡 installer and signed release-bundle workflow prepared; first release assets and fresh-VM setup/login/restart pending | `install.sh`, `.github/workflows/release-image.yaml`, [guide](../DEPLOY.md#install-in-one-command), issue #12 |
| Install matrix | n/a | 🟡 four checkout-install scenarios and nightly/tag workflow prepared; first real matrix run, Debian, update and backup/restore pending; arm64 opt-in | `scripts/install-scenarios.sh`, `.github/workflows/install-matrix.yaml`, [coverage and reports](../DEPLOY.md#install-matrix), issue #13 |
| Request IDs and opt-in error tracking | n/a | ✅ response/log correlation always enabled; API error events scrubbed before sending, tracking off by default | `core/request_id.py`, `core/logging.py`, `core/error_tracking.py`, `core/scrub.py`, `DEPLOY.md`, issue #16 |
| Login/logout | yes | ✅ | `api/auth.py`, `pages/LoginPage.tsx` |
| Login rate limiting | n/a | ✅ persisted IP/email lockout, mode-specific limits, hosted escalation, generic failures; atomic attempt reservations | `core/rate_limit.py`, `core/deployment_policy.py`, issue #20 |
| Create users | yes | ✅ admin-only form and user management | `api/users.py`, `pages/UsersPage.tsx`, T06, T57, [usage](operations/users-and-access.md) |
| Roles admin/clerk/read-only | yes | ✅ admin can change role and deactivate; write buttons hidden for read-only | `core/rbac.py` (`OWNER_ADMIN`, `CLERK`, `READ_ONLY`), T57, [usage](operations/users-and-access.md) |
| Change password | yes | ✅ own change and admin reset, sessions revoked | `api/auth.py` (`POST /api/auth/password`), `api/users.py` (`/reset-password`), `components/Topbar.tsx`, T14, T57, [usage](operations/users-and-access.md) |
| Management mandates with documents | no | ✅ edit/end, bound definition protected | `api/mandates.py`, `pages/MandateDetailPage.tsx`, T55, [usage](operations/selector-pages.md) |
| Source governance (scopes, approvals, quarantines) | no | ✅ one admin permissions area with state, next step, team rights, preparation and review lists; guarded activation and emergency pause; new organizations start in preparation; activation runbook available | `pages/PermissionsPage.tsx`, `components/SourceSecurityPanel.tsx`, [runbook](operations/accounting-scope-setup.md#owner-activation-runbook), #73 |
| Dashboard | yes | ✅ deadlines, due tasks, monthly rent and vacancy KPIs | `pages/DashboardPage.tsx`, `components/DashboardKpis.tsx`, `api/reports.py` (`/dashboard-kpis`), `services/dashboard_kpi_service.py`, T42, T52, [usage](operations/bank-and-payments.md) |

## Properties and units

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Create/edit property (rental/WEG/SEV/mixed) | yes | ✅ including reference-protected deletion | `api/properties.py`, `pages/PropertiesPage.tsx`, T56, [usage](operations/selector-pages.md) |
| Year built, total area | yes | ✅ optional, stored explicitly | `models/property.py` (`construction_year`, `total_area`), `pages/PropertyDetailPage.tsx`, T55, [usage](operations/selector-pages.md) |
| Photos | yes | ❌ | – |
| Units (type, area, heated area) | yes | ✅ | `api/units.py`, `pages/PropertyDetailPage.tsx` |
| Record MEA (co-ownership share) | yes | ✅ scale confirmed by hand ("MEA-Skala bestätigen") or via declaration import; units need a confirmed scale (409 otherwise) | `api/properties.py` (`POST /{id}/mea-scale/confirm`), `api/units.py`, `pages/PropertyDetailPage.tsx`, T05 |
| Declaration of division (Teilungserklärung) import (AI, review) | no | ✅ needs feature flag and OpenAI key | `api/declaration_imports.py`, `workers/declaration_import.py`, `pages/DeclarationImportPage.tsx` |
| Property knowledge with sources | no | ✅ | `services/property_knowledge_service.py`, `pages/PropertyKnowledgePage.tsx` |
| Meters, meter readings | yes | ✅ | `api/meters.py`, `models/meter.py`, `components/MeterPanel.tsx`, T29, T30, [usage](operations/meters.md) |
| Master data CSV import | yes | ✅ template, preview, atomic creation of properties/units/contacts | `services/master_import_service.py`, `api/properties.py` (`/import/*`), `components/MasterImport.tsx`, T51, [usage](operations/tasks-and-documents.md) |

## People and tenancies

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Contacts, tenant/owner profile | yes | ✅ | `api/persons.py`, `api/tenant_profiles.py`, `api/owner_profiles.py`, `pages/PersonDetailPage.tsx` |
| Vendors | yes | ✅ edit/end with contact source right | `api/vendors.py`, T55, [usage](operations/selector-pages.md) |
| Ownership with share | yes | ✅ end ownership ("Eigentum beenden") and new owner from the next day | `api/ownership.py` (`PATCH /{id}`), `pages/UnitDetailPage.tsx`, T26, [usage](operations/tenancies-and-rent.md) |
| Create lease (base rent, operating cost advance, deposit) | yes | ✅ | `api/leases.py`, `pages/UnitDetailPage.tsx` |
| Change/end lease, tenant change | yes | ✅ end and note always editable; amounts locked (409) once payments are booked or a settlement is final | `api/leases.py` (`PATCH /{id}`), `pages/UnitDetailPage.tsx`, T04 |
| Rent change with effective date | yes | ✅ | `models/lease_rent_change.py`, `api/leases.py` (`/rent-changes`), `engine/mietkonto.py`, T23, T24, [usage](operations/tenancies-and-rent.md) |
| Graduated (Staffel) / index rent | yes | ✅ | `api/leases.py` (`/rent-changes/staffel`, `/rent-changes/index`), `engine/index_rent.py`, T43, T44, [usage](operations/tenancies-and-rent.md) |
| Security deposit management | partial | 🟡 target, paid and refunded tracked; no installments, no recording of interest credits | `engine/mietkonto.py` (`deposit_statement`), `api/payments.py` (`/deposits`), `pages/DepositsPage.tsx`, T25, [usage](operations/tenancies-and-rent.md) |
| Lease template, handover protocol | yes | ✅ lease-filled contract starting point with legal-review caveat; move-in/out protocol records unit readings and retains an immutable PDF | #37, `services/lease_document_service.py`, `components/LeaseDocuments.tsx`, [usage](operations/tasks-and-documents.md#lease-contracts-and-handovers-37) |

## Tenant ledger, bank and payments

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Bank accounts | yes | ✅ edit/end, historical references kept | `api/bank_accounts.py`, `pages/BankAccountsPage.tsx`, T55, [usage](operations/selector-pages.md) |
| Expected charges (Sollstellung) for rent/operating costs, day-accurate | yes | ✅ derived live, including rent history | `engine/mietkonto.py` (`tenant_expected_charges`) |
| Record, book, reverse payment | yes | ✅ drafts editable | `api/payments.py`, `pages/PaymentDetailPage.tsx`, T07 |
| Tenant/owner ledger (Mietkonto), open items | yes | ✅ | `api/payments.py` (`/mietkonto/*`, `/open-items`), `pages/MietkontoPage.tsx`, `pages/OpenItemsPage.tsx` |
| Bank statement import CSV/CAMT | partial | ✅ CSV (generic plus DKB profiles) and CAMT.053 (.001.02/.08, XML/ZIP), preview, duplicate detection | `engine/bank_import.py`, `services/bank_import_service.py`, `api/bank_accounts.py` (`POST /{id}/imports`), `pages/BankTransactionsPage.tsx`, T15–T19, [usage](operations/bank-and-payments.md) |
| Bank retrieval PSD2/FinTS | yes | ❌ | – |
| Match suggestions, split booking | yes | ✅ apply suggestion, split, ignore | `engine/bank_matching.py`, `api/bank_transactions.py` (`/assign`, `/ignore`), `pages/BankTransactionsPage.tsx`, T20–T22, [usage](operations/bank-and-payments.md) |
| SEPA direct debit | yes | ✅ pain.008 for monthly rent, creditor ID and tenant mandates | `services/sepa_export.py`, `api/sepa.py`, `components/SepaExport.tsx`, T50, [usage](operations/tasks-and-documents.md) |

## Vouchers and costs

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Cost item entry/confirm | yes | ✅ database values with German labels | `api/cost_entries.py`, `pages/CostEntriesPage.tsx`, `lib/costVocabulary.ts`, T02 |
| BetrKV cost categories | yes | ✅ fixed seeded set | `api/cost_categories.py`, migration `0005` |
| Voucher upload, costs from voucher | yes | ✅ | `api/invoice_documents.py`, `pages/InvoiceDocumentDetailPage.tsx` |
| OCR, e-invoice XML | yes | ✅ runs as the `invoice-worker` service in the production Compose file; automatic OCR is off in the portable stack | `workers/invoice_extraction.py`, `core/invoice_ocr.py`, `core/einvoice.py`, `docker-compose.prod.yml`, T10 |
| Inbox (Posteingang) with review chain | partial | 🟡 `inbox-worker` configured in both Compose files (portable profile `inbox`); upload-to-cost scenario added; real-stack acceptance still requires a working ClamAV installation and signatures | [Operational flow](operations/inbox-review-chain.md), `workers/workflow_runner.py`, `pages/InboxPage.tsx`, [#35](https://github.com/itskeKs/verwalino/issues/35) |
| Clarification cases (Klärungsfälle) | no | ✅ | `api/exceptions.py`, `pages/ExceptionsPage.tsx` |
| GoBD: immutability, reversal, correction | n/a | ✅ | `services/cost_entry_service.py` |

## Operating and heating cost statements

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Operating cost statement per lease: compute, finalize, PDF, correction | yes | ✅ | `api/settlements.py`, `services/settlement_service.py`, `services/settlement_pdf.py` |
| Keys area/MEA/units, pro rata, vacancy | yes | ✅ | `engine/distribution.py` |
| Keys consumption/persons/external/manual | yes | ✅ inputs shown and sent with the database key names | `pages/SettlementDetailPage.tsx`, `lib/costVocabulary.ts`, T03 |
| Advance payments from payments | yes | ✅ any period, including across the year boundary | `services/settlement_service.py` (`set_advance_payments_derived`), `services/payment_service.py` (`derive_nk_advance_total_for_period`), `api/settlements.py` (`/advance-payments-derived`), T08 |
| Batch run per property | yes | ✅ drafts for all leases, existing ones skipped | `api/settlements.py` (`POST /batch`), `pages/SettlementsPage.tsx`, T27, [usage](operations/statements.md) |
| Suggestion for a new advance payment | yes | ✅ accepting appends a rent change | `api/settlements.py` (`/advance-proposal`), `pages/SettlementDetailPage.tsx` ("Vorauszahlungsvorschlag"), T28, [usage](operations/statements.md) |
| Heating costs per HeizkostenV | yes | ✅ internal basic/consumption split or external billing | `engine/heizkosten.py`, `engine/heating_settlement.py`, `services/heating_input_service.py`, `components/HeatingInputs.tsx`, T45, T46, [usage](operations/statements.md) |
| Consumption from meters | yes | ✅ prefilled from readings, manual entry otherwise | `engine/meter_consumption.py`, `api/settlements.py` (`/consumption-suggestions`, `/heating-consumption`), T31, [usage](operations/meters.md) |

## WEG (homeowners' association)

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Economic plan, Hausgeld expected amounts, PDF | no | ✅ | `api/economic_plans.py`, `services/payment_service.py`, `services/wirtschaftsplan_pdf.py` |
| Economic plan prefilled from the previous year | no | ✅ preview, then draft from last year's actual costs per category | `api/economic_plans.py` (`/previous/preview`, `/previous`), `components/PreviousPlan.tsx`, T54, [usage](operations/statements.md) |
| Annual settlement, PDF | no | ✅ | `api/weg_settlements.py`, `services/weg_jahresabrechnung_pdf.py` |
| Adopt paid Hausgeld | no | ✅ | `api/weg_settlements.py` (`/paid-advances-derived`), `services/weg_settlement_service.py` (`derive_paid_advances`), `pages/WegSettlementDetailPage.tsx`, T09 |
| Maintenance reserve (Erhaltungsrücklage) | no | 🟡 opening balance and withdrawals are manual inputs; no reserve ledger | `engine/weg.py`, `pages/WegSettlementDetailPage.tsx` |
| Asset report (Vermögensbericht) § 28 Abs. 4 WEG | no | ✅ | `engine/vermoegensbericht.py`, `services/vermoegensbericht_pdf.py`, `pages/AssetReportsPage.tsx` |
| Special levy (Sonderumlage) | no | ✅ distributed by MEA as owner expected amount `SONDERZAHLUNG`, appears in the owner ledger and can be dunned | `models/special_levy.py`, `api/special_levies.py`, `services/special_levy_service.py`, `pages/SpecialLeviesPage.tsx`, T32, T33, [usage](operations/weg.md) |
| Mid-year owner change | no | ✅ annual settlement is day-accurate per owner | `api/ownership.py`, `services/weg_settlement_service.py`, T26, [usage](operations/tenancies-and-rent.md) |
| Owners' meeting, minutes, resolution register | no | ✅ | `api/meetings.py`, `pages/MeetingDetailPage.tsx`, `pages/ResolutionRegisterPage.tsx` |
| Meeting invitation as PDF | no | ✅ one page per owner, hint for less than three weeks' notice (§ 24 Abs. 4 WEG) | `api/meetings.py` (`/{id}/invitation.pdf`), `services/meeting_pdf.py`, T34, [usage](operations/weg.md) |

## Dunning, deadlines, tasks

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Dunning notice, 3 levels, fee, PDF | partial | ✅ | `api/dunning.py`, `engine/dunning.py`, `services/dunning_pdf.py` |
| Default interest, selected dunning run | yes | ✅ interest at base rate + 5 pp (§ 288 Abs. 1 BGB) shown on the PDF; preview of arrears above a threshold, selected ones become drafts in one step | `engine/dunning.py`, `services/dunning_batch_service.py`, `api/dunning.py` (`/batch/preview`, `/batch`), `components/DunningBatch.tsx`, T35, T53, [usage](operations/bank-and-payments.md) |
| Deadline monitor (Fristenmonitor) | partial | ✅ derived | `api/fristen.py`, `engine/fristen.py`, `pages/FristenmonitorPage.tsx` |
| Tasks, follow-ups | yes | ✅ create from property and unit, due tasks on the dashboard | `models/task.py`, `api/tasks.py`, `services/task_service.py`, `pages/TasksPage.tsx`, T41, T42, [usage](operations/tasks-and-documents.md) |

## Documents, communication, reports

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Documents per property/unit/person | yes | ✅ "Dokumente" panel: upload, open, archive | `models/attachment.py`, `api/attachments.py`, `components/AttachmentsPanel.tsx`, T36, T37, [usage](operations/tasks-and-documents.md) |
| Letter templates, dispatch by e-mail/letter | yes | ✅ placeholder templates produce one PDF per recipient; e-mail for settlement, dunning notice and invitation (SMTP disabled until configured) | `api/letters.py`, `services/letter_service.py`, `pages/LettersPage.tsx`, `api/mail.py`, `services/mail_service.py`, `components/MailDispatch.tsx`, T47, T48, [usage](operations/tasks-and-documents.md) |
| Tenant portal | yes | ❌ | – |
| Year overview per property (Anlage V) | yes | ✅ income/expense overview plus default Anlage V mapping aid, unassigned/excluded items, exact reconciliation and separate CSV; tax review required | `api/reports.py` (`/property-year`, `/property-year.csv`, `/property-year/anlage-v`, `/property-year/anlage-v.csv`), `services/report_service.py`, `engine/anlage_v.py`, `pages/ReportsPage.tsx`, [M6 §6.12](m6-zahlungen-mietkonto.md#anlage-v-mapping-aid-34), T38, T39, #34, [usage](operations/statements.md) |
| CSV/DATEV export | yes | ✅ CSV for payments, costs and year overview; DATEV EXTF batch with explicitly configured SKR03/SKR04 accounts | `api/payments.py` (`/export.csv`), `api/cost_entries.py` (`/export.csv`), `api/reports.py` (`/datev.csv`), `services/datev_export.py`, `components/DatevExport.tsx`, T39, T40, T49, [usage](operations/tasks-and-documents.md) |
| GDPR (DSGVO) access/erasure | n/a | ✅ access on screen only | `api/dsgvo.py`, `services/dsgvo_service.py`, `components/DsgvoPanel.tsx` |

## Gaps

- Photos (Properties and units): no photo upload or display for properties or units (#39).
- Advance payments (People and tenancies): a lease has one combined advance; operating-cost and heating advances are not separate (#67).
- Security deposit management (People and tenancies): no installments with due dates (#70) and no booking of interest credits (#38).
- VAT-liable letting (People and tenancies): no VAT on rent, advances and statements for units with the VAT option (#71); no input VAT apportionment (#72).
- Bank retrieval PSD2/FinTS (Tenant ledger, bank and payments): no automatic bank statement retrieval; import is by CSV/CAMT file only (#40).
- Inbox (Posteingang) with review chain (Vouchers and costs): worker service and upload-to-cost scenario are present; real-stack acceptance with ClamAV signatures remains pending ([#35](https://github.com/itskeKs/verwalino/issues/35), [runbook](operations/inbox-review-chain.md)).
- Settlement period (Operating and heating cost statements): the property's period start month is stored but statements and economic plans do not use it; periods are not validated for length or overlap (#68).
- Maintenance reserve (Erhaltungsrücklage) (WEG): opening balance and withdrawals are manual inputs; no reserve ledger or bank-account tracking (#36).
- Dunning fees and default interest (Dunning, deadlines, tasks): fees are fixed constants and interest needs a base rate typed per notice; neither is configurable per organization (#69).
- Tenant portal (Documents, communication, reports): not built; planned right after the hosted launch (#41).
- Year overview per property (Anlage V) (Documents, communication, reports): mapping aid available; no automatic tax-year cash-outflow calculation, ownership allocation, organization overrides or ELSTER submission.
