@browser
Feature: Bank assignment
  Rent receipts can be assigned from a suggestion or an explicit split.
  Unrelated receipts can be ignored without being credited to rent.

  Background:
    Given a rental bank account with a full rent receipt, a partial receipt and an unrelated receipt

  Scenario: Accepting a rent suggestion pays both parts of the monthly rent
    When the manager accepts the January rent suggestion
    Then the January rent account shows both rent parts paid in full

  Scenario: Splitting a partial receipt leaves the unpaid rent visible
    When the manager splits the February receipt into 500 EUR rent and 100 EUR advance payment
    And the manager ignores the unrelated receipt
    Then the reloaded transaction filters separate assigned and ignored receipts
    And the February rent account shows 250 EUR still open
