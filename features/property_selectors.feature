@browser
Feature: Property selectors
  Long property names remain available in native selectors without widening forms.
  Cost filters and the expanded cost form retain saved costs for both properties.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And properties with a long and a short name are available

  Scenario Outline: Property selectors fit before and after keyboard selection
    When the manager opens <screen> at <width> pixels wide
    Then the property selector and surrounding controls fit before and after keyboard selection

    Examples:
      | screen      | width |
      | Tasks       | 360   |
      | Letters     | 360   |
      | Reports     | 360   |
      | Tasks       | 1280  |
      | Letters     | 1280  |
      | Reports     | 1280  |
      | Costs       | 360   |
      | Levies      | 360   |
      | Meetings    | 360   |
      | Resolutions | 360   |
      | Payments    | 360   |
      | Person      | 360   |
      | Costs       | 1280  |
      | Levies      | 1280  |
      | Meetings    | 1280  |
      | Resolutions | 1280  |
      | Payments    | 1280  |
      | Person      | 1280  |
      | CostForm    | 360   |
      | CostForm    | 1280  |

  Scenario Outline: The existing property form keeps its responsive columns
    When the manager starts a new property at <width> pixels wide
    Then its controls fit the established responsive columns

    Examples:
      | width |
      | 360   |
      | 1280  |
