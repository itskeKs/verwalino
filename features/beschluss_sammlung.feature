Feature: Beschluss Sammlung
  Register entries retain the meeting date separately from the day of entry.

  Scenario: A later register entry keeps the date and place of its meeting
    Given an administrator of "Verwaltung Alpha" is signed in
    And Gartenstraße 5 has a held meeting on 15 June 2026 with a resolution
    When the manager enters that resolution into the register
    Then the register shows 15 June 2026 as the meeting date and a separate entry date
    And the register extract contains the meeting date and place
