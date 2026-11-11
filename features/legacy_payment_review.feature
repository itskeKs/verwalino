@browser
Feature: Legacy payment review
  The legacy-payment review previews only payments it can prove for the
  scope. Applying a selection is replay-safe, keeps the original bookings
  and never reports an empty success when access is gone.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And a legacy bank account with two rent receipts and a reviewed scope exists

  Scenario: The review separates provable rows and applies the selection once
    When the manager opens the legacy-payment review for the scope
    Then the proven receipt is selectable and the historical one is blocked with a reason
    And the page fits a 390 pixel wide viewport
    When the manager applies the proven receipt with a reason and the first committed reply is lost
    Then the review reports one assigned payment and the original bookings remain
    When the manager's grant is revoked and the preview is reloaded
    Then the review is unavailable without an empty-success claim
