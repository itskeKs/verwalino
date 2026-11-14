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

  @hosted
  Scenario: An administrator without a second factor enrols during cooling-off and revokes the deletion
    Given a separate organization has rental records and an original for export
    When its administrator requests the retention preview
    And the administrator confirms the exact organization name
    And the operator removes the second factor of the administrator
    Then the organization is read-only during cooling-off
    When the administrator enrols a second factor
    Then the organization is read-only during cooling-off
    When the administrator revokes the deletion
    Then the organization accepts new personal records again

  Scenario: The operator executes a due deletion without changing retained records
    Given a separate organization has rental records and an original for export
    When its administrator requests the retention preview
    And the administrator confirms the exact organization name
    And the operator executes deletion after seven days
    Then the original remains unchanged and the former owner cannot sign in

  Scenario: Imported rent vouchers retain anonymous bank dependencies after erasure
    Given a separate organization has rental records and an original for export
    And its administrator imports and books a synthetic rent payment
    When its administrator requests the retention preview
    And the administrator confirms the exact organization name
    And the operator executes deletion after seven days
    Then the imported voucher and own account are unchanged and its bank line is anonymized
    And the original remains unchanged and the former owner cannot sign in

  @browser
  Scenario: Organization lifecycle controls work at desktop and phone width
    When the administrator reviews and cancels deletion in the browser
    Then the organization controls fit desktop and phone width
