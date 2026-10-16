# 20b-mobile-push — Package 20

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 20a-listings. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Responsive reale Journeys prüfen. Companion/Push nur nach V07-Architekturentscheidung; kein Offlinewrite ohne Konflikt-/Securitydesign.

## Gezielt zu suchende Einstiegspfade

- `frontend/src/App.tsx`
- `frontend/src/pages/InboxPage.tsx`
- `backend/app/integrations/`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F7b — Mobile and push.** P5.8: responsive acceptance accompanies B2/D1/E1/E4;
companion-client decision after V07. Extend shared responsive shell/pages and add
**new** push subscription/delivery modules plus `integrations/push.py` only after
client architecture selection. Test small-screen upload/camera, meter entry, case
and message journeys with keyboard/screen-reader focus, interrupted transfer and
reauthentication. Do not promise offline writes without a conflict/security design.
Notifications use non-sensitive payloads and access rechecks; revoked subscriptions
cannot expose finance/document content on lock screens. Additive subscription
migration; rollback cancels pending push and retains ordinary web workflows.
A30; no authenticated mobile coverage. V04/V06/V07 gate provider, privacy and
native-vs-PWA/companion scope; responsive web alone cannot close companion parity.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/20b-mobile-push.md` schreiben; in `../state.md`
`current: steps/21-ux-search.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/21-ux-search.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/21-ux-search.md` anhand von state.md und contract.md weiter.“
