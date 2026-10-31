Feature: Organization isolation
  Every organization manages only its own data. A user who is signed in to one
  organization never sees, opens or changes the records of another
  organization, not even when they know the other record's id.

  Background:
    Given the organizations "Verwaltung Alpha" and "Verwaltung Beta"
    And each organization has a property, a person and a bank account of its own
    And an administrator of "Verwaltung Alpha" is signed in

  Scenario Outline: A user sees only the <records> of their own organization
    When the user lists the <records>
    Then the list shows the <records> of "Verwaltung Alpha"
    And the list does not show the <records> of "Verwaltung Beta"

    Examples:
      | records       |
      | properties    |
      | persons       |
      | bank accounts |

  Scenario Outline: A <record> of another organization cannot be opened by its id
    When the user opens the <record> of "Verwaltung Beta" by its id
    Then the <record> is reported as not found

    Examples:
      | record       |
      | property     |
      | person       |
      | bank account |

  Scenario Outline: A <record> of another organization cannot be changed
    When the user tries to rename the <record> of "Verwaltung Beta" to "Taken over by Alpha"
    Then the change is refused
    And "Verwaltung Beta" still has its <record> unchanged

    Examples:
      | record       |
      | property     |
      | person       |
      | bank account |

  Scenario: Signing in again keeps the session in the same organization
    When the administrator signs in again
    Then the signed-in session belongs to "Verwaltung Alpha"
    When the user lists the properties
    Then the list shows the properties of "Verwaltung Alpha"
    And the list does not show the properties of "Verwaltung Beta"

  @hosted
  Scenario: A public registration cannot claim an existing organization
    When a visitor registers using the name of another organization
    And the visitor follows the emailed confirmation link
    Then the registration creates a separate organization with the same name
    And the existing administrator stays in the original organization

  Scenario: A password recovery link changes only the mailbox owner's account
    Given a user who is signed in
    And password reset mail is captured
    When the user requests a password reset link
    And the user submits the captured recovery link
    Then the password reset is accepted without signing in
    And the old session is no longer valid
    And the other organization's administrator remains signed in
