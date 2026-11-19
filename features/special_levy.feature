@area:weg
Feature: Special levy
  A new special levy needs a recorded adopted resolution for the same property.
  Historical obligations retain their frozen amounts when their authority is linked.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And Gartenstraße 5 has two owners and an adopted roof repair resolution

  Scenario: A special levy without a resolution is refused without an obligation
    When the manager requests a special levy without selecting a resolution
    Then the manager is told that a special levy needs a resolution
    And no special levy has been created

  Scenario: The adopted roof repair resolution leads to its frozen levy
    When the manager creates the roof repair levy due on 1 July 2026
    Then the owner shares are 720 euros and 480 euros due on 1 July 2026
    And the roof repair register entry shows its levy amount and due date

  Scenario: A corrected ownership entry no longer takes part in a special levy
    When Berta's ownership entry is corrected to start on 1 August 2026
    And the manager requests the roof repair levy due on 1 July 2026
    Then the levy is refused because a unit has no owner on its due date
    And no special levy has been created

  Scenario: A sale recorded after the levy moves Berta's share to the buyer on the due date
    When the manager creates the roof repair levy due on 1 July 2026
    And Berta's unit is sold to Clara from 1 June 2026, recorded after the levy
    Then the levy shows 480 euros for Clara and nothing for Berta
    And Clara's owner account shows 480 euros due and Berta's shows nothing due

  Scenario: A levy due before the sale stays with the seller although the sale is recorded afterwards
    When the manager creates the roof repair levy due on 1 July 2026
    And Berta's unit is sold to Clara from 1 August 2026, recorded after the levy
    Then the levy shows 480 euros for Berta and nothing for Clara

  Scenario: The Mahnlauf still asks for an accounting scope once the organization holds a levy
    When the manager creates the roof repair levy due on 1 July 2026
    Then the Mahnlauf asks for an accounting scope
    And an overview opened without an accounting scope is explained in German

  Scenario: A tenant of a property without an accounting scope keeps the rent account and the notice once the organization holds a levy
    Given Sonnenweg 5 has no accounting scope and its tenant owes 700 euros rent for January 2026
    When the manager creates the roof repair levy due on 1 July 2026
    Then the rent account and the open items of that tenant show 700 euros open for January 2026
    And the manager can create a tenant notice over 700 euros for that rent

  @browser
  Scenario Outline: Missing resolution feedback preserves the levy inputs at <width> pixels
    When the manager clicks create without a resolution in the browser at <width> pixels
    Then the browser explains the missing resolution and retains the financial inputs
    And no special levy has been created

    Examples:
      | width |
      | 1280  |
      | 360   |

  @browser
  Scenario Outline: The manager creates and follows the resolution levy at <width> pixels
    When the manager creates the roof repair levy in the browser at <width> pixels
    Then the levy links to the meeting of 15 May and TOP 3
    And the register links back to the levy due on 1 July

    Examples:
      | width |
      | 1280  |
      | 360   |
