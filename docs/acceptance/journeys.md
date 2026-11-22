# Core journeys

Written from the point of view of a private landlady, not derived from the code. This file is the
truth a blind acceptance run checks against. Legal expectations follow the BGB; where an
interpretation can be disputed, the journey says so. Format and rules: [README.md](README.md).

The journeys build on each other: journey 0 puts Verwalino into operation, journey 1 creates the
data that journeys 2 to 5 work with. Journey 6 stands alone.

The [money-input twin](../../features/journeys/money_inputs.feature) covers the tenancy amounts
of journey 1 and manual 60 EUR cost entry of journey 2 at 1280 and 360 px. It also checks German
field feedback for invalid money. Other steps remain outside this focused twin; the remaining
twins come with #163–#176 and the twin of journey 6 with #160.

The [dashboard greeting twin](../../features/journeys/dashboard_greeting.feature) checks the exact
“Guten Tag” heading and separate organization context at desktop and phone widths.

**Reference date:** The statement runs to October 2026 and the figures are those as of 31.10.2026.
Rents for November and later that are already due on the day of the run (November: third working
day = 04.11.2026) rightly show as open.

**Notation:** Amounts are written here as 14,660.00 €; the software shows them in German notation
(14.660,00 €). German terms in quotes are quoted verbatim from the law or the software.

## Sample data „Musterhaus“

| | |
| --- | --- |
| User | Sabine Muster, a private landlady, owner of the property |
| Property | Musterstraße 1, 12345 Musterstadt; account DE75512108001245126199 |
| Statement | [sparkasse-2026-01-10.csv](data/sparkasse-2026-01-10.csv): January to October 2026, Sparkasse format CSV-CAMT, example IBANs, fictional data |

| | Unit 1 (ground floor) | Unit 2 (upper floor) |
| --- | --- | --- |
| Tenant | Anna Beispiel | Bernd Test |
| Start of tenancy | 01.01.2026 | 01.03.2026 |
| „Nettokaltmiete“ (net rent excluding service charges) | 750.00 € | 600.00 € |
| Advance payment for operating costs | 150.00 € | 120.00 € |
| Monthly amount due (total rent) | 900.00 € | 720.00 € |
| Deposit | 2,250.00 € (= 3 times the net rent, the maximum under § 551 BGB), paid in one sum | none |

The statement contains: all rents on time and in full, except Anna's April payment (800.00 € on
07.04.); the deposit; ten advance payments for general electricity; the building insurance; and one
private Amazon booking.

## Journey 0: Putting Verwalino into operation with the setup token

**Goal:** Sabine has self-hosted Verwalino and sets it up.
**She does:** opens the setup page with the one-time token that the portable install printed,
creates her property-management organization and her account, and signs in.
**Expected:**
- With the token the setup works without outside help; afterwards Sabine is signed in.
- The token is spent: a second attempt with it is refused.

**Checked by:** agent
**Also at 360 px:** no
**Automated:** not yet

## Journey 1: Creating the property

**Goal:** Sabine maps her house.
**She does:** creates the Musterhaus with both units, both tenancies and Anna's deposit.
She enters Anna's amounts as “750,00”, “150,00” and “2.250,00”; invalid “abc”
shows a German field problem and keeps her input. A separate heating advance may be zero.
**Expected:**
- The overview shows one property, two units and two running tenancies with the amounts of the
  table.
- The new MIET property's details and new-unit form ask for no MEA setup while no unit has
  a recorded MEA. A new WEG property's details offer “MEA-Skala bestätigen” with
  “Skala noch nicht bestätigt” and its new-unit form offers “MEA (Miteigentumsanteil)”.
- If she adds “Wohnung 1” a second time, saving is refused with “Eine Einheit mit dieser Nummer
  gibt es in diesem Objekt schon.” The form keeps the entered number so she can correct it.
- **Additional check, deposit:** if Sabine mistakenly enters 2,400.00 € for Anna (more than three
  net rents), saving is refused with „Die Kaution darf höchstens drei Nettokaltmieten betragen
  (2.250,00 €).“ and her input is kept. She then enters the correct 2,250.00 € and saves.
