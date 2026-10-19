# MVP Hausverwaltung

## 1. Ziel

Das MVP ist erreicht, wenn alle Kernabläufe einer Haus- und WEG-Verwaltung im Eigenbetrieb in der
laufenden App von Anfang bis Ende funktionieren: Stammdaten, Mietverträge, Mietkonto mit
Bankimport, Belege und Kosten, Betriebskostenabrechnung, WEG-Wirtschaftsplan und Jahresabrechnung,
Mahnung, Dokumente und eine Jahresübersicht für die Steuer. Referenz für Umfang und Bedienablauf
ist die Referenzsoftware (`docs/fachreferenz.md`, Abläufe RF01–RF31). Die Referenzsoftware hat keine
WEG-Verwaltung, dort ist diese App schon weiter; Rückstand besteht bei Bankimport,
Vertragslebenszyklus, Zählern, Heizkosten, Dokumenten, Aufgaben und Exporten.

## 2. Funktionskatalog

Status aus dem Code, Stand 2026-10-16, Migrations-Head `0123`. Testlauf: Backend 2677 grün,
1 echter Fehler (T00), 1 instabiler Parallelitätstest; Frontend 317 grün, 1 instabiler Test.
Kurzpfade: `api/ services/ engine/ models/ workers/` liegen unter `backend/app/`,
`pages/ components/` unter `frontend/src/`. Referenz: ja/teilw./nein.

### 2.1 Zugang und Organisation

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Login/Logout | ja | ✅ | `api/auth.py`, `pages/LoginPage.tsx` |
| Benutzer anlegen | ja | 🟡 Backend da, UI nur Liste | `api/users.py:102`, `pages/UsersPage.tsx` |
| Rollen Admin/Sachbearbeiter/Nur-Lesen | ja | 🟡 fest, nicht änderbar | `core/rbac.py` |
| Passwort ändern | ja | ❌ | `api/auth.py` |
| Mandate mit Dokumenten | nein | 🟡 kein Bearbeiten/Beenden | `api/mandates.py` |
| Quellen-Governance (Bereiche, Freigaben, Quarantänen) | nein | ✅ im Default-Modus PREPARATION nicht blockierend, überlädt das Menü | `services/financial_scope_service.py:59-72` |
| Dashboard | ja | 🟡 nur Fristen und Zähler | `pages/DashboardPage.tsx` |

### 2.2 Objekte und Einheiten

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Objekt anlegen/bearbeiten (Miete/WEG/SEV/Misch) | ja | ✅ (kein Löschen) | `api/properties.py`, `pages/PropertiesPage.tsx` |
| Baujahr, Gesamtfläche, Fotos | ja | ❌ | `models/property.py` |
| Einheiten (Typ, Fläche, beheizte Fläche) | ja | ✅ | `api/units.py`, `pages/PropertyDetailPage.tsx` |
| MEA erfassen | ja | 🟡 Skala nur per Teilungserklärung, sonst 409; Endpoint ohne UI | `api/units.py:208-221`, `api/properties.py:189` |
| Teilungserklärung-Import (KI, Prüfung) | nein | ✅ (Flag + OpenAI-Key) | `api/declaration_imports.py` |
| Gebäudewissen mit Quellen | nein | ✅ | `pages/PropertyKnowledgePage.tsx` |
| Zähler, Zählerstände | ja | ❌ nur Datenmodell | `models/meter.py` |
| CSV-Import Stammdaten | ja | ❌ | – |

### 2.3 Personen und Mietverhältnisse

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Kontakte, Mieter-/Eigentümerprofil | ja | ✅ | `api/persons.py`, `pages/PersonDetailPage.tsx` |
| Dienstleister | ja | 🟡 kein Bearbeiten | `api/vendors.py` |
| Eigentum mit Anteil | ja | 🟡 kein Beenden, kein Eigentümerwechsel | `api/ownership.py` |
| Mietvertrag anlegen (Kaltmiete, NK, Kaution) | ja | ✅ | `api/leases.py`, `pages/UnitDetailPage.tsx` |
| Vertrag ändern/beenden, Mieterwechsel | ja | 🟡 nur Anlegen, Ende nicht nachtragbar | `api/leases.py` |
| Mietänderung mit Stichtag | ja | ❌ | `models/lease_agreement.py` |
| Staffel-/Indexmiete | ja | ❌ | – |
| Kautionsverwaltung | teilw. | 🟡 nur Felder | `models/lease_agreement.py:58` |
| Vertragsvorlage, Übergabeprotokoll | ja | ❌ | – |

### 2.4 Mietkonto, Bank und Zahlungen

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Bankkonten | ja | ✅ (kein Bearbeiten) | `api/bank_accounts.py` |
| Sollstellung Miete/NK tagegenau | ja | ✅ live abgeleitet | `engine/mietkonto.py:203` |
| Zahlung erfassen, buchen, stornieren | ja | 🟡 Entwurf im UI nicht änderbar | `api/payments.py`, `pages/PaymentDetailPage.tsx` |
| Mietkonto je Mieter/Eigentümer, Offene Posten | ja | ✅ | `pages/MietkontoPage.tsx`, `pages/OpenItemsPage.tsx` |
| Kontoauszug-Import CSV/CAMT | teilw. | ❌ | – |
| Bankabruf PSD2/FinTS | ja | ❌ | – |
| Zuordnungsvorschläge, Splitbuchung | ja | ❌ Handzuordnung je Zahlung | `engine/mietkonto.py:347` |
| SEPA-Lastschrift | ja | ❌ | – |

