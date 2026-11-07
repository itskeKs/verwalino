@browser @journey
Feature: Finding people for a new tenancy or ownership
  Existing people become selectable only after the manager marks their relevant profile.

  Scenario Outline: An empty selection explains how to mark only Anna
    Given a fresh organization with Anna Beispiel and Bernd Test without profiles
    When I follow the empty <kind> hint and mark only Anna at <width> px
    Then returning through the app offers Anna but not Bernd for <kind>

    Examples:
      | kind      | width |
      | tenancy   | 1280  |
      | tenancy   | 360   |
      | ownership | 1280  |
      | ownership | 360   |
