@browser
Feature: Declaration import start and principal
  Starting an import is replay-safe when the committed reply is lost. A job
  whose principal lost permission is durably cancelled, the page explains
  the cancellation, and an explicit retry creates a new receipt while the
  old command still acknowledges its original one. A started import opens a
  status page at its own address that lists its steps and shows the same after a reload.

  Background:
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization enables declaration import with its synthetic AI key

  Scenario: A lost start reply replays the same command and a revoked principal cancels the job
    When the clerk uploads a PDF and starts the import and the first committed reply is lost
    Then the import is queued with its receipt
    When the administrator revokes the clerk's permission
    Then the import is durably cancelled
    And the clerk's page explains the cancellation
    And the clerk's page fits a 390 pixel wide viewport
    When the clerk retries the import
    Then the retry creates a new receipt while the old command acknowledges the original

  Scenario: A started import opens a status page that shows its steps and survives a reload
    When the clerk uploads a PDF and starts the import
    Then the clerk's page shows the import waiting for processing with its steps
    And a reload shows the same steps and they fit a 360 pixel wide viewport
