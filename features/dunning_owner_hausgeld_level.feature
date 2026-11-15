@area:weg
Feature: Dunning level of an owner's Hausgeld across WEGs
  Without an Abrechnungskreis, a dunning notice counts only the earlier notices and the payments
  of its own WEG. An owner with a unit in two WEGs gets a reminder for the first notice of the
  second WEG, whatever was demanded or paid in the first.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna owns a unit in Gartenstraße 5 with a Hausgeld of 400 euros a month and one in Lindenweg 2 with 200 euros a month

  Scenario: A notice in one WEG does not raise the level of the first notice in the other WEG
    Given Anna was reminded about the Hausgeld of March 2026 in Gartenstraße 5 as of 20 March 2026
    When the manager creates a notice for Anna's Hausgeld of March 2026 in Lindenweg 2 as of 20 March 2026
    Then the notice is a level 1 reminder with 0.00 fee and 200.00 total

  Scenario: A second notice in the same WEG still escalates
    Given Anna was reminded about the Hausgeld of March 2026 in Gartenstraße 5 as of 20 March 2026
    And Anna was reminded about the Hausgeld of March 2026 in Lindenweg 2 as of 20 March 2026
    When the manager creates a notice for Anna's Hausgeld of March 2026 in Gartenstraße 5 as of 25 March 2026
    Then the notice is a level 2 dunning with 2.50 fee and 402.50 total

  Scenario: A payment for one WEG does not reduce the amount demanded in the other WEG
    Given Anna paid 400.00 euros of Hausgeld for March 2026 in Gartenstraße 5
    When the manager creates a notice for Anna's Hausgeld of March 2026 in Lindenweg 2 as of 20 March 2026
    Then the notice is a level 1 reminder with 0.00 fee and 200.00 total
    And the owner statement of Lindenweg 2 for March 2026 shows 200.00 open