### 2.5 Belege und Kosten

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Kostenposition erfassen/bestätigen | ja | 🟡 Formular sendet `full`/`AREA` statt `UMLAGEFAEHIG`/`WOHNFLAECHE` | `pages/CostEntriesPage.tsx:55-71` |
| Kategorien BetrKV | ja | ✅ 29 feste | `api/cost_categories.py` |
| Beleg hochladen, Kosten aus Beleg | ja | ✅ | `pages/InvoiceDocumentDetailPage.tsx` |
| OCR, E-Rechnung XML | ja | 🟡 Worker wird im Betrieb nicht gestartet | `workers/invoice_extraction.py`, `docker/worker-entrypoint.sh` |
| Posteingang mit Prüfkette | teilw. | 🟡 verdrahtet, ~9 Schritte, Worker fehlt | `api/intake_batches.py` |
| Klärungsfälle | nein | ✅ | `api/exceptions.py` |
| GoBD: unveränderbar, Storno, Korrektur | – | ✅ | `services/cost_entry_service.py` |

### 2.6 Betriebs- und Heizkostenabrechnung

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| BK je Vertrag: Berechnen, Finalisieren, PDF, Korrektur | ja | ✅ | `api/settlements.py`, `services/settlement_pdf.py` |
| Schlüssel Fläche/MEA/Einheiten, Zeitanteil, Leerstand | ja | ✅ | `engine/distribution.py` |
| Schlüssel Verbrauch/Personen/extern/manuell | ja | 🟡 UI zeigt Eingaben wegen falscher Schlüsselnamen nie | `pages/SettlementDetailPage.tsx:76-95` |
| Vorauszahlungen aus Zahlungen | ja | 🟡 nur volles Kalenderjahr | `services/settlement_service.py:229` |
| Sammellauf je Objekt | ja | ❌ | `pages/SettlementsPage.tsx` |
| Vorschlag neue Vorauszahlung | ja | ❌ | – |
| Heizkosten nach HeizkostenV | ja | ❌ nur Fremdabrechnung je Einheit | – |
| Verbrauch aus Zählern | ja | ❌ | – |

### 2.7 WEG

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Wirtschaftsplan, Hausgeld-Soll, PDF | nein | ✅ | `api/economic_plans.py`, `services/payment_service.py:422` |
| Jahresabrechnung, PDF | nein | ✅ | `api/weg_settlements.py` |
| Gezahltes Hausgeld übernehmen | nein | 🟡 Service ohne Route/UI | `services/weg_settlement_service.py:501` |
| Erhaltungsrücklage | nein | 🟡 nur Eingabewerte | `engine/weg.py` |
| Vermögensbericht § 28 Abs. 4 WEG | nein | ✅ | `engine/vermoegensbericht.py` |
| Sonderumlage | nein | ❌ | – |
| Eigentümerwechsel unterjährig | nein | 🟡 Engine ja, Eigentum nicht beendbar | `api/ownership.py` |
| Versammlung, Niederschrift, Beschluss-Sammlung | nein | ✅ | `api/meetings.py` |
| Einladung als PDF | nein | ❌ nur Textfeld | `services/meeting_pdf.py` |

### 2.8 Mahnwesen, Fristen, Aufgaben

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Mahnung, 3 Stufen, Gebühr, PDF | teilw. | ✅ | `api/dunning.py`, `engine/dunning.py` |
| Verzugszinsen, Mahnlauf | nein | ❌ | `engine/dunning.py:26` |
| Fristenmonitor | teilw. | ✅ abgeleitet | `api/fristen.py` |
| Aufgaben, Wiedervorlagen | ja | ❌ | – |

### 2.9 Dokumente, Kommunikation, Berichte

| Funktion | Referenz | Status im Code | Fundstelle |
| --- | --- | --- | --- |
| Dokumente je Objekt/Einheit/Person | ja | 🟡 nur Mandats- und Importdokumente | `api/mandates.py`, `api/property_documents.py` |
| Briefvorlagen, Versand per E-Mail/Brief | ja | ❌ | – |
| Mieterportal | ja | ❌ (F10) | – |
| Jahresübersicht je Objekt (Anlage V) | ja | ❌ | – |
| CSV-/DATEV-Export | ja | ❌ | – |
| DSGVO Auskunft/Löschung | – | ✅ (Auskunft nur am Bildschirm) | `api/dsgvo.py` |

## 3. Aufgaben

Backend-Tests laufen in `backend/` gegen die flüchtige Test-DB `localhost:55432`: vorher
`source ../scripts/test-env.sh` (T01; bis dahin die Exporte aus
`docs/operations/declaration-imports.md`, „Lokale Tests“). Frontend-Tests in `frontend/`.
„Ablauf in der App“ heißt lokal über `scripts/dev-local.sh` (T01), nie gegen Port 8000
(Produktion). Neue Migrationen ab `0124`.

### Phase 1 – Vorhandenes durchgängig nutzbar machen

- [x] **T00 Roten Test reparieren** — Test-Assertion auf neuen Worker-Eintrag mit `HV_WORKER_KIND` umstellen.
  Dateien: `backend/tests/test_declaration_runtime_config.py`
  Fertig, wenn: `uv run pytest tests/test_declaration_runtime_config.py` grün.
- [x] **T01 Sichere lokale Startumgebung** — Vite-Proxy-Ziel aus `HV_API_TARGET`, Default `http://127.0.0.1:18000` (heute `:8000` = Produktion). `scripts/test-env.sh` exportiert die Laufzeit-, Migrator- und Review-Verbindungen für `:55432`; `scripts/dev-local.sh` startet bei Bedarf die Test-DB, migriert, legt per `app.admin_cli` einen Test-Admin an, startet API auf 18000 und Vite auf 13000.
  Dateien: `frontend/vite.config.ts`, `scripts/test-env.sh` (neu), `scripts/dev-local.sh` (neu), `backend/app/admin_cli.py`, `infra/docker-compose.declaration-test.yml`
  Fertig, wenn: `uv run pytest tests/test_disposable_database_guard.py` grün UND Ablauf in der App: „auf :13000 als Test-Admin anmelden, Dashboard erscheint, keine Anfrage an :8000“.
