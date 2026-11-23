Feature: Dunning set-off of an overpaid month
  A rental agreement owes its rent and advance payments of all months, less everything paid. What a month
  was paid beyond its charge settles the oldest due open position of the agreement, whatever its type, so a
  notice never demands more than the open items show.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in

  Scenario Outline: A month paid twice over leaves the notice at what the open items show
    Given a rental agreement of <rent> EUR rent and <advance> EUR advance payment per month in 2026
    And 1000 EUR is booked as rent for June 2026
    When the manager looks at the open items of 2026 as of 15 January 2027
    Then the open items add up to <open> EUR
    When the manager creates a notice for 2026 as of 15 January 2027
    Then the notice demands <open> EUR for the positions the open items list

    Examples:
      | rent | advance | open |
      | 500  | 100     | 6200 |
      | 850  | 0       | 9200 |

  Scenario: A surplus larger than the whole debt leaves no notice
    Given a rental agreement of 500 EUR rent and 0 EUR advance payment per month in 2026
    And 7000 EUR is booked as rent for June 2026
    When the manager looks at the open items of 2026 as of 15 January 2027
    Then the open items show a credit of 1000 EUR
    When the manager creates a notice for 2026 as of 15 January 2027
    Then no notice is created because nothing is overdue

  Scenario: Interest on a settled position ends when the money arrived
    # The base rate of the first half of 2026 is 1.27 %, with 5 points 6.27 % a year. March rent of 600 EUR fell
    # due on 4 March; April was paid 1000 EUR on 10 April, which settles 400 EUR of March. Interest on 600 EUR
    # for 36 days (5 March to 9 April) and on the 200 EUR left for 22 days (10 April to 1 May) is 4.47 EUR.
    Given the organization enables automatic dunning interest
    And a rental agreement of 600 EUR rent and 0 EUR advance payment per month in 2026
    And 1000 EUR is booked as rent for April 2026 on 10 April 2026
    When the manager creates a notice for March and April 2026 as of 1 May 2026
    Then the notice demands 200 EUR with 4.47 EUR interest
