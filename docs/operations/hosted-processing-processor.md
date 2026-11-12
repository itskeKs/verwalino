# Record of processing activities — processor template

**Draft — complete privately before launch. Version 2026-11-12.** Article 30(2) GDPR requires
categories of processing for each controller, identities/contact details, transfers and a
general TOM description. Ongoing hosted processing does not fit the occasional-processing
exception. [Sources S01, S07, S08](hosted-legal.md#primary-sources-and-dates).

## Processor identity

fogu UG (haftungsbeschränkt), Im Großenbruch 34, 66583 Spiesen-Elversberg, Germany;
managing director Christian Kunkel; hello@fogu.app. DPO: [OPEN Q11: assessment and contact if
appointed]. Other processors/joint processors and representatives: [identify if applicable].
An Article 27 representative is not required for this German-established processor. Private
register owner/deputy: [OPEN Q12]. Approval date, reviewer and next review: [complete].

## One entry per controller / processing chain

| Field | Complete in restricted records |
| --- | --- |
| Customer and organization reference | [Legal name, address, authorized representative and contact; internal organization identifier] |
| Controller(s) on whose behalf processing occurs | [Name/contact for every controller; distinguish the manager acting as controller from a processor for its client] |
| Controller DPO / representative | [Contact if applicable; state reason if not applicable] |
| Upstream authorization if customer is processor | [Controller identity, contract chain and authorization for this sub-processing] |
| Executed AV-Vertrag | [Version, annex versions, start/end, evidence of authorized electronic acceptance/signature] |
| Instructions and rights contacts | [Named authorized persons, secure contact, instruction record; verify changes] |
| Categories performed for this controller | [Select and describe from P01–P06 below; disable/omit unused processing] |
| Sub-processors | [Exact legal entities, task, places incl. remote support, downstream recipients, AV agreement, authorization/version] |
| International transfers | [Country/recipient, mechanism and scope, safeguards/assessment; Article 49(1) second-subparagraph documentation if used; or verified none] |
| General TOM / evidence | [Versioned AV annex 2, deployed settings, test/restore evidence, physical/organizational controls] |
| Return/deletion/continued archive | [Customer instruction, categories/bases/deadlines, usable return test, backups, completion/exception evidence] |
| Changes / review | [Date, approver, notice/objection outcome, next review] |

The additional instruction/retention fields support Article 28 and accountability; Article 30(2)
itself does not require the controller's full lawful-basis/purpose register (S01).

## Processing categories

| ID | Operations | Data / subjects | Places and recipients to verify |
| --- | --- | --- | --- |
| P01 Hosted management | Collect through customer input/import; store, organize, retrieve, edit and calculate rentals, WEG/SEV management and communications | Tenant/owner/vendor/representative and customer-staff identifiers, addresses, contact/bank data, leases, deposits, costs, payments, arrears, consumption and ownership/meeting records | Host: Contabo Germany stated; actual entity/site/access OPEN Q02 |
| P02 Documents/evidence | Upload, hash, retain originals; generate PDFs, audit/source links, review and corrections | Originals, correspondence, invoices, signatures, financial and audit records; persons mentioned in files | Local filesystem/database; authorized staff; sensitive data may occur incidentally. Customer must establish Art. 9/10 conditions where relevant |
| P03 Mail | Prepare and submit customer-requested messages/PDFs, retain dispatch evidence | Recipients, content, generated documents and delivery metadata | SMTP provider/regions/sub-processors OPEN Q03; recipient mail systems are not automatically the operator's sub-processors |
| P04 Optional AI/OCR | Local OCR; selected-provider extraction and property Q&A; store results/provenance/usage | Document pages/text, questions/answers and person-related contents; encrypted API key as service credential | Local OCR is not a third party. Remote OpenAI, Anthropic or compatible endpoint only after role, agreement, approval, regions and transfer analysis OPEN Q06 |
| P05 Security/support/recovery | Organization isolation, authorized troubleshooting, backup/restore and incident assistance | Customer records only as necessary; audit and support evidence | Operator personnel and approved hosting/backup/maintenance recipients; OPEN Q04/Q05/Q12; metadata processed for own purposes belongs in controller record |
| P06 Return/restriction/erasure | Permission-filtered export, seven-day cooling-off, operator erasure and restricted evidence retrieval | Organization archive, account identities, retained documents and dependencies | Customer/authorized successor; protected residual storage and backups. OPEN Q07/Q08 final purge, maximum duration and retrieval service |

Technical evidence and distinctions between source and deployed practice are in the
[fact map](hosted-legal.md#repository-fact-map), [export documentation](organization-export.md)
and [retention procedure](hosted-retention.md). This template neither authorizes new recipients
nor approves unknown transfers. Keep customer-specific versions available to the authority
under Article 30(4), with access limited to responsible staff (S01).
