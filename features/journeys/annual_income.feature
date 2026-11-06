@journey @browser
Feature: Annual income without deposits
  Journey 5 keeps trust money separate from received rental income.

  Scenario: The annual overview and CSV exclude Anna's deposit through 31 October 2026
    Given the journey 5 rental receipts through 31 October 2026 and a separate 2250 euro deposit
    And confirmed electricity costs of 10 times 60 euros and insurance of 312 euros
    When the landlord displays the property year overview in the browser
    Then all annual expense columns are reachable without horizontal page scrolling
    And the overview shows 14660 euros income and the separate deposit on desktop and at 360 px
    And both CSV exports show 14660 euros income and the overview CSV includes the expenses and balance
