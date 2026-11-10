@browser
Feature: Mandate sources
  Mandate documents are listed and downloadable only inside an explicitly
  granted scope. Export needs the exact grant, archiving keeps the original
  bytes with the review reason, and a failed reload hides the stale list
  without an empty-success claim.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a WEG mandate with a scoped clerk grant exists

  Scenario: A scoped upload is audience-only, export needs the exact grant and archiving keeps the original
    When the clerk uploads a contract into the scope with a reason
    Then the clerk sees the contract without a delete action
    And the administration cannot see the contract in the mandate's documents
    When the clerk's grant gains "Exportieren"
    Then the clerk can download the original bytes
    When the clerk archives the contract with a reason
    Then the archived contract shows its reason and stays downloadable
    And the mandate page fits a 390 pixel wide viewport
    When the documents fail to reload
    Then the stale list is hidden without an empty-success claim
    When the clerk's grant is revoked
    Then the mandate shows no accessible documents
