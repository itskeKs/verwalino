Feature: Organization export import
  An owner administrator can take the visible organization data and saved
  originals in a documented product archive without exporting credentials.

  Scenario: An administrator downloads the organization's data and originals
    Given a separate organization has rental records and an original for export
    When its administrator downloads the organization archive
    Then the archive contains its rental records and the unchanged original
    And the archive contains no credentials or data of the other organization

  Scenario: An administrator imports into an empty organization with consistent identities
    Given a separate organization has rental records and an original for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization
    Then the imported organization has equal entity counts and normalized content

  Scenario: Missing dependencies refuse the whole import
    Given a separate organization has rental records and an original for export
    When its administrator downloads the organization archive
    And a required person is removed from the archive before import
    Then the import lists missing dependencies and writes nothing

  @browser
  Scenario: Import refusals keep technical identifiers in a disclosure
    Given a separate organization has rental records and an original for export
    When its administrator downloads the organization archive
    And a required person is removed from the archive before import
    And the administrator inspects the import refusal in the browser
    Then the import explanation and technical details fit desktop and phone width
