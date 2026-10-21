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
- [x] **T03 BK-Zusatzeingaben anzeigen** — DB-Schlüssel über `costVocabulary` normalisiert; Compute- und Finalize-Body mit Verbrauch, Personenzahl, externen und manuellen Beträgen geprüft. Ursache des HTTP-409: Die Finalisierung sendete 200 vor dem Commit im Request-Cleanup; ASGITransport wartete dagegen auf das Cleanup. BK-Finalisierung nutzt nun `Depends(get_db, scope="function")`: Commit vor Antwort, Entwurfs-PDF bleibt 409.
  Geprüft auf `mvp/phase-1`: Frontend 15 Tests, Backend (M3 + beide Journeys) 37 Tests grün; `scripts/dev-local.sh`, Chrome auf :13000 mit API :18000: Wasserverbrauch 30, Heizung extern 1.200 €, berechnet/finalisiert 1.800 €, sofortiger PDF-Abruf 200 und im Browser geöffnet; PDF-Parser bestätigt ein gültiges einseitiges PDF. Regression liest den DB-Status bereits bei `http.response.start`.
  Der anschließende vollständige Mietjahr-Durchstich und der nächste Arbeitsschritt sind unter **T12** dokumentiert.
  Dateien: `frontend/src/pages/SettlementDetailPage.tsx` (`rg -n "needs|unit_metrics"`), `frontend/src/lib/costVocabulary.ts`, `backend/app/api/settlements.py`, `backend/app/engine/types.py`
  Fertig, wenn: `npm test -- src/pages/SettlementDetailPage.test.tsx` (neu) und `uv run pytest tests/test_m3_settlement.py` grün UND Ablauf in der App: „Wasser nach Verbrauch, Heizung extern eingeben, berechnen, finalisieren, PDF öffnen“.
- [x] **T04 Mietvertrag ändern und beenden** — `PATCH /api/leases/{id}` für Ende und Notiz; Beträge nur ohne gebuchte Zahlung und finalisierte Abrechnung (sonst 409); Ende/Notiz bleiben danach editierbar; Überlappungsprüfung bleibt; Knöpfe in der Einheit.
  Dateien: `backend/app/api/leases.py`, `backend/app/repositories/lease_agreement_repo.py`, `frontend/src/pages/UnitDetailPage.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `uv run pytest tests/test_lease_update.py` (neu) grün UND Ablauf in der App: „Vertrag zum 31.12. beenden, Nachmieter ab 01.01. anlegen“.
- [x] **T05 MEA-Skala von Hand bestätigen** — Auf der Objektseite Feld „MEA-Nenner“ und Knopf für `POST /api/properties/{id}/mea-scale/confirm` (`{"denominator": n}`); der Nenner muss eine positive ganze Zahl sein.
  Dateien: `frontend/src/pages/PropertyDetailPage.tsx`, `frontend/src/api/endpoints.ts`, `backend/app/api/properties.py:71,189-220`, `backend/app/api/units.py:200-221`
  Fertig, wenn: `uv run pytest tests/test_declaration_mea_scale.py` grün UND Ablauf in der App: „WEG-Objekt anlegen, Nenner 1000, Einheiten mit 300/300/400 MEA ohne 409“.
- [x] **T06 Benutzer in der UI anlegen** — Formular (E-Mail, Name, Rolle, Startpasswort) für `POST /api/users`, nur für Admins; neue Benutzer können sich mit ihrer zugewiesenen Rolle anmelden.
  Dateien: `frontend/src/pages/UsersPage.tsx`, `frontend/src/api/endpoints.ts`, `backend/app/api/users.py`
  Fertig, wenn: `uv run pytest tests/test_rbac.py` grün UND Ablauf in der App: „Sachbearbeiter anlegen und mit ihm anmelden“.
- [x] **T07 Zahlungsentwurf bearbeiten** — Bearbeiten-Formular für Entwürfe über das vorhandene `PATCH /api/payments/{id}`; das Mietkonto berücksichtigt den korrigierten Zuordnungsmonat auch bei abweichender Wertstellung.
  Dateien: `frontend/src/pages/PaymentDetailPage.tsx`, `frontend/src/api/endpoints.ts`, `backend/app/api/payments.py:56-75`
  Fertig, wenn: `uv run pytest tests/test_m6_zahlungen.py` grün UND Ablauf in der App: „Monat im Entwurf korrigieren, buchen, Mietkonto stimmt“.
- [x] **T08 BK-Vorauszahlungen für jeden Zeitraum** — Sperre „nur volles Kalenderjahr“ aufheben: alle gebuchten `NK_VORAUSZAHLUNG` des Vertrags mit Monat im Abrechnungszeitraum, auch über den Jahreswechsel; ein abweichendes Bezugsjahr wird weiterhin mit 422 abgelehnt.
  Dateien: `backend/app/services/settlement_service.py:205-260`, `backend/app/services/payment_service.py`, `frontend/src/pages/SettlementDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m3_settlement.py -k derived` grün (neu: 01.07.–30.06. und Auszug 30.09.) UND Ablauf in der App: „Abrechnung Jan–Sep übernimmt die Zahlungen Jan–Sep“.
- [x] **T09 WEG: gezahltes Hausgeld übernehmen** — Route `POST /api/weg-settlements/{id}/paid-advances-derived` auf `derive_paid_advances`, Knopf in der Jahresabrechnung.
  Dateien: `backend/app/api/weg_settlements.py`, `backend/app/services/weg_settlement_service.py:501-540`, `frontend/src/pages/WegSettlementDetailPage.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `uv run pytest tests/test_m4_weg.py -k derive` (neu) grün UND Ablauf in der App: „12 Hausgeldzahlungen, Übernahme ergibt richtige Ist-Vorschüsse“.
