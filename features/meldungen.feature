@browser
Feature: Meldungen
  A user who started a Teilungserklärung import and left its page finds it again under "Meldungen",
  which every page of the application offers with the number of open ones. Only the user who started
  an import is told about it.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization enables declaration import with its synthetic AI key

  Scenario: A started import is found again under Meldungen by the user who started it and by nobody else
    When the clerk uploads a PDF and starts the import
    Then the clerk finds the import under Meldungen on another page and follows it to its status page
    And the clerk's Meldungen are readable at 360 pixels
    But the administrator has no Meldung of that import
