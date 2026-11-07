Feature: Annual settlement after a change of advance mode
  Journey 4 carries booked advances through a prospective change of rental rates.

  Scenario: Deriving a year-only advance retires the previous draft calculation
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with 750 euros rent and 240 euros combined advance
    When the manager changes to 150 euros operating and 90 euros heating from July
    And books one 2340 euro advance for 2026 without a month
    And calculates the annual draft with 1900 euros operating and 1000 euros heating costs and zero advances
    And derives the advances from bookings and reopens the draft
    Then the reloaded draft has 2340 euros in advances and no calculated balance or lines
    When the manager confirms the new advance basis and calculates and finalizes the draft
    Then the statement and PDF show one unallocated advance of 2340 euros and 560 euros payable
    And neither advance setter can change the finalized result or PDF