- [ ] **T10 Beleg-Worker im Betrieb** — Lokale Umsetzung und Produktionsintegration getrennt bearbeiten; der Produktionsschutz bleibt unverändert.
  **Offene Restaufgabe aus Phase 1: lokaler Teil von T10.** Der aktuelle nächste Abschnitt ist T14 auf `mvp/phase-2` (siehe Phase 2); T10 wird dadurch nicht abgeschlossen. `worker-entrypoint.sh` wählt über `HV_WORKER_KIND` (`declaration_import` Default, `invoice_extraction`, `intake_extraction`). Worker lokal als Prozess ausschließlich mit Test-DB `127.0.0.1:55432` und separatem lokalen Dokumentenspeicher starten; keine neue Compose-Datei anlegen. App über `scripts/dev-local.sh` auf UI :13000 / API :18000 prüfen. Produktionsdateien und produktive Dienste dabei nicht anfassen.
  Dateien für den lokalen Teil: `docker/worker-entrypoint.sh`, `backend/app/workers/invoice_extraction.py`, `backend/tests/test_invoice_extraction_worker.py`, `scripts/dev-local.sh`, `scripts/test-env.sh` (Start-/Umgebungsmuster), `DEPLOY.md` (lokalen Start knapp dokumentieren).
  Lokaler Teil fertig, wenn: in `backend/` nach `source ../scripts/test-env.sh` `uv run pytest tests/test_invoice_extraction_worker.py` grün UND Ablauf in der App: „lokal Worker starten, synthetische PDF-Rechnung hochladen, erwarteter Betrag und Datum erscheinen“. Benötigte OCR-/Extraktionsabhängigkeiten vorab prüfen; fehlenden Zugriff oder fehlende Voraussetzungen hier als konkrete Blockade festhalten, nicht durch manuell gesetzte Extraktionsergebnisse ersetzen. Danach nur den lokalen Teil als geprüft vermerken, T10 insgesamt noch nicht abhaken.
  **Produktionsrest blockiert bis zu gesonderter ausdrücklicher Freigabe:** Dienst `invoice-worker` in der Produktions-Compose ergänzen und Konfiguration validieren. Vorher müssen Aufgabenfreigabe, verbindliche Compose-Regel und technischer Guard widerspruchsfrei geklärt sein; diese Aufgabe erlaubt weder eine Guard-Umgehung noch einen derzeit verbotenen Compose-Aufruf. Keine pauschale Guard-Abschaltung, kein Deployment und kein produktiver Neustart. Erst nach separat freigegebener und geprüfter Dienstkonfiguration sowie bestandenem lokalen Durchstich ist T10 vollständig fertig.
- [x] **T11 Menü entrümpeln** — Quellenfreigaben, Altquellen, Berechtigungen, Freigaben vorbereiten, Quarantänen und Seitenauszüge in eine Admin-Gruppe „Erweitert“.
  Dateien: `frontend/src/components/Sidebar.tsx`, `frontend/src/App.tsx` (nur lesen)
  Fertig, wenn: `npm test -- src/App.test.tsx` grün UND Ablauf in der App: „Sachbearbeiter sieht keine Governance-Einträge“.
- [x] **T12 Durchstich Mietjahr (Test)** — Vollständiger Chrome-Oberflächendurchstich auf `mvp/phase-1` mit `scripts/dev-local.sh`, UI :13000 / API :18000 und ausschließlich Test-DB :55432 erfolgreich (19.10.2026). „T12 Abnahme Mietjahr 2025“: Objekt, 75-m²-Wohnung, Testmieter, Jahresvertrag 01.01.–31.12.2025 und Bankkonto über Formulare angelegt; Jan–Nov je 500 € Kaltmiete + 100 € NK als 22 Zahlungen gebucht; Dezember unbezahlt. Grundsteuer 2.400 €, Müll 480 €, Wasser 600 € erfasst und bestätigt; Verbrauch 600 und Datengrundlage geprüft, BK berechnet und finalisiert, danach Mietkonto und Mahnung geprüft.
  Soll/Ist: Jahresmiete 7.200 €/6.600 €, genau Dezember mit 500 € + 100 € offen = **600 € Mietrückstand**. Davon getrennt: BK-Kosten 3.480 € − **1.100 € DERIVED**-Vorauszahlungen = **2.380 € Nachzahlung**. Mahnung Stufe 1 zum 15.01.2026: Hauptforderung 600 €, Gebühr 0 €, Gesamt 600 €. BK- und Mahn-PDF jeweils sofort über UI geöffnet sowie auf :18000 mit HTTP 200, `application/pdf`, `%PDF-` und PDF-Parser als gültige einseitige PDFs einschließlich Beträgen geprüft.
  Behoben nach roten Regressionen: Mietkonto hatte Rückstand/Guthaben samt Farben vertauscht; BK-PDF zeigte die exklusive DB-Vertragsgrenze 01.01.2026 statt des letzten Vertragstags 31.12.2025. Zentrale Datumskonvertierung für neue Snapshots; eingefrorene Bestandsdokumente bleiben unverändert. Journey prüft zusätzlich den vollständigen Jahreskontoauszug unabhängig vom BK-Saldo und das Vertragsende im PDF.
  Tests: nach `source ../scripts/test-env.sh` in `backend/` **66 grün** mit `uv run pytest tests/test_journey_miete.py tests/test_m3_settlement.py tests/test_m7_dunning.py`; bestehender BK-Snapshot-Namenstest zusätzlich grün. Frontend: `npm test -- src/pages/MietkontoPage.test.tsx` **3 grün**, Build und gezielter ESLint-Lauf grün. Finalisierungssperren, RLS und fachliche Schutzprüfungen unverändert.
  Dateien: `backend/tests/test_journey_miete.py`, `backend/app/services/render_names.py`, `frontend/src/pages/MietkontoPage.tsx`, `frontend/src/pages/MietkontoPage.test.tsx` (neu); bestehende Fixtures in `backend/tests/conftest.py` unverändert genutzt.
  Der anschließende WEG-Oberflächendurchstich und die Prüfung seines früheren HTTP-409 sind unter **T13** dokumentiert. **Nächster Abschnitt: T14 auf eigener Branch `mvp/phase-2`, ausgehend vom nach `main` übernommenen Phase-1-Stand.** T10 bleibt als Restaufgabe mit getrennten lokalen und produktiven Abschlussbedingungen offen; Phase 1 ist deshalb fachlich noch nicht vollständig abgeschlossen.
