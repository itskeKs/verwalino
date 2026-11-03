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

  @browser
  Scenario: The owner's payment explanation fits desktop and a 360 pixel phone
    Given Anna Berg's WEG plan is 7200 euros and her cost share is 6400 euros
    And Anna has booked 6600 euros of Hausgeld and the second owner has paid in full
    When I finalize their WEG annual settlement
    Then the Einzelabrechnung shows all four amounts and the payment without horizontal scrolling
