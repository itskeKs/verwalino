Feature: Inbox documents read by the organization's AI models
  A PDF or an image in the inbox that carries no structured invoice data goes to the organization's reading and
  extraction models only after the organization has enabled the AI import and confirmed the text that names inbox
  documents. Each document of an original is read from its own pages, a value is shown with the page and the passage
  where it was found, and whatever happens on the way the original is kept, nothing is posted, and what the models
  used stays visible at the document.

  Scenario: A scanned inbox document is read by both models and shows where its values were found
    Given an inbox reviewer with a property and accounting scope
    And the organization has enabled the AI import for the inbox with its own key and both models
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan and the inbox processes it
    Then the inbox document proposes 312,00 EUR dated 15 January 2026 from "Mustermann Versicherungen AG"
    And the amount is shown as found on page 1 in "Gesamtbetrag 312,00 EUR"
    And the invoice date is shown as found on page 2 in "Rechnungsdatum 15.01.2026", read by the reading model
    And no cost was created from the inbox document
    And the inbox document shows the usage of the reading and of the extraction model

  Scenario: Each document of an original is read from its own pages only
    Given an inbox reviewer with a property and accounting scope
    And the organization has enabled the AI import for the inbox with its own key and both models
    When the reviewer uploads a four-page original with an insurance invoice on pages 1 and 2 and a water bill on pages 3 and 4
    And the reviewer marks pages 1 to 2 and pages 3 to 4 as documents and the inbox processes them
    Then the inbox document of pages 1 to 2 proposes 312,00 EUR from "Mustermann Versicherungen AG"
    And the inbox document of pages 3 to 4 proposes 99,00 EUR from "Stadtwerke Test GmbH"
    And neither document was asked about a page of the other

  Scenario: A structured e-invoice uses neither model
    Given an inbox reviewer with a property and accounting scope
    And the organization has enabled the AI import for the inbox with its own key and both models
    When the reviewer uploads a structured e-invoice and the inbox processes it
    Then nothing of the inbox document was sent to a provider
    And the inbox document proposes the values of the e-invoice
    And the inbox document shows no usage

  Scenario: An organization that confirmed before inbox documents were covered keeps its invoices and holds its inbox documents back
    Given an inbox reviewer with a property and accounting scope
    And the organization enabled the AI import and confirmed the provider with the text that names invoices but not inbox documents
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan and the inbox processes it
    Then nothing of the inbox document was sent to a provider
    And the inbox document says that the organization has to confirm again and proposes nothing
    And the original of the inbox document can still be downloaded
    When the administrator confirms the provider again and the reviewer asks for the processing once more
    Then the inbox document proposes 312,00 EUR dated 15 January 2026 from "Mustermann Versicherungen AG"

  Scenario: A provider failure keeps the original and the usage that was already incurred
    Given an inbox reviewer with a property and accounting scope
    And the organization has enabled the AI import for the inbox with its own key and both models
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan whose extraction fails at the provider and the inbox processes it
    Then the inbox document says that the processing failed and proposes nothing
    And no cost was created from the inbox document
    And the original of the inbox document can still be downloaded
    And the inbox document shows the usage of the reading and of the failed extraction
    When the reviewer asks for the processing once more after the provider works again
    Then the inbox document proposes 312,00 EUR dated 15 January 2026 from "Mustermann Versicherungen AG"
    And the inbox document shows the usage of both attempts

  Scenario: A reached monthly limit stops the inbox document before anything is sent
    Given an inbox reviewer with a property and accounting scope
    And the organization has enabled the AI import for the inbox with a monthly limit of one cent
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan and the inbox processes it
    Then nothing of the inbox document was sent to a provider
    And the inbox document says that the monthly budget is exhausted and proposes nothing
    And the original of the inbox document can still be downloaded
    And the inbox document shows no usage

  Scenario: Another organization cannot see what an inbox document used
    Given an inbox reviewer with a property and accounting scope
    And the organization has enabled the AI import for the inbox with its own key and both models
    When the reviewer uploads a two-page building-insurance invoice whose second page is a scan and the inbox processes it
    And Verwaltung Beta asks for the usage of that inbox document
    Then the usage of the inbox document is not found
