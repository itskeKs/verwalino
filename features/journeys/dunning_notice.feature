@browser @journey
Feature: Readable issued dunning notice
  A manager can read the frozen amounts and issue time on a phone.

  Scenario: Owner drafts open immediately after a July ownership change
    Given Karl Weiß owns unit 2 until June and Lena Roth owns it from July
    When the manager creates Karl's and Lena's notices for the 2026 plan as of 15 January 2027
    Then each draft opens immediately with only its six monthly advances and no duplicate

  Scenario Outline: An owner notice explains when the July charge is not yet overdue
    Given Anna Berg's 2026 plan charges 600 EUR in July due on 3 July
    When the manager submits Anna's July notice as of 2 July 2026 at <width> px
    Then the German refusal is fully in view without manual scrolling, the selections remain and no draft is created
    When the manager changes only the reference date to 4 July 2026 and resubmits
    Then exactly one owner draft opens with 600 EUR principal for the charge due on 3 July

    Examples:
      | width |
      | 1280  |
      | 360   |

  Scenario: An ordinary tenant draft opens immediately after creation
    Given an unpaid rental agreement for 700 EUR rent and no advance payment
    When the manager creates an ordinary January tenant notice as of 2 March 2026
    Then the tenant draft opens immediately with 700 EUR principal and no duplicate

  Scenario: A paid-off earlier arrears does not raise the level of a later notice
    Given a tenant whose January 2026 rent was reminded and then paid in full
    When the manager creates a notice for the March 2026 arrears as of 20 April 2026
    Then the notice is a level 1 reminder with 0.00 fee and 700.00 total
    When the manager issues the notice and creates another one on 5 May 2026
    Then the second notice is a level 2 dunning with 2.50 fee and 702.50 total

  Scenario: Two single-month notices make a combined notice level 2
    Given a tenant whose January and February 2026 rent were each reminded once in their own notice
    When the manager creates a notice for the January and February 2026 arrears as of 25 March 2026
    Then the combined notice is a level 2 dunning with 2.50 fee and 1402.50 total

  Scenario Outline: A missing accounting scope is explained in German before a draft is created
    Given a rental agreement with receipts in two separate accounting scopes
    When the manager submits a June tenant notice without an accounting scope at <width> px
    Then the form explains the accounting scope requirement in German and no draft exists
    And the explanation is fully in view without manual scrolling and the form keeps its values
    When the manager selects the first accounting scope and submits the same notice
    Then exactly one tenant draft exists and opens with 750 EUR principal

    Examples:
      | width |
      | 1280  |
      | 360   |

  Scenario: Issued principal, interest and arrears are readable without horizontal scrolling
    Given an unpaid rental agreement for 1234.56 EUR rent and no advance payment
    When the manager creates a January notice as of 2 March 2026 with automatic interest
    And the manager confirms "Mahnung ausgeben"
    Then the issued principal, interest and open amounts are readable without horizontal scrolling
    And the issue time is displayed in German date and minute format

  Scenario: The issued April reminder explicitly names the first interest day
    Given April rent of 700 EUR with 600 EUR paid on 7 April 2026
    When the manager checks April reminders on 8 April 2026
    And the manager opens the created April reminder
    And the manager confirms "Mahnung ausgeben"
    Then the issued April reminder names 8 April 2026 as the interest start in the detail and PDF

  Scenario Outline: A tenant notice keeps an explicit zero fee
    Given an unpaid rental agreement for 700 EUR rent and no advance payment
    And the standard first dunning fee is 2.50 EUR without automatic interest
    When the manager creates the January tenant notice with "0,00" fee at <width> px
    Then the notice has 700 EUR principal, no fee, no interest and 700 EUR total

    Examples:
      | width |
      | 1280  |
      | 360   |

  Scenario Outline: An owner notice keeps an explicit zero fee
    Given Anna Berg's 2026 plan charges 600 EUR in July due on 3 July
    And the standard first dunning fee is 2.50 EUR without automatic interest
    When the manager creates Anna's July owner notice with "0,00" fee at <width> px
    Then the notice has 600 EUR principal, no fee, no interest and 600 EUR total

    Examples:
      | width |
      | 1280  |
      | 360   |
