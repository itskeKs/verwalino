@journey @browser
Feature: Property MEA guidance
  A rental house without co-ownership shares needs no MEA setup.
  A WEG property offers scale confirmation with an understandable German status.

  Scenario Outline: New properties offer only relevant MEA controls
    Given an administrator of "Verwaltung Alpha" is signed in
    And a new <management_type> property without co-ownership shares
    When the manager opens its details and new unit form on desktop and phone
    Then the MEA controls match the property's management type

    Examples:
      | management_type |
      | MIET            |
      | WEG             |
