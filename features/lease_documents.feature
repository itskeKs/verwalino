Feature: Lease documents
  A lease provides a contract starting point and a retained handover original.
  Handover readings become ordinary readings of the unit's meters.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a lease with a landlord, a tenant and a unit meter

  Scenario: A landlord downloads a contract filled from the lease
    When the landlord creates a lease contract
    Then the contract PDF contains the parties, unit and rent

  Scenario: An incoming handover records readings and retains its protocol
    When the manager records an incoming handover with a reading of 125.5000
    Then the handover PDF lists the meter and its reading
    And the meter history contains the handover reading
