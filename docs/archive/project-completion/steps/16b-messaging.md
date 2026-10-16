# 16b-messaging — Package 16

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 16a-portal. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Audience/Channel-/Attachmentpreview, eingefrorener Payload, Outbox und Unknown-Outcome-Reconciliation; kein blindes Resend.

## Gezielt zu suchende Einstiegspfade

- `backend/app/workers/outbox.py`
- `backend/app/integrations/`
- `frontend/src/api/`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**E4 — Messages, announcements and channel delivery.** P4.4 after E3/B2/F1 local
templates. Add **new** `message`, `delivery` module families,
`services/delivery_service.py`, `integrations/delivery.py`, `api/messages.py`,
`api/deliveries.py`, `MessagesPage.tsx`, `DeliveryDetailPage.tsx`. Preview each
recipient/channel, merged content, template/artifact revision and attachment
disclosure before send approval. Separate accepted/delivered/read/failed/unknown.
Recheck grants/recipient eligibility at effect time. Provider idempotency/status
lookup handles unknown outcomes; absent both, hold for reconciliation, never blind
resend. B2 outbox commits with approved intent. Add migrations for immutable payload
and recipient histories; rollback stops unsent jobs, never erases sent evidence.
A21; RF20/RF21/RF23/RF29. Fake email/portal/post tests cover timeout, callback replay,
revoked audience, no-email recipient and delivery failure recovery. V04/V06 gate
live provider/channel evidence and processor/retention decisions.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/16b-messaging.md` schreiben; in `../state.md`
`current: steps/16c-inbound-email.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/16c-inbound-email.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/16c-inbound-email.md` anhand von state.md und contract.md weiter.“
