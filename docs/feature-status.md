# Feature status

Current feature catalog of Verwalino by area, compared with the reference product described in
[domain-reference.md](domain-reference.md). Status as of 2026-10-30, migration head `0141`.
Status: ✅ implemented and reachable in the running app, 🟡 partial (reason given), ❌ not implemented.
Reference product: whether the reference software offers the feature (yes/partial/no, n/a = not a
reference feature). Short paths: `api/ services/ engine/ models/ workers/ core/` are under
`backend/app/`, `pages/ components/ lib/` under `frontend/src/`; other paths are relative to the
repository root. Task IDs (`T…`) are the historical task numbers of the former MVP task list (see git
history). Update this page in the same PR whenever a feature's status changes.

## Access and organization

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Deployment mode | n/a | ✅ validated selfhost/hosted policy, public mode/version metadata, setup gate; hosted features remain deferred | `core/config.py`, `core/deployment_policy.py`, issue #18 |
| Browser first-run setup | n/a | ✅ selfhost-only, one-time installer token, 24-hour expiry, atomic first organization/admin; real-stack acceptance pending | `api/setup.py`, `pages/SetupPage.tsx`, `scripts/self-host.sh`, issue #8 |
| Login/logout | yes | ✅ | `api/auth.py`, `pages/LoginPage.tsx` |
| Create users | yes | ✅ admin-only form and user management | `api/users.py`, `pages/UsersPage.tsx`, T06, T57 |
| Roles admin/clerk/read-only | yes | ✅ admin can change role and deactivate; write buttons hidden for read-only | `core/rbac.py` (`OWNER_ADMIN`, `CLERK`, `READ_ONLY`), T57 |
| Change password | yes | ✅ own change and admin reset, sessions revoked | `api/auth.py` (`POST /api/auth/password`), `api/users.py` (`/reset-password`), `components/Topbar.tsx`, T14, T57 |
| Management mandates with documents | no | ✅ edit/end, bound definition protected | `api/mandates.py`, `pages/MandateDetailPage.tsx`, T55 |
| Source governance (scopes, approvals, quarantines) | no | ✅ non-blocking in the default mode `PREPARATION`; menu entries grouped under the admin-only "Erweitert" group | `services/financial_scope_service.py`, `components/Sidebar.tsx`, T11 |
| Dashboard | yes | ✅ deadlines, due tasks, monthly rent and vacancy KPIs | `pages/DashboardPage.tsx`, `components/DashboardKpis.tsx`, `api/reports.py` (`/dashboard-kpis`), `services/dashboard_kpi_service.py`, T42, T52 |

## Properties and units

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Create/edit property (rental/WEG/SEV/mixed) | yes | ✅ including reference-protected deletion | `api/properties.py`, `pages/PropertiesPage.tsx`, T56 |
| Year built, total area | yes | ✅ optional, stored explicitly | `models/property.py` (`construction_year`, `total_area`), `pages/PropertyDetailPage.tsx`, T55 |
| Photos | yes | ❌ | – |
| Units (type, area, heated area) | yes | ✅ | `api/units.py`, `pages/PropertyDetailPage.tsx` |
| Record MEA (co-ownership share) | yes | ✅ scale confirmed by hand ("MEA-Skala bestätigen") or via declaration import; units need a confirmed scale (409 otherwise) | `api/properties.py` (`POST /{id}/mea-scale/confirm`), `api/units.py`, `pages/PropertyDetailPage.tsx`, T05 |
| Declaration of division (Teilungserklärung) import (AI, review) | no | ✅ needs feature flag and OpenAI key | `api/declaration_imports.py`, `workers/declaration_import.py`, `pages/DeclarationImportPage.tsx` |
| Property knowledge with sources | no | ✅ | `services/property_knowledge_service.py`, `pages/PropertyKnowledgePage.tsx` |
| Meters, meter readings | yes | ✅ | `api/meters.py`, `models/meter.py`, `components/MeterPanel.tsx`, T29, T30 |
| Master data CSV import | yes | ✅ template, preview, atomic creation of properties/units/contacts | `services/master_import_service.py`, `api/properties.py` (`/import/*`), `components/MasterImport.tsx`, T51 |

