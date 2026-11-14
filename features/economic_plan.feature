Feature: Monthly Hausgeld follows the resolved calendar day
  A selected calendar day moves forward over weekends and public holidays.
  Without a different resolution, the first of the month is selected.

  Scenario: The annual Hausgeld is charged exactly in twelve installments
    Given a finalized plan with annual Hausgeld of 1000 euros for one owner
    When the manager reads the owner's full-year ledger for 2026
    Then the total expected Hausgeld is 1000 euros
    And the first four monthly installments are 83.34 euros and the remaining eight are 83.33 euros

  Scenario: A three-month plan charges its whole planned amount
    Given a finalized plan from 1 July 2026 to 30 September 2026 with planned costs of 3000 euros for one owner
    When the manager opens the plan page and its PDF
    Then both show a monthly Hausgeld of 1000 euros
    When the manager reads the owner's ledger from 1 July 2026 to 30 September 2026
    Then the total expected Hausgeld is 3000 euros
    And the three monthly installments are 1000 euros each

  Scenario Outline: The owner ledger distinguishes future Hausgeld from overdue debt
    Given a finalized plan with monthly Hausgeld of 600 euros and due day <day>
    When the manager reads the owner's <month> ledger on <before>
    Then the 600 euros are not yet due and the due date is <due>
    When the manager reads the owner's <month> ledger on <after>
    Then the 600 euros are due with the same due date <due>
    And an owner notice for <month> on <after> names the due date <due>

    Examples:
      | day     | month   | before     | due        | after      |
      | 3       | 2026-07 | 2026-07-02 | 2026-07-03 | 2026-07-04 |
      | 3       | 2026-10 | 2026-10-04 | 2026-10-05 | 2026-10-06 |
      | default | 2026-03 | 2026-03-01 | 2026-03-02 | 2026-03-03 |

  @browser
  Scenario Outline: The plan resolution and owner cutoff remain usable on desktop and phone
    Given a finalized plan with monthly Hausgeld of 600 euros and due day 3
    When the manager records a due day and opens the owner ledger at <width> pixels
    Then the full-year balance stays 7200 euros while July changes from future to due

    Examples:
      | width |
      | 1280  |
      | 360   |
