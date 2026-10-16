# Reparaturunterpaket 08a — Doppeladoption

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 8.

## Auftrag und Einstieg
`0109_allocation_adoptions.py:137–200`, einschlägige spätere Patches/Replacementfunktionen und bestehende Adoptions-/Duplikattests. Betrag und Zeitraum sind Teil der Businessidentität; eine Doppelbelastung ist noch nicht end-to-end bestätigt. Synthetische Fälle gleicher Lieferant/Nummer bei geändertem Hash, Betrag/Zeitraum, normaler Adoption vs Replacement, sowie Org-/Scope-/Uploadergrenzen und Belegnummernvarianten prüfen. Keine pauschale aggressive Nummernnormalisierung oder Cross-Scope-Metadatenoffenlegung. Bei bestätigter Lücke: roter Regressionstest vor kleinstem autorisiertem Fix; bei fachlich unklarer Kandidaten-/Entscheidungsregel blockieren. Widerlegte Verdachtsfälle mit Nachweis schließen.

## Abnahme
Verdacht mit Test-/Codebeleg als widerlegt, behoben oder konkret blockiert dokumentieren. Fehlende Matrixfälle nicht durch pauschal grüne Testzahlen ersetzen.

## Grenzen und Übergabe
Keine Secrets, Live-Scanner, Provider-, Produktionsaktionen, Cross-Scope-Offenlegung oder Finanzänderungen ohne Freigabe. Kein Produkt-04b, kein Commit/Push.
Receipt schreiben; State aktualisieren und erneut lesen; Kontextwechsel.
Nachfolger: `steps/08b-scanner-recovery.md`; unabhängiges Unterpaket trotz konkret festgehaltenem Blocker fortführen.
