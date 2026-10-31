@hosted
Feature: Hosted setup
  Hosted installations do not offer the operator's first-run setup, even
  before an organization has been created.

  Scenario: A fresh hosted installation does not offer first-run setup
    Given a fresh hosted installation
    Then the installation does not require setup
    When the operator sets up the installation with that token
    Then first-run setup is not found
