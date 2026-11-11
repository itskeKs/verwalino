@browser
Feature: Scoped statements and deadlines
  The rent statement, the open items and the deadline monitor follow the
  selected accounting scope. When access is revoked they report the loss
  instead of showing stale numbers or an invented all-clear.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in

  Scenario: The rent statement shows only the selected scope and rejects outside periods
    Given a lease with one booked payment in each of two granted scopes exists
    When the manager opens the rent statement for the lease and the first scope
    Then the statement shows 100,00 € and no 1.000,00 €
    When the manager requests the statement from 2024-01-01
    Then the statement reports the period as outside the accounting scope
    When the manager restores the period and retrieves the statement again
    Then the statement shows 100,00 € and no 1.000,00 €

  Scenario: Open items follow the scope and hide results after revocation
    Given a lease with one booked payment in each of two granted scopes exists
    When the manager opens the open items for the first scope
    Then the open items show 7.100,00 € outstanding
    And the page fits a 390 pixel wide viewport
    When the grant for the first scope is revoked and the open items are reloaded
    Then the open items are reported as not accessible
    And neither stale amounts nor an empty success are shown
    When the manager opens the dashboard after the revocation
    Then the dashboard reports the deadline overview as unavailable without an all-clear
    When the manager opens the deadline monitor after the revocation
    Then the monitor reports the overview as unavailable without an all-clear

  Scenario: The deadline monitor shows only the selected scope's deadlines
    Given a mixed-use property with confirmed costs in a rental and a WEG scope exists
    When the manager opens the deadline monitor and chooses the rental scope
    Then the monitor shows the rental deadline and no WEG annual statement
    When the manager chooses the WEG scope
    Then the monitor shows the WEG annual statement and no rental deadline
    And the page fits a 390 pixel wide viewport
    When the manager opens the dashboard with the WEG scope
    Then the dashboard does not invent tenant debt for the WEG scope
    When the grant for the WEG scope is revoked and the monitor is requeried
    Then the monitor reports the loss without an all-clear
