@selfhost
Feature: First run setup
  A fresh installation requires the one-time token shown to its operator before
  the first organization and administrator can be created in the browser.

  Scenario: A fresh installation can be set up and the administrator can sign in
    Given a fresh installation with an unused setup token
    When the operator sets up the installation with that token
    Then the administrator is signed in
    And the administrator can sign in again

  Scenario: A wrong token cannot claim a fresh installation
    Given a fresh installation with an unused setup token
    When the operator sets up the installation with a wrong token
    Then setup is refused
    And the installation still requires setup

  Scenario: Setup disappears after the first organization is created
    Given a fresh installation with an unused setup token
    When the operator sets up the installation with that token
    Then a second setup is not found
