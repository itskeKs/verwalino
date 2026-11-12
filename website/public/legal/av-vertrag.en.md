# Data processing agreement (AV-Vertrag)

Draft dated 12 November 2026 — not approved. Operator approval and qualified legal review remain outstanding.

Following approval, the German version is authoritative. This draft is not an executed contract or legal advice. [OPEN Qxx] marks an outstanding operator decision.

## 1. Parties, subject and duration

Processor: fogu UG (haftungsbeschränkt), Im Großenbruch 34, 66583 Spiesen-Elversberg, Deutschland, represented by Christian Kunkel, contact hello@fogu.app. Customer: [complete legal name, legal form, address, representative, contact and organization reference]. The service is hosted Verwalino for rental, WEG and SEV management. Start: [date]; duration: until the main agreement ends, and for lawfully retained processing data until return/erasure is complete. Protection duties continue for that period. [OPEN Q07/Q14: Define continuing archive instructions and electronic conclusion with version evidence.] If the customer is itself a processor, record the controller and its authorization for this sub-processing; the instruction chain applies accordingly. Annex 1 (sections 2–4), annex 2 (9–13), annex 3 (14–16) and annex 4 (17) form part of this agreement.

## 2. Annex 1 — nature and purpose

Processing supports the commissioned property-management service: collection by entry/import, organization, storage, retrieval, calculation, preparation of statements and correspondence, instructed dispatch, export, backup/recovery, restriction and erasure. Documents are read locally; external analysis is limited to separately authorized scope under annex 3. Operational/support access is limited to what the assignment requires. No own advertising, sale or training of own models using processing data. External providers must be contractually bound to this instruction; their actual terms still require review. fogu’s own account/contract and security purposes are separately described in the privacy notice.

## 3. Annex 1 — data categories

Identity, address, communication and bank data; roles and user identifiers; leases, ownership, powers of attorney and service-provider contacts; rents, deposits, payments, arrears, bank movements, costs, invoices, statements, economic plans and asset reports; meter/consumption data; meeting, vote and resolution data; tasks, messages, signatures, uploaded and generated originals; editing, access, provenance and dispatch evidence. Approved AI use additionally covers transmitted questions, text/image extracts, results and usage data. Article 9/10 data is not an intended separate purpose but may occur in free text or attachments. [OPEN Q09/Q11: Agree necessary sensitive categories, permitted scope and additional safeguards with the customer; exclude unnecessary data.]

## 4. Annex 1 — data subjects

Tenants and co-tenants, owners and former owners, representatives and authorized agents, service providers and their contacts, customer employees/users, and other people mentioned in lawfully submitted records. [Before conclusion the customer must confirm actual groups, add particularly vulnerable groups where relevant, and remove unused groups.]

## 5. Instructions, confidentiality and legality

fogu processes data only on documented customer instructions, including transfers, unless EU or Member State law requires other processing. It then informs the customer before processing unless prohibited by law on important public-interest grounds. Customer instruction contacts/deputies: [complete]; fogu contacts/deputies: [complete, OPEN Q12]; initial documented channel hello@fogu.app, with an agreed protected channel for sensitive contents. Urgent oral instructions are documented promptly. If fogu considers an instruction unlawful, it informs the customer immediately and resolves the affected processing before continuing. Authorized persons are bound to confidentiality or a statutory duty of secrecy; access is limited to necessity. fogu maintains its processing record and cooperates with the competent authority.

## 6. Rights and assistance

Taking account of the nature of processing, fogu provides appropriate measures to assist with requests under Articles 12–22 GDPR. Customer-data requests received by fogu are forwarded promptly; fogu answers only on customer instructions unless it has its own legal duty. It provides available data and information in time for the customer’s deadlines and assists rectification, restriction, transfer and lawful erasure. Taking account of available information, it also assists Articles 32–36 duties, including risk assessment, DPIAs and prior consultation. A product export is not a substitute for a complete data-subject access response. [OPEN Q12: Assign responsible staff and achievable response times before conclusion.]

## 7. Personal-data breaches

fogu notifies the customer without undue delay after becoming aware of a breach affecting its processing data, without treating 72 hours as a waiting period. The initial notice includes available details of nature, affected data/subject categories and approximate numbers, contact, likely consequences and measures taken/proposed; missing information follows without undue further delay. fogu preserves necessary evidence, limits harm and assists notification/communication under Articles 33/34 GDPR. Decisions remain with the customer as controller; fogu’s own statutory duties remain unaffected. [OPEN Q12: Enter reachable incident contacts and deputies for both parties.]

