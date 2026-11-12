# Hosted legal preparation

Research and drafting as of **12 November 2026**, for issue #26. This is not legal advice,
a certification, or permission to launch. The owner and qualified counsel must approve the
completed documents and actual operating arrangements. `OPEN Qxx` identifies an owner decision;
the questions are collected in the external review report. No customer records belong here.

## Documents and publication boundary

- [Controller processing record](hosted-processing-controller.md) and
  [processor processing record](hosted-processing-processor.md): complete and maintain private copies.
- [Breach procedure](hosted-breach-response.md): appoint the responders and exercise it.
- [Retention and service exit](hosted-retention.md): current capability and unresolved commitments.
- [Website legal content](../../website/README.md#legal-publication-boundary): German text is
  authoritative after approval; English is a translation. Privacy, terms and AV-Vertrag remain drafts.

The operator in [issue #26](https://github.com/itskeKs/verwalino/issues/26) is fogu UG
(haftungsbeschränkt), Im Großenbruch 34, 66583 Spiesen-Elversberg, represented by managing
director Christian Kunkel; Amtsgericht Krefeld HRB 15557; VAT ID DE305418362;
hello@fogu.app. The named authority is the Unabhängiges Datenschutzzentrum Saarland.
The company details are owner-supplied, not independently registry-certified. `OPEN Q01`:
confirm they remain current, a rapid direct contact channel in addition to email, and whether
a Wirtschafts-Identifikationsnummer must also be disclosed. See S03 and S12 below.

## Repository fact map

This inventory describes source at `8dcd4e26`, not an inspection of a running service.
The project status in [README](../../README.md) still warns against reliance for real property
management. Older design prose in [architecture](../architecture.md) is not deployment evidence.

| Processing | Subjects and data | Role and flow | Evidence / limits |
| --- | --- | --- | --- |
| Public website | Visitors; connection metadata, requested resources; chosen theme | Operator controller; local static assets, no analytics or runtime third-party assets found | [Website README](../../website/README.md), `website/public/theme.js`, `controls.js`; hosting logs and DNS/CDN contracts unverified |
| Signup, accounts, security | Prospective/active administrators and staff; organization/name/email, Argon2 password hash, token digests, roles, preferences, timestamps, pseudonymous IP/email throttle keys | Operator controller for service administration/security; SMTP sends confirmation/reset messages | `backend/app/api/signup.py`, `api/auth.py`, `api/password_reset.py`, `core/rate_limit.py`; [users](users-and-access.md); signup has no terms/DPA version acceptance or privacy link |
| Property management | Tenants, owners, vendors/contacts, representatives, staff and people mentioned in uploads; addresses/contact/bank data, leases, rents/deposits, arrears, bank movements, invoices, consumption, ownership, votes, correspondence, signatures, original files and audit/source records | Customer normally controller; operator processor. A customer acting as processor needs upstream authorization and the appropriate chain | [Feature status](../feature-status.md), [export inventory](organization-export.md#version-two-format-version-one-remains-importable); attachments/free text may contain Art. 9/10 data; no reliable automatic exclusion |
| Document mail | Recipient addresses, message and generated PDF (settlement, dunning, invitation), dispatch status and retained original | Processor on customer instruction; SMTP then recipient's mail system | `services/mail_service.py`, `services/document_mail_service.py`, [mail operations](../../DEPLOY.md#optional-document-smtp-t47-no-deployment-authorization); SMTP acceptance is not delivery |
| OCR / AI | Uploaded pages/images, extracted text, user questions, answers, source references, usage/cost metadata; encrypted customer API key | Local OCR; optional remote processing at organization's selected endpoint | `declarations/provider_runtime.py`, `declarations/openai_provider.py`, `core/invoice_ocr.py`, [AI runtime](../adr/0014-provider-neutral-ai-runtime.md); OpenAI, Anthropic, compatible endpoints, web Q&A/model discovery and worker extraction. BYOK does not settle the legal role or transfer mechanism |
| Hosting/storage | All app data; database and original files on local filesystem | Contabo in Germany is stated by [#24](https://github.com/itskeKs/verwalino/issues/24) and site copy | Exact contracting entity, site, remote administration, physical controls and AV agreement: `OPEN Q02`. Do not infer every recipient is in Germany |
| Backups | Database, documents and private configuration | Operator for availability, processor for customer contents | [DEPLOY backups](../../DEPLOY.md#backups): same-server backup, 14 daily/8 weekly unlabelled generations plus 10 labelled; no firm maximum age for labelled backups. Actual schedule/off-site copy/encryption unverified (`OPEN Q04`) |
| Logs, error tracking, support | Request/job/organization IDs, security and error metadata; voluntarily submitted diagnostics or support material | Controller for proportionate security/support metadata, processor for customer contents | `core/logging.py`, `core/error_tracking.py`, `core/scrub.py`; [DEPLOY](../../DEPLOY.md) prescribes self-hosted GlitchTip, disabled with empty DSN; scrubbers reduce data, do not establish anonymity. Actual destination, log fields and durations: `OPEN Q05` |
| Exit and erasure | Entire organization, users and retained evidence | Customer instruction, continuing processor duties for retained customer data | `services/organization_deletion_service.py`, migration 0147 retention catalog and subsequent amendments, [retention policy](hosted-retention.md). No automatic final purge or post-erasure customer login |

The app cookie is `hv_session`, HttpOnly, SameSite=Lax, default 7,200 seconds; Secure depends
on deployment configuration. The app uses tab session storage for signup/reset tokens and an
economic-plan form draft (including user/organization identity). Website local storage keeps
`verwalino-theme` after a theme click, without an application expiry; language comes from the URL.
No marketing consent mechanism was found. See `api/auth.py:97`, `core/config.py:45`,
`frontend/src/pages/SignupPage.tsx:10`, `PasswordResetPage.tsx:9`, `EconomicPlansPage.tsx:18`.
`OPEN Q13`: document necessity separately for every storage purpose under S04; persistent
preference storage is not exempt merely because it is called functional.

## Legal conclusions for the launch boundary

Regular hosting of personal data triggers processor duties even without a fee. Separate the
operator's own purposes from customer instructions; obtain an Article 28 agreement before
customer data is processed. The exemption for small organizations in Article 30(5) does not
cover this non-occasional processing. Maintain both records and downstream contracts (S01, S07,
S08, S09). A public template is neither an executed AV-Vertrag nor a completed internal record.

Document lawful purposes, minimization, access, deletion and risk-based controls (Articles 5,
6, 24, 25, 32). Supply Articles 13/14 information at collection, including staff details provided
by customers; account employees are not personally parties to the hosting contract, so Article
6(1)(b) cannot be used indiscriminately. Assess legitimate interests and keep the assessment
privately. Route customer data-subject requests to the controller and assist it (S01, S09).

Screen DPIA and DPO duties before launch. Article 35 depends on likely high risk, not simply
the word “cloud” or “AI”. Article 37 includes large-scale regular/systematic monitoring and
large-scale special-category/criminal data as core activities; BDSG §38 additionally uses at
least 20 persons regularly engaged in automated processing and independent triggers including
DPIA-required processing. Small headcount alone does not settle the result. `OPEN Q11`: record
staff count, actual scale, sensitive uploads and purposes, assess the DSK list, and appoint a
non-conflicted DPO if required (S01, S02, S07). The managing director is not a suitable automatic
choice where deciding processing purposes conflicts with DPO independence (Article 38(6)).

No blanket EU-only or zero-retention claim is supportable. Identify actual legal entities,
support locations and downstream recipients for hosting, SMTP, backups, tracking and AI. Assess
each third-country transfer and remote access under Articles 44–49; a customer's API key or an
“EU region” setting does not supply an AV contract or prove all processing stays in the EEA.
Use a currently valid adequacy basis within its scope or appropriate safeguards, including a
transfer assessment/supplementary measures where needed. Routine transfers should not be built
on exceptional Article 49 derogations. `OPEN Q06` (S01, S09).

Free hosting is not automatically a Schenkung or Leihe. Those contract types and their
liability privileges require classification of the actual promises and economic arrangement;
ongoing hosting, support and custody duties need individual analysis. Even the gift/loan rules
include liability for fraudulently concealed defects (BGB §§523, 524, 600); their return rules
(e.g. §604) are not a substitute for an agreed digital-data exit procedure. B2B terms still face
§307 control; §309 is not directly applicable under §310(1), but its values may inform that
control. Do not use a zero-fee liability cap or a blanket exclusion of data loss. The draft
keeps statutory liability pending counsel's classification and insurance review (`OPEN Q10`,
S05). B2B restriction must be real: a private landlord or a WEG is not automatically an
entrepreneur merely by using property software (`OPEN Q09`, BGB §§13–14 in S05).

Customer tax/commercial retention is category-specific: generally ten years for books and
accounts, eight for booking vouchers and six for relevant correspondence, subject to scope,
transition rules and extensions. The eight-year fallback in code does not classify every
record legally. A deletion guard is not itself a statutory retention duty. Outsourcing does
not remove the customer's responsibility; the operator also needs an agreed usable retrieval
and exit route and must respect applicable access duties for third-party stored tax records
(AO §147(6)). A permission-filtered JSONL/PDF/CSV archive is not by itself proof of GoBD-compliant
system migration (S06). See [retention](hosted-retention.md), `OPEN Q07–Q08`.

## Primary sources and dates

All URLs below were checked on **2026-11-12**. “No date displayed” means no reliable publication
or revision date was exposed; a search crawl date is not treated as a publication date. Statute
dates are the official site's displayed consolidated status, not a claim that every cited
section changed that day. Authority guidance informs practice; courts may interpret differently.

| ID | Source and date checked | Use / limits |
| --- | --- | --- |
| S01 | [GDPR, Regulation (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj), adopted 27 April 2016; OJ 4 May 2016; applicable since 25 May 2018 | Articles 5, 6, 9, 10, 12–22, 24, 25, 28, 30, 32–38, 44–49, 82. Statutory duties; no fee exemption |
| S02 | [BDSG §38](https://www.gesetze-im-internet.de/bdsg_2018/__38.html), [consolidated act](https://www.gesetze-im-internet.de/bdsg_2018/BJNR209710017.html): 3 July 2026 amendment text included; documentary processing not complete per official header | National DPO triggers; use current threshold of 20, not older guidance's ten |
| S03 | [DDG §5](https://www.gesetze-im-internet.de/ddg/__5.html), [act](https://www.gesetze-im-internet.de/ddg/BJNR0950B0024.html): 12 May 2026 amendment text included, documentary processing pending | Business provider information; free pricing is not a safe exemption for a business service |
| S04 | [TDDDG §25](https://www.gesetze-im-internet.de/ttdsg/__25.html), act amended 10 March 2026; [DSK OH Digitale Dienste](https://www.datenschutzkonferenz-online.de/media/oh/OH_Digitale_Dienste.pdf), November 2024 | Device access/storage covers local/session storage too; distinguish strict necessity from convenience and GDPR legal basis |
| S05 | [BGB consolidated](https://www.gesetze-im-internet.de/bgb/BJNR001950896.html), 23 July 2026; [§305](https://www.gesetze-im-internet.de/bgb/__305.html), [§307](https://www.gesetze-im-internet.de/bgb/__307.html), [§309](https://www.gesetze-im-internet.de/bgb/__309.html), [§310](https://www.gesetze-im-internet.de/bgb/__310.html), [§516](https://www.gesetze-im-internet.de/bgb/__516.html), [§521](https://www.gesetze-im-internet.de/bgb/__521.html), [§598](https://www.gesetze-im-internet.de/bgb/__598.html), [§599](https://www.gesetze-im-internet.de/bgb/__599.html), [§276](https://www.gesetze-im-internet.de/bgb/__276.html) | Incorporation, transparency and liability; §305(2)/(3) do not directly apply in B2B; electronic assent/version evidence is the recommended implementation, not a statutory checkbox formula |
| S06 | [AO §147](https://www.gesetze-im-internet.de/ao_1977/__147.html), act amended 22 October 2026; [HGB §257](https://www.gesetze-im-internet.de/hgb/__257.html), act amended 4 February 2026; [BMF GoBD 2026 handbook](https://ao.bundesfinanzministerium.de/ao/2026/Anhaenge/BMF-Schreiben-und-gleichlautende-Laendererlasse/Anhang-33/inhalt.html); [14 July 2025 amendment](https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Weitere_Steuerthemen/Abgabenordnung/2025-07-14-GoBD-2-aenderung.pdf?__blob=publicationFile&v=2) to 28 November 2019 GoBD, previously amended 11 March 2024 | GoBD are administrative guidance, not certification. Preserve original structured e-invoice data, not only rendered PDF. Handbook indexed but direct retrieval returned 503; statute and amendment text were retrieved. No later GoBD amendment found in the official search |
| S07 | DSK [Kurzpapier 1](https://www.datenschutzkonferenz-online.de/media/kp/dsk_kpnr_1.pdf), [5](https://www.datenschutzkonferenz-online.de/media/kp/dsk_kpnr_5.pdf), [13](https://www.datenschutzkonferenz-online.de/media/kp/dsk_kpnr_13.pdf), each 17 December 2018; [DPIA list v1.1](https://www.datenschutzkonferenz-online.de/media/ah/20181017_ah_DSK_DSFA_Muss-Liste_Version_1.1_Deutsch.pdf), 17 October 2018 | Source: Konferenz der unabhängigen Datenschutzbehörden des Bundes und der Länder (Datenschutzkonferenz). [Index](https://www.datenschutzkonferenz-online.de/kurzpapiere.html) marks 13 and several others under revision, and 4/12 partly outdated; do not use those as current transfer/DPO rules |
| S08 | Saarland [AV guidance and linked templates](https://www.datenschutz.saarland.de/themen/auftragsverarbeitung), page undated; [Formulierungshilfe](https://www.datenschutz.saarland.de/fileadmin/user_upload/uds/alle_Dateien_und_Ordner_bis_2025/datenschutz/ds-gvo/kurzpapiere/Auftragsverarbeitungsvertrag_Formulierungshilfe_3-2018_web.pdf), March 2018 file version; linked controller/processor register templates undated | Adapted structure, not copied boilerplate or official standard clauses. Authority expressly excludes a civil-law/AGB assessment |
| S09 | EDPB [Guidelines 07/2020](https://www.edpb.europa.eu/documents/guideline/guidelines-072020-on-the-concepts-of-controller-and-processor-in-the-gdpr_en), final 7 July 2021; [Recommendations 01/2020](https://www.edpb.europa.eu/documents/recommendation/recommendations-012020-on-measures-that-supplement-transfer-tools-to_en), final 18 June 2021; [EU 2021/915](https://eur-lex.europa.eu/eli/dec_impl/2021/915/oj), 4 June 2021 | Role analysis, safeguards; 2021/915 is an Article 28 clause set, not the separate third-country transfer SCCs |
| S10 | DSK [Standard-Datenschutzmodell v3.1](https://www.datenschutzkonferenz-online.de/media/ah/SDM-Methode-V31.pdf), adopted 14 May 2024 | TOM reference objectives: minimization, availability, integrity, confidentiality, transparency, unlinkability, intervenability; not proof of installed controls |
| S11 | Saarland [breach reporting](https://www.datenschutz.saarland.de/online-dienste/meldung-datenpanne), no publication date displayed | Current authority channel; Article 33 risk exception and Article 34 threshold still govern |
| S12 | CJEU [C-298/07 in official case-law digest](https://curia.europa.eu/site/upload/docs/application/pdf/2019-06/fiche_thematique_-commerce_electronique_et_obligations_contractuelles_-_en.pdf), judgment 16 October 2008; digest July 2024 | Additional rapid/direct/effective contact information beyond email; telephone not invariably mandatory |
| S13 | [MStV §18, authority text](https://www.die-medienanstalten.de/fileadmin/user_upload/Rechtsgrundlagen/Gesetze_Staatsvertraege/Medienstaatsvertrag_MStV.pdf), seventh amendment effective 1 December 2025 per the authority page; [VSBG §36](https://www.gesetze-im-internet.de/vsbg/__36.html), current section checked, no separate section revision date displayed | Editorial designation and consumer-dispute statements depend on scope; neither was established here |

Recheck laws, authority guidance, provider terms and the deployed data flows before final sign-off.
Counsel must decide unsettled contract classification and proportionality questions; an authority's
template cannot supply those decisions. No external system was changed by this documentation.
