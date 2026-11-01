Feature: Separate operating-cost and heating advances
  Each lease carries separate advances. Payments stay assigned to their booked
  component; a statement credits them against the corresponding tenant costs.

  Scenario: Separate monthly charges and statement credits
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with 750 euros rent, 150 euros operating and 90 euros heating advance
    Then January has three expected charges totalling 990 euros
    When the manager finalizes a statement with 1900 euros operating and 1000 euros heating costs
    Then the operating balance is 100 euros, the heating balance is minus 80 euros and the total is 20 euros
    And the PDF contains both component results
    When the manager accepts both proposed advances from January 2027
    Then one rent change carries 158.33 euros operating and 83.33 euros heating advance
