# 16a-portal — Package 16

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 15b-deposits-handover. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Separate Portalidentität und explizite Grants; geprüfte Artefakte, Former-Resident-/Haushalts-/Download-Denials.

## Gezielt zu suchende Einstiegspfade

- `backend/app/core/rbac.py`
- `backend/app/core/tenant.py`
- `backend/app/main.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**E3 — Tenant/owner portal access.** P4.3 after B1/E1; F1 registry foundations
provide publishable document refs. Add **new** `portal_identity`, `portal_grant`,
`published_document` module families, `services/portal_service.py`, `api/portal.py`,
separate portal frontend shell/pages. Invite a named relationship with audience
preflight, separate identity/verification and explicit grants. Staff role never
becomes a portal grant. Explain eligibility exclusions before invitation; missing
email cannot prevent ordinary contact creation and cannot silently select another
channel. Resident issue/readings enter E1/D1 review; shared documents expose only
approved scoped artifacts. Migration creates no grants from historical relationships
automatically. Rollback revokes/suspends future portal access, keeps required history.
A20/A31; RF05/RF20/RF21/RF24. Test household enumeration, forged download IDs,
owner/tenant segregation, revoked/former occupant policy and queued-job revocation.
V04/V06 gate invitation provider/retention/access production policy; tenant UI
reference and owner scope remain bounded R/O versus D evidence.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/16a-portal.md` schreiben; in `../state.md`
`current: steps/16b-messaging.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/16b-messaging.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/16b-messaging.md` anhand von state.md und contract.md weiter.“
