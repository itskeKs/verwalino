# Delivery and parity register

Source baseline: `1ff645a`. Full implementation is in progress, not accepted.
Reference RF01–RF31 retains the observation depth in the implementation plan §6
and authenticated reference review; observations are not implementation evidence.
The design P-row remains normative, including exclusions, failures and V gates.
R = documented reference, O = observed reference, D = target decision, V = release
validation; classification and RF mapping are inherited unchanged from those rows.

| Package | Acceptance fixtures | Delivery state | Implementation commit | Check environment/result | Approver / release gates |
| --- | --- | --- | --- | --- | --- |
| P0.1 Source integrity | A01 | Implemented; A4 browser/account gate open | `1160431` | Focused checks below | Unassigned; applicable V gates open |
| P0.2 OCR and truthful completeness | A02,A06 | Planned | — | Not run | Unassigned; applicable V gates open |
| P1.1 Shared intake and exceptions | A03,A04 | Planned | — | Not run | Unassigned; applicable V gates open |
| P1.2 Master import and portfolio | A05 | Planned | — | Not run | Unassigned; applicable V gates open |
| P2.1 Bank file import and matching | A07 | Planned | — | Not run | Unassigned; applicable V gates open |
| P2.2 Bank connection (R) | A08 | Planned | — | Not run | Unassigned; applicable V gates open |
| P2.3 Recurring finance, cashflow and loans (R) | A09 | Planned | — | Not run | Unassigned; applicable V gates open |
| P2.4 SEPA (R) | A10 | Planned | — | Not run | Unassigned; applicable V gates open |
| P3.1 Meter and external allocations (R) | A11 | Planned | — | Not run | Unassigned; applicable V gates open |
| P3.2 Readiness and executable rules | A12 | Planned | — | Not run | Unassigned; applicable V gates open |
| P3.3 Settlement packages | A06,A13,A16 | Planned | — | Not run | Unassigned; applicable V gates open |
| P3.4 Supported internal heating/CO2 | A17 | Planned | — | Not run | Unassigned; applicable V gates open |
| P4.1 Cases/tasks/deadlines (R) | A18 | Planned | — | Not run | Unassigned; applicable V gates open |
| P4.2 Dunning/reminders (R) | A19 | Planned | — | Not run | Unassigned; applicable V gates open |
| P4.3 Tenant/owner portal (R for tenant portal | A20 | Planned | — | Not run | Unassigned; applicable V gates open |
| P4.4 Messages/announcements/email/post (R) | A21 | Planned | — | Not run | Unassigned; applicable V gates open |
| P4.5 Meetings/resolutions | A22 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.1 Rental lifecycle, deposits, co-tenants (R) | A23 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.2 Rent adjustment/index/steps (R) | A24 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.3 Document folders, email intake, stationery (R) | A25 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.4 Contracts/templates/signing/handover (R provisional) | A26 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.5 DATEV, EÜR, Anlage V, financial exports (R) | A27 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.6 General migration/export and account/security settings (R) | A28 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.7 Listing integration (R) | A29 | Planned | — | Not run | Unassigned; applicable V gates open |
| P5.8 Mobile companion/push (R) | A30 | Planned | — | Not run | Unassigned; applicable V gates open |

## Current evidence

A1 (P0.1, D; RF09/RF10 observed with receipt save unverified) has passed independent backend
review with no actionable findings. New backend and frontend regressions first failed on duplicate creation
and absent source ID, respectively. PostgreSQL 16 disposable tmpfs on localhost:
55432, app_rw RLS, Python 3.13, Node 26. Focused M2/e-invoice/tenant tests:
68 passed, one registry failure; registry updated and 7 RLS checks then passed.
A final source-link/version race regression and M2 suite passed (39 tests);
the focused independent delta review found no remaining actionable race.
Frontend source/key/retry + client: 5 passed; lint and production build passed
(bundle-size warning remains). Browser/account acceptance belongs to A4 and is
not yet run. Supported A1 scope is one primary source per cost; multi-cost split
is pending B3. Manual no-source drafts remain supported. No external rollout.

## Decisions and outstanding evidence

V01–V09 remain open with the owners and evidence requirements in the
implementation plan §7. No provider, professional review policy, specialist
fixture, legal method, parity scope, security matrix or capacity target has been
approved by this implementation task. No live external action is authorized.
G1 load/restore and G2 document/operator evaluations remain unexecuted.

### A2 ordinary invoice extraction

Implemented locally; P0.2 remains incomplete until A3/A4. Synthetic German scan,
text PDF, XML and embedded XML pass; failure and restart/fencing fixtures are
included. A2 combined invoice/PDF/upload/e-invoice/RLS run: 135 passed, one
transient disposable-PostgreSQL SSL-upgrade connection failure. A focused rerun
of the disabled-worker and queue-full/version fixtures passed (2 tests). Earlier
70 focused tests passed. Frontend invoice status/retry tests: 2 passed; lint and
build passed with the existing bundle-size warning. Test runtime: Python 3.13,
Node 26, PostgreSQL 16 tmpfs; real local Poppler/Tesseract German language.
Independent review found queue backpressure could roll back a saved source;
repaired and delta reviewed with no remaining material findings. Source bytes
and manual review now survive full/disabled queues. General job fairness and
capacity, runtime deployment and browser/account acceptance remain open.
