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
