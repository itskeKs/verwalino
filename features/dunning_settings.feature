Feature: Organization dunning settings
  An organization sets its reminder fees and can enable automatic statutory interest.

  Scenario: The settings name the last covered base-rate half-year
    Given an administrator of "Verwaltung Alpha" is signed in
    When the administrator opens the dunning settings
    Then the settings report the base rate is covered until the end of 2026

  Scenario Outline: The second reminder uses the saved fee and two published base rates
    Given April rent of 700 EUR with 600 EUR paid on 7 April 2026
    And the April rental unit is "<usage>"
    When the administrator saves reminder fees of 0, 3 and 4 EUR and enables interest
    And the manager issues a first reminder for the April arrears
    And the manager prepares a second April reminder as of 31 October 2026
    Then the draft contains a 3.00 EUR fee and <interest> EUR interest at a <premium> point premium
    And the interest is split into 84 days at 1.27 percent and 123 days at 1.52 percent
    And the issued PDF shows the same <premium> point premium

    Examples:
      | usage   | interest | premium |
      | WOHNUNG | 3.64     | 5       |
      | GEWERBE | 5.91     | 9       |