- [x] **T02 Kostenformular mit DB-Werten** — Umlagefähigkeit und Schlüssel auf die DB-Werte (`UMLAGEFAEHIG`, `WOHNFLAECHE`, `VERBRAUCH`, `EXTERNE_ABRECHNUNG` …) mit deutschen Labels umstellen, gemeinsam in `frontend/src/lib/costVocabulary.ts`.
  Dateien: `frontend/src/pages/CostEntriesPage.tsx`, `frontend/src/pages/CostEntryDetailPage.tsx`, `frontend/src/lib/costVocabulary.ts` (neu), `backend/migrations/versions/0006_cost_entry_invoice_document.py:28-35`
  Fertig, wenn: `npm test -- src/pages/CostEntriesPage.test.tsx` (neu, prüft Payload) grün UND Ablauf in der App: „Hausmeister 1.200 € mit Schlüssel ‚Verbrauch‘ speichern und bestätigen, kein Fehler“.
- [x] **T03 BK-Zusatzeingaben anzeigen** — `needsConsumption`, `needsPersonCount`, `needsExternalInput`, `needsManualInput` auf die DB-Schlüssel umstellen und prüfen, dass der Compute-Body die Felder sendet, die `api/settlements.py` erwartet.
  Dateien: `frontend/src/pages/SettlementDetailPage.tsx` (`rg -n "needs|unit_metrics"`), `frontend/src/lib/costVocabulary.ts`, `backend/app/api/settlements.py`, `backend/app/engine/types.py`
  Fertig, wenn: `npm test -- src/pages/SettlementDetailPage.test.tsx` (neu) und `uv run pytest tests/test_m3_settlement.py` grün UND Ablauf in der App: „Wasser nach Verbrauch, Heizung extern eingeben, berechnen, finalisieren, PDF öffnen“.
- [x] **T04 Mietvertrag ändern und beenden** — `PATCH /api/leases/{id}` für Ende und Notiz; Beträge nur ohne gebuchte Zahlung und finalisierte Abrechnung (sonst 409); Überlappungsprüfung bleibt; Knöpfe in der Einheit.
  Dateien: `backend/app/api/leases.py`, `backend/app/repositories/lease_agreement_repo.py`, `frontend/src/pages/UnitDetailPage.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `uv run pytest tests/test_lease_update.py` (neu) grün UND Ablauf in der App: „Vertrag zum 31.12. beenden, Nachmieter ab 01.01. anlegen“.
- [x] **T05 MEA-Skala von Hand bestätigen** — Auf der Objektseite Feld „MEA-Nenner“ und Knopf für `POST /api/properties/{id}/mea-scale/confirm` (`{"denominator": n}`).
  Dateien: `frontend/src/pages/PropertyDetailPage.tsx`, `frontend/src/api/endpoints.ts`, `backend/app/api/properties.py:71,189-220`, `backend/app/api/units.py:200-221`
  Fertig, wenn: `uv run pytest tests/test_declaration_mea_scale.py` grün UND Ablauf in der App: „WEG-Objekt anlegen, Nenner 1000, Einheiten mit 300/300/400 MEA ohne 409“.
- [x] **T06 Benutzer in der UI anlegen** — Formular (E-Mail, Name, Rolle, Startpasswort) für `POST /api/users`, nur für Admins.
  Dateien: `frontend/src/pages/UsersPage.tsx`, `frontend/src/api/endpoints.ts`, `backend/app/api/users.py`
  Fertig, wenn: `uv run pytest tests/test_rbac.py` grün UND Ablauf in der App: „Sachbearbeiter anlegen und mit ihm anmelden“.
- [x] **T07 Zahlungsentwurf bearbeiten** — Bearbeiten-Formular für Entwürfe über das vorhandene `PATCH /api/payments/{id}`.
  Dateien: `frontend/src/pages/PaymentDetailPage.tsx`, `frontend/src/api/endpoints.ts`, `backend/app/api/payments.py:56-75`
  Fertig, wenn: `uv run pytest tests/test_m6_zahlungen.py` grün UND Ablauf in der App: „Monat im Entwurf korrigieren, buchen, Mietkonto stimmt“.
- [x] **T08 BK-Vorauszahlungen für jeden Zeitraum** — Sperre „nur volles Kalenderjahr“ aufheben: alle gebuchten `NK_VORAUSZAHLUNG` des Vertrags mit Monat im Abrechnungszeitraum.
  Dateien: `backend/app/services/settlement_service.py:205-260`, `backend/app/services/payment_service.py`, `frontend/src/pages/SettlementDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m3_settlement.py -k derived` grün (neu: 01.07.–30.06. und Auszug 30.09.) UND Ablauf in der App: „Abrechnung Jan–Sep übernimmt die Zahlungen Jan–Sep“.
- [x] **T09 WEG: gezahltes Hausgeld übernehmen** — Route `POST /api/weg-settlements/{id}/paid-advances-derived` auf `derive_paid_advances`, Knopf in der Jahresabrechnung.
  Dateien: `backend/app/api/weg_settlements.py`, `backend/app/services/weg_settlement_service.py:501-540`, `frontend/src/pages/WegSettlementDetailPage.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `uv run pytest tests/test_m4_weg.py -k derive` (neu) grün UND Ablauf in der App: „12 Hausgeldzahlungen, Übernahme ergibt richtige Ist-Vorschüsse“.
