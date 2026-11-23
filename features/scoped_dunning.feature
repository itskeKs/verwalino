@browser
Feature: Scoped dunning
  Open Items carries the chosen accounting scope into the notice form.
  A discarded draft remains visible in the notice history.

  Scenario: The Open Items shortcut keeps the chosen scope through correction and discard
    Given a rental agreement of 850 EUR rent with receipts in two separate accounting scopes
    When the manager opens the notice form from the first scope's open items
    And the manager corrects a reporting period outside that scope
    Then the notice form keeps the first scope selected and requires no manual rate
    When the manager creates the scoped notice
    Then the notice demands what the lease owes in total
    When the manager confirms "Entwurf verwerfen"
    Then the discarded notice remains visible as "Verworfen" after reload
