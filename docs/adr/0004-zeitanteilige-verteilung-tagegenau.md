# ADR-0004: Zeitanteilige Kostenverteilung — tagegenau (Day-Count) als Standard

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — noch kein Code/Commit)
Datum: 2026-06-01 · Kontext: M3 · Bezug: [../m3-abrechnungs-engine.md](../m3-abrechnungs-engine.md) §3 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.9, §14 · SDD §18 (mittelbar)
Autor: `engine-correctness` (Opus 4.8)

> Diese ADR entscheidet den in der Roadmap M3 und SDD §14 geforderten, aber dort nicht festgelegten **Maßstab der anteiligen Zeitraumberechnung** bei unterjährigem Eigentümer-/Mieterwechsel und Leerstand. Es ist eine **[ENTSCHEIDUNG]** für den Menschen — die ADR dokumentiert die Empfehlung und die Konsequenzen, ersetzt aber nicht die fachliche Bestätigung.

## Kontext

SDD §14 verlangt: „Bei unterjährigem Wechsel muss anteilig berechnet werden können." Eine Kostenposition trägt einen **Leistungszeitraum** (`cost_entry.service_period_from/_to`, M2). Innerhalb dieses Zeitraums kann eine Einheit nacheinander an verschiedene Mieter vermietet sein (`lease_agreement`-Perioden, M1) oder zeitweise leerstehen (Lücke zwischen Leases). Die Kosten eines Leistungszeitraums müssen **zeitproportional** auf diese Perioden aufgeteilt werden, bevor der Verteilerschlüssel greift.

Die Frage ist der **Maßstab der Zeitproportion**:

