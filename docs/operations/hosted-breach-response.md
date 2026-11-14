# Personal-data breach response — operator procedure template

**Draft, 13 November 2026.** Q12 assigns rights requests, security and incidents to
Christian Kunkel, with an absence deputy. [TO SUPPLY Q12: deputy’s name.] Complete protected
contact routes, authority and availability privately and exercise this procedure before launch. No incident or customer
data belongs in this repository. Sources: GDPR Articles 4(12), 28(3)(f), 32–34 and accountability
under Article 5(2), [S01/S11](hosted-legal.md#primary-sources-and-dates).

| Role | Private completion field |
| --- | --- |
| Accountable management / rights / incident lead | Christian Kunkel; public contact hello@fogu.app; private emergency route and authority record to complete |
| Absence cover | Named deputy from Q12 above; owner documents handover, access and notification authority privately |
| Technical containment / recovery lead / deputy | [Name, restricted access, escalation route] |
| Privacy/legal adviser; DPO if appointed | [Contact and availability; management retains accountability] |
| Customer incident contacts | [Per organization, verified emergency channel and substitute] |
| Host, mail, backup, AI and tracking contacts | [Approved providers' emergency channels and contract references] |
| Evidence store / communication channel | [Restricted case system and encrypted transfer; no public issue tracker] |

## 1. Detect, record and contain

Anyone discovering loss, destruction, alteration, unauthorized disclosure or access reports it
immediately to the incident lead. Availability loss can be a personal-data breach even without
exfiltration. Open a private case; record discovery and **awareness** timestamps with timezone,
source, known facts, affected service/organizations and uncertainty. Do not wait for a complete
forensic report to start the clock (Articles 4(12), 33; S01).

Preserve necessary evidence with restricted access and recorded provenance. Contain proportionately:
revoke compromised access, isolate the affected path, stop suspect mail/provider jobs or restrict
service, while preserving originals and evidence. Avoid indiscriminate deletion or broadcasting
customer data to diagnostic services. Record actions, responsible person and time. Seek the
appropriate operational authority; this document itself does not authorize server changes.

## 2. Separate processor and controller duties

| Affected data | Notification route and deadline |
| --- | --- |
| Customer property-management data (processor role) | Notify each affected controller **without undue delay** after becoming aware, under Art. 33(2); no low-risk exemption for this processor-to-controller notification. Do not wait 72 hours. Give the initial facts and update in phases. If the customer is an intermediary processor, follow the documented chain promptly |
| Operator account/operations data (controller role) | Assess risk to people, not merely company loss. Notify the competent authority without undue delay and, where feasible, within **72 hours of awareness**, unless unlikely to result in risk to rights/freedoms. Explain delay; incomplete facts can be supplied in phases under Art. 33(4) |
| Both roles / cross-organization incident | Run both routes; a notice to one customer is not a notice to all, and a processor notice does not discharge the operator's own controller duties |

For the operator's Saarland controller activity use the
[Unabhängiges Datenschutzzentrum Saarland reporting channel](https://www.datenschutz.saarland.de/online-dienste/meldung-datenpanne).
Verify jurisdiction at the time; customers may have another competent authority. The customer
decides its controller notifications, assisted by fogu. Notify on a customer's behalf only on a
documented mandate; never falsely imply its decision has been made (Articles 28, 33; S01/S11).

## 3. Assess and communicate

Record affected data types, sensitivity (including bank/arrears/health information in documents),
identifiability, approximate people/records, recipients, exposure duration, actual protections and
their effectiveness, containment and likely harms. Consider fraud, discrimination, financial loss,
confidentiality and loss of access. Encryption is protective only if effective and keys remain safe.

For high risk, the controller communicates to affected people without undue delay in clear language,
including contact, likely consequences and measures/advice. Apply Article 34(3) exceptions only
with recorded reasons: effective protective measures, subsequent elimination of high risk, or
disproportionate effort with equivalent public communication. The authority may still require
communication. Do not declare exemption merely because the data was password-protected (S01).

Initial customer/authority notice template (complete privately):

- Incident/case reference; sender role and secure callback/contact (DPO or other contact).
- Awareness time; incident time if known; nature of confidentiality/integrity/availability breach.
- Affected categories and approximate counts of people and records; known scope and uncertainty.
- Likely consequences and affected services/organizations without disclosing another tenant's data.
- Measures taken/proposed, mitigation and customer actions needed.
- Next update time and route; outstanding investigation; reasons for any delayed authority report.

Provide continuing assistance, evidence and updates. Do not put personal contents into email
subjects or public status pages. A target for internal escalation must leave room for the
statutory “without undue delay”. Christian Kunkel, or the deputy in his absence, escalates
immediately and sends available customer information without undue delay. No new 24-hour
contractual SLA is assumed from the staffing decision; the statutory deadline governs (S01).

## 4. Recover, close and learn

Validate restored integrity and tenant isolation; reconcile post-backup erasures and revocations
before service reopens. Confirm customer impacts, outstanding risks and remedial actions. Keep
all breaches in the private register, including non-notified ones, with facts, effects, measures
and documented notification/risk decisions (Article 33(5)). Log controller notices, acknowledgments,
updates and closure. Restrict evidence, set a justified case-specific retention/review date and
delete unnecessary copies; no universal statutory incident-log period is asserted here.

Exercise before launch and after material changes using synthetic cases: cross-organization
disclosure, misdirected document mail, provider leak and unusable backup. Record improvements,
owners and deadlines privately. Follow [retention and restoration](hosted-retention.md).
