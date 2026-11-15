@area:weg
Feature: Dunning level of special levies
  Every special levy has its own dunning level, also when two levies of one owner fall due in
  the same month.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And Gartenstraße 5 has two owners and an adopted roof repair resolution
    And two roof repair levies of 5000 euros fall due on 5 June and on 25 June 2026

  Scenario: A reminded and paid levy does not escalate the other levy of the month
    Given Anna was reminded about the first levy on 10 June and again on 12 June 2026
    And Anna paid her 3000 euro share of the first levy on 20 June 2026
    When the manager creates a notice for Anna's June levy arrears as of 28 June 2026
    Then the notice is a level 1 reminder with 0.00 fee and 3000.00 total

  Scenario: A levy reminded twice and still open gets the level 3 notice
    Given Anna was reminded about the first levy on 10 June and again on 12 June 2026
    When the manager creates a notice for Anna's June levy arrears as of 14 June 2026
    Then the notice is a level 3 dunning with 2.50 fee and 3002.50 total
