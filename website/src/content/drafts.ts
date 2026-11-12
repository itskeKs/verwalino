import type { TranslationKey } from "../i18n";

export const draftSections = {
  "privacy": [
    "draft.privacy.controller",
    "draft.privacy.hosting",
    "draft.privacy.account",
    "draft.privacy.properties",
    "draft.privacy.storage",
    "draft.privacy.email",
    "draft.privacy.ai",
    "draft.privacy.security",
    "draft.privacy.retention",
    "draft.privacy.rights",
    "draft.privacy.authority",
    "draft.privacy.sources"
  ],
  "terms": [
    "draft.terms.scope",
    "draft.terms.contract",
    "draft.terms.free",
    "draft.terms.availability",
    "draft.terms.duties",
    "draft.terms.data",
    "draft.terms.termination",
    "draft.terms.retention",
    "draft.terms.liability",
    "draft.terms.liability_basis",
    "draft.terms.law",
    "draft.terms.consumer",
    "draft.terms.information",
    "draft.terms.withdrawal",
    "draft.terms.withdrawal_form",
    "draft.terms.disputes",
    "draft.terms.sources"
  ],
  "dpa": [
    "draft.dpa.subject",
    "draft.dpa.purpose",
    "draft.dpa.data",
    "draft.dpa.subjects",
    "draft.dpa.duties",
    "draft.dpa.assistance",
    "draft.dpa.breach",
    "draft.dpa.controller",
    "draft.dpa.tom",
    "draft.dpa.tom_access",
    "draft.dpa.tom_integrity",
    "draft.dpa.tom_availability",
    "draft.dpa.tom_organization",
    "draft.dpa.processors",
    "draft.dpa.changes",
    "draft.dpa.transfers",
    "draft.dpa.deletion",
    "draft.dpa.completion",
    "draft.dpa.sources"
  ]
} as const;

export const checklist: TranslationKey[] = [
  "draft.checklist.host",
  "draft.checklist.processors",
  "draft.checklist.retention",
  "draft.checklist.tom",
  "draft.checklist.contact",
  "draft.checklist.officer",
  "draft.checklist.review"
];
