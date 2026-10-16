# 19b-transfer-settings — Package 19

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 19a-finance-exports. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Scoped Migrationbundle, isolierter Restore/Diff und Security-/Session-/Revocation-Controls. 2FA nur mit spezifiziertem Protokoll, nicht anhand beobachteter Settings behaupten.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/dsgvo_service.py`
- `backend/app/api/users.py`
- `backend/app/api/auth.py`
- `frontend/src/auth/AuthContext.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F6 — General export/restore and account/security settings.** P5.6 after B1/B4/B2;
provider-revocation controls after the corresponding adapters. Extend `api/users.py`,
`api/auth.py`, `services/dsgvo_service.py`, `UsersPage.tsx`,
`frontend/src/auth/AuthContext.tsx`; add
**new** `services/data_transfer_service.py`, `api/data_transfers.py`, `SettingsPage.tsx`.
Preserve privacy export semantics; add a distinct scoped migration bundle containing
ID relationships, source hashes and document manifest plus import dry-run/diff.
Cover membership/grants, security/session controls, notification preferences,
integration disconnect and job/download revocation. Authentication features such
as 2FA require a separately specified protocol/acceptance before claiming RF22
coverage; observed settings alone do not prove behavior. Additive preference/
export-schema versions; restore to isolated fixtures, never blanket-delete accepted
records to undo an import. Rollback retains granted-policy enforcement and old
bundle readers. A28/A31; RF12/RF22–RF24/RF28. Test round-trip links/hashes,
hidden-mandate omission, revoked sessions/jobs/downloads and corrupted bundle.
V06/V07 gate retention/security feature matrix and settings parity.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/19b-transfer-settings.md` schreiben; in `../state.md`
`current: steps/20a-listings.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/20a-listings.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/20a-listings.md` anhand von state.md und contract.md weiter.“
