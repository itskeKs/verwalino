@browser
Feature: Accounting scopes and team rights
  A scope is created explicitly on the team-rights page. A committed reply
  lost on the way is replayed by the same command, so exactly one scope and
  one grant exist. Grants give exact scoped access; revocation removes it
  while the grant history stays visible.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a property without scopes exists

  Scenario: A created scope survives a lost reply and its grant appears exactly once
    When the manager creates the scope and the first committed reply is lost
    Then exactly one scope exists for the property
    When the manager grants the scope to the clerk with "Vorbereiten" and the first committed reply is lost
    Then the clerk can open the scope
    When the manager revokes the clerk's grant
    Then the clerk can no longer open the scope
    And the grant history shows the revocation
    And the page fits a 390 pixel wide viewport

  Scenario: Pausing scope-bound financial actions blocks new scoped writes
    Given a rental scope with a granted bank account exists
    When the manager pauses the scope-bound financial actions and the first committed reply is lost
    Then the organization reports its scope-bound financial actions as paused
    And a new scoped bank account is refused
    And the existing bank account is still readable
    And the page fits a 390 pixel wide viewport
    When the manager resumes the scope-bound financial actions with a reason
    Then the organization reports its scope-bound financial actions as active
