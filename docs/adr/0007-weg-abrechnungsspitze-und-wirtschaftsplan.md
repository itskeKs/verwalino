# ADR-0007: WEG-Abrechnungsspitze (WEMoG) als Ist − Soll · Wirtschaftsplan-Vorschüsse monatsbasiert

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — noch kein Code/Commit)
Datum: 2026-06-02 · Kontext: M4 · Bezug: [../m4-weg-abrechnung.md](../m4-weg-abrechnung.md) §2, §5 · [0004-zeitanteilige-verteilung-tagegenau.md](0004-zeitanteilige-verteilung-tagegenau.md) (löst den dort offen gelassenen Plan-Maßstab) · [0005-rundung-largest-remainder.md](0005-rundung-largest-remainder.md) · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.9, §10, §17 · SDD §18 (mittelbar)
Autor: `engine-correctness` (Opus 4.8)

> Diese ADR entscheidet zwei korrektheitskritische M4-Fragen: (1) **wie die Abrechnungsspitze nach WEMoG (§ 28 II WEG) definiert ist** (Beschlussgegenstand), und (2) **welcher Maßstab für die Wirtschaftsplan-Vorschüsse gilt** — den ADR-0004 ausdrücklich für M4 offen gelassen hat. Beide sind **[ENTSCHEIDUNG]** für den Menschen; offene fachliche Punkte sind als **[§18/fachlich]** markiert.

## Kontext

**(1) Abrechnungsspitze.** Vor der WEG-Reform 2020 wurde die **gesamte** Jahresabrechnung beschlossen. Seit dem **WEMoG (§ 28 Abs. 2 WEG, in Kraft seit 01.12.2020)** ist **Beschlussgegenstand nur noch die Anpassung der Vorschüsse — die „Abrechnungsspitze"** (Nachschuss bzw. Anpassung/Minderung). SDD §6.9 verlangt das wörtlich: „Die WEG-Jahresabrechnung weist die Differenz zwischen den im Wirtschaftsplan beschlossenen **Soll-Vorschüssen** und den tatsächlichen Kosten als Abrechnungsspitze aus. Beschlussgegenstand ist die Anpassung (Nachschuss/Guthaben), nicht die gesamte Abrechnung." Die korrektheitskritische Frage ist die **präzise Definition** der Spitze und die Abgrenzung von zwei leicht zu verwechselnden Größen:
- **Soll-Vorschuss** (lt. beschlossenem Wirtschaftsplan) vs.
- **tatsächlich geleistete Zahlung** (was der Eigentümer real überwiesen hat).

**(2) Wirtschaftsplan-Maßstab.** ADR-0004 (tagegenaue Ist-Kostenverteilung) hält ausdrücklich fest: *„Für geplante monatliche Hausgeldvorschüsse (nicht Ist-Kostenverteilung) kann fachlich ein monatsbasierter Maßstab gewünscht sein (12 gleiche Monatsraten). Das ist eine andere Größe als die Ist-Kostenverteilung und wird in M4 separat entschieden."* Diese ADR löst das ein.

## Optionen

**(1) Definition der Abrechnungsspitze:**

| Option | Mechanik | Bewertung |
| --- | --- | --- |
| **A — Ist − Soll-Vorschuss** | Spitze = anteilige Ist-Kosten des Eigentümers − beschlossene Soll-Vorschüsse lt. Wirtschaftsplan. | Entspricht der WEMoG-Systematik (Beschlussgegenstand = **Anpassung des Solls**); h. M. in der WEG-Literatur. **Empfohlen.** |
| **B — Ist − tatsächlich gezahlte Vorschüsse** | Spitze = Ist-Kosten − real geleistete Zahlungen. | Vermischt die **beschlussrelevante** Anpassung mit dem **Zahlungsstand** (Rückstände/Guthaben). Ein säumiger Eigentümer hätte eine andere „Spitze" als ein zahlender bei gleichem Soll — das ist fachlich falsch für den Beschlussgegenstand (Rückstand ist Vollstreckungs-/Mahnsache, nicht Abrechnungsanpassung). Verworfen als Definition der Spitze. |

