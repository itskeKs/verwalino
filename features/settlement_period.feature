Feature: Settlement period
  A property's start month defines the period offered for statements and economic
  plans. Booked advances belong to their assigned months across year boundaries.
  A year-only advance is split across the lease's months in that calendar year.
  Consumption weights describe the property year; each invoice retains its own
  service period. An interim reading on the outgoing tenant's last day or the
  incoming tenant's first day denotes the same boundary. Multiple readings on
  either boundary date require manual input; missing interim readings use days
  with a reason. Interim readings never replace property-period boundary readings.

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

  Scenario Outline: A clipped statement credits all advances for its own lease
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year tenancy starting on <start> with <kind> advances
    When the manager finalizes the lease's clipped statement with derived advances of <total> euros
    Then the finalized clipped statement credits <total> euros

    Examples:
      | start      | kind              | total   |
      | 2025-03-15 | ten monthly 100   | 1000.00 |
      | 2025-07-01 | one year-only 600 | 600.00  |

  Scenario Outline: Either date of an occupant change splits only that unit's water share
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    And two units with annual water quantities of 1000 each and a water invoice of 1000 euros
    And an interim water reading of 600 on <date>
    When the manager finalizes clipped property-period calculations for both tenants
    Then A pays 300.00 euros and B pays 200.00 euros for water

    Examples:
      | date       |
      | 2025-09-30 |
      | 2025-10-01 |

  Scenario: A missing interim reading splits the known annual water quantity by days
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    And two units with annual water quantities of 1000 each and a water invoice of 1000 euros
    When the manager requests water suggestions for tenant A
    Then the suggestion explains the day fallback
    When the manager finalizes clipped property-period calculations for both tenants
    Then A pays 373.97 euros and B pays 126.03 euros for water

  Scenario: A December water invoice cannot charge a tenant who left in September
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    And two units with annual water quantities of 1000 each and a December water invoice of 100 euros
    When the manager finalizes clipped property-period calculations for both tenants
    Then A pays 0.00 euros and B pays 50.00 euros for water

  Scenario: A cross-boundary insurance source is charged once across two property years
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year tenancy with insurance of 3650 euros for July 2024 through June 2025
    When the manager finalizes property statements for 2024 and 2025
    Then the insurance shares are 1840.00 and 1810.00 euros

  Scenario: A batch skips a finalized legacy statement covering the clipped lease
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year tenancy with a finalized legacy statement and a subsequently shortened lease
    When the manager creates a calendar-year batch for that property
    Then the finalized legacy statement is skipped with the clipped-period reason

  Scenario: A draft legacy statement covering the clipped lease remains an overlap error
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year tenancy with a draft legacy statement and a subsequently shortened lease
    When the manager creates a calendar-year batch for that property
    Then the draft legacy statement causes an overlap error

  @browser
  Scenario: Manual unit inputs explain their property-year basis in a clipped statement
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    And two units with annual water quantities of 1000 each and a water invoice of 1000 euros
    And a person-based cost source for that property
    When the manager opens the structured inputs of tenant A's clipped statement
    Then manual consumption and persons explain the property-year basis on desktop and mobile
