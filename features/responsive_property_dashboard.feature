@browser
Feature: Responsive property dashboard
  Property details and due tasks remain usable on phones without moving the page sideways.

  Scenario Outline: Long property rows and due tasks fit the page
    Given a property with long unit rows and a long due task
    When the manager opens the property and dashboard at <width> pixels
    Then the page stays within its width and its local controls remain usable

    Examples:
      | width |
      | 360   |
      | 1280  |
