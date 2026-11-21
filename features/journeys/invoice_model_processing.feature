@journey @browser
Feature: The building-insurance invoice of Journey 2 reaches the organization's models only after its consent
  Sabine uploads the 312,00 EUR building-insurance invoice of Journey 2 under Belege and opens it. An invoice with structured
  data (XRechnung, ZUGFeRD) is read directly. A scan is read by the reading model, and the extraction model fills
  the fields; both models are the organization's own choices, fake providers stand in for them here. The
  original is kept, nothing is posted, and what the models used stays visible on the invoice.

  Scenario: The scanned invoice is read by both models and shows what they used
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator uploads the scanned building-insurance invoice
    And the worker processes the scan with the offline models
    Then the invoice page proposes 312,00 EUR dated 2026-01-15 from "Musterversicherung AG" and the IBAN of the statement's insurer line at 1280 and 360 pixels
    And the invoice page shows the usage of the reading and of the extraction model
    And the models received what the invoice prints, including the IBAN of the insurer's bank statement line
    And nothing is posted and the original can be opened

  Scenario: The settings card of the local stack, which processes no invoices, names none
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator opens the AI settings of the organization
    Then the one switch reads that it imports declarations only and no text of the card names invoices at 1280 and 360 pixels

  Scenario: XRechnung and ZUGFeRD are read directly and no provider is built
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator uploads the building-insurance invoice as XRechnung and as ZUGFeRD
    And the worker processes both without any model
    Then both invoice pages propose 312,00 EUR dated 2026-01-15 from "Musterversicherung AG" and the IBAN of the statement's insurer line as E-Rechnung
    And no provider was built and no invoice page shows a usage

  Scenario: A provider failure keeps the original and the usage already incurred
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator uploads the scanned building-insurance invoice
    And the worker processes the scan while the extraction model fails
    Then the invoice page explains in German that the extraction failed and the original is kept
    And the invoice page shows the usage of the reading that was already paid
    And nothing is posted and the original can be opened

  Scenario: Without the AI import the scan stays on the host and the page says why
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has switched the AI import off
    When the administrator uploads the scanned building-insurance invoice
    And the worker processes the scan with the offline models
    Then the invoice page explains in German that the AI evaluation is not set up and nothing was sent
    And nothing was sent to a provider
    And nothing is posted and the original can be opened
