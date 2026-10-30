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