- [ ] **T10 Beleg-Worker im Betrieb** — `worker-entrypoint.sh` wählt über `HV_WORKER_KIND` (`declaration_import` Default, `invoice_extraction`, `intake_extraction`); Dienst `invoice-worker` in der Prod-Compose ergänzen. Nur Dateien ändern, nichts deployen oder neu starten. ⚠ offen: Der verbindliche Hausverwaltung-Guard verweigert Zugriff auf die Produktions-Compose; Dienstkonfiguration und Betriebsnachweis sind daher nicht abgeschlossen.
  Dateien: `docker/worker-entrypoint.sh`, `docker-compose.prod.yml` (Dienst `worker` als Muster), `backend/app/workers/invoice_extraction.py`, `DEPLOY.md`
  Fertig, wenn: `uv run pytest tests/test_invoice_extraction_worker.py` grün und `docker compose -f docker-compose.prod.yml config -q` fehlerfrei UND Ablauf in der App: „lokal Worker starten, PDF-Rechnung hochladen, Betrag und Datum erscheinen“.
- [ ] **T11 Menü entrümpeln** — Quellenfreigaben, Altquellen, Berechtigungen, Freigaben vorbereiten, Quarantänen und Seitenauszüge in eine Admin-Gruppe „Erweitert“.
  Dateien: `frontend/src/components/Sidebar.tsx`, `frontend/src/App.tsx` (nur lesen)
  Fertig, wenn: `npm test -- src/App.test.tsx` grün UND Ablauf in der App: „Sachbearbeiter sieht keine Governance-Einträge“.
- [ ] ~~**T12 Durchstich Mietjahr (Test)**~~ — API-Test: Objekt, Einheiten, Mieter, Vertrag, 11 von 12 Monaten bezahlt, Kosten bestätigt, BK mit abgeleiteten Vorauszahlungen, PDF, Saldo eine Monatsmiete, Mahnung mit PDF. Gefundene Fehler hier beheben.
  Dateien: `backend/tests/test_journey_miete.py` (neu), `backend/tests/conftest.py`, Muster `backend/tests/test_m3_settlement.py`, `backend/tests/test_m7_dunning.py`
  Fertig, wenn: `uv run pytest tests/test_journey_miete.py` grün UND Ablauf in der App: „dieselben Schritte einmal klicken“.
⚠ offen: Der API-Endpoint `/api/settlements/{id}/advance-payments-derived` existiert nicht oder erlaubt kein PATCH (405 Method Not Allowed); der Test scheitert nach zwei Korrekturversuchen (falscher Units-Endpoint korrigiert, advance-payments-derived nicht implementiert).
- [ ] **T13 Durchstich WEG-Jahr (Test)** — API-Test: WEG mit MEA, zwei Eigentümer, Plan, Hausgeld, Kosten, Jahresabrechnung (T09), Vermögensbericht, Beschluss in der Sammlung, alle PDFs 200. ⚠ offen: ASGI-Durchstich grün; über die lokale API liefert der PDF-Abruf nach erfolgreicher Finalisierung weiterhin 409 („only available for a finalized run“), auch nach Korrektur der Review-DB-Konfiguration.
  Dateien: `backend/tests/test_journey_weg.py` (neu), `backend/tests/conftest.py`, Muster `backend/tests/test_m4_weg.py`, `backend/tests/test_m8_meetings.py`
  Fertig, wenn: `uv run pytest tests/test_journey_weg.py` grün UND Ablauf in der App: „dieselben Schritte einmal klicken“.

### Phase 2 – Fehlende Kernfunktionen

- [ ] **T14 Passwort ändern** — `POST /api/auth/password` (alt, neu; Hash wie beim Login) und Dialog im Benutzermenü.
  Dateien: `backend/app/api/auth.py`, `backend/app/core/security.py`, `frontend/src/components/Topbar.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `uv run pytest tests/test_auth_password.py` (neu) grün UND Ablauf in der App: „Passwort ändern, neu anmelden“.
- [ ] **T15 Tabelle Bankumsätze** — Modell `bank_transaction` (Konto, Buchungs-/Valutadatum, Betrag, Gegenkonto Name/IBAN, Zweck, `import_hash` eindeutig je Konto, Status NEU/ZUGEORDNET/IGNORIERT) mit RLS; `payment.bank_transaction_id` optional.
  Dateien: `backend/app/models/bank_transaction.py` (neu), `backend/migrations/versions/0124_bank_transaction.py` (neu, Muster `0016_payment_table.py`), `backend/app/repositories/bank_transaction_repo.py` (neu), `backend/app/models/payment.py`
  Fertig, wenn: `uv run pytest tests/test_rls_coverage.py tests/test_bank_transactions.py` (neu) grün.
- [ ] **T16 CSV-Kontoauszug parsen** — Reine Funktion `parse_csv(bytes)` in `engine/bank_import.py`: UTF-8/CP1252, `;`, deutsche Datums- und Dezimalformate, Spalten der Bank aus F2; unbekanntes Format gibt klare Meldung.
  Dateien: `backend/app/engine/bank_import.py` (neu), `backend/tests/fixtures/bank/` (neu, synthetisch), `backend/tests/engine/test_bank_import_csv.py` (neu)
  Fertig, wenn: `uv run pytest tests/engine/test_bank_import_csv.py` grün.
- [ ] **T17 CAMT.053 parsen** — `parse_camt053(bytes)` für XML und ZIP (`camt.053.001.02` und `.08`), Ausgabe wie T16.
  Dateien: `backend/app/engine/bank_import.py`, `backend/tests/engine/test_bank_import_camt.py` (neu)
  Fertig, wenn: `uv run pytest tests/engine/test_bank_import_camt.py` grün.
- [ ] **T18 Import-Endpoint mit Vorschau** — `POST /api/bank-accounts/{id}/imports` mit `dry_run` (Anzahl, Summe, Zeitraum, Dubletten), sonst Übernahme; gleiche Datei zweimal ergibt 0 neue.
  Dateien: `backend/app/api/bank_accounts.py`, `backend/app/services/bank_import_service.py` (neu), `backend/app/engine/bank_import.py`, `backend/app/core/uploads.py`
  Fertig, wenn: `uv run pytest tests/test_bank_import_api.py` (neu) grün.
- [ ] **T19 Import-Oberfläche und Umsatzliste** — „Kontoauszug importieren“ (Datei, Vorschau, Übernehmen) und Seite `/bankkonten/:id/umsaetze` mit Statusfilter; Vorbild `rg -n "RF11|RF12" docs/fachreferenz.md`.
  Dateien: `frontend/src/pages/BankAccountsPage.tsx`, `frontend/src/pages/BankTransactionsPage.tsx` (neu), `frontend/src/App.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `npm test -- src/pages/BankTransactionsPage.test.tsx` (neu) grün UND Ablauf in der App: „CSV importieren, Vorschau, übernehmen; zweiter Import meldet 0 neue“.
