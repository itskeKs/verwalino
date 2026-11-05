Feature: Lease documents
  A lease provides a contract starting point and a retained handover original.
  Handover readings become ordinary readings of the unit's meters.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with a landlord, a tenant and a unit meter

  Scenario: A landlord downloads a contract filled from the lease
    When the landlord creates a lease contract
    Then the contract PDF contains the parties, unit and rent

  Scenario Outline: Documents use the rent valid on their date and keep issued originals
    Given the rent changes to 750 net plus 150 operating and 90 heating on 15 March 2026
    When the manager creates a contract and rent letter dated <date>
    Then both PDFs show <net> net and <operating> operating advance
    And both PDFs <heating>
    When the rent changes again on 1 July 2026
    Then the issued contract and rent letter still download byte-identically

    Examples:
      | date       | net    | operating | heating                                          |
      | 2026-03-15 | 750,00 | 150,00    | show 90,00 heating and 990,00 total rent           |
      | 2026-03-14 | 700,00 | 150,00    | keep the original single advance without heating |

  Scenario: A contract dated after the tenancy ends shows the last rent that applied
    Given the rent changes to 750 net plus 150 operating and 90 heating on 15 March 2026
    When the manager ends the tenancy on 28 February after issuing an April contract
    And the manager creates a new contract dated 1 April
    Then the new contract shows the rent at the end of the tenancy
    And the pre-termination contract still downloads byte-identically

  Scenario: A historical circular letter uses the last tenancy day without widening recipients
    Given the rent changes to 750 net plus 150 operating and 90 heating on 15 March 2026
    When the manager ends the tenancy on 28 February
    And the manager creates a contract and rent letter dated 2026-02-28
    Then both PDFs show the rent at the end of the tenancy on 28 February
    And a circular letter dated 1 April has no eligible recipients

  Scenario: An incoming handover records readings and retains its protocol
    When the manager records an incoming handover with a reading of 125.5000
    Then the handover PDF lists the meter and its reading
    And the meter history contains the handover reading
