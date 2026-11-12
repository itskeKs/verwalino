Feature: Dunning notice payment destination
  An issued tenant notice names the unit, the property and the account to pay to,
  so the recipient can pay without guessing the destination.

  Scenario: The issued reminder names the unit, the property and the payment account
    Given April rent of 700 EUR with 600 EUR paid on 7 April 2026
    When the manager checks April reminders on 8 April 2026
    And the manager issues the created reminder
    Then the issued PDF names the unit, the property and the payment account
