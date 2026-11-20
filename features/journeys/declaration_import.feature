@browser @journey
Feature: Create an owners' association from a scanned declaration
  The fictional scan has apartments 1 and 2 with 450 shares each and garage G1
  with 100 shares, for a total of 1000 shares over a denominator of 1000.
  Reading the pages and extracting the information are two model choices of the organization.

  Scenario: The manager reads a slow scanned declaration with two different models and reviews it without an import retry
    Given an administrator of "Verwaltung Alpha" is signed in
    When the administrator chooses different models for reading documents and extracting information in the settings
    Then the settings show both choices after a reload
    When the manager uploads the fictional Lindenallee declaration in the browser
    And the offline worker finishes processing the slow scan on its first attempt
    Then page reading used only the chosen reading model and extraction only the chosen extraction model
    And the import shows the consumption of both stages
    When the manager reviews and confirms the declaration in the browser
    Then the created property contains units 1, 2 and G1 with all 1000 shares
