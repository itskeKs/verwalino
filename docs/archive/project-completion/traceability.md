# Normative Traceability — nur gezielte Zeilen nachschlagen

Nicht standardmäßig laden. Die Tabelle ist unverändert aus dem Oktoberplan
übernommen; Reference-Beobachtung ist keine eigene Implementationsevidenz.

## 6. Acceptance and reference traceability

Primary A ownership below expands the design's grouped scenarios into executable
units. The full design scenario and reference review acceptance deltas remain
normative; this table does not narrow them. A31 security and A32 migration apply to
every relevant unit, not only the listed primary owners. RF evidence informs tests;
it is never evidence that our implementation passed.

| A ID | Primary unit / package | Decisive fixture or failure | RF evidence (where present) |
| --- | --- | --- | --- |
| A01 | A1/A4, P0.1 | Both upload orders, one linked/released source and cost, retry | RF09/RF10 (receipt save unverified) |
| A02 | A2/A4, P0.2 | Text/scan/XML/embedded XML, OCR timeout/unavailable | RF09 (OCR cancelled) |
| A03 | B2/B3, P1.1 | Mixed batch, duplicates, context ambiguity, durable retry | RF09/RF10 |
| A04 | B3, P1.1 | Split/credit conservation, proposal revision and clerk race | RF09/RF25 controls only |
| A05 | B4/F2/F4, P1.2 | Reimport identities, effective diff, contract-only edit | RF02/RF03/RF05/RF19/RF28 |
| A06 | A3/D2/D3, P0.2/P3.2/P3.3 | Known 1,200 vs unknown vs verified zero; no legacy bypass | RF07/RF08/RF14/RF15 |
| A07 | C1, P2.1 | Partial/over/combined money, repeat import, manual adoption | RF07/RF08/RF11/RF12 |
| A08 | C3, P2.2 | Consent expiry/reconnect, webhook and pending/booked identity | RF11/RF23 controls only |
| A09 | C2, P2.3 | Recurrence retry, loan components, forecast versus cash | RF01; loan execution unverified |
| A10 | C4, P2.4 | XML/profile, stable instruction, return; export is not paid | RF27 controls only |
| A11 | D1, P3.1 | First reading/100→160, replacement, provider 60+40 vs 60+50 | RF13 |
| A12 | D2, P3.2 | Inactive knowledge, activated version, unsupported/conflict | RF04/RF25 controls only |
| A13 | D3, P3.3 | Rental vacancy 600/302.47/297.53, leap year, 25.21/74.79 | RF14–RF16 draft |
| A14 | D3, P3.3 | WEG 200 difference each, separate 200 arrears, owner changes | RF04 controls; professional WEG unverified |
| A15 | D3, P3.3 | SEV eligible components once, mixed-scope denial | No exercised reference scenario |
| A16 | D3, P3.3 | Stale 409, atomic PDFs, correction leaves old hash/history | RF16 draft only |
| A17 | D4, P3.4 | Specialist heating/CO2 cases, unsupported method blocks | RF07 notice does not validate method |
| A18 | E1, P4.1 | Required/optional tasks, outcome/reopen, blocker remains | RF01/RF17 |
| A19 | E2/E4, P4.2 | Payment after notice review stops release, dispute pause | No completed reference dunning |
| A20 | E3, P4.3 | Audience eligibility, household denial, former resident | RF05/RF21/RF24 boundary |
| A21 | E4, P4.4 | No-email eligibility, fake timeout/replay, unknown delivery | RF20/RF21/RF23/RF29 |
| A22 | E5, P4.5 | Voting/proxy/minutes and immutable register amendment | No authenticated meeting acceptance |
| A23 | F2/F4, P5.1 | One co-tenant obligation, deposit, termination/handover | RF03/RF05/RF06/RF07/RF19 |
| A24 | F3, P5.2 | Index/step and advance basis, explicit effective adoption | RF06/RF07/RF16 |
| A25 | F1/B2/B3, P5.3 | Folder moves, resend, spoofing, frozen stationery | RF09/RF10/RF22/RF29/RF30 |
| A26 | F4, P5.4 | Prefill scope, missing data, terms/signature revision mismatch | RF18/RF19 draft; RF29 |
| A27 | F5, P5.5 | VAT profile block, reconciled artifacts/importer, failed download | RF25/RF26/RF31 unverified delivery |
| A28 | F6/B4/C1, P5.6 | Scoped export/restore, CSV preview and stable outcome | RF12/RF22/RF23/RF28 |
| A29 | F7a, P5.7 | Approved public fields only, reconciled withdraw/retry | RF03/RF30 boundary |
| A30 | F7b, P5.8 | Camera/keyboard journeys, revoked and private push | Mobile reference unverified |
| A31 | B1/E3/F6/G1, all | Two orgs + two grants within org, every access/effect surface | RF24 proves no rights matrix |
| A32 | Every migration; G1 | Hash/money/history preservation, unknown backfill, rollback | Target migration policy, not O |
| A33 | G1/G2 | Full fixture latency, fairness, restore and report | Large reference portfolios unverified |

RF coverage index (all observations are assigned, none becomes a delivery claim):
RF01→C2/E1; RF02→B4; RF03→B4/F2/F7a; RF04→D2/D3;
RF05→B4/F2/E3; RF06→F2/F3; RF07→A3/C1/D1/F2/F3;
RF08→A3/C1; RF09→A1/A2/B2/B3/F1; RF10→B2/B3/F1;
RF11→C1/C3; RF12→C1/F6; RF13→D1; RF14→D2/D3;
RF15→A3/D2/D3; RF16→D3/F3; RF17→E1; RF18→F4;
RF19→B4/F2/F4; RF20→E3/E4; RF21→E3/E4; RF22→F1/F6;
RF23→C2/C3/E1/E4/F6; RF24→B1/E3/F6; RF25→B3/D2/F5;
RF26→F5; RF27→C4; RF28→B4/F6; RF29→F1/E4/F4;
RF30→F1/F7a; RF31→F5.
