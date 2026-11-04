Feature: WEG settlement and the owner's Hausgeld balance
  The resolved cost adjustment remains separate from payments owed under the plan.

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
