# Record of processing activities — processor template

**Draft — complete privately before launch. Version 2026-11-13.** Article 30(2) GDPR requires
categories of processing for each controller, identities/contact details, transfers and a
general TOM description. Ongoing hosted processing does not fit the occasional-processing
exception. [Sources S01, S07, S08](hosted-legal.md#primary-sources-and-dates).

## Processor identity

fogu UG (haftungsbeschränkt), Im Großenbruch 34, 66583 Spiesen-Elversberg, Germany;
managing director Christian Kunkel; hello@fogu.app. DPO: [Complete the owner’s DPO assessment and contact if
appointment is required]. Other processors/joint processors and representatives: [identify if applicable].
An Article 27 representative is not required for this German-established processor. Private
register owner: Christian Kunkel; deputy as specified in the [breach procedure](hosted-breach-response.md). Approval date, reviewer and next review: [complete].

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
| Customer's own AI processor (Q06), separate from operator sub-processors | [Provider identity, direct customer contract, instruction and authority, data/functions, locations/access, retention/training exclusion and transfer basis; validate actual role. Recorded key-save confirmation depends on unimplemented #617] |
| International transfers | [Country/recipient, mechanism and scope, safeguards/assessment; Article 49(1) second-subparagraph documentation if used; or verified none] |
| General TOM / evidence | [Versioned AV annex 2, deployed settings, test/restore evidence, physical/organizational controls] |
| Complete return and deletion (no archive service) | [Customer instruction, categories/bases/deadlines, usable return test, backups, completion/exception evidence] |
| Changes / review | [Date, approver, notice/objection outcome, next review] |

The additional instruction/retention fields support Article 28 and accountability; Article 30(2)
itself does not require the controller's full lawful-basis/purpose register (S01).

## Processing categories

| ID | Operations | Data / subjects | Places and recipients to verify |
| --- | --- | --- | --- |
| P01 Hosted management | Collect through customer input/import; store, organize, retrieve, edit and calculate rentals, WEG/SEV management and communications | Tenant/owner/vendor/representative and customer-staff identifiers, addresses, contact/bank data, leases, deposits, costs, payments, arrears, consumption and ownership/meeting records | Q02: Contabo retained; execute AV agreement and verify entity/site/access before launch; facts in [legal preparation](hosted-legal.md) |
| P02 Documents/evidence | Upload, hash, retain originals; generate PDFs, audit/source links, review and corrections | Originals, correspondence, invoices, signatures, financial and audit records; persons mentioned in files | Local filesystem/database; authorized staff; sensitive data may occur incidentally. Customer must establish Art. 9/10 conditions where relevant |
| P03 Mail | Prepare and submit customer-requested messages/PDFs, retain dispatch evidence | Recipients, content, generated documents and delivery metadata | Q03: EU provider with AV agreement; provider particulars to supply in [legal preparation](hosted-legal.md); recipient mail systems are not automatically the operator's sub-processors |
| P04 Optional AI/OCR | Local OCR; transmit necessary document text/page images, questions and context on customer instruction for extraction/property Q&A; receive/store results, provenance and usage; authenticate model discovery | Document pages/text, questions/answers and person-related contents; customer API key as service credential, encrypted in storage | Q06 recipient category: customer's directly commissioned AI processor, not operator sub-processor under the intended model. Current choices: OpenAI, Anthropic, OpenAI-compatible endpoint. Content processing needs a stored key/model/enabled function; model discovery can use an entered unsaved key without content. Hosted launch includes AI, but recorded key-save confirmation depends on #617. Verify actual roles, customer contract, locations, retention/training exclusion and transfer safeguards; fogu retains instruction, security, unlawful-instruction notice and exporter duties. Local OCR has no external recipient |
| P05 Security/support/recovery | Organization isolation, authorized troubleshooting, backup/restore and incident assistance | Customer records only as necessary; audit and support evidence | Q04/Q12: Christian Kunkel and named deputy; Contabo managed backup under hosting AV agreement, contract/product/retention proof pending; own-purpose metadata in controller record |
| P06 Return/restriction/erasure | Permission-filtered export, seven-day cooling-off, operator erasure and restricted evidence retrieval | Organization archive, account identities, retained documents and dependencies | Q07/Q08: complete export before end, then deletion; no archive service. Complete return, final purge and bounded backup erasure require implementation; ordinary operator notice 90 days, customer may terminate any time |

Technical evidence and distinctions between source and deployed practice are in the
[fact map](hosted-legal.md#repository-fact-map), [export documentation](organization-export.md)
and [retention procedure](hosted-retention.md). This template neither authorizes new recipients
nor approves unknown transfers. Keep customer-specific versions available to the authority
under Article 30(4), with access limited to responsible staff (S01).
For Q06, maintain the separate recipient/instruction record without treating the customer's
provider as an approved operator sub-processor. Counsel must validate this classification,
including private landlords/WEGs and manager authority; see the
[Q06 role and duties review](hosted-legal.md#customer-selected-ai-q06-roles-and-remaining-duties).

Q09 includes private landlords and WEGs as customers, including consumers. Consumer status does
not remove GDPR controller duties or the Article 28 contract. Record the actual controller and
representative/upstream chain for each organization; see the [consumer and role analysis](hosted-legal.md#consumer-customers-q09-and-liability-q10).
Q11: the owner completes the [DPO/DPIA assessment](hosted-dpo-dpia-assessment.md) privately.
Q14: record versioned terms/DPA assent before first data entry; notify new sub-processors 30 days
ahead and allow 21 days for objections. Both workflows require implementation, not just a register.
