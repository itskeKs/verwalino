# ADR-0005: Rundung der Verteilung — kaufmännisch auf Cent + Largest-Remainder gegen Cent-Drift

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — noch kein Code/Commit)
Datum: 2026-06-01 · Kontext: M3 · Bezug: [../m3-abrechnungs-engine.md](../m3-abrechnungs-engine.md) §4 · [../architecture.md](../architecture.md) §5.1 (I4) · SDD §6.9, §10, §18
Autor: `engine-correctness` (Opus 4.8)

> Diese ADR entscheidet den in SDD §18 explizit offenen Punkt „Welche Rundungsregeln gelten pro Abrechnung" für die **Verteilungs-Engine** (M3/M4). Es ist eine **[ENTSCHEIDUNG]** für den Menschen. Die ADR legt die *technische* Rundungsmechanik fest, die die Kern-Invariante „Summe der Anteile = Gesamtkosten, kein Cent geht verloren" garantiert. Die *fachliche* Frage, ob für eine bestimmte Abrechnungsart eine andere Regel vorgeschrieben ist, bleibt fachlich abzunehmen.

## Kontext

Geld wird exakt als `NUMERIC(14,2)` / `Decimal` geführt (I4, architecture.md §5.1). Eine Kostenposition (Gesamtbetrag) wird über einen Verteilerschlüssel (MEA, Fläche, …) und ggf. einen Zeitanteil (ADR-0004) auf mehrere Adressaten verteilt. Die exakten, ungerundeten Einzelanteile sind im Allgemeinen **keine** glatten Cent-Beträge (z. B. 1000,00 € ÷ 3 = 333,3333… €). Beim Runden jedes Anteils auf Cent entsteht ein **Rundungsrest** (3 × 333,33 € = 999,99 € ≠ 1000,00 €). Dieser „Cent-Drift" ist ein klassischer, prüfungsrelevanter Abrechnungsfehler: Die Summe der ausgewiesenen Mieter-/Eigentümeranteile muss **exakt** die ausgewiesenen Gesamtkosten der Kostenart ergeben — sonst ist die Abrechnung formell angreifbar und rechnerisch falsch.

