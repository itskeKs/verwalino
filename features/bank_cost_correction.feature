@browser
Feature: Bank coverage in a reviewed allocation correction

  Scenario: A manager carries one covered position to its chosen replacement
    Given a reviewed 119 euro allocation has a 60 euro debit on its second part
    When the manager assigns that coverage to the third proposed part at desktop and mobile widths
    Then the correction leaves the first two parts open and the third part fully covered
