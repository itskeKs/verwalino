Feature: Combined and separate operating-cost and heating advances
  A manager chooses how the lease collects operating and heating advances.
  All rent and advance charges share the third working day of the rental period.

  Scenario: Separate monthly charges and statement credits
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with 750 euros rent, 150 euros operating and 90 euros heating advance
    Then January has three expected charges totalling 990 euros
    And all April rent and advances are due on 7 April 2026
    When the manager derives missing heating advances and finalizes a statement with 1900 euros operating and 1000 euros heating costs
    Then the operating balance is 100 euros, the heating balance is minus 80 euros and the total is 20 euros
    And the PDF contains both component results
    When the manager accepts both proposed advances from January 2027
    Then one rent change carries 158.33 euros operating and 83.33 euros heating advance

  Scenario: A statement without heating retains the former single advance block
    Given an administrator of "Verwaltung Alpha" is signed in
    When the manager finalizes Bernd's statement without heating costs or heating advances
    Then the statement and PDF show one block of paid advances and a credit of 470 euros

  Scenario: One combined advance offsets operating and heating costs together
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with 750 euros rent and 240 euros combined advance
    Then April has two expected charges totalling 990 euros
    And all April rent and advances are due on 7 April 2026
    When the manager finalizes the annual statement with 1900 euros operating and 1000 euros heating costs
    Then the statement and PDF show 2880 euros combined credit and 20 euros payable
    And the combined proposal is 241.67 euros and adoption keeps the finalized statement unchanged

  Scenario: A July switch preserves the earlier bookings and credits each advance once
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with 750 euros rent and 240 euros combined advance
    When the manager books the first six combined advances
    And changes to 150 euros operating and 90 euros heating from July
    And the manager finalizes the annual statement with 1900 euros operating and 1000 euros heating costs
    Then the original bookings remain unchanged
    And the statement credits 1440 euros combined, 900 euros operating and 540 euros heating, with 20 euros payable

  Scenario: An annual advance across both modes is credited once without a component split
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with 750 euros rent and 240 euros combined advance
    When the manager changes to 150 euros operating and 90 euros heating from July
    And books one 2340 euro advance for 2026 without a month
    And finalizes the statement with 1900 euros operating and 1000 euros heating costs for that advance
    Then the statement and PDF show one unallocated advance of 2340 euros and 560 euros payable

  Scenario: Standalone refunds reduce each derived advance and finalized amounts stay frozen
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna paid 50 euros each in operating and heating advances for July 2026
    When the manager books a 70 euro refund for each advance and derives the 2026 statement
    Then each advance is minus 20 euros
    When the manager finalizes the statement with 100 euros operating and 80 euros heating costs
    Then the statement and PDF show both negative advances and 220 euros due
    When the manager books another refund for each advance
    Then the finalized statement and PDF still show both negative advances and 220 euros due
