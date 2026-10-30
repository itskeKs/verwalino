@browser
Feature: Dunning batch
  Managers review overdue rent and explicitly confirm which drafts to create.
  Editing or cancelling a preview does not create notices.

  Background:
    Given three unpaid rental agreements in one accounting scope

  Scenario: Editing and cancelling a preview leaves no notices
    When the manager previews October arrears above 100 EUR as of 15 November 2026
    Then the preview offers three unselected arrears of 850 EUR each
    When the manager changes the minimum arrears
    Then the reviewed selection is cleared
    When the manager previews and cancels the selection on a phone
    Then no notice appears in the reloaded history

  Scenario: Confirming the selection creates three persistent drafts
    When the manager previews October arrears above 100 EUR as of 15 November 2026
    And the manager selects the three arrears and confirms "Mahnentwürfe anlegen"
    Then three unissued drafts remain in the history after reload
