Feature: Vorwegabzug
  A reasoned commercial preallocation applies to the same cost position in every
  tenant statement, while each group uses the position's distribution key.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in

  Scenario: A commercial shop receives half the waste cost and both flats share the remainder
    Given a shop of 60 square metres and flats of 75 and 65 square metres with annual waste costs of 1200 euros
    When the manager calculates the statements without a commercial preallocation
    Then the shop and flats owe 360.00, 450.00 and 390.00 euros
    When the manager assigns 50 percent of the waste cost to commerce because of the restaurant waste bins
    Then the shop and flats owe 600.00, 321.43 and 278.57 euros
    And every finalized statement PDF explains the 600 euro commercial preallocation and its reason

  @browser
  Scenario: The manager enters a reasoned commercial share on desktop and a narrow phone
    Given a shop of 60 square metres and flats of 75 and 65 square metres with annual waste costs of 1200 euros
    When the manager opens the commercial position in the draft at desktop and phone widths
    And the manager enters and calculates the reasoned commercial share through the form
    Then the draft shows the commercial deduction and the flat's 321.43 euro share without clipped inputs
