Feature: Dunning interest
  The issued notice and its PDF show the interest reviewed by the manager.

  @browser
  Scenario: Issuing a notice preserves the reviewed interest in its PDF
    Given April rent of 700 EUR with 600 EUR paid on 7 April 2026
    When the administrator saves the dunning settings in the browser
    And the manager issues a first reminder for the April arrears
    And the manager creates an April notice as of 31 October 2026
    Then the reloaded notice shows 3.64 EUR interest and 106.64 EUR in total
    When the manager confirms "Mahnung ausgeben"
    Then the issued notice still shows the reviewed amounts after reload
    And "PDF herunterladen" opens a PDF with the tenant and the reviewed interest
    And the rent account lists the issued interest separately from the open rent

  Scenario: April rent paid on the third working day is on time
    Given April rent of 700 EUR with 700 EUR paid on 7 April 2026
    When the manager checks April reminders on 7 April 2026
    Then no April reminder or interest is due

  Scenario: April rent short by 100 EUR accrues interest from 8 April
    Given April rent of 700 EUR with 600 EUR paid on 7 April 2026
    When the manager checks April reminders on 8 April 2026
    Then the April reminder claims 100 EUR due on 7 April and interest from 8 April