- [x] **T13 Durchstich WEG-Jahr (Test)** — Vollständiger Chrome-Oberflächendurchstich auf `mvp/phase-1` mit `scripts/dev-local.sh`, UI :13000 / API :18000 und ausschließlich Test-DB :55432 erfolgreich (19.10.2026). Wiederholbar mit `scripts/acceptance/t13_weg_year_browser.cjs` (vorhandenes Playwright/Chrome, `pdftotext`; Paketpfad bei Bedarf über `HV_PLAYWRIGHT_PACKAGE`). Nur synthetische Formulareingaben, keine API-/DB-Anlage als UI-Ersatz: WEG-Objekt mit bestätigtem Nenner 1.000, Einheiten 600/400 MEA, zwei Eigentümer samt Eigentumszuordnung, Wirtschaftsplan, 23 gebuchte Hausgeldzahlungen, bestätigte Kosten, T09-Übernahme, berechnete/finalisierte Jahresabrechnung, Vermögensbericht, Versammlung mit TOP/Protokoll und angenommenem Beschluss, Sammlungseintrag Nr. 1.
  Soll/Ist je Eigentümer: Ada 600/1.000 MEA, Hausgeld **3.600 €/3.600 €**, Kostenanteil **3.000 €** (1.200 Garten + 1.800 Verwaltung), Rücklage 600 €, Abrechnungsspitze **−600 €**; Bruno 400/1.000 MEA, Hausgeld **2.400 €/2.200 €**, Kostenanteil **2.000 €** (800 Garten + 1.200 Verwaltung), Rücklage 400 €, Spitze **−400 €**. Summen: Soll **6.000 €**, Ist **5.800 €**, Kosten **5.000 €**, Rücklagenzuführung **1.000 €**, Spitze **−1.000 €**. Die Summen werden in Cent aus den sichtbaren Eigentümerzellen addiert und mit Plan, Zahlungsübernahme und Objektübersicht abgeglichen. Der separate Dezember-Hausgeldrückstand beträgt **200 €**; Ist-Zahlungen verändern nicht die beschlussrelevante Spitze (Ist-Kosten minus Plan-Soll). Vermögensbericht mit ausdrücklich geprüften Verwalter-Angaben: Rücklage 0 + 1.000 − 0 = **1.000 €**, Bank 5.800 − 5.000 bezahlte Kosten = **800 €**, Forderung Bruno **200 €**, keine Verbindlichkeiten.
  WEG-PDF-409 ausdrücklich im Browser reproduziert: Finalisieren meldete `200 finalized`, sofortiges PDF dennoch `409`; nach Neuladen `200`. Neue Regressionen prüfen den PDF-Abruf bereits bei `http.response.start` und waren für Wirtschaftsplan, Jahresabrechnung und Vermögensbericht jeweils rot. Nur diese drei nachgewiesenen Finalisierungsrouten schließen nun ihre Transaktion vor der Erfolgsantwort ab (`scope="function"`); kein pauschaler Routenumbau. Zweiter UI-Fehler behoben: Versammlungsart/-status und Beschlussergebnis verwendeten falsche Kleinbuchstaben (Anlage führte zu HTTP 500, TOP-/Beschlussbedienung blieb verborgen); UI und Typen entsprechen jetzt den bestehenden API-Werten `ORDENTLICH`/`PLANNED`/`HELD`/`ANGENOMMEN` bzw. den Alternativen.
  Alle fünf angebotenen PDFs unmittelbar über die UI geöffnet und zusätzlich direkt auf :18000 mit HTTP 200, `application/pdf`, `%PDF-` und PDF-Textparser einschließlich Namen/Beträgen geprüft: Wirtschaftsplan, WEG-Jahresabrechnung, Vermögensbericht, Niederschrift und Beschluss-Sammlung. Abschließender Browserlauf ohne unerwartete API-Fehler oder Seitenfehler; anonyme Session-401 vor dem Login separat geprüft. Keine Einladungs-PDF-/Sonderumlage-Funktion ergänzt, T10 und Phase 2 unverändert.
  Tests: nach `source ../scripts/test-env.sh` in `backend/` **57 grün** mit `uv run pytest tests/test_journey_weg.py tests/test_m4_weg.py tests/test_m8_meetings.py -q`; Frontend **13 grün** mit `npm test -- src/pages/MeetingsPage.test.tsx src/pages/AssetReportDetailPage.test.tsx src/components/FinancialCoverageReview.test.tsx`, Build und gezielter ESLint-Lauf grün. Dateien: `backend/app/api/economic_plans.py`, `backend/app/api/weg_settlements.py`, `backend/tests/test_journey_weg.py`, `frontend/src/api/types.ts`, `frontend/src/pages/MeetingsPage.tsx`, `frontend/src/pages/MeetingDetailPage.tsx`, `frontend/src/pages/MeetingsPage.test.tsx` (neu), `scripts/acceptance/t13_weg_year_browser.cjs` (neu); bestehende Fixtures unverändert.

### Phase 2 – Fehlende Kernfunktionen

**Start auf eigener Branch `mvp/phase-2` vom nach `main` übernommenen Phase-1-Stand. Nächste Aufgabe: T32**; Aufgaben einzeln in Reihenfolge. T10 bleibt als offene Phase-1-Restaufgabe bestehen, sein Produktionsrest benötigt weiterhin gesonderte Freigabe.