- Sabine finds every step without having to guess. Where she hesitates, that is a finding.
- With Anna and Bernd both unmarked, the empty new-tenancy selection explains the
  route through “Personen” and “Rollen / Profile”. Sabine follows it, marks only
  Anna as a tenant and returns through “Objekte” to the unit: Anna is selectable,
  Bernd is absent and the hint is gone. The empty ownership form gives the
  analogous route for the owner profile.

**Checked by:** agent
**Also at 360 px:** no
**Automated:** tenancy money entry and deposit refusal/correction at 1280 and 360 px, in [money_inputs.feature](../../features/journeys/money_inputs.feature); new MIET/WEG MEA guidance at both widths in [property_mea.feature](../../features/journeys/property_mea.feature); duplicate unit number refusal and unit area entry/correction at both widths in [unit_numbers.feature](../../features/journeys/unit_numbers.feature); empty tenant/owner selection, profile designation and return navigation at both widths in [profile_hints.feature](../../features/journeys/profile_hints.feature)

## Journey 2: Importing the bank statement and assigning the payments

**Goal:** Sabine wants to know who has paid.
**She does:** uploads the original Sparkasse CSV-CAMT without rewriting dates and assigns
what Verwalino does not assign by itself.
**Expected:**
- The first import holds 31 transactions with counterparty names and IBANs.
  Anna Beispiel's 800.00 € receipt on 07.04.2026 suggests her lease.
- Anna: January to October all paid, only in April 800.00 € instead of 900.00 € →
  **100.00 € open**. The payment on 07.04. was **on time** (see journey 3), only too low.
  In “Mietkonto”, keep 01.01.–31.12.2026 and set “Fällig am” to 01.11.2026:
  “Saldo” is 1,900.00 €, “davon fällig” is 100.00 € (“Rückstand”), and
  “noch nicht fällig” is 1,800.00 € (November/December). Change only “Fällig am”
  to 04.11.2026: the due part becomes 1,000.00 €, the future part 900.00 €,
  and “Saldo” stays 1,900.00 €. This compares due dates against currently booked
  allocations; it does not reconstruct past booking history.
- Bernd: March to October paid in full, nothing open.
- Anna's 2,250.00 € of 02.01. are the **deposit**, not rent. Split the receipt as “Kaution”,
  select Anna's tenancy and enter `2.250,00`. Invalid text keeps the split open with
  guidance for the amount and leaves the receipt unassigned.
- General electricity (10 × 60.00 € = 600.00 €) and the building insurance (312.00 €) are expenses
  of the property.
  Enter and confirm the January electricity cost for 60.00 €, then assign its
  −60.00 € bank debit to that existing cost. The debit becomes “Zugeordnet”, the
  cost has 0.00 € remaining bank coverage, and the year still counts 60.00 € once.
