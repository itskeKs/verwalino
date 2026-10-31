@hosted
Feature: Organization quotas
  Each hosted organization has a shared storage allowance for retained documents.

  Scenario: An organization with exhausted storage cannot upload another document
    Given another hosted organization has filled its property allowance
    And a hosted organization has filled its storage allowance
    When a member uploads another document
    Then the upload is refused with the German storage quota message

  Scenario: Quota usage belongs only to the signed-in organization
    Given two newly signed-up hosted organizations
    When the administrator adds a property and reads the organization quota
    Then only that organization's property usage has increased
