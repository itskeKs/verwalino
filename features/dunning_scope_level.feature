Feature: Dunning level of a rental agreement across accounting scopes
  The level of a tenant notice counts how often the same months of the same rental agreement were
  already demanded, in whichever accounting scope the earlier notice was made. The amounts stay
  with the scope of the notice.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a rental agreement of 600 EUR rent that two accounting scopes of 2026 cover

  Scenario: The second scope's notice for a month the first scope demanded is a dunning
    Given the first scope issued a reminder for June 2026 as of 15 July 2026
    When the manager creates a notice for June 2026 in the second scope as of 20 July 2026
    Then the notice is a level 2 dunning with 2.50 fee and 602.50 total

  Scenario: A notice that was not issued does not raise the level
    Given the first scope prepared a reminder for June 2026 as of 15 July 2026 and did not issue it
    When the manager creates a notice for June 2026 in the second scope as of 20 July 2026
    Then the notice is a level 1 reminder with 0.00 fee and 600.00 total

  Scenario: Another month of the rental agreement is a first reminder
    Given the first scope issued a reminder for June 2026 as of 15 July 2026
    When the manager creates a notice for July 2026 in the second scope as of 20 August 2026
    Then the notice is a level 1 reminder with 0.00 fee and 600.00 total
