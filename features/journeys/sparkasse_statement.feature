@journey @browser
Feature: Sparkasse statement
  The original synthetic acceptance statement imports without rewriting dates,
  keeps counterparties for rent suggestions and can be uploaded again safely.

  Scenario Outline: January electricity matches its existing cost without doubling the expense
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna Beispiel's rental account and the January to October Sparkasse statement
    And a confirmed 60 EUR electricity cost for the January Sparkasse debit
    When the manager previews the original Sparkasse statement in "Kontoauszug"
    And the manager confirms the Sparkasse statement
    Then the browser confirms 31 imported transactions
    When the manager assigns the January Sparkasse electricity debit at <width> px
    Then the electricity debit is assigned and the cost has 0 EUR remaining
    And the yearly electricity expense remains 60 EUR

    Examples:
      | width |
      | 1280  |
      | 360   |

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

  Scenario Outline: A long lease label leaves the split dialog readable and editable
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna Beispiel's rental account and the January to October Sparkasse statement
    And Anna's dwelling has a long descriptive name
    When the manager previews the original Sparkasse statement in "Kontoauszug"
    And the manager confirms the Sparkasse statement
    And the manager opens Anna's April split at <width> px
    Then the split explanation and labelled fields fit the dialog
    When the manager corrects the split to 650 EUR rent and 150 EUR advance with a note
    Then Anna's April receipt remains assigned after reload

    Examples:
      | width |
      | 1280  |
      | 800   |
      | 360   |
