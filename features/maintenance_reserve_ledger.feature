Feature: Maintenance reserve ledger
  Reserve planning and cash movements are separate. Final statements retain the
  ledger reviewed at finalization and the next period carries its closing balance.

  Scenario: Booked reserve contributions fund the annual reserve development
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    When I review its reserve ledger and record the imported opening balance
    And I finalize the WEG settlement with the reviewed ledger
    Then the reserve closing balance includes the cash contribution
    And the finalized reserve ledger remains unchanged
