@browser
Feature: Scoped creation forms
  Creation forms offer only the accounting scopes the manager may use.
  Changing the property or the account type clears a chosen scope, and a
  cost draft started from an uploaded invoice replays a lost reply with the
  chosen scope retained.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a property with granted rental and WEG scopes and one scope without grant exists

  Scenario: The bank-account form restricts scopes to the granted ones
    When the manager starts a new bank account for the property
    And the manager chooses the WEG scope and the account type "KAUTION"
    Then the scope selection is cleared and only rental scopes remain
    When the manager chooses the rental scope and switches to the other property
    Then the scope selection is cleared
    And the account form fits a 390 pixel wide viewport
    When the manager returns to the first property and saves the account
    Then the account is created with the rental scope

  Scenario: Cost, settlement, plan and report forms offer only the matching scope type
    When the manager starts a cost, a settlement, a plan and a report for the property
    Then each form offers only the scope type matching the screen

  Scenario: A cost draft from an uploaded invoice replays a lost reply with the scope retained
    Given an uploaded invoice exists
    When the manager starts the cost draft from the invoice and the first committed reply is lost
    Then the draft is created once with the chosen scope
