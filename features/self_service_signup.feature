@hosted
Feature: Self service signup
  A hosted organization can register and confirm its administrator's mailbox
  before the administrator can sign in.

  Scenario: A new administrator confirms the email and signs in
    Given a hosted signup form with a test mailbox
    When the administrator registers a new organization
    Then the test mailbox receives a confirmation email with the correct recipient, sender, subject and token link
    And the unconfirmed administrator cannot sign in
    When the administrator confirms the emailed link with the chosen password
    Then the new organization administrator is signed in
    And the new administrator can sign in again

  Scenario: Sign-up refuses without both assents and records them on confirmation
    Given a hosted signup form with a test mailbox
    When the visitor submits the sign-up without assent to both documents
    Then the sign-up is refused with a structured error and no mail is sent
    When the administrator registers a new organization with assent to both documents
    Then the test mailbox receives a confirmation email with the correct recipient, sender, subject and token link
    When the administrator confirms the emailed link with the chosen password
    Then the new organization administrator is signed in
    And both assent versions are recorded for the organization and the administrator
    And the settings show both assents with version, time and administrator

  Scenario: A public registration cannot claim an existing organization
    Given the organizations "Verwaltung Alpha" and "Verwaltung Beta"
    And an administrator of "Verwaltung Alpha" is signed in
    When a visitor registers using the name of another organization
    And the visitor follows the emailed confirmation link
    Then the registration creates a separate organization with the same name
    And the existing administrator stays in the original organization
