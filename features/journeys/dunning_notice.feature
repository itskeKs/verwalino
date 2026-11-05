@browser @journey
Feature: Readable issued dunning notice
  A manager can read the frozen amounts and issue time on a phone.

  Scenario: Issued principal, interest and arrears are readable without horizontal scrolling
    Given an unpaid rental agreement for 1234.56 EUR rent and no advance payment
    When the manager creates a January notice as of 2 March 2026 with automatic interest
    And the manager confirms "Mahnung ausgeben"
    Then the issued principal, interest and open amounts are readable without horizontal scrolling
    And the issue time is displayed in German date and minute format

  Scenario: The issued April reminder explicitly names the first interest day
    Given April rent of 700 EUR with 600 EUR paid on 7 April 2026
    When the manager checks April reminders on 8 April 2026
    And the manager opens the created April reminder
    And the manager confirms "Mahnung ausgeben"
    Then the issued April reminder names 8 April 2026 as the interest start in the detail and PDF
