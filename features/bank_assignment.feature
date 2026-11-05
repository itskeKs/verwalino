@browser
Feature: Bank assignment
  Rent receipts can be assigned from a suggestion or an explicit split.
  Unrelated receipts can be ignored without being credited to rent.

  Background:
    Given a rental bank account with a full rent receipt, a partial receipt and an unrelated receipt

  Scenario: Accepting a rent suggestion pays both parts of the monthly rent
    When the manager accepts the January rent suggestion
    Then the January receipt appears under the "Zugeordnet" filter
    And the January rent account shows both rent parts paid in full

  Scenario: Splitting a partial receipt leaves the unpaid rent visible
    When the manager accepts the January rent suggestion
    And the manager splits the February receipt into 500 EUR rent and 100 EUR advance payment
    And the manager ignores the unrelated receipt
    Then the reloaded transaction filters separate assigned and ignored receipts
    And the "Neu" filter shows no transactions
    And the February rent account shows 250 EUR still open

  Scenario: Assigning an electricity debit keeps the existing yearly expense once
    Given a confirmed electricity cost of 60 EUR and a 60 EUR bank debit
    When the manager assigns the electricity debit to the existing cost
    Then the electricity debit is assigned and the cost has 0 EUR remaining
    And the yearly electricity expense remains 60 EUR

  Scenario: Two outgoing instalments cover one existing cost position
    Given a confirmed electricity cost of 120 EUR and two 60 EUR bank debits
    When the manager assigns the electricity debit to the existing cost
    Then the electricity debit is assigned and the cost has 60 EUR remaining
    When the manager assigns the second electricity debit to the existing cost
    Then the electricity debit is assigned and the cost has 0 EUR remaining
    And the yearly electricity expense remains 120 EUR

  Scenario: A corrected cost keeps coverage from its original version
    Given a confirmed electricity cost of 150 EUR and two 60 EUR bank debits
    When the manager assigns the electricity debit to the existing cost
    And the manager confirms a correction of the electricity cost to 120 EUR
    Then the electricity debit is assigned and the cost has 60 EUR remaining
    When the manager assigns the second electricity debit to the existing cost
    Then the electricity debit is assigned and the cost has 0 EUR remaining
    And the yearly electricity expense remains 120 EUR
