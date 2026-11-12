Feature: User invitation
  An administrator adds a colleague by e-mail. The colleague sets a password
  through the single-use link and signs in with the invited role. A used,
  expired or revoked link is refused in German.

  Scenario: An invited colleague sets a password through the emailed link
    Given an administrator of "Verwaltung Alpha" is signed in
    And a test mailbox for invitations
    When the administrator invites "kollegin@example.test" as "CLERK"
    Then the test mailbox receives an invitation with a single-use link valid for 72 hours
    When the invitee sets a password through the emailed link
    Then the invitation is accepted without signing in
    And the invitee can sign in with the invited role
    When the invitee sets a password through the emailed link again
    Then the invitation is refused in German

  Scenario: A revoked invitation is refused in German
    Given an administrator of "Verwaltung Alpha" is signed in
    And a test mailbox for invitations
    When the administrator invites "kollegin@example.test" as "READ_ONLY"
    Then the test mailbox receives an invitation with a single-use link valid for 72 hours
    When the administrator revokes the invitation for "kollegin@example.test"
    And the invitee sets a password through the emailed link
    Then the invitation is refused in German
