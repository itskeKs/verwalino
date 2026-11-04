Feature: Heating settlement
  Missing interim heat readings use the approved degree-day table. Hot water
  and basic costs retain calendar-day shares; exact interim readings take priority.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in

  Scenario: A midyear tenant change without interim readings uses degree days only for heating consumption
    Given a unit with a tenant change on 1 July 2026 and annual heating and hot-water readings
    When the manager finalizes both tenants' internal heating statements
    Then heating consumption costs are 1166.09 and 833.91 euros
    And hot-water consumption costs are 198.36 and 201.64 euros
    And both PDFs explain the degree-day heating split and the daily hot-water split

  Scenario: An exact interim heat reading takes priority over degree days
    Given a unit with a tenant change on 1 July 2026 and annual heating and hot-water readings
    And an exact heat reading at the tenant change records 40 percent of consumption
    When the manager finalizes both tenants' internal heating statements
    Then heating consumption costs are 800.00 and 1200.00 euros
    And both PDFs retain the interim reading without a degree-day fallback

  Scenario: Exact degree-day weights preserve the balancing cent for a small heating pool
    Given a unit with a midyear tenant change and annual heating costs of 23 euros
    When the manager finalizes both tenants' internal heating statements
    Then heating consumption costs are 6.71 and 4.79 euros with the exact tied cent assigned to the larger share
