Feature: PDF recipient wording
  Owner-facing reports explain their values without internal development notes.

  Scenario: The asset report explains administrator statements to its recipient
    Given an administrator of "Verwaltung Alpha" is signed in
    And Gartenstraße 5 has a finalized asset report for 2026 with a declared bank balance of 500 euros
    When the administrator downloads that asset report twice
    Then the report explains administrator statements without development notes
    And downloading leaves the finalized report unchanged