- [x] **T14 Passwort ändern** — `POST /api/auth/password` (alt, neu; Hash wie beim Login) und Dialog im Benutzermenü.
  Dateien: `backend/app/api/auth.py`, `backend/app/core/security.py`, `frontend/src/components/Topbar.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `uv run pytest tests/test_auth_password.py` (neu) grün UND Ablauf in der App: "Passwort ändern, neu anmelden".
  Prüfung 20.10.2026: Der frühere Abschluss war vorzeitig; die damaligen curl-Prüfungen belegten keinen Browserablauf. Echter Chrome-Lauf mit `scripts/acceptance/t14_password_browser.cjs` gegen `scripts/dev-local.sh` (:13000/:18000, ausschließlich `infra-postgres-1` :55432): Bestätigungsfehler, falsches Altpasswort (401), schwaches Neupasswort (422), Wechsel im Dialog (204), automatische Abmeldung, altes Passwort abgewiesen, Neuanmeldung mit neuem Passwort und Session nach Reload; Ausgangspasswort über den Dialog wiederhergestellt. Keine Seitenfehler. Echter `ApiError` reproduzierte zuvor einen roten UI-Test (1 rot/4 grün); Fehlerauswertung korrigiert, zusätzlich Validierungs-/Netzwerkfehler getestet. Backend `uv run pytest tests/test_auth_password.py -q`: 4 grün, 2 bestehende Alembic-DeprecationWarnings. Frontend `npm test -- src/components/Topbar.test.tsx`: 7 grün; `npm run build` erfolgreich (bestehende Chunkgrößenwarnung), gezielter ESLint für Topbar, Test und drei UI-Hilfskomponenten grün; acht zusätzliche T14-Lintfehler behoben. Frühere Commits sind kein Nachweis eines damaligen Browserabschlusses.
- [x] **T15 Tabelle Bankumsätze** — Modell `bank_transaction` (Konto, Buchungs-/Valutadatum, Betrag, Gegenkonto Name/IBAN, Zweck, `import_hash` eindeutig je Konto, Status NEU/ZUGEORDNET/IGNORIERT) mit RLS; `payment.bank_transaction_id` optional.
  Dateien: `backend/app/models/bank_transaction.py` (neu), `backend/migrations/versions/0124_bank_transaction.py` (neu, Muster `0016_payment_table.py`), `backend/app/repositories/bank_transaction_repo.py` (neu), `backend/app/models/payment.py`
  Fertig, wenn: `uv run pytest tests/test_rls_coverage.py tests/test_bank_transactions.py` (neu) grün.
  Ergebnis 20.10.2026: vorgeschriebener Lauf 18 grün; zusammen mit `tests/test_m6_zahlungen.py tests/test_m6_bank_accounts.py` 46 grün (je 2 bestehende Alembic-DeprecationWarnings). Vorher rote Tests belegten akzeptierte Fremdorg-Konten, kontofremde Zahlungslinks und veränderbare Links bei gebuchten Zahlungen. Neue Folgemigration `0125_bank_transaction_coherence.py` schließt diese Lücken per Composite-FKs und zusätzlichem GoBD-Trigger; committierte 0124 unverändert. Echte Runtime-RLS-Prüfung ohne Repositoryfilter beidseitig für SELECT/UPDATE/DELETE/INSERT, exakte NEU-ID-Sollmenge statt abgeschwächter Hash-Teilmenge, Status-Constraint und signed Decimal-Beträge geprüft; keine Skips/Suppressionsmarker in den geprüften Phase-2-Testdateien. Echter Test-DB-Roundtrip 0125→0124→0125 mit unveränderten Zahlungs-/Umsatzzeilen sowie head→0123→head mit geprüfter Entfernung/Wiederherstellung von Tabelle, Zahlungsfeld, RLS und FKs erfolgreich. 0125 ist damit belegt; nachfolgende neue Migrationen benötigen die nächste freie Revision. Kein Import-/UI-Abschluss behauptet.
- [x] **T16 CSV-Kontoauszug parsen** — Reine Funktion `parse_csv(bytes)` in `engine/bank_import.py`: UTF-8/CP1252, `;`, deutsche Datums- und Dezimalformate, Spalten der Bank aus F2; unbekanntes Format gibt klare Meldung.
  Dateien: `backend/app/engine/bank_import.py` (neu), `backend/tests/fixtures/bank/` (neu, synthetisch), `backend/tests/engine/test_bank_import_csv.py` (neu)
  Fertig, wenn: `uv run pytest tests/engine/test_bank_import_csv.py` grün.
  Ergebnis 20.10.2026: Auf Nutzerwunsch allgemeines Spaltenschema plus online belegte DKB-Giro-Alt-/2023-Semikolonprofile statt Blockade durch F2; feste Quellenverknüpfung und Grenzen in README. Tatsächliche Nutzerbank bleibt unbekannt, keine freie Mapping-UI und keine pauschale Bankkompatibilität behauptet. Vor Implementierung roter Importtest; zusätzlicher fachlich roter Test zeigte fälschlich importierte DKB-Tagessaldozeile (3 statt 2), gezielt behoben. 40 synthetische CSV-Tests grün (Soll netto 1.034,55 EUR, UTF-8/BOM/CP1252, Gegenparteien, Mehrfachbuchungen, Encoding-/Spaltenunabhängigkeit, Formate/Fehler/Größenbegrenzung); kombiniert mit vorgeschriebenen T15-/RLS-Tests 58 grün, 2 bestehende Alembic-Warnungen. Kein API-/Browserabschluss für T18/T19 vorweggenommen.
- [x] **T17 CAMT.053 parsen** — `parse_camt053(bytes)` für XML und ZIP (`camt.053.001.02` und `.08`), Ausgabe wie T16.
  Dateien: `backend/app/engine/bank_import.py`, `backend/tests/engine/test_bank_import_camt.py` (neu)
  Fertig, wenn: `uv run pytest tests/engine/test_bank_import_camt.py` grün.
  Ergebnis 20.10.2026: 39 synthetische CAMT-Tests grün; gemeinsam mit T16 und Bankumsatz-/RLS-Tests 97 grün, 2 bestehende Alembic-Warnungen. Vor Implementierung roter Funktionsimport; zwei spätere fachlich rote Tests belegten ignorierte Detailrichtung und fehlende Mehrkontenprüfung über ZIP-Dateigrenzen, beide behoben. Beide Namespace-Versionen, exakte Sammelbuchungen (700 + 150 − 200,01 = 649,99 EUR), XML-/CSV-identische Ausgabe, Datumszeitwerte, ZIP, DTD-/Entitätenschutz auch bei UTF-16, Archivpfade, Größen-/Zeilengrenzen und Abbruch ohne Teilerfolg geprüft. Keine API-/UI-Übernahme behauptet.
- [x] **T18 Import-Endpoint mit Vorschau** — `POST /api/bank-accounts/{id}/imports` mit `dry_run` (Anzahl, Summe, Zeitraum, Dubletten), sonst Übernahme; gleiche Datei zweimal ergibt 0 neue.
  Dateien: `backend/app/api/bank_accounts.py`, `backend/app/services/bank_import_service.py` (neu), `backend/app/engine/bank_import.py`, `backend/app/core/uploads.py`
  Fertig, wenn: `uv run pytest tests/test_bank_import_api.py` (neu) grün.
  Ergebnis 20.10.2026: Vor Implementierung 12 rote API-/Uploadpfadtests; danach vorgeschriebener API-Lauf 18 grün, kombiniert mit CSV/CAMT, Bankumsätzen/RLS, Zahlungen/Bankkonten und Uploadlimits 161 grün (2 bestehende Alembic-Warnungen). Sichere Vorschau als Vorgabe ohne Persistenz; exakte Sollsumme 649,99 EUR, Zeitraum, Dubletten, zweite/gleichzeitige Übernahme, Org-/Rollenprüfung, CSV/XML/ZIP, Ziel-IBAN und Größenbegrenzung geprüft. Erzwungener Fehler nach erstem echten Insert belegt vollständigen Rollback; 1.001 Buchungen über Insert-/Hash-Batchgrenzen geprüft (10,01 EUR, zweite Übernahme null neue). Keine Dateiextraktion, keine gebuchten Zahlungen oder Änderung finalisierter Dokumente. Oberfläche und tatsächlicher Import-Browserablauf bleiben T19.
- [x] **T19 Import-Oberfläche und Umsatzliste** — „Kontoauszug importieren“ (Datei, Vorschau, Übernehmen) und Seite `/bankkonten/:id/umsaetze` mit Statusfilter; Vorbild `rg -n "RF11|RF12" docs/fachreferenz.md`.
  Dateien: `frontend/src/pages/BankAccountsPage.tsx`, `frontend/src/pages/BankTransactionsPage.tsx` (neu), `frontend/src/App.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `npm test -- src/pages/BankTransactionsPage.test.tsx` (neu) grün UND Ablauf in der App: „CSV importieren, Vorschau, übernehmen; zweiter Import meldet 0 neue“.
  Ergebnis 20.10.2026: Vor Implementierung rote UI-Importauflösung und 6 rote Listen-API-Tests; später echter Browserfehler am Statuslabel und zwei rote UI-Gegenproben (Label/Dateiformate), gezielt behoben. Vorgeschriebener UI-Lauf 11 grün, Bankkonten/Routing/Topbar weitere 16 grün, Backend Import/Liste/Bankumsätze/RLS 42 grün. Build und gezielter ESLint grün. Echter Chrome-Lauf `scripts/acceptance/t19_bank_import_browser.cjs` gegen `scripts/dev-local.sh`: Objekt/Konto ausschließlich per UI angelegt, ungültiges CSV mit sichtbarem 422-Fehler, Vorschau 3 neue/649,99 EUR ohne Übernahme, Dateiwechsel verwirft Freigabe, expliziter Import, drei persistierte signed Beträge nach Reload; zweiter Import 0 neue/3 Dubletten, dieselben Buchungen als CAMT-XML und ZIP ebenfalls 0 neue, Statusfilter und keine Seitenfehler. READ_ONLY/Org-Trennung/Pagination per UI-/API-Tests geprüft. Frische Nachprüfung T14: 4 Backend-/7 UI-Tests und kompletter Passwort-Browserlauf mit wiederhergestelltem Ausgangspasswort grün; T16/T17 einzeln erneut 40/39 grün, feste DKB-Formatquelle direkt abgerufen. Gesamtlint bewusst nicht grün: 6 verbliebene Ausgangsfehler in drei seit Phase-2-Start unveränderten Dateien; 3 alte `any`-Stellen in der bearbeiteten API-Datei strenger typisiert. Lokale App nach Prüfung gestoppt. T20–T42 und Gesamtprüfungen bleiben offen.
