@journey @browser
Feature: Due rent within the full-year account
  Journey 2 distinguishes Anna's real April arrears from rent not yet due.

  Scenario Outline: The November reference date leaves future rent out of arrears
    Given Anna's full-year rent account has 100 euros unpaid through October
    When the manager opens the full-year rent account at <width> px with cutoff 2026-11-01
    Then the account keeps 1900 euros balance with 100 euros due and 1800 euros not yet due
    When the manager changes the cutoff to 2026-11-04
    Then the account keeps 1900 euros balance with 1000 euros due and 900 euros not yet due

    Examples:
      | width |
      | 1280  |
      | 360   |
