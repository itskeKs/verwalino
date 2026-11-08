@browser @journey
Feature: Create an owners' association from a scanned declaration
  The fictional scan has apartments 1 and 2 with 450 shares each and garage G1
  with 100 shares, for a total of 1000 shares over a denominator of 1000.

  Scenario: The manager reviews the scanned declaration and creates all three units
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization enables declaration import with its synthetic AI key
    When the manager uploads the fictional Lindenallee declaration in the browser
    And the deterministic declaration worker extracts the uploaded scan
    And the manager reviews and confirms the declaration in the browser
    Then the created property contains units 1, 2 and G1 with all 1000 shares