- [ ] **T20 Zuordnungsvorschläge** — Reine Funktion `suggest_matches` in `engine/bank_matching.py`: Umsatz gegen offenes Soll (`tenant_expected_charges`, `owner_expected_charges`) über IBAN, Betrag, Name, Monat; Ergebnis mit Splitzeilen und Konfidenz.
  Dateien: `backend/app/engine/bank_matching.py` (neu), `backend/app/engine/mietkonto.py`, `backend/tests/engine/test_bank_matching.py` (neu)
  Fertig, wenn: `uv run pytest tests/engine/test_bank_matching.py` grün (Warmmiete, Teilzahlung, fremde IBAN, Hausgeld).
- [ ] **T21 Umsatz zuordnen (API)** — `GET /api/bank-transactions?status=NEU` mit Vorschlägen, `POST /{id}/assign` (Zeilen decken den Umsatz genau, erzeugt gebuchte Zahlungen), `POST /{id}/ignore`; Doppelzuordnung 409.
  Dateien: `backend/app/api/bank_transactions.py` (neu), `backend/app/main.py`, `backend/app/services/bank_import_service.py`, `backend/app/services/payment_service.py`
  Fertig, wenn: `uv run pytest tests/test_bank_transaction_assign.py` (neu) grün.
- [ ] **T22 Umsatz zuordnen (UI)** — Je Umsatz „Vorschlag übernehmen“, Dialog zum Aufteilen, „Ignorieren“.
  Dateien: `frontend/src/pages/BankTransactionsPage.tsx`, `frontend/src/api/endpoints.ts`, `frontend/src/api/types.ts`
  Fertig, wenn: `npm test -- src/pages/BankTransactionsPage.test.tsx` grün UND Ablauf in der App: „Warmmiete übernehmen, Mietkonto zeigt Kaltmiete und NK bezahlt“.
- [ ] **T23 Mietänderung mit Stichtag (Backend)** — Tabelle `lease_rent_change` (gültig ab, Kaltmiete, Vorauszahlung) mit RLS, `GET/POST /api/leases/{id}/rent-changes`; `tenant_expected_charges` nimmt je Tag den gültigen Betrag.
  Dateien: `backend/app/models/lease_rent_change.py` (neu), `backend/migrations/versions/0125_lease_rent_change.py` (neu), `backend/app/api/leases.py`, `backend/app/engine/mietkonto.py:203-256`
  Fertig, wenn: `uv run pytest tests/engine/test_golden_mietkonto.py tests/test_rent_changes.py` (neu: Erhöhung zum 15.03.) grün.
- [ ] **T24 Mietänderung (UI)** — Liste und Formular „Miete ändern ab …“ in der Einheit.
  Dateien: `frontend/src/pages/UnitDetailPage.tsx`, `frontend/src/api/endpoints.ts`, `frontend/src/api/types.ts`
  Fertig, wenn: `npm test -- src/pages/UnitDetailPage.test.tsx` (neu) grün UND Ablauf in der App: „+50 € ab 01.04., Mietkonto zeigt ab April das neue Soll“.
- [ ] **T25 Kaution** — Kaution als eigenes Soll außerhalb des Mietsaldos, Zahlung/Rückzahlung Typ `KAUTION`, Seite `/kautionen` (Soll, gezahlt, zurück).
  Dateien: `backend/app/engine/mietkonto.py`, `backend/app/api/payments.py`, `frontend/src/pages/DepositsPage.tsx` (neu), `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`
  Fertig, wenn: `uv run pytest tests/test_deposits.py` (neu) grün UND Ablauf in der App: „1.800 € in zwei Raten, Übersicht vollständig, Mietkonto unverändert“.
- [ ] **T26 Eigentümerwechsel** — `PATCH /api/ownership/{id}` setzt das Ende; in der Einheit „Eigentum beenden“ und neuer Eigentümer ab Folgetag.
  Dateien: `backend/app/api/ownership.py`, `backend/app/repositories/ownership_history_repo.py`, `frontend/src/pages/UnitDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_ownership_change.py` (neu, Abrechnung tagegenau) grün UND Ablauf in der App: „Verkauf zum 30.06., Jahresabrechnung zeigt beide anteilig“.
- [ ] **T27 BK-Sammellauf je Objekt** — `POST /api/settlements/batch` (Objekt, Zeitraum) legt Entwürfe für alle Verträge an, vorhandene übersprungen; Knopf auf der Abrechnungsliste.
  Dateien: `backend/app/api/settlements.py`, `backend/app/services/settlement_service.py:140-200`, `frontend/src/pages/SettlementsPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m3_settlement.py -k batch` (neu) grün UND Ablauf in der App: „drei Verträge, ein Klick, drei Entwürfe, kein Duplikat“.
