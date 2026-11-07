@browser @journey
Feature: Post import assignment
  A manager sees the confirmed assignment without losing their place in the imported statement.

  Scenario Outline: Assigning a receipt in a long imported statement keeps the confirmation visible
    Given a rental bank account with a full rent receipt, a partial receipt and an unrelated receipt
    When the manager imports sixty receipts and assigns one lower in the list at <width> pixels
    Then the assignment is confirmed in the viewport at the retained statement position

    Examples:
      | width |
      | 1280  |
      | 360   |
