Feature: Dated household sizes distribute costs by person-days
  Occupied and vacancy households are separate saved histories. Vacancy costs
  remain with the owner and missing occupied counts never become a guess.

  Scenario: A growing household and a two-month vacancy share exactly 1250 euros
    Given an administrator of "Verwaltung Alpha" is signed in
    And Anna has two persons from January and three from June, while Bernd moves in alone in March
    When the manager calculates both 2026 persons-key statements for 1250 euros
    Then Anna pays 901.45 euros for 944 person-days and Bernd pays 292.21 euros for 306 person-days
    And the owner's 59 vacancy person-days cost 56.34 euros and all shares total 1250 euros
    When the manager deletes Bernd's occupied household row
    Then the persons-key draft requires the missing count from 1 March 2026

  @browser
  Scenario Outline: Household history is saved through the unit editor at <width> pixels
    Given an administrator of "Verwaltung Alpha" is signed in
    And a persons-key property has no saved households for Bernd's unit
    When the manager enters Bernd's occupied household and saves the editable vacancy suggestion at <width> pixels
    Then the saved unit editor retains both household streams after reloading
    And Bernd's statement uses the saved household and costs 292.21 euros

    Examples:
      | width |
      | 1280  |
      | 360   |