## People and tenancies

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Contacts, tenant/owner profile | yes | ✅ | `api/persons.py`, `api/tenant_profiles.py`, `api/owner_profiles.py`, `pages/PersonDetailPage.tsx` |
| Vendors | yes | ✅ edit/end with contact source right | `api/vendors.py`, T55 |
| Ownership with share | yes | ✅ end ownership ("Eigentum beenden") and new owner from the next day | `api/ownership.py` (`PATCH /{id}`), `pages/UnitDetailPage.tsx`, T26 |
| Create lease (base rent, operating cost advance, deposit) | yes | ✅ | `api/leases.py`, `pages/UnitDetailPage.tsx` |
| Change/end lease, tenant change | yes | ✅ end and note always editable; amounts locked (409) once payments are booked or a settlement is final | `api/leases.py` (`PATCH /{id}`), `pages/UnitDetailPage.tsx`, T04 |
| Rent change with effective date | yes | ✅ | `models/lease_rent_change.py`, `api/leases.py` (`/rent-changes`), `engine/mietkonto.py`, T23, T24 |
| Graduated (Staffel) / index rent | yes | ✅ | `api/leases.py` (`/rent-changes/staffel`, `/rent-changes/index`), `engine/index_rent.py`, T43, T44 |
| Security deposit management | partial | 🟡 target, paid and refunded tracked; no installments, no recording of interest credits | `engine/mietkonto.py` (`deposit_statement`), `api/payments.py` (`/deposits`), `pages/DepositsPage.tsx`, T25 |
| Lease template, handover protocol | yes | ❌ only generic letter templates exist (see Documents, communication, reports) | – |

## Tenant ledger, bank and payments

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Bank accounts | yes | ✅ edit/end, historical references kept | `api/bank_accounts.py`, `pages/BankAccountsPage.tsx`, T55 |
| Expected charges (Sollstellung) for rent/operating costs, day-accurate | yes | ✅ derived live, including rent history | `engine/mietkonto.py` (`tenant_expected_charges`) |
| Record, book, reverse payment | yes | ✅ drafts editable | `api/payments.py`, `pages/PaymentDetailPage.tsx`, T07 |
| Tenant/owner ledger (Mietkonto), open items | yes | ✅ | `api/payments.py` (`/mietkonto/*`, `/open-items`), `pages/MietkontoPage.tsx`, `pages/OpenItemsPage.tsx` |
| Bank statement import CSV/CAMT | partial | ✅ CSV (generic plus DKB profiles) and CAMT.053 (.001.02/.08, XML/ZIP), preview, duplicate detection | `engine/bank_import.py`, `services/bank_import_service.py`, `api/bank_accounts.py` (`POST /{id}/imports`), `pages/BankTransactionsPage.tsx`, T15–T19 |
| Bank retrieval PSD2/FinTS | yes | ❌ | – |
| Match suggestions, split booking | yes | ✅ apply suggestion, split, ignore | `engine/bank_matching.py`, `api/bank_transactions.py` (`/assign`, `/ignore`), `pages/BankTransactionsPage.tsx`, T20–T22 |
| SEPA direct debit | yes | ✅ pain.008 for monthly rent, creditor ID and tenant mandates | `services/sepa_export.py`, `api/sepa.py`, `components/SepaExport.tsx`, T50 |

## Vouchers and costs

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Cost item entry/confirm | yes | ✅ database values with German labels | `api/cost_entries.py`, `pages/CostEntriesPage.tsx`, `lib/costVocabulary.ts`, T02 |
| BetrKV cost categories | yes | ✅ fixed seeded set | `api/cost_categories.py`, migration `0005` |
| Voucher upload, costs from voucher | yes | ✅ | `api/invoice_documents.py`, `pages/InvoiceDocumentDetailPage.tsx` |
| OCR, e-invoice XML | yes | ✅ runs as the `invoice-worker` service in the production Compose file; automatic OCR is off in the portable stack | `workers/invoice_extraction.py`, `core/invoice_ocr.py`, `core/einvoice.py`, `docker-compose.prod.yml`, T10 |
| Inbox (Posteingang) with review chain | partial | 🟡 API, pages and pipeline stages exist, but `workers/workflow_runner.py` (intake stages) is not started by any Compose service; end-to-end operation unverified | `api/intake_batches.py`, `workers/workflow_runner.py`, `workers/intake_extraction.py`, `pages/InboxPage.tsx` |
| Clarification cases (Klärungsfälle) | no | ✅ | `api/exceptions.py`, `pages/ExceptionsPage.tsx` |
| GoBD: immutability, reversal, correction | n/a | ✅ | `services/cost_entry_service.py` |

