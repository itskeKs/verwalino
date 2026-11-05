@journey @browser
Feature: Dashboard greeting and organization context
  The dashboard greets the signed-in manager without treating the organization as their name.

  Scenario Outline: Organization context remains separate from the greeting
    Given a fresh organization named "Sabine Muster Hausverwaltung"
    When the manager opens the dashboard at <width> pixels
    Then the dashboard shows the exact greeting and the organization above the heading

    Examples:
      | width |
      | 1280  |
      | 360   |
