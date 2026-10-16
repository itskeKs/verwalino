# 06-documents-templates — Package 6

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 05-master-import. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Nur lokale Registry-/Folder-/Tag-/Template-Fundamente; Inbound-Mail erst 16c.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/property_document_service.py`
- `backend/app/services/mandate_document_service.py`
- `frontend/src/components/knowledge/PropertyDocuments.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F1 — Document organization, stationery and inbound email (staged).** P5.3:
local foundation after B2/B3, inbound email after E4. Extend
`services/property_document_service.py`, `services/mandate_document_service.py`,
existing document APIs and `components/knowledge/PropertyDocuments.tsx`; add **new**
`document_folder`, `template_version`, `intake_address` module families,
`api/document_folders.py`, `api/templates.py`, `DocumentsPage.tsx`, `TemplatesPage.tsx`.
Folders/tags are virtual organizational metadata, never scope or ledger ownership.
Freeze stationery/template versions in output artifacts. Later, controlled intake
address tokens feed B2 quarantine with attachment/message effect keys; sender address
is not mandate authority, and forwarding/resent attachments cannot double book.
Backfill virtual folder refs without touching released sources; rollback pauses
email ingestion, preserves registry/folders/template reads. A25; RF09/RF10/RF22/
RF29/RF30. Tests cover inbox/property/cost repeat, folder move preserving links,
spoofed email, attachment retry and template-version rendering. V04/V06 gate live
mail intake and privacy. Local templates need neither E4 nor a provider.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/06-documents-templates.md` schreiben; in `../state.md`
`current: steps/07a-bank-file-import.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/07a-bank-file-import.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/07a-bank-file-import.md` anhand von state.md und contract.md weiter.“
