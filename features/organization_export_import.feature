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
    And the archive is imported into an empty organization after its source organization was deleted
    Then the imported organization has equal entity counts and normalized content

  Scenario: A reversed payment and its Storno are restored linked and unchanged
    Given a separate organization has a booked payment and its Storno for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization after its source organization was deleted
    Then the imported organization has equal entity counts and normalized content
    And the imported payments list shows the original linked to its Storno with the archived dates

  Scenario: An organization whose property was created in the property form is restored beside its source
    Given a separate organization has a property from the property form with a unit and a booked rent payment for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization
    Then the imported property list shows the form property and its payment of 700.00 on 15.03.2026

  Scenario: An organization whose tasks were created in the task form is restored beside its source
    Given a separate organization has a task on its property and a task without a property from the task form for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization
    Then the imported task list shows both tasks with their titles, due dates and status and the first one linked to the imported property

  Scenario: A rent payment a clerk booked on a scope-classified bank account is restored with the clerk as its reviewer
    Given a separate organization has a rent payment that a clerk booked on a scope-classified bank account for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization after its source organization was deleted
    Then the imported organization has equal entity counts and normalized content
    And the imported payments list shows the clerk's payment of 700.00 on 15.03.2026 and the tenant account shows the archived totals
    And the restored binding of that payment names the clerk's account, which cannot sign in, as its reviewer

  Scenario: An organization whose owner contact has a data domain is restored with its label
    Given a separate organization has an owner contact with a data domain for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization after its source organization was deleted
    Then the imported owner contact shows its data domain labelled after the contact's imported identifier

  Scenario: The owner notices of a special levy are restored with the owner accounts
    Given a separate organization has a WEG with two roof repair levies, an issued owner notice and a draft for export
    When its administrator downloads the organization archive
    And the archive is imported into an empty organization after its source organization was deleted
    Then the imported organization has equal entity counts and normalized content
    And the imported owner notices and owner accounts show the archived levy amounts

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
