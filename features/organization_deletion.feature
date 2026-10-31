Feature: Organization deletion
  Owner administrators can review statutory retention, save a fresh export,
  confirm the exact organization name and revoke deletion during seven days.
  Completed erasure preserves retained originals and denies every former login.

  Scenario: An administrator previews deletion and revokes during cooling-off
    Given a separate organization has rental records and an original for export
    When its administrator requests the retention preview
    Then the preview offers a fresh export and explains operator-only retention
    When the administrator confirms the exact organization name
    Then the organization is read-only during cooling-off
    When the administrator revokes the deletion
    Then the organization accepts new personal records again

  Scenario: The operator executes a due deletion without changing retained records
    Given a separate organization has rental records and an original for export
    When its administrator requests the retention preview
    And the administrator confirms the exact organization name
    And the operator executes deletion after seven days
    Then the original remains unchanged and the former owner cannot sign in

  @browser
  Scenario: Organization lifecycle controls work at desktop and phone width
    When the administrator reviews and cancels deletion in the browser
    Then the organization controls fit desktop and phone width
