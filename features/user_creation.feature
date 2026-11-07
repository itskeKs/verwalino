@browser
Feature: User creation
  Administrators can identify every new-user field on desktop and phone and create
  a user with the chosen role without moving the page sideways.

  Scenario: The administrator creates a read-only user with labelled fields that fit both screen sizes
    Given an administrator of "Verwaltung Alpha" is signed in
    When the administrator fills the new-user form on desktop and phone
    Then every new-user control has a visible label and fits its card without sideways page scrolling
    When the administrator creates the read-only user using the keyboard
    Then the new user appears with the selected read-only role
    And trying to create the same user again preserves all entered fields
