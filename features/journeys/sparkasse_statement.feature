Feature: Sparkasse statement
  The original synthetic acceptance statement imports without rewriting dates,
  keeps counterparties for rent suggestions and can be uploaded again safely.

  Scenario: Anna's April partial rent is recognised in the original Sparkasse statement
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna Beispiel's rental account and the January to October Sparkasse statement
    When the user previews the statement
    Then the preview shows 31 new transactions
    And the bank account holds no transactions
    When the user imports the statement
    Then 31 transactions are imported
    And Anna Beispiel's 800 EUR receipt on 07.04.2026 retains her name and IBAN
    And Anna's April receipt suggests her lease for the 800 EUR partial rent
    When the user imports the statement
    Then no transaction is imported
    And 31 transactions are recognised as already imported
    And the bank account holds 31 transactions
