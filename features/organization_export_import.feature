Feature: Organization export import
  An owner administrator can take the visible organization data and saved
  originals in a documented product archive without exporting credentials.

  Scenario: An administrator downloads the organization's data and originals
    Given a separate organization has rental records and an original for export
    When its administrator downloads the organization archive
    Then the archive contains its rental records and the unchanged original
    And the archive contains no credentials or data of the other organization