## 8. Customer duties and audit rights

The customer is responsible for purposes, lawful bases, notices, lawful instructions and required upstream sub-processing authorization. fogu makes available all information demonstrating Article 28 compliance and allows and contributes to audits, including inspections, by the customer or its appointed auditor. Meaningful evidence may be reviewed first, without excluding necessary on-site inspection. Scope and timing are reasonably coordinated where the reason and urgency permit; incidents, justified suspicion or authority requirements are not subject to blanket annual or advance-notice limits. Other customers’ data and trade secrets are protected without frustrating the audit. This draft imposes no fee that prevents statutory audit or assistance.

## 9. Annex 2 — safeguards and evidence status

fogu undertakes to implement appropriate technical and organizational measures under Article 32 GDPR considering risk, state of the art and cost. The following describes source capabilities and documented operating procedures; it does not prove actual deployment. [OPEN Q12: Before conclusion record implementation, accountable role, check date and private evidence for every measure.] Equivalent improvements are permitted without reducing the agreed protection. Material changes are documented and coordinated with the customer where they affect agreed protection. No certification is claimed.

## 10. Annex 2 — confidentiality and separation

Present in source: organization RLS and composite foreign keys; additional source/data-domain permissions; role checks and separate runtime/maintenance privileges; Argon2 password hashes; signed HttpOnly sessions revoked on relevant account changes; login limiting; local document storage and private export files. HTTPS with Caddy and SMTP TLS are documented operating paths. [OPEN Q02/Q03/Q12: Evidence TLS/Secure-cookie configuration, actual administrator rights, MFA for privileged operational access, access reviews, device security and host physical controls.] RLS does not prevent every privileged operator access. Full database/original encryption at rest or end-to-end encryption is not asserted as deployed.

## 11. Annex 2 — integrity, traceability and minimization

Present: database guards against unlawful changes to confirmed financial data, original-file hash checks, editing/provenance evidence, permission-checked exports and bounded document processing. Error reports are scrubbed and secret fields are excluded from exports. Immutable evidence can remain personal data. [OPEN Q05/Q12: Evidence log access, detection/response, change approval, patch cadence and regular effectiveness checks.] A hash and technical immutability guard alone are not a GoBD attestation.

## 12. Annex 2 — availability and recovery

Documented backups pair database and documents, with private configuration additionally included in the server format. They initially reside on the same server; this alone does not cover server loss. Rotation keeps 14 daily/8 weekly unlabelled generations and ten newest labelled backups; it is not a fixed maximum age for every copy. [OPEN Q04/Q12: Add the actual schedule, separate encrypted location, key management, maximum age including labelled copies, recovery objectives and successful restore tests.] Deletions and access revocations after a backup must be reconciled before restored service is reopened; fully automatic reconciliation is not claimed.

## 13. Annex 2 — organization and rights

Existing tools support access, permission-checked export, restriction and organization erasure with retention reconciliation. [OPEN Q07/Q12: Establish and evidence confidentiality commitments, training, access reviews, instruction/incident registers, supplier review, incident deputies and deadline tracking.] Retention expiry and deletion of final copies require a separate procedure; protected evidence currently has no automatic final purge. Evaluate effectiveness regularly and after material changes; provide the customer with necessary private evidence.

## 14. Annex 3 — recipients and sub-processor register

Complete each row before conclusion: legal entity/address; service and data categories; storage, processing and support countries; further sub-processors; DPA/safeguard evidence; authorization and date; transfer basis; deletion period. Hosting: Contabo, Germany per operator decision — exact entity, address and contract/access locations [OPEN Q02]. SMTP: [provider, places and periods OPEN Q03]. External backups: [provider/location or verified absence of external recipient, OPEN Q04]. Error tracking: self-hosted GlitchTip is documented, actual host/recipient [OPEN Q05]. Optional AI: OpenAI, Anthropic or a compatible endpoint are technically available, but are not an approved provider list [OPEN Q06]. Assess maintenance suppliers with possible data access too. Local OCR is not itself a sub-processor. Open entries do not constitute authorization; a completed list is required for each use.

## 15. Annex 3 — authorization and changes

