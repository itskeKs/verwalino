Feature: Booking the monthly Hausgeld of a finalized plan in one step
  The manager books the Hausgeld that an owner paid during the plan year from the finalized
  economic plan in one confirmation instead of entering one payment per month. The plan names the
  owner and the monthly amount, the ownership periods name who owns a unit in each month, and a
  month that is already booked is never offered again.

  Scenario: A year of Hausgeld is booked in one step and is not offered again
    Given Gartenstraße 5 has a finalized plan for 2026 with a monthly Hausgeld of 600 euros for Anna Berg
    When the manager books all twelve months of Anna Berg from the bank account with January on 5 January
    Then twelve booked payments of Anna Berg for 2026 exist with a total of 7200 euros
    And the January payment has the value date 5 January 2026
    And the Hausgeld booking offers no open month of Anna Berg any more

  Scenario: A buyer is offered the months from the transfer
    Given the second flat of Gartenstraße 5 belongs to Karl Weiß until 30 June 2026 and to Lena Roth from 1 July 2026
    When the manager opens the Hausgeld booking of the plan
    Then Karl Weiß is offered January to June and Lena Roth July to December, 400 euros each
