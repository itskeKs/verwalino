@journey @browser
Feature: The documents of the inbox are read by the organization's models from their own pages
  Sabine puts a scan into the inbox, a building-insurance invoice of 312,00 EUR on pages 1 and 2 and a water bill of
  99,00 EUR on pages 3 and 4, and marks the two documents. Each document is read from its own pages by the organization's
  reading and extraction models, every value shows its page and the passage it was found in, and what the models used
  stays visible. A structured e-invoice is read directly. Without the organization's consent, or when a provider fails,
  the original stays, nothing is applied, and the screen says in German what happened. This stack processes no invoices
  (the portable stack), so its consent text names the inbox and no invoices; fake providers stand in for the models.

  Scenario: Each document of the scan is proposed from its own pages and shows where its values were found
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the reviewer uploads a four-page original with an insurance invoice on pages 1 and 2 and a water bill on pages 3 and 4
    And the reviewer marks pages 1 to 2 and pages 3 to 4 as documents and the inbox processes them
    Then the source page proposes 312,00 EUR with the number RE-2026-0042 from "Mustermann Versicherungen AG" for pages 1 to 2 at 1280 and 360 pixels
    And the source page proposes 99,00 EUR from "Stadtwerke Test GmbH" for pages 3 to 4, read by the reading model, at 1280 and 360 pixels
    And the source page of pages 3 to 4 shows the usage of the reading and of the extraction model at 1280 and 360 pixels
    And neither document was asked about a page of the other

  Scenario: A structured e-invoice is read directly and uses neither model
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the reviewer uploads a structured e-invoice and the inbox processes it
    Then nothing of the inbox document was sent to a provider
    And the source page shows the values of the e-invoice read directly and no usage at 1280 and 360 pixels

  Scenario: An organization that confirmed the first text is asked once for the inbox, without its key, and then its documents are read
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization enabled the AI import and confirmed the provider with the first text, which names declarations only
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan and the inbox processes it
    Then nothing of the inbox document was sent to a provider
    And the batch page explains in German that the organization has to confirm again and that the original is kept at 1280 and 360 pixels
    When the administrator confirms the text for the inbox in the AI settings at 1280 and 360 pixels
    And the administrator asks for the processing once more
    Then the source page proposes 312,00 EUR with the number RE-2026-0042 from "Mustermann Versicherungen AG" for pages 1 to 2 at 1280 and 360 pixels

  Scenario: Without the AI import the document stays on the host and the page says why
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has switched the AI import off
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan and the inbox processes it
    Then nothing of the inbox document was sent to a provider
    And the source page explains in German that the AI evaluation is not set up, that the original is kept and that nothing was applied at 1280 and 360 pixels

  Scenario: A provider failure keeps the original and shows the usage already incurred
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan whose extraction fails at the provider and the inbox processes it
    Then the source page explains in German that the extraction failed, that the original is kept and that a new attempt can be requested at 1280 and 360 pixels
    And the source page shows the usage of the reading and of the failed extraction at 1280 and 360 pixels
