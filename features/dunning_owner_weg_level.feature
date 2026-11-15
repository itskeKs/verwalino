@area:weg
Feature: Dunning level of an owner across WEGs
  A dunning notice counts only the earlier notices of its own WEG. An owner with units in two WEGs
  gets a reminder for the first notice of the second WEG, whatever was demanded in the first.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And Gartenstraße 5 has two owners and an adopted roof repair resolution
    And Anna also owns the only unit of Lindenweg 2, a second WEG with an adopted roof repair resolution
    And a roof repair levy of 5000 euros falls due on 15 June 2026 in Gartenstraße 5
    And a roof repair levy of 4000 euros falls due on 15 June 2026 in Lindenweg 2

  Scenario: A notice in one WEG does not raise the level of the first notice in the other WEG
    Given Anna was reminded about her June levy in Gartenstraße 5 on 16 June 2026
    When the manager creates a notice for Anna's June levy arrears in Lindenweg 2 as of 16 June 2026
    Then the notice is a level 1 reminder with 0.00 fee and 4000.00 total

  Scenario: A second notice in the same WEG still escalates
    Given Anna was reminded about her June levy in Gartenstraße 5 on 16 June 2026
    And Anna was reminded about her June levy in Lindenweg 2 on 16 June 2026
    When the manager creates a notice for Anna's June levy arrears in Gartenstraße 5 as of 17 June 2026
    Then the notice is a level 2 dunning with 2.50 fee and 3002.50 total
