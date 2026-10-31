@hosted
Feature: Self service signup
  A hosted organization can register and confirm its administrator's mailbox
  before the administrator can sign in.

  Scenario: A new administrator confirms the email and signs in
    Given a hosted signup form with captured confirmation mail
    When the administrator registers a new organization
    Then a confirmation email is sent
    And the unconfirmed administrator cannot sign in
    When the administrator confirms the emailed link with the chosen password
    Then the new organization administrator is signed in
    And the new administrator can sign in again

  Scenario: A public registration cannot claim an existing organization
    Given the organizations "Verwaltung Alpha" and "Verwaltung Beta"
    And an administrator of "Verwaltung Alpha" is signed in
    When a visitor registers using the name of another organization
    And the visitor follows the emailed confirmation link
    Then the registration creates a separate organization with the same name
    And the existing administrator stays in the original organization