- [ ] **T28 Neue Vorauszahlung vorschlagen** — Nach Finalisieren Vorschlag Mieteranteil/Monate; „Übernehmen ab …“ legt eine Mietänderung (T23) an.
  Dateien: `backend/app/services/settlement_service.py`, `backend/app/api/settlements.py`, `frontend/src/pages/SettlementDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m3_settlement.py -k proposal` (neu) grün UND Ablauf in der App: „Vorschlag übernehmen, Mietkonto zeigt neue Vorauszahlung“.
- [ ] **T29 Zähler-API** — `GET/POST/PATCH /api/meters` und `GET/POST /api/meters/{id}/readings` auf die vorhandenen Tabellen.
  Dateien: `backend/app/api/meters.py` (neu), `backend/app/repositories/meter_repo.py` (neu), `backend/app/models/meter.py`, `backend/app/main.py`
  Fertig, wenn: `uv run pytest tests/test_meters_api.py` (neu, mit Org-Trennung) grün.
- [ ] **T30 Zähler-UI** — Abschnitt „Zähler“ in Objekt und Einheit: anlegen, Stand erfassen, Verlauf; Vorbild `rg -n "RF13" docs/fachreferenz.md`.
  Dateien: `frontend/src/components/MeterPanel.tsx` (neu), `frontend/src/pages/UnitDetailPage.tsx`, `frontend/src/pages/PropertyDetailPage.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `npm test -- src/components/MeterPanel.test.tsx` (neu) grün UND Ablauf in der App: „Kaltwasserzähler mit Ständen 01.01./31.12., Verbrauch sichtbar“.
- [ ] **T31 Verbrauch aus Zählerständen** — Bei `VERBRAUCH` ohne Handwert Verbrauch je Einheit aus Ständen ±31 Tage um Periodenbeginn/-ende vorbelegen, sonst Handeingabe verlangen.
  Dateien: `backend/app/engine/meter_consumption.py` (neu), `backend/app/services/settlement_service.py`, `backend/app/repositories/meter_repo.py`, `frontend/src/pages/SettlementDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/engine/test_meter_consumption.py tests/test_m3_settlement.py` grün UND Ablauf in der App: „Wasserkosten nach Zählern abgerechnet“.
- [ ] **T32 Sonderumlage (Backend)** — Tabelle `special_levy` (Objekt, Betrag, Fälligkeit, optional Beschluss), Verteilung nach MEA als Eigentümer-Soll `SONDERZAHLUNG`; `GET/POST /api/special-levies`.
  Dateien: `backend/app/models/special_levy.py` (neu), `backend/migrations/versions/0126_special_levy.py` (neu), `backend/app/api/special_levies.py` (neu), `backend/app/engine/mietkonto.py:257-300`
  Fertig, wenn: `uv run pytest tests/test_special_levy.py` (neu: 10.000 € auf 300/300/400) grün.
- [ ] **T33 Sonderumlage (UI)** — Seite unter WEG: anlegen, Anteile je Eigentümer; erscheint im Eigentümerkonto und ist mahnbar.
  Dateien: `frontend/src/pages/SpecialLeviesPage.tsx` (neu), `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `npm test -- src/pages/SpecialLeviesPage.test.tsx` (neu) grün UND Ablauf in der App: „Eigentümerkonto zeigt 3.000 € offen“.
- [ ] **T34 Einladung zur Versammlung als PDF** — `GET /api/meetings/{id}/invitation.pdf`: je Eigentümer eine Seite mit Anschrift, Termin, Tagesordnung; Hinweis bei weniger als drei Wochen (§ 24 Abs. 4 WEG).
  Dateien: `backend/app/services/meeting_pdf.py`, `backend/app/api/meetings.py:435-470`, `frontend/src/pages/MeetingDetailPage.tsx` (`rg -n "pdf"`)
  Fertig, wenn: `uv run pytest tests/test_m8_meetings.py -k invitation` (neu) grün UND Ablauf in der App: „Einladung laden, eine Seite je Eigentümer“.
- [ ] **T35 Verzugszinsen** — Offener Betrag × (Basiszinssatz + 5 Pp.) × Tage/365 ab Fälligkeit (§ 288 Abs. 1 BGB), Basiszinssatz als Eingabe mit Vorgabe aus Konfiguration, Ausweis im PDF.
  Dateien: `backend/app/engine/dunning.py`, `backend/app/services/dunning_service.py`, `backend/app/services/dunning_pdf.py`, `frontend/src/pages/DunningNoticesPage.tsx`
  Fertig, wenn: `uv run pytest tests/engine/test_dunning_engine.py tests/test_m7_dunning.py` grün UND Ablauf in der App: „Mahnung über 700 € nach 60 Tagen zeigt Zinsen“.
- [ ] **T36 Dokumentenablage (Backend)** — Tabelle `attachment` nach Muster `mandate_document` (Upload, Download, Archivieren), Bezug Objekt und optional Einheit, Vertrag oder Person; Routen `/api/attachments`.
  Dateien: `backend/app/models/mandate_document.py` und `backend/app/services/mandate_document_service.py` (Muster), `backend/app/models/attachment.py` (neu), `backend/migrations/versions/0127_attachment.py` (neu), `backend/app/api/attachments.py` (neu)
  Fertig, wenn: `uv run pytest tests/test_attachments.py tests/test_rls_coverage.py` grün.
- [ ] **T37 Dokumentenablage (UI)** — Komponente „Dokumente“ in Objekt, Einheit und Person: hochladen, öffnen, archivieren; Vorbild `rg -n "RF10" docs/fachreferenz.md`.
  Dateien: `frontend/src/components/AttachmentsPanel.tsx` (neu), `frontend/src/pages/PropertyDetailPage.tsx`, `frontend/src/pages/UnitDetailPage.tsx`, `frontend/src/pages/PersonDetailPage.tsx`
  Fertig, wenn: `npm test -- src/components/AttachmentsPanel.test.tsx` (neu) grün UND Ablauf in der App: „Vertrags-PDF an der Einheit ablegen und öffnen“.
