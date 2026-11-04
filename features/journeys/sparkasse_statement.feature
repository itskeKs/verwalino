@journey @browser
Feature: Sparkasse statement
  The original synthetic acceptance statement imports without rewriting dates,
  keeps counterparties for rent suggestions and can be uploaded again safely.

  Scenario: Anna's April partial rent is recognised in the original Sparkasse statement
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna Beispiel's rental account and the January to October Sparkasse statement
    When the manager previews the original Sparkasse statement in "Kontoauszug"
    Then the browser shows 31 new transactions and an empty transaction list
    When the manager confirms the Sparkasse statement
    Then the browser confirms 31 imported transactions
    And Anna's April receipt shows 800 EUR on 07.04.2026 with her name and IBAN
    And Anna's April receipt visibly suggests her lease
    When the manager previews the original Sparkasse statement in "Kontoauszug"
    Then the browser shows 31 duplicate transactions and no new transactions
    When the manager confirms the Sparkasse statement
    Then the browser confirms no imported transactions and still lists 31 after reload

  Scenario: A Sparkasse statement for another account is refused without importing anything
    Given a rental bank account ready for a statement
    When the manager previews the original Sparkasse statement in "Kontoauszug"
    Then the browser refuses the foreign account statement in German and lists no transactions