[OPEN Q14 — proposal:] The customer gives general written authorization only for the register completed at conclusion. fogu provides at least 30 days’ advance notice in text form of intended additions/replacements, with tasks, data, countries and safeguards; the customer may object on privacy grounds within 21 days. Processing by the new provider does not start before the notice period ends or while a justified objection remains unresolved. The parties seek an equivalent alternative; otherwise the affected service may end without penalty, with secure return. Urgent exceptions require specific prior authorization. fogu imposes the same privacy duties on sub-processors and remains responsible to the customer for their performance (Article 28(4) GDPR). Merely editing a webpage does not replace notification.

## 16. Annex 3 — locations and international processing

Hosting in Germany is not a promise that every data flow stays there. [OPEN Q06: Establish the role and contract chain for each AI endpoint and other recipient; a customer key or customer-entered URL does not remove fogu’s own duties.] Processing or access outside the EU/EEA requires documented instructions/authorization and compliance with Articles 44 onwards GDPR. Before processing, record and make available the specific adequacy decision with verified scope or appropriate safeguards, including required transfer assessment and supplementary measures. Blanket customer consent does not replace this assessment. A flow is not approved while its recipient or basis remains unresolved.

## 17. Annex 4 — return, service exit and erasure

After the service ends, at the customer’s choice fogu returns or erases processing data and deletes existing copies unless a specific applicable statutory storage duty prevents this. Continued archiving for customer obligations requires documented continuing instructions; the customer’s retention duty is not automatically fogu’s own duty. Before account closure a permission-checked .tar.gz archive is available with JSONL, checksum manifest, originals and payment/cost CSV; download lasts at most 24 hours and cleanup is a separate job. Unauthorized evidence and credentials are excluded and importability is limited. [OPEN Q07: Supplement missing data lawfully due for return and verify handover/readability.] Erasure confirmation starts seven days of read-only access with export/cancellation; subsequent execution is by the operator, not automatic on day seven. Afterwards there is no customer login; unbound data is erased/minimized while protected originals, evidence and dependencies remain restricted. Current source has no automatic final purge of these remnants. [OPEN Q04/Q07/Q08: Before conclusion agree and implement execution time, residual categories/bases/end dates, an accessible retrieval route, separate archiving, final erasure and maximum age for every backup copy.] Server rotation of 14 daily/8 weekly generations plus ten labelled backups is insufficient on its own. Restores must reconcile later erasures. fogu records execution, remaining copies, reasons/periods and subsequent complete erasure. Indefinite retention is not agreed.

## 18. Conclusion and priority

Following approval the German version is authoritative; this agreement prevails over conflicting main-agreement provisions on processing. Statutory data-subject rights, supervisory powers and Article 82 GDPR remain unaffected. Changes and instructions are documented; Article 28(9) permits electronic form. Complete all open entries and annexes before conclusion; actual customer data, signatures and internal security evidence must not enter the public repository. Customer: [name/authority, date, signature or electronic acceptance evidence]. fogu: [representative, date, signature or electronic acceptance evidence]. Contract/annex version: [complete]. Operator and counsel approval: [outstanding].

## Legal sources and review date

Checked on 12 November 2026: Articles 28, 30, 32–36, 44 onwards GDPR: https://eur-lex.europa.eu/eli/reg/2016/679/oj; Saarland authority’s drafting guidance and templates: https://www.datenschutz.saarland.de/themen/auftragsverarbeitung; safeguards reference: DSK Standard Data Protection Model v3.1, 14 May 2024: https://www.datenschutzkonferenz-online.de/media/ah/SDM-Methode-V31.pdf. This draft requires individual completion; it is not official standard contractual clauses or evidence of legal approval.

## Required before approval

- Q01/Q02: Confirm operator contact, hosting contracting entity, storage and access locations.

- Q03/Q06/Q14: Establish email, AI and other recipients, contracts, international transfers and change notices.

- Q04/Q05/Q07/Q08: Agree and implement periods, backups, complete return, restricted retention and final erasure.

- Q12: Evidence deployed safeguards; verify responsibilities, deputies, recovery and incident procedures.

- Q01: Use hello@fogu.app; add other addresses and rapid direct contact only after confirmation.

- Q11/Q13: Assess DPO appointment, impact assessment and necessity of browser storage.

- Q09/Q10/Q14: Approve business-customer scope, contract formation and liability. Publishing this draft is not approval.
