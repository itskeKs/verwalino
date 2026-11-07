@browser
Feature: Public auth design
  Signing in keeps readable feedback and useful input on desktop and phones.

  Scenario Outline: A user corrects a failed sign-in and reaches the overview
    Given the public login is open at <width> pixels
    When the user submits an incorrect password
    Then the sign-in error is focused and the email remains available
    When the user signs in with the correct password
    Then the authenticated overview is available

    Examples:
      | width |
      | 1280  |
      | 360   |
