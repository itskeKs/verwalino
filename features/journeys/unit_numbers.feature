@journey @browser
Feature: Unit numbers
  Each unit number identifies one unit in a property.

  Scenario Outline: A property refuses a duplicate unit number
    Given a rental property with an existing "Wohnung 1"
    When the manager tries to add another "Wohnung 1" at <width> px
    Then the unit form explains the conflict and keeps the number for correction

    Examples:
      | width |
      | 1280  |
      | 360   |
