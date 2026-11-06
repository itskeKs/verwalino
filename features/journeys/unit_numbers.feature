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

  Scenario Outline: Unit areas remain editable and accept a decimal comma
    Given a rental property with an existing "Wohnung 1"
    When the manager tries to save a new unit with invalid area at <width> px
    Then the area field explains the refusal and the form retains the input
    When the manager corrects the area to "75,50" and saves
    Then the saved unit shows 75.50 square metres
    When the manager edits the area to "76,25" and saves
    Then the saved unit shows 76.25 square metres

    Examples:
      | width |
      | 1280  |
      | 360   |
