Feature: Dunning set-off of an overpaid month
  A rental agreement owes its rent and advance payments of every calendar year, less what was paid for that
  year. What a month was paid beyond its charge settles the oldest due open position of the same calendar year
  and the same type (rent or advance payment), so a notice never demands more than the open items show.

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

  Scenario Outline: A surplus settles the oldest open rent of the year wherever the notice looks
    # Rent 500 EUR and advance 100 EUR a month; June was paid 1000 EUR for 500 EUR owed. The 500 EUR over settle the
    # oldest due open rent of 2026, January's, whichever months a notice covers. January to May owes
    # 5 x 600 = 3000 EUR less that 500 EUR = 2500 EUR; June to December owes 7 x 600 = 4200 EUR less the 500 EUR paid
    # for June's own rent = 3700 EUR; the two add up to the 6200 EUR of the whole year.
    Given a rental agreement of 500 EUR rent and 100 EUR advance payment per month in 2026
    And 1000 EUR is booked as rent for June 2026
    When the manager creates a notice for <first> to <last> 2026 as of 15 January 2027
    Then the notice demands <open> EUR

    Examples:
      | first   | last     | open |
      | January | May      | 2500 |
      | June    | December | 3700 |

  Scenario: A surplus paid for a later month settles an earlier month of the notice
    # Rent 500 EUR a month; April was paid 1000 EUR for 500 EUR owed on 2 April. The 500 EUR over settle January's
    # rent, the oldest due open rent of the year, so a notice for January to March demands February and March,
    # 2 x 500 = 1000 EUR, and not 3 x 500 = 1500 EUR.
    Given a rental agreement of 500 EUR rent and 0 EUR advance payment per month in 2026
    And 1000 EUR is booked as rent for April 2026 on 2 April 2026
    When the manager creates a notice for January to March 2026 as of 15 April 2026
    Then the notice demands 1000 EUR

  Scenario: A surplus of rent settles no advance payment
    # Rent 500 EUR and advance 100 EUR a month; January was paid 1000 EUR as rent for 500 EUR owed, nothing else was
    # paid. The 500 EUR over settle February's rent, the oldest due open rent of the year, and nothing else: an advance
    # payment is no rent. A notice for January to February demands the two advances, 2 x 100 = 200 EUR.
    Given a rental agreement of 500 EUR rent and 100 EUR advance payment per month in 2026
    And 1000 EUR is booked as rent for January 2026
    When the manager creates a notice for January to February 2026 as of 15 April 2026
    Then the notice demands 200 EUR

  Scenario Outline: A payment of a type without a charge settles no rent
    # Rent 500 EUR a month; February and March are paid, January is not. A payment of another type for March (the
    # balance of a settlement, a deposit) is no money paid beyond a rent, so it settles nothing: January's 500 EUR stay
    # open.
    Given a rental agreement of 500 EUR rent and 0 EUR advance payment per month in 2026
    And 500 EUR is booked as rent for February 2026
    And 500 EUR is booked as rent for March 2026
    And 300 EUR is booked as <type> for March 2026
    When the manager creates a notice for January to March 2026 as of 15 April 2026
    Then the notice demands 500 EUR

    Examples:
      | type         |
      | NACHZAHLUNG  |
      | KAUTION      |

  Scenario: A surplus larger than the debt of its year leaves no notice
    # 2026 owes 12 x 500 = 6000 EUR in rent and 7000 EUR were paid for June, so 1000 EUR stay a credit, shown on the
    # month that was overpaid. January 2027, due on 5 January 2027, is a position of the next year: the credit is not
    # set off against it.
    Given a rental agreement of 500 EUR rent and 0 EUR advance payment per month in 2026
    And 7000 EUR is booked as rent for June 2026
    When the manager looks at the open items of 2026 as of 15 January 2027
    Then the open items show a credit of 1000 EUR
    When the manager creates a notice for 2026 as of 15 January 2027
    Then no notice is created because nothing is overdue

  Scenario: Interest on a settled position ends when the money arrived
    # The base rate of the first half of 2026 is 1.27 %, with 5 points 6.27 % a year. January and February are paid, so
    # March rent of 600 EUR, which fell due on 4 March, is the oldest open position. April was paid 1000 EUR on 10
    # April, which settles 400 EUR of March, although April is not part of the notice. Interest on 600 EUR for 36 days
    # (5 March to 9 April) and on the 200 EUR left for 22 days (10 April to 1 May) is 4.47 EUR.
    Given a rental agreement of 600 EUR rent and 0 EUR advance payment per month in 2026
    And the organization enables automatic dunning interest
    And 600 EUR is booked as rent for January 2026
    And 600 EUR is booked as rent for February 2026
    And 1000 EUR is booked as rent for April 2026 on 10 April 2026
    When the manager creates a notice for March 2026 as of 1 May 2026
    Then the notice demands 200 EUR with 4.47 EUR interest
