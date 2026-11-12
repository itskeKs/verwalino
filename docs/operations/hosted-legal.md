# Hosted legal preparation

Research and drafting as of **13 November 2026**, for issue #26. This is not legal advice,
a certification, or permission to launch. The owner and qualified counsel must approve the
completed documents and actual operating arrangements. `OPEN Q06` is the remaining product decision. `TO SUPPLY` marks missing facts;
implementation and counsel review labels are not reopened owner decisions. No customer records belong here.

## Documents and publication boundary

- [Controller processing record](hosted-processing-controller.md) and
  [processor processing record](hosted-processing-processor.md): complete and maintain private copies.
- [Breach procedure](hosted-breach-response.md): owner and named deputy; complete and exercise it.
- [DPO/DPIA assessment](hosted-dpo-dpia-assessment.md): owner completes and signs a private assessment (Q11).
- [Retention and service exit](hosted-retention.md): decided policy, current capability and implementation dependencies.
- [Website legal content](../../website/README.md#legal-publication-boundary): German text is
  authoritative after approval; English is a translation. Privacy, terms and AV-Vertrag remain drafts.

The operator in [issue #26](https://github.com/itskeKs/verwalino/issues/26) is fogu UG
(haftungsbeschränkt), Im Großenbruch 34, 66583 Spiesen-Elversberg, represented by managing
director Christian Kunkel; Amtsgericht Krefeld HRB 15557; hello@fogu.app. The named authority is
the Unabhängiges Datenschutzzentrum Saarland. These details are owner-supplied, not independently
registry-certified. The latest Q01 decision supersedes the previously printed VAT number.

| Decision | Missing fact / required evidence |
| --- | --- |
| Q01 telephone as second direct channel | [TO SUPPLY Q01: monitored telephone number] |
| Q01 tax identifier | [TO SUPPLY Q01: VAT identification number or Wirtschafts-Identifikationsnummer; identify type and disclose both if applicable under DDG §5] |
| Q02 Contabo retained; owner concludes AV agreement before launch | [TO SUPPLY Q02: data-centre location from hosting contract]; verify contracting entity/address, access countries and downstream providers in the executed agreement |
| Q03 EU mail provider with AV agreement | [TO SUPPLY Q03: mail provider]; complete entity/address, locations, retention and contract before use |
| Q04 host-managed backup under the same AV agreement | [TO SUPPLY Q04: backup product from contract]; [TO SUPPLY Q04: backup retention, including maximum age of all copies]; verify all-copy expiry and recovery safeguards |
| Q12 owner handles rights, security and incidents | Christian Kunkel; deputy fact is recorded in the [breach procedure](hosted-breach-response.md) |
| Q06 external AI on hosted service | [OPEN Q06: decide permitted external AI, roles, providers, countries, retention and transfer safeguards before activation] |

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
| Hosting/storage | All app data; database and original files on local filesystem | Contabo in Germany is stated by [#24](https://github.com/itskeKs/verwalino/issues/24) and site copy | Q02 retains Contabo; verify exact contracting entity, site, remote administration, physical controls and execute AV agreement before launch. Do not infer every recipient is in Germany |
| Backups | Database, documents and private configuration | Operator for availability, processor for customer contents | [DEPLOY backups](../../DEPLOY.md#backups): same-server backup, 14 daily/8 weekly unlabelled generations plus 10 labelled; no firm maximum age for labelled backups. Q04 selects host-managed backup under the same AV agreement; contract, activation and recovery evidence still required |
| Logs, error tracking, support | Request/job/organization IDs, security and error metadata; voluntarily submitted diagnostics or support material | Controller for proportionate security/support metadata, processor for customer contents | `core/logging.py`, `core/error_tracking.py`, `core/scrub.py`; [DEPLOY](../../DEPLOY.md) prescribes self-hosted GlitchTip, disabled with empty DSN; scrubbers reduce data, do not establish anonymity. Q05: logs 14 days, error reports 30 days, closed support 90 days; actual destinations/fields and enforced cleanup still need evidence |
| Exit and erasure | Entire organization, users and retained evidence | Customer instruction, continuing processor duties for retained customer data | `services/organization_deletion_service.py`, migration 0147 retention catalog and subsequent amendments, [retention policy](hosted-retention.md). No automatic final purge or post-erasure customer login |

The app cookie is `hv_session`, HttpOnly, SameSite=Lax, default 7,200 seconds; Secure depends
on deployment configuration. The app uses tab session storage for signup/reset tokens and an
economic-plan form draft (including user/organization identity). Website local storage keeps
`verwalino-theme` after a theme click, without an application expiry; language comes from the URL.
No marketing consent mechanism was found. See `api/auth.py:97`, `core/config.py:45`,
`frontend/src/pages/SignupPage.tsx:10`, `PasswordResetPage.tsx:9`, `EconomicPlansPage.tsx:18`.
Q13 keeps these stores and no consent banner. Document purpose and duration in the privacy notice.
Counsel must validate strict necessity for each purpose under S04, particularly persistent theme
preference and the tab-bound draft; the owner’s choice is not a statutory exemption. No tracking
is authorized. If the exception fails, resolve the conflict before launch rather than asserting
consent or silently changing storage.

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
DPIA-required processing. Small headcount alone does not settle the result. Q11 assigns the owner to record
staff count, scale, sensitive uploads and purposes in the [assessment template](hosted-dpo-dpia-assessment.md),
assess the DSK list, and arrange a non-conflicted DPO if required (S01, S02, S07). The managing director is not a suitable automatic
choice where deciding processing purposes conflicts with DPO independence (Article 38(6)).

No blanket EU-only or zero-retention claim is supportable. Identify actual legal entities,
support locations and downstream recipients for hosting, SMTP, backups, tracking and AI. Assess
each third-country transfer and remote access under Articles 44–49; a customer's API key or an
“EU region” setting does not supply an AV contract or prove all processing stays in the EEA.
Use a currently valid adequacy basis within its scope or appropriate safeguards, including a
transfer assessment/supplementary measures where needed. Routine transfers should not be built
on exceptional Article 49 derogations. `OPEN Q06` (S01, S09).

## Consumer customers (Q09) and liability (Q10)

**Current-primary-source review: 13 November 2026; research and drafting, not legal advice.**
Q09 admits businesses, private landlords and WEGs. Natural-person status depends on the purpose
of the transaction, not the product label ([BGB §§13–14](https://www.gesetze-im-internet.de/bgb/__13.html)).
The BGH treats a WEG with at least one consumer member as a consumer when acting outside
commercial/professional purposes, including where represented by a professional manager
([VIII ZR 243/13, 25 March 2015](https://juris.bundesgerichtshof.de/cgi-bin/rechtsprechung/document.py?Art=pm&Blank=1&Datum=2015-3&Gericht=bgh&file=dokument.pdf&linked=urt&nr=70979)).
Do not require a false business-status declaration. Confirm the contracting party and authority.

**Terms control applies despite no fee.** Consumers receive incorporation/access and assent
protection under [§305](https://www.gesetze-im-internet.de/bgb/__305.html), transparency and
essential-duty control under [§307](https://www.gesetze-im-internet.de/bgb/__307.html), and
[§§308–309](https://www.gesetze-im-internet.de/bgb/__309.html), subject to
[§310(3)](https://www.gesetze-im-internet.de/bgb/__310.html), including certain one-use forms.
Section 309 no. 7 bars exclusions/caps for negligent personal injury and gross fault, including
representatives/assistants; no. 8(a) protects release from the contract after attributable breach.
No. 8(b) concerns new goods/work contracts, not automatically every SaaS arrangement. In B2B,
§309 is not directly applicable, but §307 remains. A damages clause must not erase defect,
performance, termination or withdrawal rights (same sources; S05).

**Free service and personal data.** Under [§312(1a)](https://www.gesetze-im-internet.de/bgb/__312.html)
and [§327(3)](https://www.gesetze-im-internet.de/bgb/__327.html), consumer-data provision can
bring a no-money contract within distance-contract and digital-product law. The exception is
exclusive processing to perform the service or meet legal requirements, with no other purpose.
A GDPR legitimate-interest basis does not itself satisfy that exception. Hosting that stores and
processes user data fits the digital-service definition in §327(2); an AGPL licence for source
code does not automatically exempt hosted operations under §327(6) no. 6. Assess account,
security, diagnostics, support, any improvement/marketing and Q06 provider uses individually.
**Fact-sensitive/unsettled here:** the full purpose boundary, contract classification and any
privilege analogous to gift/loan cannot be established from free pricing or source code alone
([§§516](https://www.gesetze-im-internet.de/bgb/__516.html),
[521](https://www.gesetze-im-internet.de/bgb/__521.html),
[598](https://www.gesetze-im-internet.de/bgb/__598.html),
[599](https://www.gesetze-im-internet.de/bgb/__599.html)). Counsel must approve the applicable
branch before consumer signup; the public draft includes the conditional consumer protections.

Where applicable, preserve statutory provision/conformity remedies, security updates for the
provision period and change protections ([§327f](https://www.gesetze-im-internet.de/bgb/__327f.html),
[§327i](https://www.gesetze-im-internet.de/bgb/__327i.html),
[§327r](https://www.gesetze-im-internet.de/bgb/__327r.html)).
[§327s(4)](https://www.gesetze-im-internet.de/bgb/__327s.html) leaves damages exclusions to
ordinary controls; it does not license a blanket disclaimer. Q10's strongest defensible draft
therefore excludes ordinary negligence outside essential duties, limits ordinary-negligence
essential-duty damages to foreseeable typical loss, and expressly preserves intent, gross fault,
negligent personal injury, fraud, guarantees, mandatory product liability and GDPR liability.
No zero-fee cap, blanket data-loss exclusion or shortened limitation period. Counsel must check
this specific wording, classification and risk; no claim of judicially established validity.
Bases: §§276(3), 307, 309 nos. 7–8, 310 (S05), GDPR Art. 82 (S01).

**Information and withdrawal if §312(1a) applies.** Give clear pre-contract information under
[Art. 246a §§1](https://www.gesetze-im-internet.de/bgbeg/art_246a__1.html)
and [4 EGBGB](https://www.gesetze-im-internet.de/bgbeg/art_246a__4.html): features, identity,
address/telephone/email, zero price/additional costs, performance/complaints, duration/termination,
applicable defect rights, updates, functionality/technical protection, compatibility/interoperability,
ADR and withdrawal with model form. Supply the confirmation on a durable medium before service
begins ([§312f](https://www.gesetze-im-internet.de/bgb/__312f.html)); a mutable webpage alone
is insufficient. Telephone and operational particulars must be completed; signup delivery is
an implementation dependency, not achieved by publishing drafts.

For distance contracts in scope, [§312g](https://www.gesetze-im-internet.de/bgb/__312g.html)
and [§355](https://www.gesetze-im-internet.de/bgb/__355.html) normally give 14 days from
conclusion, not before proper information; absent information, expiry is generally at 12 months
and 14 days. The current [§356(5) no. 1](https://www.gesetze-im-internet.de/bgb/__356.html)
ends a no-price **service** right only on full performance: opening an account is not full
performance of ongoing hosting. Distinguish §356(6) no. 1 for non-physical **digital content**,
where beginning performance can suffice. Do not use a content-download waiver for ongoing SaaS.
No service value compensation is due under [§357a(2)](https://www.gesetze-im-internet.de/bgb/__357a.html)
without a price obligation. Q08 termination at any time does not replace withdrawal information.
The current [§356a](https://www.gesetze-im-internet.de/bgb/__356a.html) also requires an online
withdrawal/confirmation function and prompt durable acknowledgment where applicable; not implemented
by this task. The [§312k](https://www.gesetze-im-internet.de/bgb/__312k.html) termination button
has a separate paid-performance trigger; do not equate it with the withdrawal function.

**Dispute resolution.** [VSBG §36](https://www.gesetze-im-internet.de/vsbg/__36.html) requires
website/terms information on willingness or obligation to participate; the exception for no more
than ten persons on 31 December of the previous year concerns only §36(1) no. 1. An actual
participation commitment/duty requires the competent body's name, address and website. Q09 does
not establish headcount, willingness or a sector duty: counsel must verify and complete the
publication text, without inventing a nonparticipation decision. After an unresolved consumer
dispute, [§37](https://www.gesetze-im-internet.de/vsbg/__37.html) separately requires text-form
information and a participation statement, without that small-employer exemption. The EU ODR
platform stopped accepting complaints on 20 March 2025 and was discontinued on 20 July 2025;
Regulation 524/2013 was repealed from the latter date. Do not add an obsolete platform link
([Regulation (EU) 2024/3228, Arts. 1–2](https://eur-lex.europa.eu/eli/reg/2024/3228/oj)).

**The DPA model remains appropriate.** Civil-law consumer status is distinct from GDPR roles.
A private landlord deciding tenant-data purposes/means is a controller; letting is economic
activity, not exclusively personal/household activity under GDPR Art. 2(2)(c), recital 18 and
Art. 4(7). Using the hosted service does not supply a household exemption. This application
follows the statutory distinction and the DSK's landlord guidance
([GDPR](https://eur-lex.europa.eu/eli/reg/2016/679/oj),
[DSK rental guidance v2.0, January 2026](https://www.datenschutzkonferenz-online.de/media/oh/OH_Einholung_von_Selbstauskuenften_Mietinteressenten_V2.pdf)).
A self-managing WEG can likewise determine purposes/means and contract as controller through its
authorized representative. A professional manager is not automatically its processor: identify
separate/joint controllership or processing from actual decisions, and obtain upstream authority
where relevant. The exact WEG/manager allocation is fact-sensitive, not settled by these terms
([EDPB Guidelines 07/2020](https://www.edpb.europa.eu/documents/guideline/guidelines-072020-on-the-concepts-of-controller-and-processor-in-the-gdpr_en),
[authority guidance on property management](https://www.datenschutz.rlp.de/themen/immobilienverwaltung)).
The operator remains processor for instructed contents and controller for its own account/operations
purposes; electronic Article 28 agreement and completed annexes remain necessary (S01/S09).

## Decided exit policy

Customer tax/commercial retention is category-specific: generally ten years for books and
accounts, eight for booking vouchers and six for relevant correspondence, subject to scope,
transition rules and extensions. The eight-year fallback in code does not classify every
record legally. A deletion guard is not itself a statutory retention duty. Outsourcing does
not remove the customer's responsibility; the operator also needs an agreed usable retrieval
and exit route and must respect applicable access duties for third-party stored tax records
(AO §147(6)). A permission-filtered JSONL/PDF/CSV archive is not by itself proof of GoBD-compliant
system migration (S06). Q07 selects complete export then deletion, with no archive service; Q08 allows customer
termination at any time and requires 90 days’ operator notice, with export until the end.
See [retention](hosted-retention.md) for the implementation gaps. Q14 selects versioned electronic
terms/DPA assent before data entry and 30-day advance sub-processor notice with 21-day objections;
assent, notices and objection handling are implementation dependencies.

## Primary sources and dates

First-pass sources S01–S13 were checked on **2026-11-12**. Second-pass checks on **2026-11-13**
cover GDPR, BGB, BDSG, EGBGB, VSBG, the DSK DPIA list, Saarland DPIA page and S14–S19 below. “No date displayed” means no reliable publication
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
| S13 | [MStV §18, authority text](https://www.die-medienanstalten.de/fileadmin/user_upload/Rechtsgrundlagen/Gesetze_Staatsvertraege/Medienstaatsvertrag_MStV.pdf), seventh amendment effective 1 December 2025 per the authority page; [VSBG §36](https://www.gesetze-im-internet.de/vsbg/__36.html), current section checked, no separate section revision date displayed | No editorial activity established. Consumer scope is now affirmative; see Q09 analysis and S16 |
| S14 | [BGB](https://www.gesetze-im-internet.de/bgb/BJNR001950896.html) header: 23 July 2026; sections linked in the Q09/Q10 analysis checked 13 November 2026, including current §356(5)/(6) and §356a | Do not reuse older §356 paragraph numbering; section pages have no individual amendment date. Fact-sensitive purpose/type classification remains for counsel |
| S15 | [EGBGB](https://www.gesetze-im-internet.de/bgbeg/BJNR006049896.html) header: 22 October 2026; Art. 246a §§1/4 and model form in [Annex 2](https://www.gesetze-im-internet.de/bgbeg/art_253anlage_2.html) | Current pre-contract information includes digital-product defect rights and update information; no claim of official-model safe harbour for the adapted draft |
| S16 | [VSBG](https://www.gesetze-im-internet.de/vsbg/BJNR025410016.html) header: 8 December 2025; [EU 2024/3228](https://eur-lex.europa.eu/eli/reg/2024/3228/oj), adopted 19 December 2024, OJ 30 December 2024 | §§36/37 current text; ODR complaint cutoff 20 March 2025, repeal/discontinuation 20 July 2025 |
| S17 | [BGH VIII ZR 243/13](https://juris.bundesgerichtshof.de/cgi-bin/rechtsprechung/document.py?Art=pm&Blank=1&Datum=2015-3&Gericht=bgh&file=dokument.pdf&linked=urt&nr=70979), judgment 25 March 2015 | WEG consumer treatment; judgment date, not search crawl date |
| S18 | [DSK rental guidance v2.0](https://www.datenschutzkonferenz-online.de/media/oh/OH_Einholung_von_Selbstauskuenften_Mietinteressenten_V2.pdf), January 2026; [Rhineland-Palatinate property-management guidance](https://www.datenschutz.rlp.de/themen/immobilienverwaltung), no publication/revision date displayed | Landlord GDPR duties; actual WEG/manager role allocation requires individual analysis. Older examples on the authority page are not a current headcount rule |
| S19 | [Rome I, Regulation (EC) 593/2008](https://eur-lex.europa.eu/eli/reg/2008/593/oj), 17 June 2008; OJ 4 July 2008; current consolidated version shown as 24 July 2008, checked 13 November 2026 | Article 6 preserves applicable mandatory consumer protection despite choice of German law |

Recheck laws, authority guidance, provider terms and the deployed data flows before final sign-off.
Counsel must decide unsettled contract classification and proportionality questions; an authority's
template cannot supply those decisions. No external system was changed by this documentation.