## Operating and heating cost statements

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Operating cost statement per lease: compute, finalize, PDF, correction | yes | ✅ | `api/settlements.py`, `services/settlement_service.py`, `services/settlement_pdf.py` |
| Keys area/MEA/units, pro rata, vacancy | yes | ✅ | `engine/distribution.py` |
| Keys consumption/persons/external/manual | yes | ✅ inputs shown and sent with the database key names | `pages/SettlementDetailPage.tsx`, `lib/costVocabulary.ts`, T03 |
| Advance payments from payments | yes | ✅ any period, including across the year boundary | `services/settlement_service.py` (`set_advance_payments_derived`), `services/payment_service.py` (`derive_nk_advance_total_for_period`), `api/settlements.py` (`/advance-payments-derived`), T08 |
| Batch run per property | yes | ✅ drafts for all leases, existing ones skipped | `api/settlements.py` (`POST /batch`), `pages/SettlementsPage.tsx`, T27 |
| Suggestion for a new advance payment | yes | ✅ accepting appends a rent change | `api/settlements.py` (`/advance-proposal`), `pages/SettlementDetailPage.tsx` ("Vorauszahlungsvorschlag"), T28 |
| Heating costs per HeizkostenV | yes | ✅ internal basic/consumption split or external billing | `engine/heizkosten.py`, `engine/heating_settlement.py`, `services/heating_input_service.py`, `components/HeatingInputs.tsx`, T45, T46 |
| Consumption from meters | yes | ✅ prefilled from readings, manual entry otherwise | `engine/meter_consumption.py`, `api/settlements.py` (`/consumption-suggestions`, `/heating-consumption`), T31 |

## WEG (homeowners' association)

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Economic plan, Hausgeld expected amounts, PDF | no | ✅ | `api/economic_plans.py`, `services/payment_service.py`, `services/wirtschaftsplan_pdf.py` |
| Economic plan prefilled from the previous year | no | ✅ preview, then draft from last year's actual costs per category | `api/economic_plans.py` (`/previous/preview`, `/previous`), `components/PreviousPlan.tsx`, T54 |
| Annual settlement, PDF | no | ✅ | `api/weg_settlements.py`, `services/weg_jahresabrechnung_pdf.py` |
| Adopt paid Hausgeld | no | ✅ | `api/weg_settlements.py` (`/paid-advances-derived`), `services/weg_settlement_service.py` (`derive_paid_advances`), `pages/WegSettlementDetailPage.tsx`, T09 |
| Maintenance reserve (Erhaltungsrücklage) | no | 🟡 opening balance and withdrawals are manual inputs; no reserve ledger | `engine/weg.py`, `pages/WegSettlementDetailPage.tsx` |
| Asset report (Vermögensbericht) § 28 Abs. 4 WEG | no | ✅ | `engine/vermoegensbericht.py`, `services/vermoegensbericht_pdf.py`, `pages/AssetReportsPage.tsx` |
| Special levy (Sonderumlage) | no | ✅ distributed by MEA as owner expected amount `SONDERZAHLUNG`, appears in the owner ledger and can be dunned | `models/special_levy.py`, `api/special_levies.py`, `services/special_levy_service.py`, `pages/SpecialLeviesPage.tsx`, T32, T33 |
| Mid-year owner change | no | ✅ annual settlement is day-accurate per owner | `api/ownership.py`, `services/weg_settlement_service.py`, T26 |
| Owners' meeting, minutes, resolution register | no | ✅ | `api/meetings.py`, `pages/MeetingDetailPage.tsx`, `pages/ResolutionRegisterPage.tsx` |
| Meeting invitation as PDF | no | ✅ one page per owner, hint for less than three weeks' notice (§ 24 Abs. 4 WEG) | `api/meetings.py` (`/{id}/invitation.pdf`), `services/meeting_pdf.py`, T34 |

