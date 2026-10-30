@browser
Feature: Bank statement browser
  A manager reviews a statement in the browser and explicitly confirms the import.

  Scenario: Confirming a reviewed statement makes its transactions visible after reload
    Given a rental bank account ready for a statement
    When the manager previews the statement in "Kontoauszug"
    Then the browser shows the statement summary and an empty transaction list
    When the manager changes the selected statement
    Then "Übernehmen" is hidden until another preview
    When the manager previews and confirms the statement
    Then the imported transactions remain visible after reload
    And the "Zugeordnet" filter shows no transactions
