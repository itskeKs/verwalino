Feature: Maintenance reserve ledger
  Reserve planning and cash movements are separate. Final statements retain the
  ledger reviewed at finalization and the next period carries its closing balance.
  Booked evidence references stay frozen: later cost corrections or resolution
  challenges do not alter the ledger.

  Scenario: Booked reserve contributions fund the annual reserve development
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    When I review its reserve ledger and record the imported opening balance
    And I finalize the WEG settlement with the reviewed ledger
    Then the reserve closing balance includes the cash contribution
    And the finalized reserve ledger remains unchanged

  @browser
  Scenario: Late closed-year cash is visible in the next open reserve period
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    When I review its reserve ledger and record the imported opening balance
    And I finalize the WEG settlement with the reviewed ledger
    And I prepare the next WEG year and asset report
    Then a late receipt is shown separately with its original date on both reserve pages


  Scenario: A reserve payout needs both cost receipt and resolution before booking
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    When I try to book a reserve payout without its cost receipt and resolution
    Then booking is refused and the payout remains an editable draft

  @browser
  Scenario: Reserve evidence is required in payment and correction forms
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    Then payment and correction forms require cost receipt and resolution on desktop and mobile
