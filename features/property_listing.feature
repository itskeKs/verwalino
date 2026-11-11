@browser
Feature: Property list paging and bounded filters
  Long result sets page in without duplicates. The bank-account property
  filter stays bounded and keeps an explicitly selected property across
  searches.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And 55 properties named "Paging-selector-acceptance" exist

  Scenario: The property list pages from fifty to fifty-five unique rows
    When the manager searches properties for "Paging-selector-acceptance"
    Then the property list shows 50 rows
    When the manager loads the next properties
    Then the property list shows 55 unique property links
    And the page fits a 390 pixel wide viewport

  Scenario: The bank-account filter stays bounded and keeps the selected property
    When the manager opens the bank accounts
    Then the property filter offers at most 51 entries
    When the manager selects the first property in the filter
    And the manager searches the property filter for "no-matching-property"
    Then the property filter still holds the selected property

  Scenario: A contact search without matches reports it
    When the manager searches persons for "not-a-real-contact"
    Then the page reports that no persons exist
