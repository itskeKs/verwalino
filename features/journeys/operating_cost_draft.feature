@browser @journey
Feature: Operating cost draft
  The manager can open the saved statement immediately for the selected tenancy.

  Scenario: The statement picker offers the lease-bounded period on desktop and mobile
    Given an administrator of "Verwaltung Alpha" is signed in
    And a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October
    When the manager opens the statement picker for tenant A
    Then the offered and selected statement period ends on 30 September at desktop and mobile widths
    When the manager saves tenant A's statement
    Then the draft opens immediately for tenant A from 1 January through 30 September 2025