- **tagegenau (Day-Count, „actual/actual"):** Anteil = belegte Tage / Gesamttage des Leistungszeitraums.
- **monatsgenau (30/360 oder „angefangener Monat = voller Monat"):** Anteil in Monatsschritten; ein angefangener Monat zählt z. B. voll oder nach 30-Tage-Konvention.

Das ist eine **Kern-Korrektheitsentscheidung**: Sie bestimmt, welcher Cent welchem Mieter/Eigentümer/Leerstand zugeordnet wird. Sie muss explizit, einheitlich und getestet sein — nicht implizit aus einer Bibliotheks-Default fallen.

## Optionen

| Option | Mechanik | Stärke | Schwäche |
| --- | --- | --- | --- |
| **A — tagegenau (actual/actual)** | Anteil = `belegte_Tage / Tage_im_Leistungszeitraum`, beide inklusive Endtag. | Genauste, fairste Abbildung; unabhängig von Monatslängen/Schaltjahr; eine einzige, leicht erklärbare Regel; bei Mietverhältnissen die in Rechtsprechung/Literatur verbreitete, unangreifbare Methode. | Bruchteile sind „krumm" (z. B. 73/365) — verlangt exakte Dezimalrechnung und kontrollierte Rundung (ADR-0005). |
| **B — monatsgenau (angefangener Monat voll)** | Jeder berührte Kalendermonat zählt voll; Anteil in n/12 o. ä. | Einfache, „runde" Zahlen; in manchen Hausgeld-/Vorschuss-Kontexten üblich. | Bei Wechsel mitten im Monat **systematische Doppel- oder Unterzählung** (zwei Parteien zahlen denselben Monat voll → Summe > 100 %, oder eine Korrektur-Sonderregel nötig). Schaltjahr-/Monatslängen-Verzerrung. Für die Mieter-BK angreifbar. |
| **C — 30/360** | Jeder Monat = 30 Tage, Jahr = 360 Tage. | Im Bank-/Zinsumfeld verbreitet; glatte Nenner. | Im Hausverwaltungskontext unüblich, erklärungsbedürftig; verzerrt 31-Tage-Monate und Februar; kein fachlicher Vorteil hier. |

## Entscheidung

**Option A — tagegenau (actual/actual), inklusive beider Randtage — als verbindlicher Standardmaßstab für M3.** *(Empfehlung, [ENTSCHEIDUNG])*

Präzise Definition (gilt für die gesamte Engine):

1. Ein Zeitraum `[from, to]` ist **tag-inklusiv** an beiden Enden. Seine Länge in Tagen ist `(to − from).days + 1`.
2. Die **Überschneidung** zweier Zeiträume (Leistungszeitraum ∩ Mieter-/Eigentümerperiode) ist `[max(from₁,from₂), min(to₁,to₂)]`, sofern `max(from) ≤ min(to)`, sonst leer (0 Tage).
3. Der **Zeitanteil** einer Belegungsperiode an einer Kostenposition ist
   `tage_überschneidung / tage_leistungszeitraum` — exakt als `Decimal`/Bruch, **nicht** vorgerundet.
4. Die Summe der Zeitanteile aller lückenlos-überschneidungsfreien Perioden (inkl. Leerstand) über den Leistungszeitraum ist **exakt 1** (Tage summieren sich auf die Gesamttage). Das ist die zentrale Invariante, die Cent-Drift durch Zeitaufteilung ausschließt (zusammen mit ADR-0005).
5. **Leerstandstage** (kein aktiver Lease) sind eine eigene „Periode" mit Adressat = Eigentümer der Einheit im jeweiligen Teilintervall (SDD §6.9). Auch hier tagegenau.
6. Bei **Eigentümerwechsel** innerhalb des Leistungszeitraums wird die Eigentümer-Sphäre (WEG/Hausgeld, M4; Leerstand-Zuordnung in M3) ebenfalls tagegenau auf die `ownership_history`-Perioden aufgeteilt.

**Begründung:** Die tagegenaue Methode ist die einzige, die ohne Sonderregel die Invariante „Summe der Zeitanteile = 1" erfüllt und damit *bauartbedingt* keinen Cent verliert oder doppelt zählt. Sie ist unabhängig von Monatslängen und Schaltjahren, ist in der Mietrechtsprechung die unangefochtene Methode für unterjährige Abrechnung, und sie ist trivial nachrechenbar (Tage zählen). Die „krummen" Bruchteile sind kein Problem, weil Geld ohnehin exakt als `Decimal` geführt und die Rundung als bewusste, getestete Operation am Schluss erfolgt (ADR-0005). Monatsgenau (B) erkauft glatte Zahlen mit einer systematischen Ungenauigkeit beim Monatswechsel, die genau den korrektheitskritischen Fall (unterjähriger Wechsel) betrifft — das ist hier das falsche Tauschgeschäft.

## Konsequenzen

**Positiv:**
- Eine einzige, projektweit einheitliche Zeitregel — kein „mal so, mal so" je Kostenart.
- Invariante „Σ Zeitanteile = 1" ist beweisbar (Property-based Test, m3-Doc §9) und schließt die häufigste Fehlerquelle (Doppelzählung beim Wechsel) aus.
- Tagegenau ist auditierbar: Prüfer kann „belegte Tage / Gesamttage" von Hand nachrechnen.
- Wiederverwendbar für M4 (Hausgeld: Eigentümerwechsel) ohne Methodenbruch.

**Negativ / zu beachten:**
- Verlangt **exakte Dezimal-/Bruchrechnung** der Zeitanteile und kontrollierte Rest-Verteilung (ADR-0005); ein naives `float`-Tagesverhältnis wäre falsch. Wird durch I4 (Decimal überall) und ADR-0005 abgefangen.
- Die **Schaltsekunden-/Zeitzonenfrage** entfällt: Es wird auf **Kalendertagen** (`DATE`, kein `TIMESTAMP`) gerechnet, konsistent zu `valid_from/_to` und `service_period_*` (alle `DATE`).
- **Halboffene vs. tag-inklusive Intervalle:** Die DB-EXCLUDE-Constraints (M1) verwenden `daterange` mit Standard-Semantik `[ )` (oberer Rand exklusiv). Die Engine rechnet tag-**inklusiv** `[ ]`. Diese Differenz ist bewusst und wird an der Lese-Grenze sauber umgerechnet (m3-Doc §3.4): ein Lease `valid_from=2025-01-01, valid_to=2025-06-30` belegt die Tage 01-01 bis 30-06 **einschließlich** 30-06. Die Engine darf den oberen Rand nicht „verlieren". Dies ist ein explizit getesteter Edge-Case (Tag-genauer Wechsel: Auszug 30-06 / Einzug 01-07 → keine Lücke, kein Überlapp).

**Nicht verhandelbar (Folge dieser ADR):**
1. Alle Zeitanteile werden tagegenau, tag-inklusiv, als `Decimal`/Bruch (nicht `float`) berechnet.
2. Die Summe der Zeitanteile einer vollständigen, überschneidungsfreien Periodenmenge über den Leistungszeitraum ist exakt 1.
3. Leerstand ist eine vollwertige Periode mit Eigentümer-Adressat, kein „Rest", der verschwindet.

> **[OFFEN/§18] Hausgeld-Vorschüsse / Wirtschaftsplan (M4):** Für **geplante** monatliche Hausgeldvorschüsse (nicht Ist-Kostenverteilung) kann fachlich ein monatsbasierter Maßstab gewünscht sein (12 gleiche Monatsraten). Das ist eine **andere** Größe als die Ist-Kostenverteilung und wird in M4 separat entschieden; diese ADR bindet nur die **Ist-Kostenverteilung** (BK-Abrechnung M3, Jahresabrechnung M4) auf tagegenau. Hier festgehalten, damit M4 es nicht stillschweigend vermischt.

> **Keine Selbst-Zertifizierung:** Die tagegenaue Methode ist die transparente, gängige und nachrechenbare Wahl; ob sie im konkreten mietvertraglichen Einzelfall (z. B. abweichende vertragliche Abrede zum Abrechnungsmaßstab) zwingend ist, ist fachlich (Steuerberater/Fachanwalt) abzunehmen.
