Feature: Inbox review chain
  Inbox documents become costs only after processing and explicit human review.

  Scenario: Confirming an inbox proposal creates a cost with the reviewed values
    Given an inbox reviewer with a property and accounting scope
    When the reviewer uploads a text invoice to the inbox
    And the inbox processes the document
    Then the invoice proposal is ready for review without creating a cost
    When the reviewer checks the original and confirms a corrected amount of 120.00 EUR
    Then a confirmed cost contains the reviewed invoice values

  Scenario: A batch the reviewer uploaded is a Meldung of the reviewer until its processing finished
    Given an inbox reviewer with a property and accounting scope
    When the reviewer uploads a text invoice to the inbox
    Then the reviewer finds the batch among the Meldungen as "WAITING" at the stage "inspection"
    When the inbox processes the document
    Then the invoice proposal is ready for review without creating a cost
    And the reviewer finds no Meldung of the batch
