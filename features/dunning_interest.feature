Feature: Dunning interest
  The issued notice and its PDF show the interest reviewed by the manager.

  @browser
  Scenario: Issuing a notice preserves the reviewed interest in its PDF
    Given an unpaid rental agreement for 700 EUR rent and no advance payment
    When the manager creates a January notice as of 2 March 2026 with a base rate of "2,50"
    Then the reloaded notice shows 7.91 EUR interest and 707.91 EUR in total
    When the manager confirms "Mahnung ausgeben"
    Then the issued notice still shows the reviewed amounts after reload
    And "PDF herunterladen" opens a PDF with the tenant and the reviewed interest

  Scenario: April rent paid on the third working day is on time
    Given April rent of 700 EUR with 700 EUR paid on 7 April 2026
    When the manager checks April reminders on 7 April 2026
    Then no April reminder or interest is due

  Scenario: April rent short by 100 EUR accrues interest from 8 April
    Given April rent of 700 EUR with 600 EUR paid on 7 April 2026
    When the manager checks April reminders on 8 April 2026
    Then the April reminder claims 100 EUR due on 7 April and interest from 8 April
