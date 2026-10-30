@browser
Feature: Dunning interest
  The issued notice and its PDF show the interest reviewed by the manager.

  Scenario: Issuing a notice preserves the reviewed interest in its PDF
    Given an unpaid rental agreement for 700 EUR rent and no advance payment
    When the manager creates a January notice as of 2 March 2026 with a base rate of "2,50"
    Then the reloaded notice shows 8.63 EUR interest and 708.63 EUR in total
    When the manager confirms "Mahnung ausgeben"
    Then the issued notice still shows the reviewed amounts after reload
    And "PDF herunterladen" opens a PDF with the tenant and the reviewed interest
