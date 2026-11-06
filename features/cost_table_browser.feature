@browser
Feature: Populated cost tables stay within the page
  A manager can read every cost column and open details on a narrow screen
  by scrolling the table inside its card.

  Scenario Outline: Populated costs retain their values, filters and keyboard actions
    Given an isolated cost list with Aprilhaus gardening and several additional costs
    When the manager opens the populated cost list at <width> pixels
    Then the cost table stays inside the page and exposes every column
    And the cost filters and expanded creation form preserve the populated table
    And the manager opens the confirmed gardening cost details using the keyboard

    Examples:
      | width |
      | 360   |
      | 1280  |
