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

  Scenario: A successor's assigned payment remains visible without cost or plan shares
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
