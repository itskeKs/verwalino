Feature: Login lockout
  Too many wrong passwords temporarily prevent signing in, even with the
  correct password. Once the waiting period ends, the correct password works.

  Scenario: A user can sign in again after a temporary lockout
    Given a user who is signed in
    When the user enters too many wrong passwords
    Then the user cannot sign in with the current password
    When the login waiting period has elapsed
    Then the user can sign in with the current password
