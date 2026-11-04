@browser @journey
Feature: German money input in tenancies and costs
  Journey 1 retains Anna's rent, both advances and deposit; journey 2 retains a 60 EUR cost.

  Scenario Outline: Anna's tenancy accepts German amounts
    Given a rental property and tenant ready for a new tenancy
    When I save a tenancy with German rent and deposit at <width> px
    Then the reloaded tenancy retains 750 EUR rent, 150 EUR operating advance, 90 EUR heating advance and 2250 EUR deposit
    And invalid tenancy money shows a German field problem without losing my input

    Examples:
      | width |
      | 1280  |
      | 360   |

  Scenario Outline: A manual cost accepts German amounts
    Given a rental property ready for a manual cost
    When I save a 60 EUR cost with a decimal comma at <width> px
    Then the reloaded cost retains 60 EUR
    And invalid cost money shows a German field problem without losing my input

    Examples:
      | width |
      | 1280  |
      | 360   |
