Feature: Person retention
  Retained booked repayments keep their structured recipient identifiable.
  Individual disclosure, erasure and organization preview agree on that binding.

  Scenario: A repayment alone restricts its former recipient instead of erasing the name
    Given an otherwise inactive person has only a booked outgoing repayment
    When the administrator reviews that person's erasure
    Then the repayment is the person's sole retention reason
    When the administrator requests erasure of the repayment recipient
    Then the person is restricted and the repayment still identifies its recipient

  Scenario: Requesting erasure directly also preserves a retained repayment recipient
    Given an otherwise inactive person has only a booked outgoing repayment
    When the administrator requests erasure of the repayment recipient
    Then the person is restricted and the repayment still identifies its recipient

  Scenario: A later invoice keeps its supplier readable beyond the service year
    Given an otherwise inactive supplier has a confirmed 2020 invoice for services in 2017
    When the administrator reviews that person's erasure
    Then individual and organization previews retain the supplier through 2028
    When the administrator requests erasure of the supplier
    Then the supplier is restricted and the confirmed invoice is unchanged