- [x] **T20 Zuordnungsvorschläge** — Reine Funktion `suggest_matches` in `engine/bank_matching.py`: Umsatz gegen offenes Soll (`tenant_expected_charges`, `owner_expected_charges`) über IBAN, Betrag, Name, Monat; Ergebnis mit Splitzeilen und Konfidenz.
  Dateien: `backend/app/engine/bank_matching.py` (neu), `backend/app/engine/mietkonto.py`, `backend/tests/engine/test_bank_matching.py` (neu)
  Fertig, wenn: `uv run pytest tests/engine/test_bank_matching.py` grün (Warmmiete, Teilzahlung, fremde IBAN, Hausgeld).
  Ergebnis 20.10.2026: Vorgeschriebener Lauf 31 grün; zusammen mit Mietkonto-Golden-/Invariantentests, Import-API, Bankumsätzen und RLS 95 grün (je 2 bestehende Alembic-Warnungen). Vor Implementierung roter Modulimport; danach sechs fachlich rote Negativfälle für Kaution/Rückbuchung, gemischte Miet-/Hausgeldzwecke, doppelte Ziele sowie direkt angehängte Referenzziffern, gezielt behoben. Reine frozen DTOs und bestehende `OpenItem`-Berechnung aus echtem Miet-/Eigentümer-Soll/Ist verwendet, keine zweite Finanzberechnung oder Buchung. Sollwerte: Warmmiete 700+150=850 EUR, Teilzahlung 600 EUR, Hausgeld 300 EUR; bereits bezahlte Komponenten, exakte Cents, fremde IBAN, Name, Monat und Mehrdeutigkeit geprüft. Unklare Teilzahlungen erhalten leere Splitbeträge und `unallocated_amount` statt erfundener Tilgungsfolge; Wertstellungsmonat ist nur prüfpflichtiger Fallback. Reine Engine-Abhängigkeiten per AST geprüft, keine Migration/DB-/API-Implementierung; Integration und erneute Validierung folgen in T21/T22.
- [x] **T21 Umsatz zuordnen (API)** — `GET /api/bank-transactions?status=NEU` mit Vorschlägen, `POST /{id}/assign` (Zeilen decken den Umsatz genau, erzeugt gebuchte Zahlungen), `POST /{id}/ignore`; Doppelzuordnung 409.
  Dateien: `backend/app/api/bank_transactions.py` (neu), `backend/app/main.py`, `backend/app/services/bank_import_service.py`, `backend/app/services/payment_service.py`
  Fertig, wenn: `uv run pytest tests/test_bank_transaction_assign.py` (neu) grün.
  Ergebnis 20.10.2026: Vor Implementierung drei rote Endpoint-Prüfungen (404); zusätzliche rote Mehr-Objekt-Eigentümer-Gegenprobe behoben. Vorgeschriebener Lauf 37 grün, eigener abschließender Scope-/Nachbarschaftslauf 132 grün (je 2 bestehende Alembic-Warnungen). Warmmiete 850=700 KALT+150 NK, tatsächliches Soll/Ist, Teilzahlung, explizite Vorperiode, finalisiertes Hausgeld-Soll, negative Umsätze als OUT, Audit/Retention und unveränderliche Zahlungslinks geprüft. RLS-/Org-/Objekt-/Profil-/Periodenprüfung, widerrufene Kreisrechte, prepare+confirm ohne approve, assign/assign- und assign/ignore-Konkurrenz sowie vollständiger Rollback nach erster tatsächlicher Buchung nachgewiesen. Tenantgesperrter Bankumsatz, vorhandener Zahlungsdienst und ambient Transaktion wiederverwendet; keine Migration oder Lockerung bestehender Schutzprüfungen. API/README/Fachseite dokumentiert; Browserintegration bleibt ausdrücklich T22.
- [x] **T22 Umsatz zuordnen (UI)** — Je Umsatz „Vorschlag übernehmen“, Dialog zum Aufteilen, „Ignorieren“.
  Dateien: `frontend/src/pages/BankTransactionsPage.tsx`, `frontend/src/api/endpoints.ts`, `frontend/src/api/types.ts`
  Fertig, wenn: `npm test -- src/pages/BankTransactionsPage.test.tsx` grün UND Ablauf in der App: „Warmmiete übernehmen, Mietkonto zeigt Kaltmiete und NK bezahlt“.
  Ergebnis 20.10.2026: Rote Funktions-/Alle-Filter-/Beschriftungsgegenproben gezielt behoben; 45 Seitentests und 20 benachbarte Tests, Build und gezielter Lint grün. API-All-Filter und Zuordnung 38 Tests, Kreisrechte weitere 4 grün; erneute getrennte Parserläufe CSV 40/CAMT 39 grün. Vollständiger echter Chrome-Lauf `scripts/acceptance/t22_bank_assignment_browser.cjs` gegen `scripts/dev-local.sh`: Objekt, Einheit, Person/Mieter, Vertrag 700/150 und Konto ausschließlich per UI; CSV 850+600+25=1.475 EUR, Warmmiete exakt 700/150 gebucht und Januar-Mietkonto nach Reload offen 0, stale Zweittab sichtbar 409 ohne Doppelzahlung, ungültige Splitbeträge ohne POST, explizit 500/100 im Februar mit 250 Rest gebucht, 25 ignoriert ohne Zahlung. Reload: Zugeordnet 2/Ignoriert 1/Alle 3/Neu 0; Jahres-Soll 10.200/Ist 1.450/Saldo 8.750 EUR. Bestehende T19-Prüfungen erhalten; exakte Geldarithmetik und Fehlereingaben geprüft, im Browser gefundene Select-Beschriftungen korrigiert. App anschließend gestoppt; T23 noch nicht begonnen.
