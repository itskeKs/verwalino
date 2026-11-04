@browser
Feature: Save lifecycle
  Saving master data and assigning receipts keeps the current page visible.
  Confirmed writes appear immediately, without a manual reload.

  Scenario Outline: Saving keeps the page and shows the new data
    Given a rental bank account with a full rent receipt, a partial receipt and an unrelated receipt
    And the rental property has many units, contacts, accounts and past leases
    When the manager saves units, people, accounts and an assignment at <width> pixels
    Then each saved page confirms the write and shows the new values without reloading

    Examples:
      | width |
      | 1280  |
      | 360   |
