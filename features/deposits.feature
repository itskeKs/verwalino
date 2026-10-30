@browser
Feature: Deposits
  Deposit receipts and refunds appear separately from the rent account.

  Scenario: Two deposit instalments and a refund leave the retained deposit visible
    Given a rental agreement with an expected deposit of 1800 EUR and a deposit bank account
    When the manager opens "Kautionen" for the rental accounting scope
    Then the deposit overview shows 0 EUR paid, 0 EUR refunded and 0 EUR held
    When the manager books a deposit receipt of 900 EUR on 2 January 2026
    Then the booked deposit detail shows "Zahlungstyp Kaution" without "Buchen"
    And the deposit overview shows 900 EUR paid, 0 EUR refunded and 900 EUR held
    When the manager books a deposit receipt of 900 EUR on 16 January 2026
    Then the deposit overview shows 1800 EUR paid, 0 EUR refunded and 1800 EUR held
    When the manager books a deposit refund of 300 EUR on 1 February 2026
    Then the reloaded deposit overview shows 1800 EUR paid, 300 EUR refunded and 1500 EUR held
    And the rent account still shows no rent payments
