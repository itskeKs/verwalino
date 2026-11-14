@hosted
Feature: Second factor during cooling-off
  In the hosted offer every staff account needs a confirmed second factor, and an
  organization in the cooling-off period of its deletion is read-only. An
  administrator whose factor was reset in that period can still enrol and then
  revoke the deletion.

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
