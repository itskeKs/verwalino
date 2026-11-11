@browser
Feature: Shared building data domains
  Preparing a shared building-data domain is explicit and replay-safe. A
  prepared team grant can be revoked while its history stays visible.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a WEG property exists

  Scenario: A prepared domain survives a lost reply and its grant can be revoked
    When the manager prepares the domain from the permissions page and the first committed reply is lost
    Then the domain is prepared once and its team page opens
    When the manager prepares a grant for the clerk with "Bearbeiten"
    Then the grant shows as prepared without expiry
    When the manager revokes the grant
    Then the revocation stays visible in the grant history
    And the domain page fits a 390 pixel wide viewport
