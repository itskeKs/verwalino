Feature: Password change
  A signed-in user can replace their own password. From then on only the new
  password signs them in, and nothing changes when the current password is
  entered wrongly.

  Scenario: The user changes the password
    Given a user who is signed in
    When the user changes the password, entering the current password correctly
    Then the password change is accepted
    And the user can sign in with the new password
    And the user can no longer sign in with the old password

  Scenario: A wrong current password changes nothing
    Given a user who is signed in
    When the user changes the password, entering a wrong current password
    Then the password change is refused
    And the user can still sign in with the current password
    And the user cannot sign in with the new password
