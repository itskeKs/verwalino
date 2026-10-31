Feature: Password reset
  Users in either deployment mode can recover access through their mailbox.
  A successful reset ends every previously issued session.

  Scenario: A forgotten password is replaced through email
    Given a user who is signed in
    And a test mailbox for password recovery
    When the user requests a password reset link
    Then the test mailbox receives a password reset email with the correct recipient, sender, subject and token link
    When the user submits a new password through the emailed link
    Then the password reset is accepted without signing in
    And the test mailbox receives a password reset confirmation email
    And the old session is no longer valid
    And the user can sign in with the new password
    And the user can no longer sign in with the old password
