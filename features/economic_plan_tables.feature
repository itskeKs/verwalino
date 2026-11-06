@browser
Feature: Economic plan tables stay inside their cards
  Calculated advances and planned costs remain readable by scrolling each table
  on a phone, without moving the whole page sideways.

  Scenario Outline: Complete economic plan amounts remain reachable on phone and desktop
    Given eight equal WEG units with long owner names and five-digit planned costs
    When I calculate and display their <state> economic plan
    Then both economic plan tables scroll independently on a 360 pixel phone and retain their desktop values

    Examples:
      | state      |
      | calculated |
      | finalized  |
