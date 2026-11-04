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


  Scenario Outline: Editors respect lost access and the last confirmed account data
    Given a rental bank account with a full rent receipt, a partial receipt and an unrelated receipt
    And another private contact is available for editing
    When the manager opens follow-up editors during delayed refreshes at <width> pixels
    Then lost contact access closes the private form and the account retains its confirmed name

    Examples:
      | width |
      | 1280  |
      | 360   |
