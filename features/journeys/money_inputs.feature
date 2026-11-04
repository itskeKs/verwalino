@browser @journey
Feature: German money input in tenancies and costs
  Journey 1 retains Anna's rent, both advances and deposit; journey 2 retains a 60 EUR cost.

  Scenario Outline: Anna's tenancy accepts German amounts
    Given a rental property and tenant ready for a new tenancy
    When I correct an excessive deposit and save a tenancy with German amounts at <width> px
    Then the reloaded tenancy retains 750 EUR rent, 150 EUR operating advance, 90 EUR heating advance and 2250 EUR deposit
    And invalid tenancy money shows a German field problem without losing my input

    Examples:
      | width |
      | 1280  |
      | 360   |

  Scenario: A commercial tenancy is exempt from the residential deposit cap
    Given a commercial unit and tenant ready for a new tenancy
    When I save a commercial tenancy with 750 EUR rent and 2400 EUR deposit
    Then the reloaded commercial tenancy retains 2400 EUR deposit

  Scenario Outline: A manual cost accepts German amounts
    Given a rental property ready for a manual cost
    When I save a 60 EUR cost with a decimal comma at <width> px
    Then the reloaded cost retains 60 EUR
    And invalid cost money shows a German field problem without losing my input

    Examples:
      | width |
      | 1280  |
      | 360   |
