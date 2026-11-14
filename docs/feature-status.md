# Feature status

Current feature catalog of Verwalino by area. Status as of 2026-11-07, migration head `0178`.
Status: ✅ implemented and reachable in the running app, 🟡 partial (reason given), ❌ not implemented.
Short paths: `api/ services/ engine/ models/ workers/ core/` are under
`backend/app/`, `pages/ components/ lib/` under `frontend/src/`; other paths are relative to the
repository root. Task IDs (`T…`) are the historical task numbers of the former MVP task list (see git
history). Update this page in the same PR whenever a feature's status changes.

The hosted instance uses the portable base through the [hosted deploy workflow](../DEPLOY.md#hosted-server).
The optional [production app overlay](../DEPLOY.md#production-app-mode-and-mail-opt-in) enables the
owner's mode and public-host configuration; mail also needs private SMTP settings. Declaration import and Q&A are enabled per organization in Einstellungen with its own provider key; the portable declaration worker is included. Automatic invoice OCR remains disabled in this stack.

The bilingual public website has a separate image and main-push publish/sign/deploy workflow (#24),
with an optional closed container behind Caddy. Operator enablement and the first live rollout are
pending; registration stays closed and legal review remains with #26. See the
[website hosting runbook](operations/website-hosting.md).

## Access and organization

Organization administrators configure the provider and key (encrypted at rest), one
provider-listed model, import/Q&A switches and an optional known-price monthly
limit in Einstellungen. Other roles retain personal design settings and cannot
read or change organization AI settings. There is no environment credential fallback.
The bounded provider transport/settings decision is described in [ADR 0014](adr/0014-provider-neutral-ai-runtime.md). Model correctness, vision capability and remaining live acceptance are tracked in [#399](https://github.com/itskeKs/verwalino/issues/399); provider listing alone does not certify them.


| Feature | Status | Location |
| --- | --- | --- |
| Deployment mode | ✅ validated selfhost/hosted policy, public mode/version/mail-capability metadata, setup/signup gates; managed production app overlay opt-in, live mail/signup acceptance pending; remaining hosted features deferred | `core/config.py`, `core/deployment_policy.py`, `infra/docker-compose.hosted-app.yml`, issues #18, #150; [operator steps](../DEPLOY.md#production-app-mode-and-mail-opt-in) |
| Organization quotas | ✅ hosted free constants (2 GiB, 25 MiB/upload, 20 properties, 200 units), atomic write refusals, usage card; model calls are capped only by the organization's optional monthly USD limit in Einstellungen (over existing ledgers; no limit or an unpriced model means no cap); overrides deferred | `core/quotas.py`, `services/quota_service.py`, `services/ai_settings_service.py`, `components/OrganizationQuotaCard.tsx`, issue #22 |
| Organization export/import and deletion | 🟡 owner-admin archive v2 export/import with remapping and atomic refusals; preview, fresh export, seven-day read-only deletion/cancel and operator erasure with bounded lock waits; protected historical evidence adapters and real fresh-install acceptance remain | `api/organizations.py`, `services/organization_export_service.py`, `services/organization_import_service.py`, `services/organization_deletion_service.py`, `components/OrganizationDataCard.tsx`, [format and limits](operations/organization-export.md), issue #23 |
| Browser first-run setup | ✅ selfhost-only, one-time installer token, 24-hour expiry, atomic first organization/admin; real-stack acceptance pending | `api/setup.py`, `pages/SetupPage.tsx`, `scripts/self-host.sh`, issue #8 |
| Manual diagnostics reports | ✅ admin download and installer bundle with structural redacted logs; manual attachment only, fake-Docker acceptance | `diagnostics.py`, `api/diagnostics.py`, `pages/UsersPage.tsx`, `scripts/self-host.sh`, issue #17 |
| One-command release installation | 🟡 installer and signed release-bundle workflow prepared; first release assets and fresh-VM setup/login/restart pending | `install.sh`, `.github/workflows/release-image.yaml`, [guide](../DEPLOY.md#install-in-one-command), issue #12 |
| Install matrix | 🟡 four checkout-install scenarios and nightly/tag workflow; Debian, update and backup/restore pending; arm64 opt-in | `scripts/install-scenarios.sh`, `.github/workflows/install-matrix.yaml`, [coverage and reports](../DEPLOY.md#install-matrix), issue #13 |
| Hosted staging and canary | 🟡 isolated staging/deploy-on-main, restricted mail, 15-minute production API canary and dual failure alerts implemented; first deploy, DNS, secrets and live alert checks pending | `infra/docker-compose.staging.yml`, `scripts/deploy-server.sh`, `scripts/canary.py`, [runbook](operations/staging-and-canary.md), issue #25 |
| Request IDs and opt-in error tracking | ✅ response/log correlation always enabled; API and worker error events scrubbed before sending (at most one per job attempt, tagged with worker and job ID), tracking off by default; both Compose files forward the two optional keys | `core/request_id.py`, `core/logging.py`, `core/error_tracking.py`, `workers/error_tracking.py`, `core/scrub.py`, `DEPLOY.md`, issues #16, #88 |
| Login/logout | ✅ | `api/auth.py`, `pages/LoginPage.tsx` |
| Personal design | ✅ default Klar and optional Warm per user, persistent settings for every authenticated role; shared controls, responsive top navigation, priority tasks and property cards | `api/auth.py` (`/preferences`), `models/user.py`, `pages/SettingsPage.tsx`, `design/DesignThemeContext.tsx`, [usage](operations/users-and-access.md#choosing-your-personal-design) |
| Login rate limiting | ✅ persisted IP/email lockout, mode-specific limits, hosted escalation, generic failures; atomic attempt reservations; shared signup limiter and active signup honeypot | `core/rate_limit.py`, `core/deployment_policy.py`, issue #20 |
| Second factor (TOTP) | ✅ staff accounts enrol an authenticator app (QR code or typed secret), confirm with one code and receive ten one-time recovery codes (shown once, stored hashed); login asks for the code after the password, wrong codes count for the lockout, recovery codes work once and report how many remain; an OWNER_ADMIN resets a user's factor (audited, hosted reset ends the target's sessions); the operator resets a locked-out sole administrator on the host with `admin_cli reset-second-factor` (audited with the stated reason, ends the account's sessions); hosted mode requires the factor for every staff role before the application opens, self-hosted keeps it optional; enrolment and sign-in with a code or a recovery code stay possible while the organization is in the cooling-off period of its deletion (nothing else does), so an account without a factor can enrol and then cancel the deletion | `api/auth.py` (`/second-factor`, `/login/second-factor`), `api/users.py` (`/reset-second-factor`), `admin_cli.py` (`reset-second-factor`), `services/second_factor_service.py`, `models/user_second_factor.py`, `models/user_recovery_code.py`, migrations 0188 and 0189, `components/SecondFactorEnrolment.tsx`, `pages/LoginPage.tsx`, `pages/SettingsPage.tsx`, issues #208, #657, #659 |
| Self-service signup | ✅ hosted only, captured confirmation email, 24-hour single-use token, atomic tenant/owner creation, durable cooldown and generic responses; versioned assent to the terms and AV-Vertrag recorded at confirmation (Q14 on #26); never-confirmed requests are deleted by the hosted declaration worker without sign-up traffic (24 h + 7 d eligibility, #634) | `api/signup.py`, `pages/SignupPage.tsx`, `core/legal_documents.py`, issue #19, #633 |
| Create users | ✅ admin-only form and user management | `api/users.py`, `pages/UsersPage.tsx`, T06, T57, [usage](operations/users-and-access.md) |
| User invitations | ✅ admin invites by e-mail when mail is available, 72-hour single-use link, Mailpit-verified accept page, revoke and resend audited, addresses of any organization refused, finished invitations deleted after 7 days; without mail the admin-chosen-password path stays | `api/user_invitations.py`, `pages/UsersPage.tsx`, `pages/InvitationAcceptPage.tsx`, issue #558 |
| Roles admin/clerk/read-only | ✅ admin can change role and deactivate; write buttons hidden for read-only | `core/rbac.py` (`OWNER_ADMIN`, `CLERK`, `READ_ONLY`), T57, [usage](operations/users-and-access.md) |
| Password recovery | ✅ both modes, Mailpit-verified reset and confirmation mail, one-hour single-use token, tenant-context containment, three mails/address/hour, sessions and links revoked on password changes/deactivation, bounded retention; link hidden without SMTP/public origin | `api/password_reset.py`, `pages/PasswordResetPage.tsx`, issue #21 |
| Change password | ✅ own change and admin reset, sessions revoked | `api/auth.py` (`POST /api/auth/password`), `api/users.py` (`/reset-password`), `components/Topbar.tsx`, T14, T57, [usage](operations/users-and-access.md) |
| Management mandates with documents | ✅ edit/end, bound definition protected | `api/mandates.py`, `pages/MandateDetailPage.tsx`, T55, [usage](operations/selector-pages.md) |
| Source governance (scopes, approvals, quarantines) | ✅ one admin permissions area with state, next step, team rights, preparation and review lists; guarded activation and emergency pause; new organizations start in preparation; activation runbook available | `pages/PermissionsPage.tsx`, `components/SourceSecurityPanel.tsx`, [runbook](operations/accounting-scope-setup.md#owner-activation-runbook), #73 |
| Dashboard | ✅ deadlines, due tasks, monthly rent and vacancy KPIs | `pages/DashboardPage.tsx`, `components/DashboardKpis.tsx`, `api/reports.py` (`/dashboard-kpis`), `services/dashboard_kpi_service.py`, T42, T52, [usage](operations/bank-and-payments.md) |

## Properties and units

| Feature | Status | Location |
| --- | --- | --- |
| Create/edit property (rental/WEG/SEV/mixed) | ✅ including reference-protected deletion | `api/properties.py`, `pages/PropertiesPage.tsx`, T56, [usage](operations/selector-pages.md) |
| Year built, total area | ✅ optional, stored explicitly | `models/property.py` (`construction_year`, `total_area`), `pages/PropertyDetailPage.tsx`, T55, [usage](operations/selector-pages.md) |
| Photos | ❌ | – |
| Units (name, type, area, heated area) | ✅ names over 50 characters are rejected when editing; the form keeps the entered name and shows the error at the field | `api/units.py`, `pages/PropertyDetailPage.tsx`, #214 |
| Record MEA (co-ownership share) | ✅ scale confirmed by hand ("MEA-Skala bestätigen") or via declaration import; units need a confirmed scale (409 otherwise). Property details offer confirmation and the unit MEA field for WEG/MISCH/SEV, and for MIET only with an existing recorded unit MEA (including zero); unconfirmed status reads "Skala noch nicht bestätigt" | `api/properties.py` (`POST /{id}/mea-scale/confirm`), `api/units.py`, `pages/PropertyDetailPage.tsx`, T05 |
| Organization AI settings | ✅ administrator-only provider selection (OpenAI, compatible API with base URL, Anthropic), encrypted key, full provider model list, one model for import/escalation/Q&A, separate switches, persisted endpoint request timeout and optional known-price monthly limit; no environment fallback; storing or replacing the key requires the recorded provider confirmation (who, time, text version and destination; refusal code `ORGANIZATION_AI_CONFIRMATION_REQUIRED`), a stored key without a matching confirmation stays unused until confirmed, removing the key ends the instruction and keeps the record, and destination changes are audited; key set/replace/remove and switch changes are audited as `ai_settings.updated` without the key | `api/ai_settings.py`, `services/ai_settings_service.py`, `models/organization_ai_confirmation.py`, `pages/SettingsPage.tsx`, #160, #617 |
| Declaration of division (Teilungserklärung) import (AI, review) | 🟡 organization configuration, deterministic browser twin and working recorded OpenAI evidence exist; provider transports and truthful per-run tokens/runtime/unpriced display exist (#66); model-specific evaluation and acceptance continue in #399 | `api/declaration_imports.py`, `workers/declaration_import.py`, `pages/DeclarationImportPage.tsx` |
| Property knowledge with sources | ✅ | `services/property_knowledge_service.py`, `pages/PropertyKnowledgePage.tsx` |
| Meters, meter readings | ✅ | `api/meters.py`, `models/meter.py`, `components/MeterPanel.tsx`, T29, T30, [usage](operations/meters.md) |
| Master data CSV import | ✅ template, preview, atomic creation of properties/units/contacts | `services/master_import_service.py`, `api/properties.py` (`/import/*`), `components/MasterImport.tsx`, T51, [usage](operations/tasks-and-documents.md) |

## People and tenancies

| Feature | Status | Location |
| --- | --- | --- |
| Contacts, tenant/owner profile | ✅ | `api/persons.py`, `api/tenant_profiles.py`, `api/owner_profiles.py`, `pages/PersonDetailPage.tsx` |
| Vendors | ✅ edit/end with contact source right | `api/vendors.py`, T55, [usage](operations/selector-pages.md) |
| Ownership with share | ✅ end ownership ("Eigentum beenden") and new owner from the next day | `api/ownership.py` (`PATCH /{id}`), `pages/UnitDetailPage.tsx`, T26, [usage](operations/tenancies-and-rent.md) |
| Create lease (base rent, operating and heating advances, deposit) | ✅ residential deposit capped at three net base rents on create and deposit/base-rent edits; other unit usage types exempt | `api/leases.py`, `engine/deposit.py`, `pages/UnitDetailPage.tsx`, #167 |
| Change/end lease, tenant change | ✅ end and note always editable; amounts locked (409) once payments are booked or a settlement is final | `api/leases.py` (`PATCH /{id}`), `pages/UnitDetailPage.tsx`, T04 |
| Rent change with effective date | ✅ full base rent and separate operating/heating rates | `models/lease_rent_change.py`, `api/leases.py` (`/rent-changes`), `engine/mietkonto.py`, T23, T24, [usage](operations/tenancies-and-rent.md) |
| Graduated (Staffel) / index rent | ✅ | `api/leases.py` (`/rent-changes/staffel`, `/rent-changes/index`), `engine/index_rent.py`, T43, T44, [usage](operations/tenancies-and-rent.md) |
| Security deposit management | 🟡 target, paid and refunded tracked; no installments, no recording of interest credits | `engine/mietkonto.py` (`deposit_statement`), `api/payments.py` (`/deposits`), `pages/DepositsPage.tsx`, T25, [usage](operations/tenancies-and-rent.md) |
| Lease template, handover protocol | ✅ contract uses document-date rent and shows the last valid rent with its date for ended leases; immutable issued PDFs retained; move-in/out protocol records unit readings | #37, #219, `services/lease_document_service.py`, `components/LeaseDocuments.tsx`, [usage](operations/tasks-and-documents.md#lease-contracts-and-handovers-37) |

## Tenant ledger, bank and payments

| Feature | Status | Location |
| --- | --- | --- |
| Bank accounts | ✅ edit/end, historical references kept | `api/bank_accounts.py`, `pages/BankAccountsPage.tsx`, T55, [usage](operations/selector-pages.md) |
| Expected charges (Sollstellung) for rent/operating costs, day-accurate | ✅ derived live, including rent history | `engine/mietkonto.py` (`tenant_expected_charges`) |
| Record, book, reverse payment | ✅ drafts editable | `api/payments.py`, `pages/PaymentDetailPage.tsx`, T07 |
| Tenant/owner ledger (Mietkonto), open items | ✅ tenant and owner accounts split signed due/future balances at a selectable cutoff without clipping the reporting period; owner rows expose their due date | `api/payments.py` (`/mietkonto/*`, `/open-items`), `pages/MietkontoPage.tsx`, `pages/OpenItemsPage.tsx`, [semantics](m6-zahlungen-mietkonto.md#31-balance--open-items-per-person-pure-arithmetic-decimal) |
| Bank statement import CSV/CAMT | ✅ CSV (generic, DKB and Sparkasse CSV-CAMT profiles) and CAMT.053 (.001.02/.08, XML/ZIP), preview, duplicate detection | `engine/bank_import.py`, `services/bank_import_service.py`, `api/bank_accounts.py` (`POST /{id}/imports`), `pages/BankTransactionsPage.tsx`, T15–T19, [usage](operations/bank-and-payments.md) |
| Bank retrieval PSD2/FinTS | ❌ | – |
| Match suggestions, split booking | ✅ apply suggestion, split, ignore; whole outgoing transactions can cover an existing confirmed same-property cost up to its total. Coverage follows ordinary corrections and explicitly selected reviewed allocation successors | `engine/bank_matching.py`, `api/bank_transactions.py` (`/assign`, `/ignore`), `pages/BankTransactionsPage.tsx`, T20–T22, [usage](operations/bank-and-payments.md) |
| SEPA direct debit | ✅ pain.008 for monthly rent, creditor ID and tenant mandates | `services/sepa_export.py`, `api/sepa.py`, `components/SepaExport.tsx`, T50, [usage](operations/tasks-and-documents.md) |

## Vouchers and costs

| Feature | Status | Location |
| --- | --- | --- |
| Cost item entry/confirm | ✅ database values with German labels | `api/cost_entries.py`, `pages/CostEntriesPage.tsx`, `lib/costVocabulary.ts`, T02 |
| BetrKV cost categories | ✅ fixed seeded set | `api/cost_categories.py`, migration `0005` |
| Voucher upload, costs from voucher | ✅ | `api/invoice_documents.py`, `pages/InvoiceDocumentDetailPage.tsx` |
| OCR, e-invoice XML | ✅ runs as the `invoice-worker` service in the production Compose file; automatic OCR is off in the portable stack | `workers/invoice_extraction.py`, `core/invoice_ocr.py`, `core/einvoice.py`, `docker-compose.prod.yml`, T10 |
| Inbox (Posteingang) with review chain | 🟡 `inbox-worker` configured in both Compose files (portable profile `inbox`); upload-to-cost scenario added; real-stack acceptance still requires a working ClamAV installation and signatures | [Operational flow](operations/inbox-review-chain.md), `workers/workflow_runner.py`, `pages/InboxPage.tsx`, [#35](https://github.com/itskeKs/verwalino/issues/35) |
| Clarification cases (Klärungsfälle) | ✅ | `api/exceptions.py`, `pages/ExceptionsPage.tsx` |
| GoBD: immutability, reversal, correction | ✅ | `services/cost_entry_service.py` |

## Operating and heating cost statements

| Feature | Status | Location |
| --- | --- | --- |
| Property settlement period | 🟡 month-based offers, lease-clipped single/batch statements and offers, short-window service-day cost shares, plan dates and prior actuals; NK/HK advances credited separately over lease-covered months/days, day splits only between statements of the same lease; frozen-credit remainder after late end edits, bucket-aware DERIVED end guard and legacy check, outside-lease payment notice once on the statement containing the relevant lease boundary; single nonidentical overlaps with finalized periods rejected within the acting user's visibility and, by a database guard (0182), against runs of other accounting scopes whatever the user may read; batch skips finalized coverage of the clipped span; identical repeats allowed | `engine/periods.py`, `engine/distribution.py`, `engine/rounding.py`, `services/payment_service.py`, `api/properties.py` (`/settlement-periods`), `pages/SettlementsPage.tsx`, issues #68, #95, #122, #136 |
| Operating cost statement per lease: compute, finalize, PDF, correction | ✅ | `api/settlements.py`, `services/settlement_service.py`, `services/settlement_pdf.py` |
| Commercial preallocation per position | ✅ explicit percentage or fixed amount with required reason, shared across tenant drafts, exact group/occupant cents, frozen statement/PDF evidence; direct amounts/internal HeizkostenV excluded | `engine/settlement.py`, `models/commercial_preallocation.py`, `components/CommercialPreallocationInputs.tsx`, [semantics](m3-abrechnungs-engine.md#63-commercial-preallocation-vorwegabzug-gewerbe-202), #202 |
| Keys area/MEA/units, pro rata, vacancy | ✅ | `engine/distribution.py` |
| Keys consumption/persons/external/manual | ✅ inputs shown and sent with the database key names | `pages/SettlementDetailPage.tsx`, `lib/costVocabulary.ts`, T03 |
| Combined or separate operating and heating advances per lease | ✅ explicit dated choice, day-exact ledger, bank assignment, SEPA, one tenant due date, and an overall settlement credit for payments spanning both modes without a component assignment | `models/lease_agreement.py`, `engine/mietkonto.py`, `features/advance_split.feature`, #67, #311 |
| Advance payments from payments | ✅ combined and separate credits with retained NK/HK payment totals and sources, including across year boundaries | `services/settlement_service.py` (`set_advance_payments_derived`), `services/payment_service.py` (`derive_nk_advance_total_for_period`), `api/settlements.py` (`/advance-payments-derived`), T08 |
| Batch run per property | ✅ drafts for all leases, existing ones skipped | `api/settlements.py` (`POST /batch`), `pages/SettlementsPage.tsx`, T27, [usage](operations/statements.md) |
| Suggestion for a new advance payment | ✅ separate operating/heating proposals; accepting appends one rent change | `api/settlements.py` (`/advance-proposal`), `pages/SettlementDetailPage.tsx` ("Vorauszahlungsvorschlag"), T28, [usage](operations/statements.md) |
| Heating costs per HeizkostenV | ✅ full-period basic/consumption split; exact interim readings, otherwise degree days for heat and days for hot water/basic costs; frozen method/weights in PDF, or external billing | `engine/heizkosten.py`, `engine/heating_settlement.py`, `services/heating_input_service.py`, `components/HeatingInputs.tsx`, T45, T46, [usage](operations/statements.md) |
| Dated household sizes | ✅ tenant statements use saved occupied/vacancy histories and exact person-days; missing matching data blocks; frozen owner vacancy shares visible; large raw bases persist (#201) | `api/units.py` (`/household`), `engine/person_days.py`, `components/HouseholdPanel.tsx`, [semantics](m3-abrechnungs-engine.md#23-personenzahl--saved-dated-household-sizes) |
| Consumption from meters | ✅ property-year weights with invoice-period pools; either occupant-boundary date accepted, ambiguous readings require manual input, missing interim readings use degree days for internal heat and days for other consumption, with a reason | `engine/meter_consumption.py`, `api/settlements.py` (`/consumption-suggestions`, `/heating-consumption`), T31, [usage](operations/meters.md) |

## WEG (homeowners' association)

| Feature | Status | Location |
| --- | --- | --- |
| Economic plan, Hausgeld expected amounts, PDF | ✅ plan-start addressees; dated transfer of the unit’s ongoing advances (#207); a plan shorter than twelve months is charged in full, one installment per plan month (#602); a plan starting or ending mid-month shares its first and last installment by the plan days only (#662) | `api/economic_plans.py`, `services/payment_service.py`, `services/wirtschaftsplan_pdf.py` |
| Book the monthly Hausgeld of a finalized plan in one step | ✅ "Hausgeld buchen" on the plan: one row per owner and month from the plan lines and ownership periods, bank account chosen once, ticked months created and booked together (all or none), booked months shown and never offered again | `api/economic_plans.py` (`/{id}/hausgeld-booking`), `services/hausgeld_booking_service.py`, `components/HausgeldBooking.tsx`, `features/hausgeld_booking.feature`, [semantics](m4-weg-abrechnung.md#24a-booking-the-monthly-hausgeld-from-the-finalized-plan-186), #186 |
| Monthly Hausgeld due day | ✅ days 1–28/default 1 with working-day shift; historical 29–31 clamp to month end before shifting without changing frozen records; ledger, dunning/PDF and monitor share the date (#206) | `engine/fristen.py` (`owner_due_date`), `services/payment_service.py`, [semantics](m6-zahlungen-mietkonto.md#22-owner-expected-amount-per-owner-from-the-finalized-economic-plan) |
| Economic plan prefilled from the previous year | ✅ preview, then draft from last year's actual costs per category | `api/economic_plans.py` (`/previous/preview`, `/previous`), `components/PreviousPlan.tsx`, T54, [usage](operations/statements.md) |
| Annual settlement, PDF | ✅ resolved Spitze including planned reserve Soll (#401), plus frozen booked Hausgeld balance and resulting payment; owner/unit/period provenance (#204); co-owners are weighed by their share relative to the unit's active owners in every ownership span, and a unit whose active shares add up to neither 1 nor 100 refuses the calculation with its number (#404) | `api/weg_settlements.py`, `engine/weg.py`, `services/weg_jahresabrechnung_pdf.py` |
| Adopt paid Hausgeld | ✅ | `api/weg_settlements.py` (`/paid-advances-derived`), `services/weg_settlement_service.py` (`derive_paid_advances`), `pages/WegSettlementDetailPage.tsx`, T09 |
| Maintenance reserve (Erhaltungsrücklage) | ✅ cash ledger, dated owner/unit shares, explicit unassigned cash, booking-gated payouts (cost AND resolution), unit contribution corrections capped by booked Hausgeld with audited draft reductions/deletions after reversals, evidenced withdrawal corrections and adjustments bounded by net receipt withdrawals, audited draft opening totals with difference preview, release of unbacked imported payout drafts and frozen annual carry-over | `engine/reserve_ledger.py`, `services/reserve_ledger_service.py`, `components/ReserveLedgerPanel.tsx`. Open follow-ups: #191 (double burden), #211 (`bank_transaction` retention) |
| Asset report (Vermögensbericht) § 28 Abs. 4 WEG | ✅ includes confirmed deletion of empty drafts; bookings, ledger and retained evidence refuse deletion (#199) | `engine/vermoegensbericht.py`, `services/vermoegensbericht_pdf.py`, `pages/AssetReportsPage.tsx` |
| Special levy (Sonderumlage) | ✅ new levies require an adopted same-property resolution; historical unlinked levies can be linked once without changing frozen MEA shares, owner accounts or dunning; register/levy cross-links respect disclosure grants | `models/special_levy.py`, `api/special_levies.py`, `services/special_levy_service.py`, `pages/SpecialLeviesPage.tsx`, T32, T33, [usage](operations/weg.md) |
| Mid-year owner change | ✅ annual settlement remains day-accurate and its plan Soll follows dated ownership (#402); dated plan/report recipients, successor Hausgeld and shared recipient retention (#207) | `api/ownership.py`, `services/weg_settlement_service.py`, T26, [usage](operations/tenancies-and-rent.md) |
| Owners' meeting, minutes, resolution register | ✅ immutable meeting date and separate entry date in page and new PDF extracts | `api/meetings.py`, `pages/MeetingDetailPage.tsx`, `pages/ResolutionRegisterPage.tsx` |
| Meeting invitation as PDF | ✅ one page per owner, hint for less than three weeks' notice (§ 24 Abs. 4 WEG) | `api/meetings.py` (`/{id}/invitation.pdf`), `services/meeting_pdf.py`, T34, [usage](operations/weg.md) |

## Dunning, deadlines, tasks

| Feature | Status | Location |
| --- | --- | --- |
| Dunning notice, 3 levels, fee, PDF | ✅ dated rent and Hausgeld obligations, organization fees per level, optional override, and frozen issued amounts in the notice and PDF; issued tenant notices also render the frozen unit/property identity and the payment destination (single eligible active current account with IBAN auto-selected, explicit per-notice selection when several, issuance refused with guidance when none); the level counts only issued notices of the same person (same lease for tenants) that cover a currently overdue item | `api/dunning.py`, `engine/dunning.py`, `services/dunning_pdf.py`, `services/dunning_service.py` |
| Default interest, selected dunning run | ✅ optional organization-wide interest at statutory base rate + 5 or 9 pp, split by rate and payment dates; selected batch drafts use the same policy | `engine/dunning.py`, `services/dunning_batch_service.py`, `api/dunning.py` (`/batch/preview`, `/batch`), `components/DunningBatch.tsx`, T35, T53, [usage](operations/bank-and-payments.md) |
| Deadline monitor (Fristenmonitor) | ✅ derived; WEG scopes include owner arrears from the same dated ledger used by dunning | `api/fristen.py`, `services/fristen_service.py`, `engine/fristen.py`, `pages/FristenmonitorPage.tsx` |
| Tasks, follow-ups | ✅ create from property and unit, due tasks on the dashboard | `models/task.py`, `api/tasks.py`, `services/task_service.py`, `pages/TasksPage.tsx`, T41, T42, [usage](operations/tasks-and-documents.md) |

## Documents, communication, reports

| Feature | Status | Location |
| --- | --- | --- |
| Documents per property/unit/person | ✅ "Dokumente" panel: upload, open, archive | `models/attachment.py`, `api/attachments.py`, `components/AttachmentsPanel.tsx`, T36, T37, [usage](operations/tasks-and-documents.md) |
| Letter templates, dispatch by e-mail/letter | ✅ placeholder templates produce one PDF per recipient with document-date rent, separate positive heating advance and total; e-mail for settlement, dunning notice and invitation (SMTP disabled until configured) | #219, `api/letters.py`, `services/letter_service.py`, `pages/LettersPage.tsx`, `api/mail.py`, `services/mail_service.py`, `components/MailDispatch.tsx`, T47, T48, [usage](operations/tasks-and-documents.md) |
| Transactional mail templates | templates ready, senders pending: 15 typed German text/HTML templates with hosted/self-hosted footers; the operator-notice sender exists for the sub-processor notice (`python -m app.operator_notice subprocessor-notice`, #633) | `app/mail/`, #125; sender adoption #111/#115; [previews](operations/mail-previews.md) |
| Tenant portal | ❌ | – |
| Year overview per property (Anlage V) | ✅ income/expense overview plus default Anlage V mapping aid, unassigned/excluded items, exact reconciliation and separate CSV; tax review required | `api/reports.py` (`/property-year`, `/property-year.csv`, `/property-year/anlage-v`, `/property-year/anlage-v.csv`), `services/report_service.py`, `engine/anlage_v.py`, `pages/ReportsPage.tsx`, [M6 §6.12](m6-zahlungen-mietkonto.md#anlage-v-mapping-aid-34), T38, T39, #34, [usage](operations/statements.md) |
| CSV/DATEV export | ✅ CSV for payments, costs and year overview; DATEV EXTF batch with explicitly configured SKR03/SKR04 accounts | `api/payments.py` (`/export.csv`), `api/cost_entries.py` (`/export.csv`), `api/reports.py` (`/datev.csv`), `services/datev_export.py`, `components/DatevExport.tsx`, T39, T40, T49, [usage](operations/tasks-and-documents.md) |
| GDPR (DSGVO) access/erasure | ✅ access on screen, shared retention categories, retained booked recipients restricted; expired/unbound people anonymized, including incidental reserve reason/audit names | `api/dsgvo.py`, `services/dsgvo_service.py`, `components/DsgvoPanel.tsx` |

## Gaps

- Photos (Properties and units): no photo upload or display for properties or units (#39).
- Security deposit management (People and tenancies): no installments with due dates (#70) and no booking of interest credits (#38).
- VAT-liable letting (People and tenancies): no VAT on rent, advances and statements for units with the VAT option (#71); no input VAT apportionment (#72).
- Bank retrieval PSD2/FinTS (Tenant ledger, bank and payments): no automatic bank statement retrieval; import is by CSV/CAMT file only (#40).
- Inbox (Posteingang) with review chain (Vouchers and costs): worker service and upload-to-cost scenario are present; real-stack acceptance with ClamAV signatures remains pending ([#35](https://github.com/itskeKs/verwalino/issues/35), [runbook](operations/inbox-review-chain.md)).
- Settlement period (Operating and heating cost statements): economic plans that overlap a plan outside the acting user's visible accounting scopes are not detected; tenant statements of one lease are guarded across scopes by the database. Identical periods remain allowed (#68, #95).
- Maintenance reserve investment remains outside scope; ledger cash movements are not automatic bank reconciliation (#36).
- Tenant portal (Documents, communication, reports): not built; planned right after the hosted launch (#41).
- Year overview per property (Anlage V) (Documents, communication, reports): mapping aid available; no automatic tax-year cash-outflow calculation, ownership allocation, organization overrides or ELSTER submission.


Organization lifecycle (#23): archive v2 with v1 import, consistent ID remapping,
empty-tenant admission and atomic refusal, retention preview/fresh-export offer,
typed confirmation, seven-day read-only cooling-off/cancel, and operator erasure
are implemented. Historical protected scope/evidence restoration can still be
refused under current integrity rules; accounting scopes, bindings, grants and
attachments are restored only when the importing administrator is the archived
actor of every such row (a second administrator or a clerk as actor is always
refused; the source organization must be deleted or the installation another
one, with the same address), and beside the living source organization such an
import is refused (#134, [details](operations/organization-export.md)). A real fresh-install import is maintainer
acceptance; local validation uses two synthetic organizations in the test DB.
