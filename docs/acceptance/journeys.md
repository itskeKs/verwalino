# Core journeys

Written from the point of view of a private landlady, not derived from the code. This file is the
truth a blind acceptance run checks against. Legal expectations follow the BGB; where an
interpretation can be disputed, the journey says so. Format and rules: [README.md](README.md).

The journeys build on each other: journey 0 puts Verwalino into operation, journey 1 creates the
data that journeys 2 to 5 work with. Journey 6 stands alone.

No automated twin exists yet. The twins come with the fixes of #163–#176; the twin of journey 6
with #160.

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
**Expected:**
- The overview shows one property, two units and two running tenancies with the amounts of the
  table.
- **Additional check, deposit:** if Sabine mistakenly enters 2,400.00 € for Anna (more than three
  net rents), Verwalino points out that at most 2,250.00 € is permitted. She then enters the
  correct 2,250.00 €.
- Sabine finds every step without having to guess. Where she hesitates, that is a finding.

**Checked by:** agent
**Also at 360 px:** no
**Automated:** not yet

## Journey 2: Importing the bank statement and assigning the payments

**Goal:** Sabine wants to know who has paid.
**She does:** uploads the CSV and assigns what Verwalino does not assign by itself.
**Expected:**
- Anna: January to October all paid, only in April 800.00 € instead of 900.00 € →
  **100.00 € open**. The payment on 07.04. was **on time** (see journey 3), only too low.
- Bernd: March to October paid in full, nothing open.
- Anna's 2,250.00 € of 02.01. are the **deposit**, not rent.
- General electricity (10 × 60.00 € = 600.00 €) and the building insurance (312.00 €) are expenses
  of the property.
- The private Amazon booking (34.99 €) belongs to nothing and is counted nowhere.
- If Sabine uploads the same file a second time, no booking is created twice.

**Checked by:** agent
**Also at 360 px:** no
**Automated:** not yet

## Journey 3: Dunning letter to Anna

**Goal:** Sabine asks Anna in writing for the missing 100.00 € of April.
**She does:** creates a dunning letter („Mahnung“) for Anna and looks at it as Anna would receive it.
**Expected:**
- **Due date:** third working day of the month (§ 556b BGB). In April 2026 the 03.04. (Good
  Friday) and the 06.04. (Easter Monday) are public holidays, and Saturday does not count.
  Working days: 01.04., 02.04., 07.04. → due on **07.04.2026**, in default from **08.04.2026**
  without a reminder (§ 286 Abs. 2 Nr. 1 BGB).
- Principal 100.00 €, correct tenant, unit, month April 2026.
- **Default interest** (Anna is a consumer: base rate plus 5 percentage points, § 288 Abs. 1 BGB)
  on 100.00 € from 08.04.2026:
  - 08.04. to 30.06.2026: 84 days at 6.27 % (base rate 1.27 %) = 1.44 €
  - from 01.07.2026 at 6.52 % (base rate 1.52 %): 0.0179 € per day, to 31.10.2026 (123 days)
    = 2.20 €
  - **to 31.10.2026 together 3.64 €**, afterwards just under 2 cents per day up to the date of the
    dunning letter. Deviations up to 5 cents (how the first and last day are counted) are not a
    finding.
- If Verwalino shows no interest, that is a finding of the kind "missing", not an error.
- The document can be downloaded or printed and is readable on a phone (360 px).

**Checked by:** agent
**Also at 360 px:** the dunning letter
**Automated:** not yet

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
**Automated:** not yet

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
**Automated:** not yet

## Journey 6: Creating an owners' association from the declaration of division

**Goal:** Sabine takes over the management of a condominium association (WEG) and does not want to
type the property in by hand.
**She does:** looks in Verwalino for the way to upload a declaration of division and uploads a
fictional scanned declaration of division. The file is added under `docs/acceptance/data/` with
the first issue that touches this journey (#160); until then the journey cannot be run.
**Expected:**
- Either Verwalino creates a property from it with all units, per unit the number, the kind
  (apartment, garage, …) and the co-ownership share, and the shares add up to the denominator of
  the declaration of division; for that Sabine checks some units against the document.
- Or Verwalino tells Sabine clearly that this is not possible here and what she can do instead. A
  way that is offered and then fails or loads forever is a defect.
- In any case: where it ends and what Sabine sees there. For this journey that is the most
  important result.

**Checked by:** agent
**Also at 360 px:** no
**Automated:** not yet

## Not covered

- **Registration** (hosted operation): the confirmation links point to the public host. It could
  only be checked on a hosted test instance.
