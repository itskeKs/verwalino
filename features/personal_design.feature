Feature: Personal design
  Every signed-in user chooses the appearance of their own account.
  The saved choice survives signing out without changing a colleague's appearance.

  Scenario: A saved personal design survives signing in again
    Given a user who is signed in
    Then the personal design is "klar"
    When the user chooses the personal design "warm"
    Then the personal design is "warm"
    When the user signs out and signs in again with the current password
    Then the personal design is "warm"

  @browser
  Scenario: Personal design can be changed from settings on desktop and phone
    Given the personal design settings are open
    When the user saves Warm and reloads the settings
    Then Warm is selected and the object navigation works
    When the user opens the settings on a narrow screen and saves Klar
    Then Klar is selected and the mobile navigation is usable