## Dunning, deadlines, tasks

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Dunning notice, 3 levels, fee, PDF | partial | ✅ | `api/dunning.py`, `engine/dunning.py`, `services/dunning_pdf.py` |
| Default interest, selected dunning run | yes | ✅ interest at base rate + 5 pp (§ 288 Abs. 1 BGB) shown on the PDF; preview of arrears above a threshold, selected ones become drafts in one step | `engine/dunning.py`, `services/dunning_batch_service.py`, `api/dunning.py` (`/batch/preview`, `/batch`), `components/DunningBatch.tsx`, T35, T53 |
| Deadline monitor (Fristenmonitor) | partial | ✅ derived | `api/fristen.py`, `engine/fristen.py`, `pages/FristenmonitorPage.tsx` |
| Tasks, follow-ups | yes | ✅ create from property and unit, due tasks on the dashboard | `models/task.py`, `api/tasks.py`, `services/task_service.py`, `pages/TasksPage.tsx`, T41, T42 |

## Documents, communication, reports

| Feature | Reference product | Status | Location |
| --- | --- | --- | --- |
| Documents per property/unit/person | yes | ✅ "Dokumente" panel: upload, open, archive | `models/attachment.py`, `api/attachments.py`, `components/AttachmentsPanel.tsx`, T36, T37 |
| Letter templates, dispatch by e-mail/letter | yes | ✅ placeholder templates produce one PDF per recipient; e-mail for settlement, dunning notice and invitation (SMTP disabled until configured) | `api/letters.py`, `services/letter_service.py`, `pages/LettersPage.tsx`, `api/mail.py`, `services/mail_service.py`, `components/MailDispatch.tsx`, T47, T48 |
| Tenant portal | yes | ❌ | – |
| Year overview per property (Anlage V) | yes | 🟡 income by payment type and expenses by category with CSV; no Anlage V line mapping | `api/reports.py` (`/property-year`, `/property-year.csv`), `services/report_service.py`, `pages/ReportsPage.tsx`, T38, T39 |
| CSV/DATEV export | yes | ✅ CSV for payments, costs and year overview; DATEV EXTF batch with explicitly configured SKR03/SKR04 accounts | `api/payments.py` (`/export.csv`), `api/cost_entries.py` (`/export.csv`), `api/reports.py` (`/datev.csv`), `services/datev_export.py`, `components/DatevExport.tsx`, T39, T40, T49 |
| GDPR (DSGVO) access/erasure | n/a | ✅ access on screen only | `api/dsgvo.py`, `services/dsgvo_service.py`, `components/DsgvoPanel.tsx` |

## Gaps

- Source access protection (Access and organization): implemented but off (mode `PREPARATION`), and its admin screens are five separate menu items (#73).
- Photos (Properties and units): no photo upload or display for properties or units (#39).
- Advance payments (People and tenancies): a lease has one combined advance; operating-cost and heating advances are not separate (#67).
- Security deposit management (People and tenancies): no installments with due dates (#70) and no booking of interest credits (#38).
- VAT-liable letting (People and tenancies): no VAT on rent, advances and statements for units with the VAT option (#71); no input VAT apportionment (#72).
- Lease template, handover protocol (People and tenancies): neither a lease contract template nor a handover protocol exists; only generic letter templates (#37).
- Bank retrieval PSD2/FinTS (Tenant ledger, bank and payments): no automatic bank statement retrieval; import is by CSV/CAMT file only (#40).
- Inbox (Posteingang) with review chain (Vouchers and costs): intake pipeline stages are not started by any Compose service (`workers/workflow_runner.py`); verify end-to-end operation and add a worker service (#35).
- Settlement period (Operating and heating cost statements): the property's period start month is stored but statements and economic plans do not use it; periods are not validated for length or overlap (#68).
- Maintenance reserve (Erhaltungsrücklage) (WEG): opening balance and withdrawals are manual inputs; no reserve ledger or bank-account tracking (#36).
- Dunning fees and default interest (Dunning, deadlines, tasks): fees are fixed constants and interest needs a base rate typed per notice; neither is configurable per organization (#69).
- Tenant portal (Documents, communication, reports): not built; planned right after the hosted launch (#41).
- Year overview per property (Anlage V) (Documents, communication, reports): no Anlage V line mapping or form-ready output (#34).
