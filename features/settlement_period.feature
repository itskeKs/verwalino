Feature: Settlement period
  A property's start month defines the period offered for statements and economic
  plans. Booked advances belong to their assigned months across year boundaries.
  A year-only advance is split across that calendar year's months.

  Scenario: An April property uses the same period for statements and plans
    Given an administrator of "Verwaltung Alpha" is signed in
    And a mixed property with a tenancy and an April settlement start
    And advances booked for December 2025 and January 2026
    When the manager chooses the offered statement period 2025/2026
    Then the draft covers 1 April 2025 through 31 March 2026
    And both booked advances are credited
    When the manager finalizes the statement
    And the manager tries to create an overlapping statement for that tenancy
    Then the overlapping statement is refused
    When the manager creates the property's economic plan for 2025
    Then the plan covers 1 April 2025 through 31 March 2026

  Scenario: A former tenant's short statement blocks a longer statement for that lease
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    When the manager finalizes statements for each tenant's occupied months
    Then A's statement covers January through September and B's covers October through December
    When the manager tries to create a free statement extending beyond tenant A's lease
    Then the statement is refused with a German lease-period error
    When the manager repeats A's January through September statement
    Then the identical statement can be finalized again

  Scenario: Calendar-year costs are shared between successive tenants by occupancy
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    And annual operating costs of 3650 euros for that unit
    When the manager finalizes clipped property-period calculations for both tenants
    Then A pays 2730 euros for January through September and B pays 920 euros for October through December

  Scenario: A property-period statement is clipped to the former tenant's lease
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    When the manager creates a property-period statement for tenant A
    Then the created statement ends on 30 September
    When the manager tries to create a free statement extending beyond tenant A's lease
    Then the statement is refused with a German lease-period error

  @browser
  Scenario: The statement picker offers the lease-bounded period on desktop and mobile
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    When the manager opens the statement picker for tenant A
    Then the offered and selected statement period ends on 30 September at desktop and mobile widths


  Scenario: A yearly advance is shared between adjacent statements without double credit
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year tenancy with a booked yearly advance of 1200 euros
    When the manager finalizes statements for January through June and July through December
    Then each statement credits 600 euros of the yearly advance
