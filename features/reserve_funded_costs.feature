Feature: Reserve funded costs
  A repair paid from the maintenance reserve reduces the reserve holdings and
  is excluded from the costs charged to owners in the annual settlement.

  Scenario: A repair paid from reserves is not charged to owners again
    Given a WEG with 600 and 400 MEA and 7000 euros of costs including a 1000 euro repair
    When I book the repair payout from reserves with its receipt and resolution
    And I finalize the annual settlement for the funded repair
    Then the owners are charged 3600 and 2400 euros
    And the reserve ledger retains the 1000 euro withdrawal
    And the annual settlement PDF agrees with the cost shares and withdrawal
