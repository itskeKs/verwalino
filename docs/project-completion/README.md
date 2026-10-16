# Project completion — Einstieg mit 64k / 65536 Kontextziel

Diese Ausführungskette ersetzt das Laden des großen Gesamtplans. Anforderungen
bleiben aus dem Oktoberplan übernommen; kein Feature wird durch die Aufteilung
als erledigt oder freigegeben behandelt. Packages 1–24 entsprechen der Arbeitsliste.

## Aktiver Auftrag: begrenzte Dokumentreparatur

**04a lokal abgeschlossen; 04b nicht begonnen; Produktionsgate 0122;
lokaler Testhead 0123.** Maßgeblich ist derzeit der
[Reparatur-State](review-remediation/state.md), anschließend dessen Vertrag und
Teilplan 09. Die folgenden allgemeinen Produktanweisungen autorisieren keine
Fortsetzung der Roadmap ab 04b. Keine Portal-/Household-/Former-Resident- oder
sonstige Produktcode-/Testimplementierung in diesem Auftrag.

Umsetzungsvorgabe: `mlx/qwen3.6-35b-a3b`; Goal-Checker: `openai/gpt-6.1-sol`,
`reasoning: medium`. Kontextziel 64k / 65536, tatsächliches Backendfenster nicht
nachgewiesen. Historische Evidence bleibt unverändert. Goal nur formulieren,
nicht starten; V01, HTTP 401, Zwei-Reviewer-Quorum und unvollständiger Backend-
Gesamtlauf bleiben offen. Nach der Reparatur pausieren, kein automatischer Nachfolger.

## Startauftrag (allgemeiner Produktablauf, derzeit nicht freigegeben)

> Lies `docs/project-completion/state.md`, `docs/project-completion/contract.md`
> und ausschließlich den dort unter `current` genannten Teilplan. Prüfe den
> letzten Receipt nur gezielt. Implementiere den begrenzten Auftrag, teste ihn,
> speichere Receipt und State und rufe nach einem Kontextwechsel den nächsten
> Teilplan auf. Lade weder den Gesamtplan noch alle Teilpläne auf einmal.

## Wiederaufnahme für mlx/qwen3.6-35b-a3b / 64k
1. `state.md` lesen: `current`, `execution`, `last_receipt`, nächste konkrete Aktion.
2. `contract.md` lesen, insbesondere Anti-Schleifen-Protokoll. Dann nur `current`.
3. Den letzten Receipt gezielt auf Tests, verworfene Ansätze und Blocker prüfen;
   Workspace/Taskstatus verifizieren. Kein erneuter Gesamtlauf ohne konkreten Anlass.
4. Eine Hypothese und einen Regressionstest bearbeiten. Bei langen Tests andere
   begrenzte Arbeit erledigen; Task-ID/Log nachverfolgen, nicht doppelt starten.
5. Receipt/State schreiben und State erneut lesen. Frische Sitzung/Compaction vor
   dem nächsten Teilplan. Ein Link allein startet keine neue Ausführung.

Die vollständige Dokumentation umfasst deutlich mehr als einen 64k-Kontext.
„Doku prüfen“ bedeutet zunächst strukturelle Prüfung aller Verweise/Zeiger;
inhaltliche Änderungen nur an Einstieg, aktuellem Auftrag und direkt betroffenen
Verträgen. Nie pauschal alle historischen Anforderungen als qualifiziert melden.

## Funktionsweise
- Kleine Pläne in `steps/`, jeweils mit Aufgaben, Einstiegspfaden, Originalvertrag,
  Checks und explizitem Next-Auftrag. Große Packages in mehrere Stufen zerlegt.
- `state.md` ist der kleine Laufzeiger, `receipts/` die diskbasierte Evidenz.
- Nach jedem Plan Kontext komprimieren oder **frische Sitzung** mit dem Startauftrag
  beginnen. Links allein leeren keinen Kontext und führen nichts automatisch aus.
- Open local blocker: aktuellen Plan halten. Open release gate: lokal belegten
  unabhängigen Nachfolger bearbeiten, Gate offen weiterführen, nie Release behaupten.
- Fortsetzen immer bei `state.md::current`, niemals automatisch wieder bei 00-start.
  00-start war der historische Einstieg; A1–A4/B1 nicht blind wiederholen.
- `index.md` nur bei Navigation/Dependencyfragen öffnen. Normative Quelle ist
  `../2026-10-01-property-management-implementation-plan.md`; nur fehlenden Abschnitt
  nachschlagen. Historische Progressblöcke wurden absichtlich nicht vervielfältigt.

## Grenzen
Die Kette selbst ist keine Umsetzung/Qualifikation von B2–G2. Tatsächliche Arbeit
und Nachweise stehen ausschließlich in State und Receipts; isolierte Tests sind
keine reale Scanner-/Runtimefreigabe. Die verfügbare Kontextgröße enthält
System-/Toolanweisungen ebenfalls; deshalb
konservatives Budget in contract.md. Bei zu großen Dateien Auftrag weiter teilen.
Eine Kette kann ohne Agent-Compaction/Sessionneustart keinen Kontextreset erzwingen.

## Lokaler Disposable-Test-Endpunkt
Für das lokale Composeprojekt `hausverwaltung-declaration-test` die drei
Test-URLs (`HV_DATABASE_URL`, `HV_DATABASE_URL_MIGRATOR`, `HV_DATABASE_URL_SYNC`)
explizit mit `127.0.0.1:55432` verwenden: Der Port ist nur auf IPv4 veröffentlicht.
`localhost` kann auf macOS bei einem ephemeren IPv6-Self-Connect den eigenen
SSLRequest zurückliefern ([belegte Diagnose](evidence/04a/ipv6-self-connect-proof.json)).
Der vorhandene Disposable-Guard erlaubt IPv4 bereits. TLS-Optionen, Rollen,
Produktionskonfiguration und GitLab-CI-Profil bleiben unverändert.