**(2) Wirtschaftsplan-Maßstab:**

| Option | Mechanik | Bewertung |
| --- | --- | --- |
| **M — monatsbasiert (12 gleiche Raten)** | Jahresvorschuss / 12, Cent-genau (Largest-Remainder über 12 Raten). | Hausgeld ist eine monatlich gleichbleibende Vorauszahlung; praktischer und fachlich erwarteter Maßstab. **Empfohlen.** |
| **T — tagegenau** | Tagesanteilige Vorschüsse. | Für einen vorausschauenden Plan unüblich/unpraktisch; tagegenau ist der **Ist**-Maßstab (ADR-0004), nicht der Plan-Maßstab. Verworfen für den Plan. |

## Entscheidung

**(1) Abrechnungsspitze = anteilige Ist-Kosten − beschlossene Soll-Vorschüsse (Option A).** *(Empfehlung, [ENTSCHEIDUNG E4-3a])*

```
Abrechnungsspitze (je Eigentümer)
   = OWNER_COST_TOTAL           (anteilige Ist-Kosten: umlagefähig + nicht umlagefähig,
                                 OHNE Rücklagenzuführung — die ist Vermögensbildung, m4 §3)
   − PLAN_ADVANCE               (Soll-Vorschüsse des Eigentümers lt. beschlossenem Wirtschaftsplan)
   > 0  → Nachschuss
   < 0  → Anpassungsguthaben/Minderung
```

- Die **Rücklagenzuführung** geht **nicht** in `OWNER_COST_TOTAL` (sie ist keine verbrauchte Kosten, sondern Ansparung — m4 §3); sie wird getrennt ausgewiesen.
- Die **tatsächlich geleisteten Zahlungen** (Zahlungsstand) werden in M4 **nachrichtlich** und **getrennt** ausgewiesen (manuell, `advance_payments_source='MANUAL'`; M6 `DERIVED`), fließen aber **nicht** in die beschlussrelevante Spitze.
- Die Spitze und ihre beiden Summanden werden **strukturiert** in `settlement_line` (`OWNER_COST_TOTAL`, `PLAN_ADVANCE`, `ABRECHNUNGSSPITZE`) und im gehashten `snapshot_payload` abgelegt — maschinell prüfbar und unveränderbar.

**(2) Wirtschaftsplan-Vorschüsse monatsbasiert: 12 gleiche Monatsraten (Option M).** *(Empfehlung, [ENTSCHEIDUNG E4-3b])*

```
Jahresvorschuss_Eigentümer = (Σ geplante Kosten + geplante Rücklagenzuführung) * MEA-Anteil
monatlicher Hausgeldbetrag = distribute_amount(Jahresvorschuss, [1]*12) -> 12 Cent-Raten
                             (Largest-Remainder, ADR-0005 -> Σ Raten == Jahresvorschuss exakt)
```

- **Plan = monatsbasiert; Ist-Abrechnung = tagegenau (ADR-0004).** Diese Trennung ist die Kern-Aussage: die rückblickende Ist-Kostenverteilung der Jahresabrechnung bleibt tagegenau (auch bei unterjährigem Eigentümerwechsel); der vorausschauende Plan rechnet in 12 gleichen Monatsraten.
- Die **12-Raten-Rundung** läuft durch dieselbe zentrale `distribute_amount`-Funktion (ADR-0005), damit `Σ Monatsraten == Jahresvorschuss` exakt gilt (kein Cent-Drift über das Jahr).

