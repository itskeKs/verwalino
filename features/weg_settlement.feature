Feature: WEG settlement and the owner's Hausgeld balance
  The resolved cost adjustment remains separate from payments owed under the plan.

  Scenario: Saved WEG drafts are readable as soon as their success response starts
    Given Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros
    When I save ten 600 euro Hausgeld drafts dated 5 January 2026 and a new plan and annual settlement
    Then every new detail is readable before its save response is cleaned up

  @browser
  Scenario: Saving opens the new payment, plan and annual settlement immediately
    Given Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros
    When I save ten Hausgeld payments and a plan and annual settlement through the UI
    Then all first detail requests succeeded and every payment offered booking

  Scenario: A settlement credit offsets unpaid Hausgeld without changing the resolution
    Given Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros
    And Anna has booked 6600 euros of Hausgeld and the second owner has paid in full
    When I finalize their WEG annual settlement
    Then Anna's resolved Spitze is minus 800 euros and her Hausgeld arrears are 600 euros
    And Anna's resulting payment is a 200 euro credit
    And the fully paid owner's payment equals the resolved Spitze
    And the PDF explains the plan, booked payments, arrears and resulting payment
    And finalization has not changed the booked Hausgeld payments

  Scenario: Booked Hausgeld without an assigned year agrees with the owner ledger
    Given Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros
    And Anna has booked 6600 euros of Hausgeld without an assigned year
    When I finalize their WEG annual settlement
    Then Anna's resolved Spitze is minus 800 euros and her Hausgeld arrears are 600 euros
    And Anna's resulting payment is a 200 euro credit
    And the fully paid owner's payment equals the resolved Spitze
    And the PDF explains the plan, booked payments, arrears and resulting payment
    And the owner's ledger is unchanged by finalization
    And finalization has not changed the booked Hausgeld payments

  Scenario: A successor's assigned payment remains visible with her dated plan Soll
    Given a successor owns unit 2 from July after the plan was finalized and all costs belong to January through June
    And the successor has booked 1900 euros for December 2026 with a January 2027 value date
    When I finalize their WEG annual settlement
    Then the finalized statement and PDF show the successor's 1900 euro credit with unit and ownership dates
    And the owner's ledger is unchanged by finalization
    And finalization has not changed the booked Hausgeld payments

  @browser
  Scenario: The owner's payment explanation fits desktop and a 360 pixel phone
    Given Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros
    And Anna has booked 6600 euros of Hausgeld and the second owner has paid in full
    When I finalize their WEG annual settlement
    Then the Einzelabrechnung shows all four amounts and the payment without horizontal scrolling

  Scenario: Plan and asset report use their key date after a July owner change
    Given Karl Weiß owns unit 2 until June and Lena Roth owns it from July
    When I finalize the asset report for their whole year
    Then the plan names Karl at the year start and the report names Lena at the year end
    And Karl and Lena each owe six monthly Hausgeld advances of 400 euros
    And their issued dunning notices each request only their six unpaid advances
    And the annual settlement still lists both ownership periods

  Scenario: The plan Soll follows the dated owner after a mid-year change
    Given Anna owns the whole unit until June and Bernd owns it from July with a 12000 euro plan and 12000 euro costs
    When I compute their WEG annual settlement
    Then Anna's Hausgeld Soll is 6000 euros and her Abrechnungsspitze is minus 49,32 euros
    And Bernd's Hausgeld Soll is 6000 euros and his Abrechnungsspitze is 49,32 euros

  @browser
  Scenario: The document addressees and key dates fit desktop and a 360 pixel phone
    Given Karl Weiß owns unit 2 until June and Lena Roth owns it from July
    When I finalize the asset report for their whole year
    Then both document pages show their dated addressees at desktop and phone widths
  @browser
  Scenario Outline: Long owner statements keep complete amounts inside their cards
    Given Anna-Katharina Freifrau von Hohenzollern-Sigmaringen has Anna's WEG plan and costs
    And Anna has booked 6600 euros of Hausgeld and the second owner has paid in full
    When I prepare their <state> WEG annual settlement
    Then the long owner's statement and whole snapshot hash fit desktop and a 360 pixel phone

    Examples:
      | state     |
      | computed  |
      | finalized |

  Scenario: Paid Hausgeld includes the buyer and identifies her missing amount
    Given Karl Weiß owns unit 2 until June and Lena Roth owns it from July
    And Anna, Karl and Lena have booked their twelve, six and six monthly Hausgeld payments
    When I take over their paid Hausgeld from the booked figures
    Then Anna, Karl and Lena have paid 7200, 2400 and 2400 euros
    And clearing Lena's paid amount asks for her missing Hausgeld in German

  @browser
  Scenario: Paid Hausgeld prefill and missing buyer amount fit desktop and phone
    Given Karl Weiß owns unit 2 until June and Lena Roth owns it from July
    And Anna, Karl and Lena have booked their twelve, six and six monthly Hausgeld payments
    Then the paid Hausgeld prefill and Lena's missing amount are visible on desktop and phone


  Scenario: Costs as planned leave no Spitze when the plan has a reserve
    Given the WEG plan has 7000 euros of costs and 6000 euros of reserve for owners with 600 and 400 MEA
    When I finalize their WEG annual settlement
    Then both owners have zero Spitze with reserve Soll of 3600 and 2400 euros and Hausgeld Soll of 7800 and 5200 euros

  Scenario: Building insurance across the year boundary is charged day-accurate in each year
    Given a WEG with one unit and one owner has Gebäudeversicherung of 3650 euros for July 2024 through June 2025
    When I compute the WEG annual settlements for 2024 and 2025
    Then the 2024 settlement charges 1840 euros of Gesamtkosten and the 2025 settlement charges 1810 euros
    And the owner's share is 1840 euros in the 2024 settlement and 1810 euros in the 2025 settlement
    And both settlements together charge exactly 3650 euros

  Scenario: Co-owners entered as percentages share their unit's costs by their relative shares
    Given Anna owns unit 1 alone and Bernd and Carla own unit 2 with shares of 50 and 50, both units of 500 MEA with 1000 euros of costs by MEA
    When I compute the WEG annual settlement of the co-owned units
    Then Anna's cost share is 500 euros and Bernd's and Carla's are 250 euros each

  Scenario: A mistaken co-owner share is corrected and the annual settlement then calculates
    Given Anna owns unit 1 alone and Bernd and Carla own unit 2 with shares of 50 and 30, both units of 500 MEA with 1000 euros of costs by MEA
    When I try to compute the WEG annual settlement of the co-owned units
    Then the calculation is refused in German, names unit 2 and points to correcting its shares
    When I correct Carla's share of unit 2 to 50
    And I compute the WEG annual settlement of the co-owned units
    Then Anna's cost share is 500 euros and Bernd's and Carla's are 250 euros each
    And the history of unit 2 shows Carla's first entry as corrected, with who corrected it and when

  Scenario: Co-owner shares that do not add up refuse the calculation and name the unit
    Given Anna owns unit 1 alone and Bernd and Carla own unit 2 with shares of 0.6 and 0.3, both units of 500 MEA with 1000 euros of costs by MEA
    When I try to compute the WEG annual settlement of the co-owned units
    Then the calculation is refused in German because the shares of unit 2 do not add up
    And the plan of the same owners still gave Bernd 333.33 euros and Carla 166.67 euros of unit 2's 500 euros

  # A special levy is an additional Soll of the owner (#405, ADR-0007 amendment of 2026-11-15).
  # The plan costs 7000 euros (Anna 4200, Berta 2800); the roof repair adds 5000 euros of costs
  # (Anna 3000, Berta 2000), so the costs are 12000 euros (Anna 7200, Berta 4800).
  Scenario Outline: A special levy for the repair is the owners' additional Soll, not a second cost share
    Given the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros
    And Anna and Berta have paid their Hausgeld in full
    And a resolved special levy of <levy> euros is due on <due>
    And Anna has paid <anna paid> euros of the levy and Berta <berta paid> euros
    When I finalize their WEG annual settlement
    Then Anna's Spitze is <anna spitze> euros and Berta's is <berta spitze> euros
    And their levy Soll is <anna soll> and <berta soll> euros and paid <anna paid> and <berta paid> euros
    And Anna's payment is <anna payment> euros and Berta's is <berta payment> euros

    Examples:
      | levy | due             | anna paid | berta paid | anna spitze | berta spitze | anna soll | berta soll | anna payment | berta payment |
      | 5000 | 1 March 2026    | 3000      | 2000       | 0           | 0            | 3000      | 2000       | 0            | 0             |
      | 6000 | 1 March 2026    | 3600      | 2400       | -600        | -400         | 3600      | 2400       | -600         | -400          |
      | 5000 | 1 March 2026    | 1000      | 2000       | 0           | 0            | 3000      | 2000       | 2000         | 0             |
      | 5000 | 1 March 2026    | 3500      | 2000       | 0           | 0            | 3000      | 2000       | -500         | 0             |
      | 5000 | 15 January 2027 | 0         | 0          | 3000        | 2000         | 0         | 0          | 3000         | 2000          |

  Scenario: The finalized statement and PDF show the special levy and keep their figures
    Given the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros
    And Anna and Berta have paid their Hausgeld in full
    And a resolved special levy of 5000 euros is due on 1 March 2026
    And Anna has paid 3000 euros of the levy and Berta 2000 euros
    When I finalize their WEG annual settlement
    Then the PDF shows the levy Soll and payment of Anna and Berta
    When Anna pays another 100 euros of the levy
    Then the finalized statement, its digest and its PDF are unchanged

  Scenario Outline: The owner on the due date owes the levy after a mid-year sale
    Given a successor owns unit 2 from July after the plan was finalized and all costs belong to January through June
    And a resolved special levy of 5000 euros is due on <due>
    When I finalize their WEG annual settlement
    Then the levy Soll is 3000 euros for Anna, <berta> euros for Berta and <clara> euros for her successor Clara

    Examples:
      | due            | berta | clara |
      | 1 March 2026   | 2000  | 0     |
      | 1 October 2026 | 0     | 2000  |

  Scenario: Co-owners share the levy by their relative shares
    Given Anna owns unit 1 alone and Bernd and Carla own unit 2 with shares of 50 and 50, both units of 500 MEA with 1000 euros of costs by MEA
    And a resolved special levy of 1000 euros is due on 1 March 2026
    When I compute the WEG annual settlement of the co-owned units
    Then the levy Soll is 500 euros for Anna and 250 euros each for Bernd and Carla

  Scenario: A corrected ownership entry takes no part in a later levy
    Given the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros
    And Berta's ownership entry was corrected to start on 1 June 2019
    And a resolved special levy of 5000 euros is due on 1 March 2026
    When I compute the WEG annual settlement with the levy
    Then the levy Soll is 3000 euros for Anna and 2000 euros once for Berta

  Scenario: A correction after the levy leaves the frozen levy share with its owner
    Given the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros
    And a resolved special levy of 5000 euros is due on 1 March 2026
    And Berta's ownership entry was corrected to start on 1 June 2019
    When I compute the WEG annual settlement with the levy
    Then the levy Soll is 3000 euros for Anna and 2000 euros once for Berta

  Scenario: A levy allocated before the sale was recorded refuses the calculation
    Given the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros
    And a resolved special levy of 5000 euros is due on 1 October 2026
    And the sale of unit 2 from Berta to Clara from July is recorded afterwards
    And Clara has paid 2000 euros of the levy in October
    When I try to compute the WEG annual settlement with the levy
    Then the calculation is refused in German, naming the 5.000,00 euro levy, unit 2 and 1 October 2026

  Scenario: A levy due before the sale stays with the seller although the sale is recorded afterwards
    Given the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros
    And a resolved special levy of 5000 euros is due on 1 March 2026
    And the sale of unit 2 from Berta to Clara from July is recorded afterwards
    When I compute the WEG annual settlement with the levy
    Then the levy Soll is 3000 euros for Anna, 2000 euros for Berta and 0 euros for her successor Clara

  @browser
  Scenario: The special levy block fits desktop and a 360 pixel phone
    Given the WEG plan has 7000 euros of costs and the roof repair cost another 5000 euros
    And Anna and Berta have paid their Hausgeld in full
    And a resolved special levy of 5000 euros is due on 1 March 2026
    And Anna has paid 3000 euros of the levy and Berta 2000 euros
    When I finalize their WEG annual settlement
    Then Anna's statement shows the special levy block without horizontal scrolling
