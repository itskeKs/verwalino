# Record of processing activities — controller template

**Draft — complete privately before launch. Version 2026-11-12.** This public template is not a
completed record. Basis: Article 30(1) GDPR; regular processing is not exempt merely because the
company is small. Structure informed by the Saarland controller template, without reproducing
its form. [Sources S01, S07, S08](hosted-legal.md#primary-sources-and-dates).

## Record owner and completion fields

| Field | Entry |
| --- | --- |
| Controller | fogu UG (haftungsbeschränkt), Im Großenbruch 34, 66583 Spiesen-Elversberg, Germany |
| Representative/contact | Managing director Christian Kunkel; hello@fogu.app |
| Joint controller, if any | [Establish per activity; none evidenced by this repository] |
| EU representative | Not applicable to this German-established controller under Article 27; reassess if structure changes |
| DPO and contact | [OPEN Q11: assessment, appointment if required, published contact and authority notification] |
| Accountable operational owner / deputy | [OPEN Q12: names and private contact routes] |
| Last approval / next review | [Date, approver, next review; review also after material changes] |
| Private evidence location | [Access-controlled register, contracts, assessments and deletion records; no secrets here] |

## Activities

Each row requires a private evidence reference and confirmation of actual recipients and periods.
Legal bases below are draft assessments, not consent requests. For employees acting for a
company use a documented Article 6(1)(f) assessment where applicable, not the company's contract
as if the employee were personally a party (Articles 5, 6, 13/14; S01).

| ID / purpose | Subjects; data and source | Proposed legal basis / interests | Recipients / transfers | Erasure / retention |
| --- | --- | --- | --- | --- |
| C01 Deliver website and maintain security | Visitors; connection metadata, timestamp/resource/error information; directly from browser/infrastructure | Art. 6(1)(f): deliver requested pages, protect service; document necessity and balancing. TDDDG assessment separate | Hosting provider, authorized operators; OPEN Q02/Q05 actual log fields/locations and any DNS/CDN | OPEN Q05 routine log period; proposed 14 days subject to verification, incident subset only as justified |
| C02 Establish/administer hosted service | Prospective customers, administrators and staff; organization/name/email, account/password hash/token digest, roles/preferences, contract evidence; direct or from customer administrator | Art. 6(1)(b) for natural-person contracting customer/pre-contract request; Art. 6(1)(f) for company representatives/staff and account administration. Supply Art. 14 notice when collected indirectly | Hosting, SMTP, authorized support; OPEN Q02/Q03 contracts, entities, regions and downstream access | Pending signup 24-hour token, confirmed row deleted; expired rows cleanup-eligible after seven more days, traffic-dependent. Accounts until deactivation/exit; OPEN Q05/Q08/Q14 actual maxima and contract evidence schedule |
| C03 Authenticate, prevent abuse and investigate errors | Users/visitors; signed session identifiers, hashed IP/email limiter keys, request/job/org identifiers, errors and security events | Art. 6(1)(f): account security, availability, incident investigation; minimization and interest assessment | Hosting, authorized responders; optional self-hosted GlitchTip, actual destination unknown; no automatic assumption of anonymity | Session default 2 hours; throttle cleanup eligibility after one day of inactivity; tracker/log maximum OPEN Q05. Holds individually reviewed |
| C04 Support, rights and legal correspondence | Requesters/customer representatives; contact details, request/response, minimal verification and case metadata | Art. 6(1)(b)/(f) for support as applicable; Art. 6(1)(c) with Arts. 12–22/33/34 for compliance; Art. 6(1)(f) for necessary claim evidence | Mail provider, authorized support, counsel/authority when necessary; customer content remains under processor instructions | OPEN Q05: proposed support 90 days after resolution; rights/incident/claims evidence schedule justified separately; no blanket legal retention of every ticket |
| C05 Operator's own commercial/statutory administration | Customer/supplier representatives; agreements and operator's own invoices/accounting records, if generated; contractual exchanges | Art. 6(1)(b)/(f) for transaction, Art. 6(1)(c) with applicable AO/HGB provisions for records actually subject to them | [Actual accountant, bank, tax authority, business systems: confirm outside app] | Applicable category-specific 10/8/6 years under AO §147/HGB §257, trigger and holds recorded; free accounts do not create fictitious invoices |

Customer property/tenant/owner records are in the [processor record](hosted-processing-processor.md),
not a controller purpose of exploiting customer data. Complete a separate record for employment,
recruiting, marketing or other company processing if it exists; it is not established here.
Sources for retention: [S01/S06](hosted-legal.md#primary-sources-and-dates).

## Mandatory attachments for the completed private record

- Recipient register for each activity: legal entity, service, location including support,
  contractual role, Article 28 agreement where needed, permitted data and access.
- Third-country register: country/recipient, Article 45 basis and verified scope or Article 46
  instrument, transfer assessment/supplementary safeguards as needed; Article 49 safeguards
  documentation if relying on its second subparagraph exceptional route. If none, record the
  verified absence, including remote access. `OPEN Q02–Q06` (S01/S09).
- Erasure schedule: trigger, maximum, responsible person, backup treatment, exceptions and
  evidence of execution. Use the [retention procedure](hosted-retention.md); do not replace
  unknown maxima with token validity (Articles 5(1)(e), 30(1)(f)).
- General TOM description: tenant/source RLS and composite keys; restricted runtime and
  maintenance roles; password hashing/session revocation; TLS configuration; private local
  document storage; immutable evidence/audit; scrubbed diagnostics; backup/restore and erasure
  controls. Record separately what is implemented in code, deployed, tested or still missing.
  See AV-Vertrag annex 2 on the website and [fact map](hosted-legal.md#repository-fact-map)
  (Articles 24, 25, 30(1)(g), 32; S01/S10).
- Article 6(1)(f) assessments, Art. 13/14 notices and delivery evidence; DPIA screening, DPO
  determination, approved device-storage analysis, access/confidentiality training and annual
  review evidence. These supporting records substantiate accountability; they are not all
  separate mandatory columns of Article 30 (S01, S02, S04, S07).

Keep the record available to the authority on request (Article 30(4)); restrict access internally.
Do not commit names of staff, customer identities, signatures, incident details or credentials.
