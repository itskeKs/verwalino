# 14a-bank-provider — Package 14

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 13-heating-co2. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Fake-/Sandbox-Adapter, Consent, Cursor und verifizierte Webhook-Reconciliation; Liveaktivierung getrennt freigeben.

## Gezielt zu suchende Einstiegspfade

- `backend/app/integrations/`
- `backend/app/models/bank_account.py`
- `backend/app/core/config.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**C3 — Bank provider connection.** P2.2 after C1 and B2. Add **new**
`integrations/banking.py`, `bank_connection` modules, `api/bank_connections.py`,
webhook ingress and `BankConnectionPage.tsx`. Adapter operations cover consent,
account discovery, cursor fetch, status/revoke and identity reconciliation; provider
credentials use supported secret storage, never browser sessions. Stored account
mapping determines org/scope; signed/replay-deduplicated webhooks never trust payload
org IDs. Pending→booked ambiguity creates a review case, not fuzzy suppression.
Migration adds connection/cursor/receipt history; rollback revokes or pauses future
sync and retains transactions, marking coverage gaps. A08; RF11/RF23 observation
only. Fake tests cover reconnection, expiry, out-of-order webhook, duplicate fetch
and account remapping denial. V04/V06 require chosen provider, contract, institution
coverage, sandbox evidence and separately authorized live activation.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/14a-bank-provider.md` schreiben; in `../state.md`
`current: steps/14b-sepa.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/14b-sepa.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/14b-sepa.md` anhand von state.md und contract.md weiter.“
