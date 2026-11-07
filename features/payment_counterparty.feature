Feature: Payment counterparty
  A rent receipt belongs to a tenancy. Rejecting a mistaken counterparty
  leaves the receipt pending so the manager can assign it correctly.

  Scenario: A rejected owner assignment can be corrected to the tenant
    Given a rental receipt and an owner for the rented unit
    When the manager submits the owner as the target for base rent
    Then the response explains that rent belongs to a tenancy and nothing is booked
    When the manager assigns the same rent to the tenancy
    Then the bank receipt is assigned and its exact amount is booked to the tenant