Zwei Teilfragen:
1. **Rundungsmodus** je Einzelwert (kaufmännisch / Banker's / abschneiden).
2. **Rest-Behandlung**, damit Σ Anteile == Gesamtbetrag **exakt** gilt.

## Optionen

**Teil 1 — Rundungsmodus:**

| Option | Mechanik | Bewertung |
| --- | --- | --- |
| **Kaufmännisch (HALF_UP)** | 0,5 rundet auf. | In Deutschland die erwartete, gängige kaufmännische Rundung; für Abrechnungen die naheliegende Wahl. **Empfohlen.** |
| Banker's (HALF_EVEN) | 0,5 rundet zur geraden Ziffer. | Statistisch bias-frei, aber für Laien/Prüfer unerwartet bei Geldabrechnungen; kein Vorteil, der den Erklärungsaufwand wert ist. |
| Abschneiden (DOWN) | immer abrunden. | Systematischer Bias, fachlich unüblich. Verworfen. |

**Teil 2 — Rest-Behandlung (kritisch):**

| Option | Mechanik | Stärke | Schwäche |
| --- | --- | --- | --- |
| **A — Largest Remainder (Hare/Hamilton)** | Jeden Anteil **abrunden** (floor auf Cent); die Differenz `Gesamt − Σ Abrundungen` ist eine ganze Zahl `k` von Cent; verteile `k` Extra-Cents auf die `k` Anteile mit dem **größten Bruchteil-Rest** (Tie-Break deterministisch). | Σ == Gesamt **exakt**, garantiert. Verteilt den Rest *fair* (an die, die am nächsten an der Aufrundung waren). Mathematisch sauber, gut testbar, gängig in Verteilungsrechnung. | Etwas mehr Logik als „einer kriegt alles". |
| **B — Differenz auf den größten/letzten Anteil** | Alle kaufmännisch runden; Rest-Differenz komplett dem größten (oder letzten) Anteil aufschlagen. | Sehr einfach. | Rest kann > 1 Cent sein und konzentriert sich willkürlich auf **einen** Adressaten; bei vielen Einheiten unfair und erklärungsbedürftig; Wahl „größter vs. letzter" ist willkürlich. |
| **C — Kein Ausgleich** | Jeden Anteil unabhängig kaufmännisch runden. | Trivial. | **Σ ≠ Gesamt** möglich (Cent-Drift). **Inakzeptabel** — verletzt die Kern-Invariante. Verworfen. |

## Entscheidung

**Kaufmännischer Modus für die Anzeige-Rundung + Largest-Remainder (Option A) für die Rest-Behandlung — als verbindliche, projektweite Rundungsmechanik der Verteilungs-Engine.** *(Empfehlung, [ENTSCHEIDUNG])*

Präziser Algorithmus (für **jede** Aufteilung eines Gesamtbetrags `G` (Cent-exakt) auf `n` Adressaten mit exakten Gewichten `wᵢ`):

```
1. Exakter Anteil:        eᵢ = G * wᵢ / Σw            (Decimal, ungerundet, hohe Präzision)
2. Abrundung auf Cent:    fᵢ = floor(eᵢ, 2)            (immer abrunden)
3. Rest in ganzen Cents:  k  = round((G − Σfᵢ) * 100)  (ganzzahlig, 0 ≤ k < n)
4. Rang nach Bruchteil:   rᵢ = eᵢ − fᵢ  (der „verlorene" Sub-Cent-Rest je Adressat)
5. Vergib +0,01 € an die k Adressaten mit größtem rᵢ.
   Tie-Break bei gleichem rᵢ: deterministisch nach (größtes eᵢ, dann stabile
   Sortierung nach Einheit-/Adressat-ID) — reproduzierbar, nicht zufällig.
6. Ergebnis cᵢ = fᵢ (+0,01 falls zugeteilt).   ES GILT: Σcᵢ == G  (exakt).
```

Diese Mechanik wird **einmal** als reine Funktion implementiert (`distribute_amount(total, weights) -> list[Decimal]`, m3-Doc §4/§10) und von **jedem** Verteilerschlüssel und von der Zeitanteils-Aufteilung verwendet. Es gibt keine andere Stelle, an der Geld auf Cent gerundet wird.

**Reihenfolge bei kombinierter Verteilung (Zeit × Schlüssel):** Erst der **exakte** (ungerundete) Wert über alle Stufen multiplizieren, **dann am Schluss einmal** auf Cent runden+ausgleichen — nie zwischendurch runden (sonst akkumulieren sich Rundungsfehler). Das heißt: Zeitanteil (ADR-0004) und Verteilungsgewicht werden exakt verkettet; die Largest-Remainder-Rundung greift auf der **finalen** Menge der `settlement_line`-Beträge je Kostenposition.

**Begründung:** Largest-Remainder ist die einzige der drei Rest-Optionen, die die Kern-Invariante `Σ == G` *garantiert* **und** den Rest *fair und deterministisch* verteilt. „Determinismus" ist hier nicht kosmetisch: Eine finalisierte Abrechnung wird als Snapshot eingefroren und gehasht (ADR-0003 §3); zweimaliges Rechnen derselben Eingaben muss bit-identisch denselben Snapshot ergeben, sonst ist die Hash-Integrität wertlos. Kaufmännische Anzeige-Rundung entspricht der deutschen Erwartung. Option C ist ausgeschlossen, weil sie genau den Fehler erzeugt, den Abrechnungssicherheit verbietet.

## Konsequenzen

**Positiv:**
- **Bauartbedingte Korrektheit:** `Σ Anteile == Gesamtkosten` ist nicht „hoffentlich", sondern eine Eigenschaft der Funktion — als Property-based Test über zufällige Beträge/Gewichte beweisbar (m3-Doc §9, „kein Cent geht verloren").
- **Determinismus → Snapshot-Hash stabil** (ADR-0003-Verzahnung).
- Eine einzige Rundungsstelle → keine verstreuten, inkonsistenten Rundungen.
- Fair: Der Rest geht an die, die rechnerisch am nächsten an der Aufrundung lagen.

**Negativ / zu beachten:**
- Bei sehr kleinen Beträgen und vielen Adressaten kann ein Adressat 1 Cent mehr/weniger als sein „naiv kaufmännisch gerundeter" Wert erhalten — das ist **korrekt** und gewollt (es ist der Preis dafür, dass die Summe exakt stimmt), muss aber in der Verfahrensdokumentation erklärt sein.
- Der Algorithmus muss **negative Beträge** (Gutschriften) und `Σw = 0` (kein gültiges Gewicht → definierter Fehler, nicht stille 0-Verteilung) sauber behandeln (m3-Doc §4.4).
- **Reihenfolge-Disziplin** (exakt rechnen, einmal am Schluss runden) ist eine Implementierungsregel, die der Code-Review und ein gezielter Test absichern müssen.

**Nicht verhandelbar (Folge dieser ADR):**
1. Es wird **nie** ein `float` für Geld oder Gewichte verwendet (I4); Verteilung rechnet in `Decimal`.
2. Es gibt **eine** zentrale Verteilungs-/Rundungsfunktion; jede Kostenverteilung läuft durch sie.
3. `Σ gerundete Anteile == Gesamtbetrag` ist nach jeder Verteilung exakt erfüllt — durch Test belegt.
4. Die Rest-Zuteilung ist **deterministisch** (reproduzierbarer Tie-Break), damit Snapshots stabil/hashbar sind.

> **[OFFEN/§18]:** Ob eine bestimmte Abrechnungsart oder ein Mietvertrag eine **abweichende** Rundungsregel vorschreibt (z. B. kaufmännisch je Position ohne Restausgleich, bewusst in Kauf genommener Differenz), ist fachlich zu prüfen. Default dieser ADR ist die rechnerisch exakte Variante (Largest-Remainder); Abweichungen wären eine bewusste fachliche Festlegung. **Keine Selbst-Zertifizierung.**
