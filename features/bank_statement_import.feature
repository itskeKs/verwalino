Feature: Bank statement import
  A CSV statement exported from the bank is first previewed, so the user sees
  how many transactions it holds, what they add up to and which period they
  cover, and nothing is stored yet. Confirming the import stores the
  transactions. Importing the same file again adds nothing new.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a bank account with the IBAN "DE02120300000000202051"
    And the DKB statement "dkb_2023.csv"

  Scenario: The preview shows the statement and stores nothing
    When the user previews the statement
    Then the preview shows 2 new transactions
    And the preview totals 1034.55 EUR for the period from 02.01.2026 to 04.01.2026
    And the bank account holds no transactions

  Scenario: Confirming the import stores the transactions
    When the user imports the statement
    Then 2 transactions are imported
    And the bank account holds 2 transactions

  Scenario: Importing the same statement again adds nothing
    Given the statement has been imported
    When the user imports the statement
    Then no transaction is imported
    And 2 transactions are recognised as already imported
    And the bank account holds 2 transactions
