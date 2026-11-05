@browser @journey
Feature: Readable issued dunning notice
  A manager can read the frozen amounts and issue time on a phone.

  Scenario: Owner drafts open immediately after a July ownership change
    Given Karl Weiß owns unit 2 until June and Lena Roth owns it from July
    When the manager creates Karl's and Lena's notices for the 2026 plan as of 15 January 2027
    Then each draft opens immediately with only its six monthly advances and no duplicate

  Scenario: An ordinary tenant draft opens immediately after creation
    Given an unpaid rental agreement for 700 EUR rent and no advance payment
    When the manager creates an ordinary January tenant notice as of 2 March 2026
    Then the tenant draft opens immediately with 700 EUR principal and no duplicate

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