- [x] **T23 Mietänderung mit Stichtag (Backend)** — Tabelle `lease_rent_change` (gültig ab, Kaltmiete, Vorauszahlung) mit RLS, `GET/POST /api/leases/{id}/rent-changes`; `tenant_expected_charges` nimmt je Tag den gültigen Betrag.
  Dateien: `backend/app/models/lease_rent_change.py` (neu), `backend/migrations/versions/0126_lease_rent_change.py` (neu; 0125 bereits belegt), `backend/app/api/leases.py`, `backend/app/engine/mietkonto.py:203-256`
  Fertig, wenn: `uv run pytest tests/engine/test_golden_mietkonto.py tests/test_rent_changes.py` (neu: Erhöhung zum 15.03.) grün.
  Ergebnis 20.10.2026: Engine zunächst fachlich rot, API dreimal 404 rot; zusätzliche echte Finanzsperrgegenprobe rot, vorhandene Sperre vor Vertragslock integriert. Root-Abschlusslauf 82 grün (44 Golden/38 API), Invarianten/RLS/Zuordnung/Scope/M6 weitere 86 grün, je 2 bestehende Alembic-Warnungen. März 700→750 ab 15.03. ergibt 727,42 KALT/150 NK, April 750; mehrere Änderungen, Fortgeltung vor Abfragebeginn, Cent-Fragmente, echte Bankvorschläge/-buchungen, Org-/Parent-RLS, Prepare-Rechte, Parallel-Duplikat 409 und unveränderte Originalverträge/Buchungen/finale Snapshots geprüft. Neue reversible 0126; echte Test-DB-Rück-/Aufmigration 0126→0125→0126 erhält exakt alle 135 bisherigen Tabellen (94 Verträge/120 Zahlungen), stellt FORCE RLS, Composite-FK, Append-only-Trigger und SELECT/INSERT-only Runtime-Rechte wieder her; committierte 0124/0125 unverändert. Ein unveränderter History-Nebentest zeigte zwischenzeitlich einen Polling-Timingfehler, danach ohne Abschwächung isoliert und im identischen Agenten-Regressionslauf grün; Gesamtprüfung bleibt ausstehend. Oberfläche folgt T24.
- [x] **T24 Mietänderung (UI)** — Liste und Formular „Miete ändern ab …“ in der Einheit.
  Dateien: `frontend/src/pages/UnitDetailPage.tsx`, `frontend/src/api/endpoints.ts`, `frontend/src/api/types.ts`
  Fertig, wenn: `npm test -- src/pages/UnitDetailPage.test.tsx` (neu) grün UND Ablauf in der App: „+50 € ab 01.04., Mietkonto zeigt ab April das neue Soll“.
  Ergebnis 20.10.2026: Zuerst 26 rote UI-Fälle bei erhaltenem Alt-Test; weitere rote Maximalwert-Gegenprobe belegte zu enge Betragsgrenze, an Numeric(14,2) angeglichen (zwölf Ganzzahlstellen, dreizehn abgewiesen). Root: 35 Seitentests/53 Nachbartests, Build und gezielter Lint grün. Echter Chrome-Lauf `scripts/acceptance/t24_rent_change_browser.cjs` gegen `scripts/dev-local.sh`: Objekt/Einheit/Person/Mieter/Vertrag ausschließlich per UI, Original 700/150, genau eine persistierte Änderung ab 01.04.2026 auf 750/150; Originaldaten nach Reload unverändert, kein PATCH/PUT/DELETE oder Zahlungs-POST. Alle 24 Mietkonto-Monats-/Typzeilen sichtbar und in beobachteter Response exakt geprüft: Januar–März 850, April–Dezember 900, Soll/Saldo 10.650, Ist 0 EUR. Veraltete Reload-Response im Prüfsystem gezielt ausgeschlossen; zwei erwartete anonyme Session-401 separat nach URL/Phase belegt, keine authentifizierten 401 oder unerwarteten HTTP-/Seiten-/Consolefehler. Rollen, Vertragstrennung, Fehler/Retry, Datum/Geld und Pending-Sperre geprüft; App anschließend gestoppt.
- [x] **T25 Kaution** — Kaution als eigenes Soll außerhalb des Mietsaldos, Zahlung/Rückzahlung Typ `KAUTION`, Seite `/kautionen` (Soll, gezahlt, zurück).
  Dateien: `backend/app/engine/mietkonto.py`, `backend/app/api/payments.py`, `frontend/src/pages/DepositsPage.tsx` (neu), `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`
  Fertig, wenn: `uv run pytest tests/test_deposits.py` (neu) grün UND Ablauf in der App: „1.800 € in zwei Raten, Übersicht vollständig, Mietkonto unverändert“.
  Ergebnis 20.10.2026: Zuerst 2 rote API-/Mietsaldo-Gegenproben (alter UUID-Catchall: 422), weitere 6 Kohärenz- und eine Legacy-Stornogegenprobe; 9 zusätzliche Labeltests rot vor sieben gezielten Select-Namensfixes. Root: 31 Pflicht-/175 Nachbar-Backendtests grün (je 2 bestehende Alembic-Warnungen), 117 Frontendtests, Build/gezielter Lint grün; Backend-Agent zusätzlich 224 Regressionen grün. Reine getrennte Decimal-Bestandsführung; aktive gebuchte KAUTION, keine Drafts/Stornos, vollständiger MIET-Scope mit Rollen-/Org-/Objekt-/Quellenprüfung und Widerruf, keine Jahres-/Kontotypbeschneidung. Fremde/inkohärente Neuzuordnung abgewiesen; Altzuordnung 409 statt falschem Nullbericht, bestehende Stornokorrektur erhalten. Snapshot-/Dokumentmetadaten unverändert; keine Migration. Echter Chrome-PASS `scripts/acceptance/t25_deposits_browser.cjs`: Stammdaten, MIET-Kreis, explizite Teamrechte und KAUTION-Konto per UI; 1.800 Soll, zwei separat erfasste/gebuchte 900-IN, echte 300-OUT, nach Reload 1.800 gezahlt/300 zurück/1.500 Bestand. Alle 24 KALT-/NK-Posten vorher/nachher unverändert (Soll/Saldo 10.200, Ist 0); fünf exakt belegte fehlende-Kreis-409 vor Auswahl und zwei anonyme Session-Probes separat erfasst, keine unerwarteten Fehler im Objekt-/Kautionsablauf. App gestoppt; separater Dashboardbefund bei T42 vermerkt.
