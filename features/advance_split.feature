Feature: Separate operating-cost and heating advances
  Each lease carries separate advances. Payments stay assigned to their booked
  component; a statement credits them against the corresponding tenant costs.

  Scenario: Separate monthly charges and statement credits
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with 750 euros rent, 150 euros operating and 90 euros heating advance
    Then January has three expected charges totalling 990 euros
    When the manager derives missing heating advances and finalizes a statement with 1900 euros operating and 1000 euros heating costs
    Then the operating balance is 100 euros, the heating balance is minus 80 euros and the total is 20 euros
    And the PDF contains both component results
    When the manager accepts both proposed advances from January 2027
    Then one rent change carries 158.33 euros operating and 83.33 euros heating advance

  Scenario: A statement without heating retains the former single advance block
    Given an administrator of "Verwaltung Alpha" is signed in
    When the manager finalizes Bernd's statement without heating costs or heating advances
    Then the statement and PDF show one block of paid advances and a credit of 470 euros

  Scenario: Standalone refunds reduce each derived advance and finalized amounts stay frozen
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna paid 50 euros each in operating and heating advances for July 2026
    When the manager books a 70 euro refund for each advance and derives the 2026 statement
    Then each advance is minus 20 euros
    When the manager finalizes the statement with 100 euros operating and 80 euros heating costs
    Then the statement and PDF show both negative advances and 220 euros due
    When the manager books another refund for each advance
    Then the finalized statement and PDF still show both negative advances and 220 euros due