**Begründung.** (1) Die WEMoG-Systematik macht die **Anpassung des Solls** zum Beschlussgegenstand; nur `Ist − Soll` bildet das korrekt ab und entkoppelt die Abrechnung sauber vom (separat zu verfolgenden) Zahlungsverhalten. (2) Hausgeld ist praktisch eine gleichbleibende Monatsrate; der monatsbasierte Plan ist der erwartete, einfach kommunizierbare Maßstab, ohne die tagegenaue Ist-Genauigkeit (ADR-0004) anzutasten. Beide Entscheidungen halten die projektweite Rundungsdisziplin (ADR-0005) ein.

## Konsequenzen

**Positiv:**
- Die beschlussrelevante Abrechnungsspitze ist **eine eindeutige, nachrechenbare Größe** (`Ist − Soll`), maschinell prüfbar (Property-Invariante I4-C: `Σ Spitzen == Σ Ist − Σ Soll`) und handberechenbar (Golden-Test §9.1 Szenario 2).
- **Saubere Trennung** Anpassung (Spitze) ↔ Zahlungsstand (Mahnwesen M6/M7) ↔ Vermögensbildung (Rücklage).
- **Methodenkonsistenz mit ADR-0004:** tagegenau für Ist, monatsbasiert für Plan — explizit getrennt, nicht stillschweigend vermischt (genau die Falle, die ADR-0004 vermeiden wollte).
- Wiederverwendung der zentralen `distribute_amount` (ADR-0005) auch für die 12-Raten-Aufteilung → keine zweite Rundungsstelle.

**Negativ / zu beachten:**
- Die Spitze setzt einen **hinterlegten Wirtschaftsplan** (Soll-Quelle) voraus. **[§18/fachlich F4-1]:** Was gilt, wenn für das Abrechnungsjahr **kein** Plan beschlossen wurde? Provisorische Annahme: `economic_plan_id` ist für `WEG_JAHRESABRECHNUNG` **erforderlich**; das Soll kommt aus dem (mindestens als `draft` erfassten) Plan. Fachlich abzunehmen (Fachanwalt WEG), ob das genügt oder ob ein Ersatz-Soll (z. B. fortgeschriebener Vorjahresplan, oder Soll = geleistete Zahlungen) nötig ist.
- Der monatsbasierte Plan abstrahiert vom unterjährigen Eigentümerwechsel (wer in welchem Monat zahlt, ist Zahlungs-/Übergangs-/Notarsache M6). Das ist gewollt, aber fachlich abzunehmen.

**Nicht verhandelbar (Folge dieser ADR):**
1. Die beschlussrelevante Abrechnungsspitze ist `anteilige Ist-Kosten − Soll-Vorschuss`, **nie** `− tatsächlich gezahlt`; der Zahlungsstand ist eine getrennte, nachrichtliche Größe.
2. Die Rücklagenzuführung zählt **nicht** als Ist-Kosten der Spitze (sie ist Vermögensbildung).
3. Plan-Vorschüsse sind monatsbasiert (12 gleiche Raten), Ist-Kostenverteilung bleibt tagegenau (ADR-0004); beide Maßstäbe sind getrennt.
4. Jede Cent-Aufteilung (MEA-Verteilung **und** 12-Monatsraten) läuft durch die zentrale `distribute_amount` (ADR-0005); `Σ == Gesamt` exakt.

> **[§18/fachlich] Keine Selbst-Zertifizierung:** Die `Ist − Soll`-Definition der Spitze und der monatsbasierte Plan-Maßstab sind die nach h. M. zum WEMoG übliche, transparente und nachrechenbare Wahl. Ob im konkreten Fall (abweichende Teilungserklärung/Beschlüsse, fehlender Plan, Sonder-Kostentragung nach § 16 II WEG) eine andere Behandlung zwingend ist, ist fachlich durch **Steuerberater/Fachanwalt für WEG-Recht** abzunehmen. Diese ADR legt die *technische* Definition und ihre nachrechenbare Umsetzung fest, nicht die abschließende Rechtsbeurteilung.
