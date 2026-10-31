Feature: Password reset
  Users in either deployment mode can recover access through their mailbox.
  A successful reset ends every previously issued session.

  Scenario: A forgotten password is replaced through email
    Given a user who is signed in
    And password reset mail is captured
    When the user requests a password reset link
    Then a password reset email is sent
    When the user submits a new password through the emailed link
    Then the password reset is accepted without signing in
    And the old session is no longer valid
    And the user can sign in with the new password
    And the user can no longer sign in with the old password