- [ ] **T38 Jahresübersicht je Objekt (Backend)** — `GET /api/reports/property-year`: Einnahmen nach Zahlungstyp, Ausgaben nach Kategorie (umlagefähig/nicht), Saldo; Grundlage Anlage V.
  Dateien: `backend/app/api/reports.py` (neu), `backend/app/services/report_service.py` (neu), `backend/app/repositories/payment_repo.py`, `backend/app/repositories/cost_entry_repo.py`
  Fertig, wenn: `uv run pytest tests/test_property_year_report.py` (neu) grün.
- [ ] **T39 Jahresübersicht (UI, CSV)** — Seite `/berichte` mit Objekt/Jahr, Tabelle und CSV (Semikolon, Dezimalkomma).
  Dateien: `frontend/src/pages/ReportsPage.tsx` (neu), `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`, `backend/app/api/reports.py`
  Fertig, wenn: `npm test -- src/pages/ReportsPage.test.tsx` (neu) grün UND Ablauf in der App: „Übersicht 2026 öffnen, CSV öffnet sich korrekt“.
- [ ] **T40 CSV-Export Zahlungen und Kosten** — `GET /api/payments/export.csv` und `/api/cost-entries/export.csv` mit den Listenfiltern, Knopf auf beiden Listen.
  Dateien: `backend/app/api/payments.py`, `backend/app/api/cost_entries.py`, `frontend/src/pages/PaymentsPage.tsx`, `frontend/src/pages/CostEntriesPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_csv_exports.py` (neu) grün UND Ablauf in der App: „Export-Summe stimmt mit der Liste“.
- [ ] **T41 Aufgaben (Backend)** — Tabelle `task` (Titel, Notiz, fällig, erledigt, optional Objekt/Einheit/Vertrag, Benutzer) mit RLS, CRUD `/api/tasks`.
  Dateien: `backend/app/models/task.py` (neu), `backend/migrations/versions/0128_task.py` (neu), `backend/app/api/tasks.py` (neu), `backend/app/main.py`
  Fertig, wenn: `uv run pytest tests/test_tasks_api.py tests/test_rls_coverage.py` grün.
- [ ] **T42 Aufgaben (UI)** — Seite `/aufgaben`, Anlegen aus Objekt und Einheit, fällige im Dashboard; Vorbild `rg -n "RF17" docs/fachreferenz.md`.
  Dateien: `frontend/src/pages/TasksPage.tsx` (neu), `frontend/src/pages/DashboardPage.tsx`, `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`
  Fertig, wenn: `npm test -- src/pages/TasksPage.test.tsx` (neu) grün UND Ablauf in der App: „Aufgabe fällig morgen erscheint im Dashboard, abhaken“.

### Phase 3 – Parität und Komfort

- [ ] **T43 Staffelmiete** — Aus Start, Betrag oder Prozent, Intervall und Stufenzahl mehrere Mietänderungen (T23) anlegen.
  Dateien: `frontend/src/pages/UnitDetailPage.tsx`, `backend/app/api/leases.py`
  Fertig, wenn: `uv run pytest tests/test_rent_changes.py -k staffel` grün UND Ablauf in der App: „+30 € jährlich, fünf Stufen sichtbar“.
- [ ] **T44 Indexmiete** — VPI alt/neu eingeben, neue Miete berechnen, 12 Monate Abstand prüfen (§ 557b BGB), als Mietänderung übernehmen.
  Dateien: `backend/app/engine/index_rent.py` (neu), `backend/app/api/leases.py`, `frontend/src/pages/UnitDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/engine/test_index_rent.py` (neu) grün UND Ablauf in der App: „VPI 110→115 bei 600 € ergibt 627,27 €“.
- [ ] **T45 HeizkostenV-Engine** — Grund-/Verbrauchsanteil 30–50/50–70 %, Warmwasser getrennt, 15 % Kürzung ohne Verbrauchserfassung (§ 12).
  Dateien: `backend/app/engine/heizkosten.py` (neu), `backend/app/engine/types.py`, `backend/tests/engine/test_heizkosten.py` (neu)
  Fertig, wenn: `uv run pytest tests/engine/test_heizkosten.py` grün.
- [ ] **T46 Heizkosten in der BK** — Schlüssel für Heizung nutzt T45 mit Zählerständen (T31), Eingaben in der Abrechnung.
  Dateien: `backend/app/services/settlement_service.py`, `backend/app/engine/settlement.py`, `frontend/src/pages/SettlementDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m3_settlement.py -k heiz` grün UND Ablauf in der App: „PDF zeigt Grund- und Verbrauchsanteil“.
- [ ] **T47 E-Mail-Versand** — SMTP aus der Umgebung, Abrechnung, Mahnung und Einladung an die Person senden, Versand am Dokument vermerken.
  Dateien: `backend/app/core/config.py`, `backend/app/services/mail_service.py` (neu), `backend/app/api/dunning.py`
  Fertig, wenn: `uv run pytest tests/test_mail_service.py` (neu, Fake-SMTP) grün UND Ablauf in der App: „Mahnung an lokalen Test-SMTP senden“.
- [ ] **T48 Briefvorlagen** — Vorlage mit Platzhaltern (`{{mieter.name}}`, `{{objekt.adresse}}`) speichern, je Empfänger als PDF erzeugen.
  Dateien: `backend/app/api/letters.py` (neu), `backend/migrations/versions/0129_letter_template.py` (neu), `frontend/src/pages/LettersPage.tsx` (neu)
  Fertig, wenn: `uv run pytest tests/test_letters.py` (neu) grün UND Ablauf in der App: „Rundschreiben an alle Mieter eines Objekts als PDF“.
