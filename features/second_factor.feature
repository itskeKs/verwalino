Feature: Second factor
  A staff account can protect itself with a second factor from an
  authenticator app. After the password the login asks for the current
  6-digit code; a wrong code is refused exactly like a wrong password.
  Without the app a recovery code works exactly once. An administrator
  can reset the factor so the user enrols again; when the only administrator
  lost the app and the recovery codes, the operator of the installation
  resets it on the host.

  Background:
    Given a user who is signed in

  Scenario: Enrolment confirms with one code and shows ten recovery codes once
    When the user starts enrolling a second factor
    And the user confirms the enrolment with the current code
    Then the enrolment answers with ten recovery codes
    And the second factor is enrolled

  Scenario: Login asks for the code and the right code signs in
    Given the user has an enrolled second factor
    When the user signs in with the password
    Then the login asks for the second-factor code
    When the user answers the second-factor step with the current code
    Then the user is signed in

  Scenario: A wrong code is refused with the generic message
    Given the user has an enrolled second factor
    When the user signs in with the password
    And the user answers the second-factor step with a wrong code
    Then the second-factor step is refused with the generic message

  Scenario: A recovery code works exactly once and says how many remain
    Given the user has an enrolled second factor
    When the user signs in with the password
    And the user answers the second-factor step with the first recovery code
    Then the user is signed in
    And the answer says that 9 recovery codes remain
    When the user signs in with the password
    And the user answers the second-factor step with the first recovery code
    Then the second-factor step is refused with the generic message

  Scenario: An administrator resets the factor and the user enrols again
    Given the user has an enrolled second factor
    When an administrator resets the user's second factor
    Then the reset is recorded in the audit log
    When the user signs in with the password
    Then the login does not ask for a second-factor code

  Scenario: An operator resets the factor of a locked-out administrator
    Given the user has an enrolled second factor
    When the operator resets the user's second factor with a stated reason
    Then the operator's reset is recorded once in the audit log with the reason
    And the user's earlier session is signed out
    When the user signs in with the password
    Then the login does not ask for a second-factor code
