# ADR-0010: Vermögenstrennung bei Zahlungen — DB-Constraint-Härtung jetzt, keine zweite RLS-Achse

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — noch kein Code/Commit)
Datum: 2026-06-02 · Kontext: M6 (löst den in M0 ausdrücklich nach M6 vertagten Punkt) · Bezug: [0001-mandantentrennung-rls.md](0001-mandantentrennung-rls.md) (§Abgrenzung: „DB-Härtung der Vermögenstrennung frühestens mit M6") · [../architecture.md](../architecture.md) §2.6, Annahme A-4, §9 Punkt 4 · [../m6-zahlungen-mietkonto.md](../m6-zahlungen-mietkonto.md) §4 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §4 (Vermögenstrennung), §6.3
Autor: `engine-correctness` (Opus 4.8)

> M0 (architecture.md A-4, ADR-0001 §Abgrenzung) hat ausdrücklich festgehalten: Vermögenstrennung wird in M0 **nur im Modell vorbereitet**, die Frage der **DB-Härtung** ist „frühestens mit M6 zu entscheiden, wenn Konten/Zahlungen real sind." M6 macht Konten/Zahlungen real — diese ADR entscheidet es. **[ENTSCHEIDUNG]** für den Menschen.

## Kontext

**Zwei Isolationsachsen** (architecture.md §2.6, SDD §4):

- **Mandantentrennung (I1):** zwischen *Organizations*. Hart per RLS erzwungen (ADR-0001) — unverändert.
- **Vermögenstrennung:** *innerhalb* einer Organization zwischen Mandaten/WEGs. Gelder und Belege der WEG „Hauptstr. 5" werden nie mit denen der WEG „Bahnhofstr. 12" vermischt (Treuhand-/Fremdgeldtrennung, SDD §4: „Gelder und Belege unterschiedlicher Mandate/WEGs werden niemals vermischt. Jede WEG bzw. jeder Auftraggeber hat eigene Konten und eine eigene Abrechnungssphäre.").

Mit M6 entstehen erstmals echte **Geldbewegungen** (`payment`) gegen **Konten** (`bank_account`). Damit wird die Vermögenstrennung von einer Modell-Vorbereitung zu einer durchzusetzenden Eigenschaft: Eine Zahlung muss garantiert dem **richtigen** Konto/Mandat/Objekt zugeordnet sein, und ein Konto gehört genau **einem** Objekt/Mandat. Ein „Quer-Buchen" (Zahlung der WEG A landet auf dem Konto der WEG B *innerhalb derselben Organization*) ist ein gravierender, treuhandrelevanter Fehler — RLS fängt ihn **nicht** ab, weil beide WEGs zur **selben** Organization gehören (RLS isoliert nur zwischen Orgs).

## Optionen

| Option | Mechanik | Isolationsstärke | Aufwand | Bewertung |
| --- | --- | --- | --- | --- |
| **A — Nur App-Layer** | Service prüft beim Buchen, dass Zahlung und Konto zum selben Mandat/Objekt gehören; keine DB-Garantie. | Schwach: ein Service-Bug oder Ad-hoc-INSERT kann querbuchen. | Niedrig | Unzureichend für Treuhandgeld — dieselbe Schwäche, die ADR-0001 für I1 gerade *nicht* akzeptiert hat. |
| **B — DB-Constraint-Härtung (CHECK/FK-Konsistenz), keine zweite RLS-Achse** | DB erzwingt die **Kohärenz** der Vermögensbezüge: (a) `bank_account` gehört genau einem `property` (+ optional `management_mandate`); (b) `payment.bank_account_id` ist Pflicht und die Zahlung erbt Objekt/Mandat **aus dem Konto** (nicht frei wählbar); (c) Composite-FK/Trigger stellen sicher, dass `payment.property_id` == `bank_account.property_id`. | Stark für die *Kohärenz* (kein Querbuchen); ohne den Betriebsaufwand einer zweiten RLS-Policy-Achse. | Mittel | **Empfohlen.** |
| **C — Zweite RLS-Achse (mandate-scoped RLS)** | Zusätzlich zur org-RLS eine mandats-/objektgebundene RLS-Policy mit einer zweiten Session-GUC (`app.current_mandate_id`). | Sehr stark (DB filtert auch innerhalb der Org). | Hoch: zweite GUC im Tenant-Context, jede mandatsübergreifende Sicht (Fristenmonitor, offene Posten gesamt — die die Verwaltung *braucht*, ADR-0001 §Konsequenzen) müsste die zweite Achse bewusst weiten; Coverage-Tests verdoppeln. | Verfrüht/überschießend für das MVP; nimmt der Verwaltung die gewollten Cross-Mandat-Sichten oder verlangt ständiges bewusstes Umgehen. |

## Entscheidung

**Option B — DB-Constraint-Härtung der Vermögens-Kohärenz; keine zweite RLS-Achse.** *(Empfehlung, [ENTSCHEIDUNG E6-4])*

1. **Konto-Anker bleibt, wird Pflicht-Drehpunkt:** `bank_account` gehört genau einem `property` (Spalte `property_id` existiert bereits) und optional einem `management_mandate`. Eine Zahlung wird **immer** gegen ein Konto erfasst (`payment.bank_account_id NOT NULL`).
2. **Zahlung erbt Vermögensbezug aus dem Konto, nicht frei wählbar:** `payment.property_id` (und `management_mandate_id`) werden **aus dem gewählten `bank_account`** abgeleitet/gespiegelt, nicht unabhängig gesetzt. DB-seitig per **Composite-Foreign-Key** abgesichert: `payment(organization_id, bank_account_id, property_id)` referenziert `bank_account(organization_id, id, property_id)` (mit passendem UNIQUE-Index auf `bank_account`). Damit ist „Zahlung auf Konto X behauptet Objekt Y, aber Konto X gehört Objekt Z" **DB-unmöglich** — nicht nur App-Konvention.
3. **Kein Querbuchen über Mandate/Objekte:** Da Objekt/Mandat der Zahlung aus dem Konto folgen und Konten je Objekt getrennt sind, ist die Vermögenstrennung als **Kohärenz-Invariante** der Tabelle durchgesetzt.
4. **Mandantentrennung (I1) unverändert:** Alle M6-Tabellen tragen `organization_id` + RLS (vier Policies, fail-closed, Muster `0010`/`0014`) wie alles andere. Die Vermögens-Härtung ist eine **zusätzliche** Kohärenz-Schicht *innerhalb* der Org, **kein** Ersatz für RLS.
5. **Konto-Typ-Stimmigkeit (Plausibilität, kein harter Zwang im MVP):** Kautionszahlungen gehören fachlich aufs Kautionskonto, Rücklagenanteile aufs Rücklagenkonto. **[§18/fachlich F6-2]** ob das im MVP als **harter** CHECK (`payment.payment_type` ↔ `bank_account.account_type`) erzwungen oder nur als **Warnung** geführt wird. Empfehlung: im MVP **Warnung/Plausibilität** (real-world-Daten sind unsauber; ein harter Zwang kann legitime Sonderfälle blockieren), die Kohärenz Objekt↔Konto (Punkt 2) aber **hart**. Fachlich abzunehmen.

**Begründung.** Treuhand-/Fremdgeldtrennung verträgt — genau wie die Mandantentrennung in ADR-0001 — keine reine App-Layer-Disziplin: ein Querbuchen ist haftungsrelevant. Eine **Composite-FK-Kohärenz** macht die Trennung zur *Eigenschaft des Speichers* (das ADR-0001-Leitmotiv), ohne den Betriebsaufwand und die Reporting-Reibung einer zweiten RLS-Achse. Option C (zweite RLS-Achse) wäre die stärkste Isolation, ist aber für ein MVP überschießend und stünde den mandatsübergreifenden Verwaltungs-Sichten (Fristenmonitor, offene Posten gesamt) im Weg, die ADR-0001 als gewollten Vorteil der Shared-DB-Architektur nennt. Option B sichert das konkret gefährdete Szenario (Querbuchen von Geld) hart ab und lässt die spätere C-Aufrüstung offen, falls ein Audit sie verlangt.

## Konsequenzen

**Positiv:**
- Querbuchen von Geld über Objekt-/Mandatsgrenzen *innerhalb* einer Org ist **DB-unmöglich** (Composite-FK), nicht nur App-Konvention — die treuhandrelevante Kernsorge ist hart adressiert.
- Kein zweiter Session-GUC, keine verdoppelten RLS-Policies, keine Reibung bei den gewollten Cross-Mandat-Sichten der Verwaltung.
- Der Vermögensbezug einer Zahlung ist **eindeutig und prüffest** (folgt aus dem Konto) — Grundlage für die saubere `DERIVED`-Ableitung in die Abrechnung (m6 §6) und für mandatsgetrennte Salden.
- Konsistent zur ADR-0001-Philosophie (Invariante als Speicher-Eigenschaft, Defense-in-Depth), eine Stufe schwächer dosiert (Kohärenz statt voller RLS), passend zum tatsächlichen Risiko.

**Negativ / zu beachten:**
- Der Composite-FK verlangt einen zusätzlichen UNIQUE-Index `(organization_id, id, property_id)` auf `bank_account` und Sorgfalt bei der Migration (m6 §8).
- Die Konto-Typ-↔-Zahlungstyp-Stimmigkeit bleibt im MVP weich (F6-2) — ein bewusst in Kauf genommener Rest, fachlich abzunehmen.
- Eine spätere Aufrüstung auf Option C (zweite RLS-Achse) bliebe möglich, wäre aber ein eigener, bewusster Schritt (Tenant-Context-Erweiterung) — hier festgehalten, nicht verbaut.

**Nicht verhandelbar (Folge dieser ADR):**
1. Eine Zahlung wird immer gegen genau ein `bank_account` erfasst; Objekt/Mandat der Zahlung folgen aus dem Konto und sind DB-seitig (Composite-FK) an das Konto gebunden — Querbuchen ist unmöglich.
2. `bank_account` gehört genau einem `property`; Vermögenstrennung ist eine Kohärenz-Invariante der Zahlungs-/Kontentabellen.
3. Mandantentrennung (I1, RLS) bleibt unverändert die primäre, org-weite Isolationsachse; die Vermögens-Härtung ergänzt sie *innerhalb* der Org, ersetzt sie nicht.
4. Die Kohärenz Objekt↔Konto ist durch einen Negativtest belegt, der einen Querbuchungs-Versuch DB-seitig scheitern sieht (m6 §7).

> **[§18/fachlich] Keine Selbst-Zertifizierung:** Diese ADR macht die Vermögenstrennung bei Zahlungen *transparent und DB-erzwungen kohärent* und legt offen, dass die *vollständige* mandatsgebundene DB-Isolation (Option C) bewusst nicht umgesetzt wird. Ob die Kohärenz-Härtung für die Treuhand-/Fremdgeldanforderungen im konkreten Betrieb genügt, ist fachlich (Steuerberater/Fachanwalt, ggf. Wirtschaftsprüfer) abzunehmen.
