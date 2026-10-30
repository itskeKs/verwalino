Feature: Settlement period
  A property's start month defines the period offered for statements and economic
  plans. Booked advances belong to their assigned months across year boundaries.

  Scenario: An April property uses the same period for statements and plans
    Given an administrator of "Verwaltung Alpha" is signed in
    And a mixed property with a tenancy and an April settlement start
    And advances booked for December 2025 and January 2026
    When the manager chooses the offered statement period 2025/2026
    Then the draft covers 1 April 2025 through 31 March 2026
    And both booked advances are credited
    When the manager tries to create an overlapping statement for that tenancy
    Then the overlapping statement is refused
    When the manager creates the property's economic plan for 2025
    Then the plan covers 1 April 2025 through 31 March 2026
