@browser
Feature: Mobile navigation
  Navigation remains visible and operable with a keyboard on phones and desktops.

  Scenario: Closed phone navigation does not receive invisible keyboard focus
    Given the signed-in manager opens the navigation dashboard at 360 pixels
    Then Tab skips the closed navigation and focuses a visible control

  Scenario: Escape closes phone navigation and returns focus to its toggle
    Given the signed-in manager opens the navigation dashboard at 360 pixels
    When the manager opens navigation and presses Escape
    Then navigation is closed and its toggle has focus

  Scenario: Phone navigation contains focus and closes through visible controls
    Given the signed-in manager opens the navigation dashboard at 360 pixels
    Then the manager can navigate and dismiss the drawer using the keyboard and backdrop

  Scenario: Desktop navigation remains available across the phone breakpoint
    Given the signed-in manager opens the navigation dashboard at 1280 pixels
    Then static navigation remains accessible when resizing across 768 pixels