- [ ] **T49 DATEV-Export** — Buchungsstapel (EXTF) für Zahlungen und Kosten mit Konto je Kategorie (Kontenrahmen aus F8).
  Dateien: `backend/app/services/datev_export.py` (neu), `backend/app/api/reports.py`
  Fertig, wenn: `uv run pytest tests/test_datev_export.py` (neu) grün UND Ablauf in der App: „Export Q1 laden, Summen stimmen“.
- [ ] **T50 SEPA-Lastschrift** — Gläubiger-ID, Mandat je Mieter, `pain.008` für die Monatsmiete.
  Dateien: `backend/app/services/sepa_export.py` (neu), `backend/app/api/payments.py`, `frontend/src/pages/PaymentsPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_sepa_export.py` (neu, XSD) grün UND Ablauf in der App: „Lastschriftdatei Oktober erzeugen“.
- [ ] **T51 Stammdaten-Import CSV** — Objekte, Einheiten, Kontakte aus CSV-Vorlage mit Vorschau und Fehlerliste.
  Dateien: `backend/app/services/master_import_service.py` (neu), `backend/app/api/properties.py`, `frontend/src/pages/PropertiesPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_master_import.py` (neu) grün UND Ablauf in der App: „1 Objekt, 4 Einheiten, 4 Kontakte importiert“.
- [ ] **T52 Dashboard-Kennzahlen** — Soll/Ist Miete des Monats, Rückstände, Leerstand; Vorbild `rg -n "RF01" docs/fachreferenz.md`.
  Dateien: `backend/app/api/reports.py`, `backend/app/engine/mietkonto.py`, `frontend/src/pages/DashboardPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_dashboard_kpis.py` (neu) grün UND Ablauf in der App: „Dashboard zeigt erwartete und eingegangene Miete“.
- [ ] **T53 Mahnlauf** — Liste aller Rückstände über Schwelle, ausgewählte als Mahnentwürfe in einem Schritt.
  Dateien: `backend/app/api/dunning.py`, `backend/app/services/dunning_service.py`, `frontend/src/pages/OpenItemsPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m7_dunning.py -k batch` (neu) grün UND Ablauf in der App: „drei Rückstände, drei Entwürfe“.
- [ ] **T54 Wirtschaftsplan aus Vorjahr** — Neuen Plan mit Ist-Kosten des Vorjahres je Kategorie vorbelegen.
  Dateien: `backend/app/services/economic_plan_service.py`, `backend/app/api/economic_plans.py`, `frontend/src/pages/EconomicPlansPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m4_weg.py -k previous` (neu) grün UND Ablauf in der App: „Plan 2027 aus Ist 2026“.
- [ ] **T55 Stammdaten-Lücken** — Bankkonto, Dienstleister und Mandat bearbeiten bzw. beenden; Objekt um Baujahr und Gesamtfläche ergänzen.
  Dateien: `backend/app/api/bank_accounts.py`, `backend/app/api/vendors.py`, `backend/app/api/mandates.py`, `backend/app/models/property.py`
  Fertig, wenn: `uv run pytest tests/test_m6_bank_accounts.py tests/test_m1_profiles_api.py` grün UND Ablauf in der App: „Bankkonto umbenennen“.
- [ ] **T56 Fehleingaben löschen** — Objekt, Einheit, Person, Vertrag löschbar, solange nichts darauf verweist (sonst 409 mit Grund).
  Dateien: `backend/app/api/properties.py`, `backend/app/api/units.py`, `backend/app/api/persons.py`, `backend/app/api/leases.py`
  Fertig, wenn: `uv run pytest tests/test_master_delete.py` (neu) grün UND Ablauf in der App: „leere Einheit löschen, Einheit mit Vertrag nennt Grund“.
- [ ] **T57 Benutzerverwaltung** — Rolle ändern, deaktivieren, Passwort zurücksetzen durch Admin; Schreibknöpfe für Nur-Lesen ausblenden.
  Dateien: `backend/app/api/users.py`, `frontend/src/pages/UsersPage.tsx`, `frontend/src/pages/UnitDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_rbac.py` grün UND Ablauf in der App: „deaktivierter Benutzer kann sich nicht anmelden“.

## 4. Offene fachliche Fragen

- F1 Welche Verwaltungsarten laufen produktiv (eigene Mietobjekte, WEG für Dritte, SEV)? Bestimmt, ob WEG-Aufgaben oder Anlage V zuerst kommen.
- F2 Welche Bank(en) und welches Format (CSV-Profil oder CAMT.053) sollen importiert werden?
- F3 Heizkosten über Messdienst (dann reicht „Externe Abrechnung“) oder eigene Verteilung nach HeizkostenV (T45/T46 vorziehen)?
- F4 Eine NK-Vorauszahlung inklusive Heizung oder getrennte BK- und HK-Vorauszahlung im Vertrag?
- F5 BK-Abrechnungszeitraum Kalenderjahr oder abweichend je Objekt?
- F6 Mahnstufen und Gebühren 0/5/10 € (heute vorläufig) beibehalten, Verzugszinsen ja oder nein?
- F7 Kaution in Raten (§ 551 BGB) und Zinsgutschrift des Kautionskontos abbilden?
- F8 Reicht die Jahresübersicht (T38) oder braucht der Steuerberater DATEV, mit SKR03 oder SKR04?
- F9 Gewerbeeinheiten mit Umsatzsteueroption (§ 9 UStG), also USt-Ausweis in der Abrechnung?
- F10 Bleibt der Quellen-Zugriffsschutz dauerhaft im Modus PREPARATION, und ist ein Mieterportal ausgeschlossen? Dann kann die Governance-Oberfläche weiter zurückgebaut werden.
