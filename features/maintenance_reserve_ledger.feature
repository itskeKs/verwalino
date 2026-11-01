Feature: Maintenance reserve ledger
  Reserve planning and cash movements are separate. Final statements retain the
  ledger reviewed at finalization and the next period carries its closing balance.
  Booked evidence references stay frozen: later cost corrections or resolution
  challenges do not alter the ledger.
  Finalization freezes opening imports for that exact period, including correction
  drafts after supersession; evidenced withdrawal and adjustment corrections remain possible.
  An opening import requires the earliest period and no overlapping, different reserve period.

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

  Scenario: The earliest draft opening can be re-attested with an audited difference
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    When I attest opening balances of 5000, 5500 and 5200 with reasons
    Then the opening is 5200 and retains entries of 5000, 500 and minus 300

  Scenario: An imported reserve payout with incomplete evidence (none or one reference) can be released and retyped
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    When I release an imported reserve payout draft with incomplete evidence (none or one reference)
    Then its bank line is new and can be assigned again

  @browser
  Scenario: Correct the draft opening and release an imported payout on desktop and mobile
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    Then the opening review and payout release work on desktop and mobile

  @browser
  Scenario: Reserve evidence is required in payment and correction forms
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    Then payment and correction forms require cost receipt and resolution on desktop and mobile

  Scenario: A unit contribution correction uses only remaining booked Hausgeld
    Given a WEG with partially assigned Hausgeld and a finalized reserve plan
    When I correct the unit contribution by 300 euros
    Then only 200 euros remain assignable and a further 300 euros is refused

  Scenario: Derived reserve totals cannot be attested
    Given a WEG with a finalized reserve plan and a booked reserve contribution
    When I attest a derived contribution or withdrawal total
    Then each attestation is refused with the derived-total read-only code

  @browser
  Scenario: A unit contribution correction shows its cap on desktop and mobile
    Given a WEG with partially assigned Hausgeld and a finalized reserve plan
    Then the unit correction shows remaining headroom and preserves inputs after refusal