- [x] **T26 Eigentümerwechsel** — `PATCH /api/ownership/{id}` setzt das Ende; in der Einheit „Eigentum beenden“ und neuer Eigentümer ab Folgetag.
  Dateien: `backend/app/api/ownership.py`, `backend/app/repositories/ownership_history_repo.py`, `frontend/src/pages/UnitDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_ownership_change.py` (neu, Abrechnung tagegenau) grün UND Ablauf in der App: „Verkauf zum 30.06., Jahresabrechnung zeigt beide anteilig“.
  Ergebnis 20.10.2026: Rote PATCH-/Jahreseigentümer-, Label- und Belegkosten-Enum-Gegenproben vor den jeweiligen Korrekturen; Root-Pflichtlauf 28 grün, Nachbarauswahl zuletzt 135 ungefiltert grün, Root-Frontend 140 grün, Build und gezielter Lint grün (bestehende Alembic-/Chunkwarnungen). Historische 0067-Migrationsprobe an die neue 0126-Abhängigkeit angepasst: echte Down-/Up-Funktionen im Rollback-Savepoint, bestehende Schutzassertions erhalten, nichtleere Miethistorie/Policies/RLS/Rechte nach Rollback unverändert; keine committierte Migration geändert. Tenant-/Source-/Kreisrechte, READ-only-Read, Widerruf, partielle Kreise, Zeilen-/Finanzsperre, identische Konkurrenz und unveränderte gebuchte/finalisierte Daten geprüft. Neuer autorisierter Input-Owner-Read verhindert, dass ein Verkäufer-only-Plan den Käufer aus der Zahlungsvollständigkeit entfernt; keine automatische Nullannahme. Vollständiger echter Chrome-PASS `scripts/acceptance/t26_ownership_change_browser.cjs`: Stammdaten, Selbstfreigabe, Verkauf 30.06./Käufer 01.07., Nullplan danach und echte bestätigte Belegkosten 365 EUR ausschließlich per UI; Verkäufer 181 Tage/181 EUR, Käufer 184 Tage/184 EUR, Berechnung/Finalisierung/Hash nach Reload unverändert. PDF über UI im echten Chrome-Viewer geöffnet; Rohdatei derselben URL zusätzlich im Speicher geparst, beide Namen/Anteile nachgewiesen. Keine unerwarteten HTTP-/Seiten-/Consolefehler; fehlendes klassisches Favicon durch gültige ICO-Variante des vorhandenen SVG behoben und im Build nachgewiesen. App anschließend gestoppt. Frühere intermittierende Privacy-Race-Aussetzer bleiben bei T42 vermerkt, nicht als belegter Ausgangsfehler eingeordnet.
- [x] **T27 BK-Sammellauf je Objekt** — `POST /api/settlements/batch` (Objekt, Zeitraum) legt Entwürfe für alle Verträge an, vorhandene übersprungen; Knopf auf der Abrechnungsliste.
  Dateien: `backend/app/api/settlements.py`, `backend/app/services/settlement_service.py:140-200`, `frontend/src/pages/SettlementsPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m3_settlement.py -k batch` (neu) grün UND Ablauf in der App: „drei Verträge, ein Klick, drei Entwürfe, kein Duplikat“.
  Ergebnis 20.10.2026: Fehlende Route zunächst rot (405); Root-Pflichtlauf 21 Batchfälle grün, Agent-Nachbarn 181 und Engine 45 grün. Root-Frontend 27 grün, Agent-Nachbarn 166 grün; Build, gezielter Lint und Diffcheck grün (bestehende Alembic-/Chunkwarnungen). Zeitgrenzen, Org/Rollen, partielle Kreise, Quellen-/Grant-Widerruf, unveränderte bestehende/finalisierte Läufe, parallele Batches und vollständiger Rollback einschließlich Audit geprüft; keine Migration geändert. Echter Chrome-PASS `scripts/acceptance/t27_settlement_batch_browser.cjs`: drei Verträge und explizite Lese-/Prepare-Freigabe nur per UI, ein Sammellauf erstellt drei unberechnete Entwürfe mit unbekannten Finanzwerten, identische Wiederholung überspringt dieselben drei IDs, Reload weiterhin exakt drei; keine Einzelanlage, Berechnung, Zahlung oder Finalisierung, keine unerwarteten HTTP-/Seiten-/Consolefehler. App danach gestoppt.
- [x] **T28 Neue Vorauszahlung vorschlagen** — Nach Finalisieren Vorschlag Mieteranteil/Monate; „Übernehmen ab …“ legt eine Mietänderung (T23) an.
  Dateien: `backend/app/services/settlement_service.py`, `backend/app/api/settlements.py`, `frontend/src/pages/SettlementDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/test_m3_settlement.py -k proposal` (neu) grün UND Ablauf in der App: „Vorschlag übernehmen, Mietkonto zeigt neue Vorauszahlung“.
  Ergebnis 20.10.2026: Fehlende Route zunächst rot; separate Response-start-Gegenprobe wies die zu späte Persistenz vor HTTP 201 nach und ist nach Funktions-Transaktionsgrenze grün. Root-Pflichtlauf 29 Vorschlagsfälle plus 55 T23-/0069-Probenfälle grün; Agent M3/Engine 366 und Source-/Finanz-/History-/RLS-Nachbarn 176 grün. Die 0069-Probe führt wegen T23 echte 0126-Down/Up-Funktionen im Rollback-Savepoint aus, mit unveränderten Schutzasserts und zusätzlicher Wiederherstellungsprüfung; keine committierte Migration geändert. Exakte eingefrorene Kalenderquoten, Halbcent-/Teilmonatsgrenzen, wirksame Kaltmiete, Rollen/Widerruf, doppelte/parallele Übernahme, Audit-Rollback und unveränderte Finanz-/Vertragsdaten geprüft. Root-Frontend zuletzt 188, Agent-Auswahl 212 grün, Build/gezielter Lint grün; vier alte Export-Lintfehler durch unveränderte Helper in reinem Modul behoben, keine Unterdrückung. Vollständiger Chrome-PASS `scripts/acceptance/t28_advance_proposal_browser.cjs`: Stammdaten, Beleg, bestätigte Jahreskosten 1.200 EUR, explizites Ist null und Finalisierung nur per UI; genau eine Übernahme 700/100 ab 2027, Original 700/150 und vollständiger finalisierter Snapshot nach Reload unverändert, alle 24 Mietkontoposten/Jahres-Soll 10.200 → 9.600 EUR nachgewiesen. Keine unerwarteten HTTP-/Seiten-/Consolefehler, App danach gestoppt. Früherer intermittierender History-Race bleibt bei T42 vermerkt, nicht als Phase-1-Ausgangsfehler bezeichnet.
- [x] **T29 Zähler-API** — `GET/POST/PATCH /api/meters` und `GET/POST /api/meters/{id}/readings` auf die vorhandenen Tabellen.
  Dateien: `backend/app/api/meters.py` (neu), `backend/app/repositories/meter_repo.py` (neu), `backend/app/models/meter.py`, `backend/app/main.py`
  Fertig, wenn: `uv run pytest tests/test_meters_api.py` (neu, mit Org-Trennung) grün.
  Ergebnis 20.10.2026: Fehlende Routen zunächst drei Fälle rot; erweiterter Rotlauf 12 Fehler/19 bestanden, zusätzliche Source-Gegenproben zweimal rot und separate Response-start-Probe vor Commit rot. Nach Korrektur Root 34 Fälle grün; feste Qwen-Schlussprüfung Pflichtdatei 32 und Source-/M1-/Master-/RLS-Nachbarn 57 grün, Diffcheck sauber, nur bestehende Alembic-Warnungen. Org-/Objekt-/Einheitsgrenzen, READ_ONLY/CLERK, exakte vierstellige Werte und Überläufe, unveränderlicher Verlauf, Fremdfelder/Null-PATCH, Read-versus-Prepare, Widerruf und PAUSED sowie Persistenz vor 201 geprüft. Bestehende Tabellen/Migrationen unverändert; keine GUI- oder Verbrauchsfunktion vorweggenommen. Die früheren Agent-Timeouts waren keine erfolgreichen Abschlüsse.
