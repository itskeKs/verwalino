@journey @browser
Feature: Missing WEG records have understandable German explanations
  Opening an unavailable payment, plan or annual settlement explains what is missing.

  Scenario: Missing WEG records are explained on desktop and on a phone
    Given an administrator of "Verwaltung Alpha" is signed in
    When I open a missing payment, economic plan and WEG settlement on desktop and phone
    Then each page explains the missing record in German without a technical error prefix
