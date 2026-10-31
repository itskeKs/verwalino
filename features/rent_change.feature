@browser
Feature: Rent change
  A dated rent change appears in the history and in the rent account from its effective month.

  Scenario: A dated rent change preserves the original contract and changes later rent
    Given a rental agreement for 700 EUR rent and 150 EUR advance payment
    When the manager records 750 EUR rent from 1 April 2026 in "Miete ändern"
    Then the reloaded rent history shows the change beside the original contract
    And the annual rent account shows the old rent until March and the new rent from April