- [x] **T30 Zähler-UI** — Abschnitt „Zähler“ in Objekt und Einheit: anlegen, Stand erfassen, Verlauf; Vorbild `rg -n "RF13" docs/fachreferenz.md`.
  Dateien: `frontend/src/components/MeterPanel.tsx` (neu), `frontend/src/pages/UnitDetailPage.tsx`, `frontend/src/pages/PropertyDetailPage.tsx`, `frontend/src/api/endpoints.ts`
  Fertig, wenn: `npm test -- src/components/MeterPanel.test.tsx` (neu) grün UND Ablauf in der App: „Kaltwasserzähler mit Ständen 01.01./31.12., Verbrauch sichtbar“.
  Ergebnis 20.10.2026: Acht Seitenbindungs-/Rollenfälle zunächst rot und fehlender Panelimport rot; Root-Pflichtlauf 22 Panelfälle plus 128 API-/Seiten-/Historiennachbarn grün, Build und gezielter Lint aller neun geänderten Frontenddateien sowie Diffcheck grün (nur bekannte Chunkwarnung). Maximalwert, vierstellige Kleinst-Differenz ohne Float, Rückgang/mehrdeutige Tage, readonly, exakte feste Zuordnung, sichere Payloads, Fehler/Retry, Doppelklick und verspätete Antworten geprüft; alte Historientests erhalten. Vollständiger Chrome-PASS `scripts/acceptance/t30_meters_browser.cjs`: ausschließlich UI-Objekt-/Einheitsanlage, unzulässige fünfstellige Ablesung sichtbar abgewiesen ohne POST und mit erhaltener Eingabe; Objektzähler 100,0000 → 250,1250 und Einheitszähler 0,0000 → 12,5000, vier echte Stände nach Reload unverändert, sichtbare Differenzen 150,1250/12,5000 und getrennte Einheitsliste. Keine Finanz-/Vertragsmutationen oder unerwarteten HTTP-/Console-/Seitenfehler. App danach absichtlich gestoppt. Kein Abrechnungsverbrauch aus T31 vorweggenommen; frühere Agent-Lese-/max_tokens-Läufe waren keine Umsetzung oder grünen Prüfnachweise.
- [x] **T31 Verbrauch aus Zählerständen** — Bei `VERBRAUCH` ohne Handwert Verbrauch je Einheit aus Ständen ±31 Tage um Periodenbeginn/-ende vorbelegen, sonst Handeingabe verlangen.
  Dateien: `backend/app/engine/meter_consumption.py` (neu), `backend/app/services/settlement_service.py`, `backend/app/repositories/meter_repo.py`, `frontend/src/pages/SettlementDetailPage.tsx`
  Fertig, wenn: `uv run pytest tests/engine/test_meter_consumption.py tests/test_m3_settlement.py` grün UND Ablauf in der App: „Wasserkosten nach Zählern abgerechnet“.
  Ergebnis 21.10.2026: Fehlende Engine/Vorbelegungsroute zunächst rot; drei fachliche Integrationsfälle (Automatik, Handwert 0, fehlende Einheit) und zwei Kreis-/Klassifikationsgegenproben rot, anschließend Root-Pflichtlauf 114 und 67 Meter-/Source-/RLS-Nachbarn grün. Eindeutige ±31-Tage-Grenzstände, Ambiguität/Rückgang/Mehrzähler, genaue Kategoriezuordnung, vollständiger Nenner, manuelle 0, vierstellige Decimalwerte und eingefrorene tatsächliche Messbasis geprüft; keine Migration geändert. Browsergefundene Kosteneditor-Vokabular-/Selectlabel- und Ergebnislabeldefekte mit roten Tests korrigiert. Root 195 Frontendtests, Build, gezielter Lint und Diffcheck grün (bestehende Alembic-/Chunkwarnungen; kein Gesamtlintabschluss). Vollständiger Chrome-PASS `scripts/acceptance/t31_meter_consumption_browser.cjs`: ausschließlich UI-Anlage zweier Verträge, zweier Zähler und vier Stände; echte Vorschläge 30/70, 100 EUR Wasser ergeben in zwei realen Jahresabrechnungen 30/70 EUR, ohne automatische Handwerte zurückzusenden. Vier explizite Datengrundlagenprüfungen, Reload und beide Finalisierungen, unveränderte Snapshots ohne neue Live-Vorschläge sowie unveränderte Verträge/Historien; keine unerwarteten HTTP-/Console-/Seitenfehler. App danach absichtlich gestoppt.
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
  Bei T25-Vorlauf beobachtet: Nach gebundener Kontoanlage liefert der Dashboard-Abruf `/api/fristen?reference_date=2026-10-20` ohne Kreis 409; bei der Dashboardintegration und vor Gesamtabschluss mitprüfen, Herkunft bisher nicht als Ausgangsfehler/Regression belegt.
  Vor Gesamtabschluss erneut prüfen: `test_waiting_history_insert_cannot_activate_an_anonymized_person` scheiterte in mehreren T23-/T26-Nachbarläufen intermittierend an der Financial-Inputs-Pollingassertion und bestand unverändert in ungefilterten Wiederholungen; keine belegte Phase-1-Baseline, weder Schutztest noch Guard abschwächen.

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
- F2 Welche Bank(en) und welches Format (CSV-Profil oder CAMT.053) sollen importiert werden? Für Phase 2 nach Nutzerklärung festgelegt: allgemeines deutsches Semikolon-CSV plus belegte DKB-Giro-Alt-/2023-Semikolonprofile (T16, feste Quelle/Grenzen im README), CAMT.053 .001.02/.08 als XML/ZIP (T17), keine freie Mapping-UI. Tatsächliche Nutzerbank und weitere Exportvarianten bleiben offen; keine pauschale Kompatibilität zugesichert, unbekannte Formate werden ausdrücklich abgewiesen.
- F3 Heizkosten über Messdienst (dann reicht „Externe Abrechnung“) oder eigene Verteilung nach HeizkostenV (T45/T46 vorziehen)?
- F4 Eine NK-Vorauszahlung inklusive Heizung oder getrennte BK- und HK-Vorauszahlung im Vertrag?
- F5 BK-Abrechnungszeitraum Kalenderjahr oder abweichend je Objekt?
- F6 Mahnstufen und Gebühren 0/5/10 € (heute vorläufig) beibehalten, Verzugszinsen ja oder nein?
- F7 Kaution in Raten (§ 551 BGB) und Zinsgutschrift des Kautionskontos abbilden?
- F8 Reicht die Jahresübersicht (T38) oder braucht der Steuerberater DATEV, mit SKR03 oder SKR04?
- F9 Gewerbeeinheiten mit Umsatzsteueroption (§ 9 UStG), also USt-Ausweis in der Abrechnung?
- F10 Bleibt der Quellen-Zugriffsschutz dauerhaft im Modus PREPARATION, und ist ein Mieterportal ausgeschlossen? Dann kann die Governance-Oberfläche weiter zurückgebaut werden.