- The private Amazon booking (34.99 €) belongs to nothing and is counted nowhere.
- If Sabine uploads the same file a second time, no booking is created twice.
- **The building-insurance invoice.** Sabine uploads the insurer's scanned invoice of 312,00 €, dated
  15.01.2026, vendor „Musterversicherung AG“ (the IBAN it prints is the one of the statement's debit line), under
  „Belege“ and opens it. Nothing is posted by the upload, and „Dokument ansehen“ always shows the original.
  - Where the invoice worker runs and an administrator has switched on „Teilungserklärungen importieren und
    Rechnungen auswerten“ (provider key and confirmation, a model under „Dokumente einlesen“ and one under
    „Informationen extrahieren“), the scan is read by the reading model and the fields are filled by the extraction
    model. The page reads „Felder vorbereitet · bitte prüfen“ and proposes **312,00 €**, **15.01.2026**,
    „Musterversicherung AG“, the number RE-2026-0042 and, in the row „IBAN des Rechnungsstellers“, the insurer's IBAN of
    the statement's debit line in groups of four; the card „Verbrauch der KI-Verarbeitung“ lists the
    reading and the extraction model with their usage. Sabine reviews the proposal, the IBAN included, and creates the
    cost herself; an IBAN the model misread is not proposed and the row reads „nicht erkannt“.
  - Without that configuration, with a failing provider or an exhausted monthly budget, the page says in German
    what happened, that the original is kept and that nothing was applied, and what was already used stays
    visible; Sabine enters the 312.00 € by hand („Kostenposition erstellen“). An organization that confirmed the
    provider before invoices were covered is asked once more, and until then nothing is sent.
  - An equivalent XRechnung or ZUGFeRD is read directly: „Als E-Rechnung erkannt“ with the same values, no usage and no
    provider call, with or without any AI configuration.
  - Where the operator switched invoice processing off (`HV_INVOICE_EXTRACTION_ENABLED=false`), the page says „Die automatische
    Verarbeitung ist auf diesem Server abgeschaltet.“, keeps the original and Sabine enters the cost by hand.
- **The same kind of document through „Posteingang“.** Sabine puts a scan with two documents into the inbox: a four-page
  original with the building-insurance invoice on pages 1 and 2 (312,00 €, number RE-2026-0042, „Mustermann Versicherungen AG“)
  and a water bill on pages 3 and 4 (99,00 €, „Stadtwerke Test GmbH“, the two pages scanned). Unstructured originals are no
  longer read locally: they go to the organization's models, never to a text-recognition program on the server, and an upload
  of more than one page is **not sent as a whole**.
  - A multipage upload waits. The batch page counts it under „Zur Sichtung“, names the stage „Aufteilung ausstehend“ and says in
    German what Sabine has to do (confirm how the original divides under „Seiten zu Belegen zusammenstellen“, either the
    documents it falls into or the whole original as one document) and that nothing was sent to a provider; the source page
    says „Wartet auf die Aufteilung“ with the same sentence and offers nothing to retry. Sabine marks pages 1–2 and 3–4 as the
    two documents („Weiteren Seitenbereich anlegen“), ticks both and saves the division („Aufteilung speichern“). A single-page
    upload needs none of this and is processed at once.
  - Where an administrator has switched on the AI import (key, a model under „Dokumente einlesen“ and one under „Informationen
    extrahieren“) and confirmed the text that names the inbox, each document is, once the division is saved, read **from its own
    pages only**, each page once: pages 1–2
    propose 312,00 € and „Mustermann Versicherungen AG“, pages 3–4 propose 99,00 € and „Stadtwerke Test GmbH“, and neither
    proposal mentions the other document. Every value shows its page and its passage („Fundstelle auf Seite 4 · vom
    Lesemodell gelesen“ with „Gesamtbetrag 99,00 EUR“ for a scanned page, „… · PDF-Text“ for a page with text); a value the
    document does not support says „Keine Fundstelle bestätigt“. The card „Verbrauch der KI-Verarbeitung“ lists the reading
    and the extraction model with their usage. The whole original (pages 1–4), which is no part of the saved division, is never
    sent and says so („gehört nicht zur bestätigten Aufteilung“). Had Sabine confirmed the whole original as one document
    instead, it would be read once with its four pages. Nothing is posted; the original can be downloaded.
  - A structured e-invoice in the inbox (XRechnung or ZUGFeRD) shows „Fundstelle im XML“ with its values, no usage card, and
    nothing is sent to a provider, with or without any AI configuration.
  - Without the AI import, with a failing provider or an exhausted monthly budget, the batch page and the source page say in German
    what happened, that the original is kept and that nothing was applied; what was already used stays visible, and „Auswertung
    erneut anfordern“ stays available once the cause is removed.
  - An organization that confirmed the provider before the inbox was covered keeps its declaration import (and its invoices where
    its text named them). Its inbox documents wait with the sentence that the organization has to confirm again, until an
    administrator ticks the confirmation once more under „KI für Ihre Organisation“ without entering the key; the card names the
    inbox and, where the operator switched invoice processing off, no invoices. Then „Auswertung erneut anfordern“ reads the document.
  - In every stack the declaration worker, which has the session secret and the route to the provider, reads the documents; the
    inbox worker has neither and never reads one. While no declaration worker runs, a document stays queued and Sabine
    enters the values by hand.

**Checked by:** agent
**Also at 360 px:** no
**Automated through the browser:** original Sparkasse preview/import, Anna's partial-rent
suggestion, duplicate detection and refusal of a statement for another account in
[sparkasse_statement.feature](../../features/journeys/sparkasse_statement.feature);
the January electricity assignment and unchanged yearly expense run at 1280 and 360 px there;
manual “60,00” cost entry in [money_inputs.feature](../../features/journeys/money_inputs.feature).
The Sparkasse twin also opens Anna's April split with a long dwelling label at
1280, 800 and 360 px: the explanation remains complete, labels sit above fields,
target and note span the row, and both bottom actions remain reachable. It corrects
an over-allocation without losing inputs and books 650.00 EUR base rent plus
150.00 EUR operating advance against her 800.00 EUR receipt. The same twin checks a
separate synthetic 2,250.00 EUR deposit receipt at 1280 and 360 px: it rejects an
invalid amount, then books the exact grouped amount to the selected tenancy.
The full-year account cutoff is automated at 1280 and 360 px in
[rent_account_due.feature](../../features/journeys/rent_account_due.feature).
The building-insurance invoice is automated in
[invoice_model_processing.feature](../../features/journeys/invoice_model_processing.feature) with the real worker and
fake providers: the scan read by both models (proposal 312,00 € / 15.01.2026 / „Musterversicherung AG“ and the IBAN of the
statement's debit line in „IBAN des Rechnungsstellers“ at 1280 and 360 px, the usage of both models, the IBAN that the
invoice prints reaching the extraction model and the stored source text), XRechnung and ZUGFeRD read with no provider built
and the same IBAN read from their payment data, a provider failure and the
unconfigured organization explained in German with the original kept. The API-level
[invoice_model_processing.feature](../../features/invoice_model_processing.feature) adds the confirmation that predates
invoices, a misread IBAN that is not proposed, the exhausted monthly budget and organization isolation. A real provider belongs to no automated run.
The inbox is automated in [inbox_model_processing.feature](../../features/journeys/inbox_model_processing.feature) with the real
workers and fake providers in a real browser at 1280 and 360 px: the four-page scan that waits (batch page and source page say in
German what to do and that nothing was sent), is divided and confirmed on the source page, and then has each of its two documents
proposed from its own pages while the whole is never sent (amounts, number and vendor from the sample, page and passage of every value, the scanned
pages read by the reading model, the usage of both models), a structured e-invoice with no model call and no usage, the
organization that confirmed the first text asked once for the inbox without its key and then read, the AI import off and a
provider failure explained in German with the original kept and the usage visible. The API-level
[inbox_model_processing.feature](../../features/inbox_model_processing.feature) adds the upload confirmed as one document and read whole,
the single page processed at once, the monthly limit and organization isolation.
The stack of the twin runs with the operator's switch for invoice processing off, so its card names the inbox and no invoices. A real provider belongs to no automated run.
The remaining assignment checks still require blind acceptance.

## Journey 3: Dunning letter to Anna

**Goal:** Sabine asks Anna in writing for the missing 100.00 € of April.
**She does:** enables automatic interest in **Mahneinstellungen** as an administrator, creates a dunning letter („Mahnung“) for Anna, and opens the issued PDF.
**Expected:**
- **Due date:** third working day of the month (§ 556b BGB). In April 2026 the 03.04. (Good
  Friday) and the 06.04. (Easter Monday) are public holidays, and Saturday does not count.
  Working days: 01.04., 02.04., 07.04. → due on **07.04.2026**, in default from **08.04.2026**
  without a reminder (§ 286 Abs. 2 Nr. 1 BGB).
- Principal 100.00 €, correct tenant, unit, month April 2026.
- Creating a notice opens its saved draft immediately, without a not-found page,
  manual reload or second creation.
- **Default interest** (Anna is a consumer: base rate plus 5 percentage points, § 288 Abs. 1 BGB)
  on 100.00 € from 08.04.2026:
  - 08.04. to 30.06.2026: 84 days at 6.27 % (base rate 1.27 %) = 1.44 €
  - from 01.07.2026 at 6.52 % (base rate 1.52 %): 0.0179 € per day, to 31.10.2026 (123 days)
    = 2.20 €
  - **to 31.10.2026 together 3.64 €**, afterwards just under 2 cents per day up to the date of the
    dunning letter. Deviations up to 5 cents (how the first and last day are counted) are not a
    finding.
- The document can be downloaded or printed and is readable on a phone (360 px).

Run the separate owner-draft check with automatic interest off, before enabling it for Anna.
Use unpaid 2026 Hausgeld of 400.00 € per month, with Karl Weiß owning January–June and
Lena Roth owning July–December. Create Karl's notice, then Lena's, for the 2026 plan as of
15.01.2027. Each draft opens immediately with 2400.00 € principal and exactly its six months.

Separately, create a manual plan for one owner with all shares, 7,200.00 € in annual costs
and zero reserve. Calculate the 600.00 € monthly advance, cancel finalization, return to
the list and reopen the draft. The annual cost is still editable and the monthly advance
still shows 600.00 €. Confirm the data basis and finalize; the saved annual and monthly
advances remain 7,200.00 € and 600.00 € at desktop and 360 px.

**Checked by:** agent
**Also at 360 px:** the dunning letter
**Automated:** [readable issued notice](../../features/journeys/dunning_notice.feature)
checks the first detail response for both owner drafts and an ordinary tenant
draft, with no reload and no duplicate. The tenant and owner fee checks set the
standard first fee to 2.50 EUR and enter `0,00` in each form at 1280 and 360 px. The
saved fee is zero; principal and total match with interest off. Owner drafts show their six months at
1280 and 360 px. It also checks four-digit principal, interest and open amounts without horizontal
scrolling at 1280 and 360 px, plus the German issue-time display. Its scope scenario
explains the missing Abrechnungskreis in German at both widths; with the first scope selected,
June rent of 850 EUR paid 100 EUR and 900 EUR into the accounts of two scopes yields no notice, and
a July notice opens with 850 EUR principal. Its April
scenario checks the explicit `Zinsbeginn: 08.04.2026` in the issued detail at
both widths and in the downloaded PDF. The separate
[interest/PDF scenario](../../features/dunning_interest.feature) checks frozen
amounts, both published rates, the issued PDF, and the ledger's separate notice information.
The [organization settings scenarios](../../features/dunning_settings.feature) also verify
the second-level fee and the residential and commercial premiums. The
[manual plan journey](../../features/journeys/economic_plan_reserve.feature)
checks cancellation, reopening and finalization at both widths.

## Journey 4: Rent change for Anna

**Goal:** Anna and Sabine agree on a new net rent of 780.00 € from 01.01.2027 (an agreement under
§ 557 Abs. 1 BGB is possible at any time, both consent).
**She does:** enters the change.
**Expected:**
- From January 2027 Anna's monthly amount due is 930.00 €; until December 2026 it stays 900.00 €.
- Payments so far and the open April amount stay unchanged.
- **Additional check, only if Verwalino offers a rent increase request under § 558 BGB,** made
  after the change above is entered: such a request to Anna taking effect before 01.04.2028 is
  rejected by Verwalino or comes with a warning. The rent must have been unchanged for 15 months
  when the increase takes effect, and the agreed increase of 01.01.2027 starts that period again
  (only increases under §§ 559 to 560 BGB do not count).

**Checked by:** agent
**Also at 360 px:** no
**Automated:** [rent_change.feature](../../features/rent_change.feature) checks the dated
history and rent account, including a combined advance of 240.00 EUR and a total of
990.00 EUR with 750.00 EUR net rent. [advance_split.feature](../../features/advance_split.feature)
checks the separate and combined annual statement values, the July transition, and April's
shared due date. The [annual settlement journey](../../features/journeys/rent_change_settlement.feature)
derives a year-only €2,340 advance after an initial calculation, reopens the draft without the old
balance, and checks the €560 payable result after recalculation and finalization.

## Journey 5: Annual overview and Anlage V

**Goal:** Sabine wants to see, for her tax return, what came in and went out so far in 2026.
**She does:** opens the annual overview 2026 and the Anlage V assignment, downloads the CSV, and
also looks at it on the phone.
**Expected (as of 31.10.2026):**
- Received in total: **14,660.00 €** (Anna 9 × 900 + 800, Bernd 8 × 720).
- Of that **rental income 12,200.00 €** and **service-charge payments 2,460.00 €**, if Anna's
  partial payment first settles the advance payment (see note): Anna 7,400 + 1,500, Bernd
  4,800 + 960.
- The deposit (2,250.00 €) is **not** counted as income.
- Expenses of the property: **912.00 €** (600.00 € electricity + 312.00 € insurance); the 34.99 €
  are missing from it.
- The figures in the view and in the downloaded CSV agree.

**Note, partial payment:** In April Anna transfers 800.00 € against an amount due of 900.00 € (750
net rent + 150 advance payment) and does not say what for. Under § 366 Abs. 2 BGB the claim that
gives the landlord less security is settled first. By the widespread view that is the advance
payment, because after the settlement period has expired it can no longer be sued for. Then
100.00 € of **net rent** are missing. If Verwalino does it the other way round, the total is the
same and only the split differs: a question left open for the owner to decide.

**Checked by:** agent
**Also at 360 px:** the annual overview
**Automated:** [annual income twin](../../features/journeys/annual_income.feature) covers
receipts, the separate deposit, Anlage V income and both CSV totals at desktop and
360 px. Expense coverage remains outside this twin.

## Journey 6: Creating an owners' association from the declaration of division

**Goal:** Sabine takes over the management of a condominium association (WEG) and does not want to
type the property in by hand.
**She does:** looks in Verwalino for the way to upload a declaration of division and uploads
the [fictional scanned declaration](data/teilungserklaerung-lindenallee-12.pdf). It describes WEG Lindenallee 12, 01234 Musterstadt: apartments 1 and 2 each own 450/1,000; garage G1 owns 100/1,000. The independent expected sum is 450 + 450 + 100 = 1,000, equal to the printed denominator. An administrator first stores the organization's provider key in Einstellungen, loads the provider's models and chooses two **different** ones: one under „Dokumente einlesen“ (reads the pages, must take images) and one under „Informationen extrahieren“. They save, reload the page and see both choices unchanged, and enable the import. The same model may be chosen for both roles; this journey uses two.
**Expected:**
- Either Verwalino creates a property from it with all units, per unit the number, the kind
  (apartment, garage, …) and the co-ownership share, and the shares add up to the denominator of
  the declaration of division; for that Sabine checks some units against the document. The scan
  has no text layer: the page is read by the reading model, not by a local text-recognition
  program, and the review shows apartments 1 and 2 at 450/1,000 each and garage G1 at 100/1,000,
  each with page 1 of the scan as its source.
- The screen shows „Verbrauch je Importlauf“ with „Dokumente einlesen“ (the reading model) and
  „Informationen extrahieren“ (the extraction model) apart, each with its tokens and cost or
  „kein Preis bekannt“, and the run total below. The provider's records show that the reading
  model was asked only for the page and the extraction model only for the extraction passes.
- Or Verwalino tells Sabine clearly that this is not possible here and what she can do instead:
  without a model under „Dokumente einlesen“ the import does not start, and a model that cannot
  read images or a reached limit is explained in German on the import screen, with the uploaded
  original kept and nothing applied. A way that is offered and then fails or loads forever is a defect.
- In any case: where it ends and what Sabine sees there. For this journey that is the most
  important result.

**Checked by:** agent
**Also at 360 px:** result page and AI settings
**Automated:** [real-browser twin](../../features/journeys/declaration_import.feature): the administrator chooses the two models in Einstellungen, the worker takes both from the saved settings and runs with offline deterministic reading and extraction providers (`backend/tests/offline_declaration_import.py`, which the user check of a PR can also run by hand, see [README.md](README.md#where-a-run-happens)). Live model discovery and import require the separate authorized user check.

## Operating cost draft acceptance check

Create an operating cost statement for a synthetic tenant who leaves on 30 September
2025. The saved draft opens immediately for that tenant and shows 1 January through
30 September 2025. The [browser twin](../../features/journeys/operating_cost_draft.feature)
checks the period picker at desktop and 360 px widths, then saves and checks the first
detail response. The separate user check in issue #373 repeats creation ten times across
synthetic leases and periods.

## Not covered

- **Registration** (hosted operation): the confirmation links point to the public host. It could
  only be checked on a hosted test instance.
