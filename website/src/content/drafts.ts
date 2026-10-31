import type { TranslationKey } from "../i18n";

export const draftSections = {
  "privacy": [
    "draft.privacy.controller",
    "draft.privacy.hosting",
    "draft.privacy.account",
    "draft.privacy.properties",
    "draft.privacy.storage",
    "draft.privacy.email",
    "draft.privacy.rights",
    "draft.privacy.authority"
  ],
  "terms": [
    "draft.terms.scope",
    "draft.terms.free",
    "draft.terms.availability",
    "draft.terms.duties",
    "draft.terms.data",
    "draft.terms.termination",
    "draft.terms.liability",
    "draft.terms.law"
  ],
  "dpa": [
    "draft.dpa.subject",
    "draft.dpa.purpose",
    "draft.dpa.data",
    "draft.dpa.subjects",
    "draft.dpa.duties",
    "draft.dpa.tom",
    "draft.dpa.processors",
    "draft.dpa.controller",
    "draft.dpa.deletion"
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
