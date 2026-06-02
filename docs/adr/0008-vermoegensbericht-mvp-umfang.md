# ADR-0008: Vermögensbericht (§ 28 IV WEG) — MVP-Umfang bei aktuellem Datenstand

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — fachliche Abnahme erforderlich; noch kein Code/Commit)
Datum: 2026-06-02 · Kontext: M4 · Bezug: [../m4-weg-abrechnung.md](../m4-weg-abrechnung.md) §6 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.3, §11, §17, §18 · [../architecture.md](../architecture.md) §5.3 (`bank_account`-Anker) · [../roadmap.md](../roadmap.md) M4/M6/M7
Autor: `engine-correctness` (Opus 4.8)

> Diese ADR grenzt den **MVP-Umfang des Vermögensberichts** (§ 28 IV WEG) gegen den **aktuellen Datenstand** (M0–M3) ab: was jetzt sauber automatisch geht, was manuell erfasst wird, was bewusst auf M6/M7 verschoben bleibt. Sie trifft **keine** eigenmächtige fachliche Festlegung — der MVP-Umfang ist **[§18/fachlich]** und vor produktivem Einsatz durch Steuerberater/Fachanwalt für WEG-Recht abzunehmen. Sie macht die Abgrenzung **transparent**, statt eine Vollständigkeit vorzutäuschen, die die Daten nicht hergeben.

## Kontext

§ 28 Abs. 4 WEG verpflichtet den Verwalter, **nach Ablauf des Kalenderjahres** einen **Vermögensbericht** zu erstellen, der enthält:
1. den **Stand der Erhaltungsrücklage**, und
2. eine **Aufstellung des wesentlichen Gemeinschaftsvermögens** (im Wesentlichen Bankkonten-Stände, Forderungen und Verbindlichkeiten der Gemeinschaft).

SDD §11: „Der Vermögensbericht enthält **mindestens** den Stand der Erhaltungsrücklage sowie eine Aufstellung des wesentlichen Gemeinschaftsvermögens (Forderungen/Verbindlichkeiten der Gemeinschaft)." SDD §18 nennt die exakte PDF-Struktur des Vermögensberichts ausdrücklich als **offenen Punkt**.

Der aktuelle Datenstand begrenzt, was **automatisch** ermittelbar ist:
- **Erhaltungsrücklage:** Zuführungen sind als `cost_entry` der Kategorie `ERHALTUNGSRUECKLAGE` (`reserve_relevant=True`, geseedet) erfassbar; die Entwicklung (Anfang + Zuführung − Entnahme = Ende) ist in M4 ableitbar (m4 §3.3).
- **`bank_account`:** existiert als **M1-Modell-Anker** (Modell-Kommentar: „model anchor: schema + FK only; full implementation in M6") — **ohne** Salden-/Buchungsspalten. **Kein Kontoauszugsimport** (SDD §3 Nicht-Ziel: „Kein Bankimport (CAMT/MT940) und kein automatischer Zahlungsabgleich").
- **Forderungen/Verbindlichkeiten:** **nicht modelliert** — offene Posten (`OpenItem`) und Zahlungsstatus sind M6/M7; eine `cost_entry` ist eine Kostenposition, kein Zahlungsstatus.

## Optionen

| Bestandteil | (A) jetzt automatisch | (B) manuell im Bericht-Snapshot | (C) bewusst auf M6/M7, Platzhalter |
| --- | --- | --- | --- |
| **Stand Erhaltungsrücklage** | **möglich** (Rücklagen-Entwicklung, m4 §3.3) | (Anfangsbestand/Entnahmen mangels Buchung manuell) | — |
| **Bankkonten-Stände** | nicht möglich (keine Saldenspalte, kein Import) | manueller Stichtagssaldo je Konto | denkbar |
| **Forderungen** | nicht möglich (`OpenItem` = M6/M7) | manuelle Position | denkbar |
| **Verbindlichkeiten** | nicht möglich (Zahlungsstatus = M6) | manuelle Position | denkbar |

Eine **Saldenspalte/Bewegungstabelle an `bank_account`** (statt manuellem Stichtagssaldo) wäre ein Vorgriff auf die M6-Buchungslogik (Zahlungen/Mietkonto) und ist **verfrüht** — sie würde das in M6 ohnehin zu bauende Modell präjudizieren.

## Entscheidung

**MVP-Umfang (zur fachlichen Bestätigung, nicht eigenmächtig final):**

1. **Stand der Erhaltungsrücklage: (A) automatisch** aus der Rücklagen-Entwicklung (m4 §3.3): `Endbestand = Anfangsbestand + Σ Zuführungen − Σ Entnahmen`. Anfangsbestand und Entnahmen werden in M4 **manuell** erfasst (es gibt noch keine Buchungslogik; **[§18/fachlich F4-4/F4-5]**), die Zuführungen kommen aus den `ERHALTUNGSRUECKLAGE`-`cost_entry`s. Die Engine prüft die Entwicklungs-Invariante (fail-loud).

2. **Bankkonten-Stände: (B) manueller Stichtagssaldo im Bericht-Snapshot** (Empfehlung). Der Sachbearbeiter erfasst je `bank_account` einen Stichtagssaldo (zum `period_end`), der in den `snapshot_payload` eingefroren und als `BANK_BALANCE`-Zeile ausgewiesen wird. **Kein** Schema-Anbau an `bank_account` (das gehört zu M6). **[§18/fachlich F4-8]**

