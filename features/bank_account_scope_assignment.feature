@area:weg
Feature: Bank account scope assignment
  A bank account saved without an Abrechnungskreis blocks the calculations of its property
  until it is assigned one. The refusal names the account and the way out, and the
  administrator assigns the Abrechnungskreis on the bank accounts page.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And Gartenstraße 5 has two owners and an adopted roof repair resolution
    And the Laufendes Konto of Gartenstraße 5 was saved without an Abrechnungskreis

  Scenario: The levy refusal names the account and the way out
    When the manager tries to create the roof repair levy due on 1 July 2026
    Then the manager is told which account needs an Abrechnungskreis

  Scenario: Assigning the Abrechnungskreis lets the levy be created
    When the administrator assigns the Abrechnungskreis of Gartenstraße 5 to the Laufendes Konto
    And the manager creates the roof repair levy due on 1 July 2026
    Then the owner shares are 720 euros and 480 euros due on 1 July 2026

  @browser
  Scenario Outline: The administrator repairs the account in the browser at <width> pixels
    When the manager tries to create the roof repair levy in the browser at <width> pixels
    Then the browser shows one refusal that names the Laufendes Konto and the assignment
    When the administrator follows the overview counter and assigns the Abrechnungskreis in the browser
    And the manager creates the roof repair levy in the browser at <width> pixels
    Then the roof repair levy shows the owner shares of 720 euros and 480 euros

    Examples:
      | width |
      | 1280  |
      | 360   |
