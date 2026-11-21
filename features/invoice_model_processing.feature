Feature: Invoices read by the organization's AI models
  An invoice that carries no structured data goes to the organization's reading and extraction models only after the
  organization has enabled the AI import and confirmed the text that names invoices. Whatever happens on the way,
  the original is kept, nothing is posted without a person, and what the models used stays visible on the invoice.

  Scenario: A scanned invoice is read by both models and shows what they used
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator uploads a scanned building-insurance invoice and the worker processes it
    Then the invoice proposes 312,00 EUR dated 15 January 2026 from "Mustermann Versicherungen AG"
    And the invoice proposes the vendor's IBAN "DE89 3704 0044 0532 0130 00" for review
    And nothing is posted
    And the invoice shows the usage of the reading and of the extraction model

  Scenario: An IBAN the model misread is not proposed and the rest of the invoice is
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator uploads a scanned building-insurance invoice whose IBAN the extraction model misread and the worker processes it
    Then the invoice proposes 312,00 EUR dated 15 January 2026 from "Mustermann Versicherungen AG"
    And the invoice proposes no vendor IBAN
    And nothing is posted

  Scenario: An organization that confirmed before invoices were covered keeps its invoices on the host
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization enabled the AI import and confirmed the provider before invoices were covered
    When the administrator uploads a scanned building-insurance invoice and the worker processes it
    Then nothing was sent to a provider
    And the invoice says that the organization has to confirm again and proposes nothing
    And the original can still be downloaded
    When the administrator confirms the provider again and starts the processing once more
    Then the invoice proposes 312,00 EUR dated 15 January 2026 from "Mustermann Versicherungen AG"

  Scenario: A provider failure keeps the original and the usage that was already incurred
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator uploads a scanned building-insurance invoice whose extraction fails at the provider and the worker processes it
    Then the invoice says that the processing failed and proposes nothing
    And nothing is posted
    And the original can still be downloaded
    And the invoice shows the usage of the reading that was already paid

  Scenario: A reached monthly limit stops the invoice before anything is sent
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with a monthly limit of one cent
    When the administrator uploads a scanned building-insurance invoice and the worker processes it
    Then nothing was sent to a provider
    And the invoice says that the monthly budget is exhausted and proposes nothing
    And the original can still be downloaded
    And the invoice shows no usage

  Scenario: Another organization cannot see what an invoice used
    Given an administrator of "Verwaltung Alpha" is signed in
    And the organization has enabled the AI import with its own key and both models
    When the administrator uploads a scanned building-insurance invoice and the worker processes it
    And Verwaltung Beta asks for the usage of that invoice
    Then the usage is not found
