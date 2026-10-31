Feature: Source access
  Activated source access protection limits staff to the property sources and
  financial data that an administrator has explicitly granted them.

  Scenario: Staff can open a property's sources and financial data only after an explicit grant
    Given an administrator of "Verwaltung Alpha" is signed in
    And a property with a reviewed original and a financial account
    And source access protection has been activated after review
    When a staff member without a grant opens the property's original and financial account
    Then the original and financial account are reported as not found
    When the administrator grants that staff member access to the property's sources and financial data
    And the staff member opens the property's original and financial account again
    Then the original and financial account are visible