3. **Forderungen/Verbindlichkeiten: (B) manuelle Erfassung mit Herkunfts-Hinweis** (Empfehlung für den MVP) als `RECEIVABLE`/`PAYABLE`-Zeilen im Bericht-Snapshot — **mit deutlichem PDF-Hinweis**, dass dies **Verwalter-Angaben** sind und **kein** automatischer Saldo aus Buchungen. Alternative (C) — bewusst auf M6/M7 verschieben mit klar markiertem Platzhalter — ist fachlich ebenso vertretbar. **Diese Wahl ist [§18/fachlich] und ausdrücklich der fachlichen Entscheidung vorbehalten.**

4. **Datenmodell:** Vermögensbericht als `settlement_run`-`run_type='VERMOEGENSBERICHT'` (objektweit), Bestands-`settlement_line`s (`RESERVE_OPENING`/`RESERVE_CONTRIBUTION`/`RESERVE_WITHDRAWAL`/`RESERVE_CLOSING`/`BANK_BALANCE`/`RECEIVABLE`/`PAYABLE` — additive `line_type`-CHECK-Erweiterung, m4 §3.2/§6.5); vollständige eingefrorene Aufstellung im `snapshot_payload`. Keine Soll-Ist-Logik.

**Begründung.** Der MVP soll § 28 IV **formal bedienbar** machen, **ohne Korrektheit vorzutäuschen, die die Daten nicht hergeben**. Der Rücklagenstand ist echt ableitbar und wird automatisch geliefert. Bankkonten-Stände und Forderungen/Verbindlichkeiten sind beim aktuellen Stand **Verwalter-Angaben** — sie als manuelle, klar gekennzeichnete Stichtagswerte im eingefrorenen Bericht zu führen ist transparenter und ehrlicher als entweder (i) ein Pflichtfeld leer zu lassen oder (ii) einen Automatismus aus unvollständigen Daten zu fingieren. Der manuelle Stichtagssaldo greift die M6-Buchungslogik **nicht** vor (kein `bank_account`-Schemaumbau).

## Konsequenzen

**Positiv:**
- § 28 IV ist im MVP **bedienbar**; der Rücklagenstand ist nachrechenbar (Entwicklungs-Invariante).
- **Ehrliche Abgrenzung:** Manuelle Werte sind als solche gekennzeichnet; keine vorgetäuschte Buchungsherleitung.
- **Kein M6-Vorgriff:** `bank_account` bleibt der reine M1-Anker; echte Salden/Bewegungen kommen sauber mit M6.
- Der Bericht ist ein **eingefrorener Stichtagsbeleg** (Snapshot + Hash), GoBD-konform wie die Abrechnungen.

**Negativ / zu beachten:**
- Manuell erfasste Bankkonten-Stände und Forderungen/Verbindlichkeiten sind **nicht** automatisch gegen Buchungen plausibilisiert — ein Eingabe-/Übertragungsfehler des Verwalters ist möglich. Ab M6 (Zahlungen/offene Posten) sollten diese Werte **abgelöst** werden durch abgeleitete Salden; der Bericht-Snapshot-`source`-Hinweis macht den Übergang nachvollziehbar.
- Ob ein manueller Stichtagssaldo und manuelle Forderungs-/Verbindlichkeitsangaben für § 28 IV im konkreten Fall **genügen**, ist **fachlich abzunehmen** (StB/Fachanwalt WEG). Diese ADR entscheidet das **nicht** abschließend.

**Nicht verhandelbar (Folge dieser ADR):**
1. Der Rücklagenstand wird aus der nachrechenbaren Entwicklung (Anfang + Zuführung − Entnahme = Ende) abgeleitet; die Invariante wird geprüft (fail-loud).
2. Manuell erfasste Werte (Bankkonten-Stände, Forderungen/Verbindlichkeiten) werden im PDF **als Verwalter-Angaben gekennzeichnet** — keine vorgetäuschte automatische Herleitung.
3. Kein Schema-Anbau an `bank_account` in M4 (kein M6-Vorgriff); der manuelle Stichtagssaldo lebt ausschließlich im eingefrorenen Bericht-Snapshot.
4. Der Vermögensbericht ist ein einzufrierender, gehashter Snapshot (ADR-0003-Mechanik, gleiche Hülle wie die Abrechnungen).

> **[§18/fachlich] Keine Selbst-Zertifizierung:** Der MVP-Umfang macht den Vermögensbericht transparent und auditierbar und legt offen, welche Werte automatisch und welche manuelle Verwalter-Angaben sind. Ob dieser Umfang § 28 IV WEG im konkreten Fall erfüllt — insbesondere die Behandlung von Forderungen/Verbindlichkeiten und die Akzeptanz manueller Stichtagssalden ohne Bankabgleich — ist vor produktivem Einsatz durch **Steuerberater/Fachanwalt für WEG-Recht** abzunehmen. Die SDD-§18-Frage „Exakte PDF-Struktur … Vermögensbericht" bleibt eine Produkt-/Fachentscheidung.
