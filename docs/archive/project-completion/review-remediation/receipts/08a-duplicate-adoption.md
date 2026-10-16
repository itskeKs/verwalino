# Receipt 08a — Doppeladoption: Fälle, Assertions und Grenzen

Stand: 2026-10-14. Originalplan §8a. **Verdacht qualifiziert; in den belegten Fällen keine unbeabsichtigte Doppelbelastung bestätigt.** Keine Code-/Policykorrektur vorgenommen, kein allgemeines Sicherheitszertifikat.

## Reale Belege

- Historischer `bg_426`: **17 passed in29.27s, Exit0**, [Ergebnistranskription](../evidence/08a-bg426-results.log), 12 Funktionen/17 Parameterfälle. Testdatei baseline-hashgleich; damaliger gesamter Worktree nicht vollständig rekonstruierbar.
- Neuer `bg_618`: **3 passed in11.05s, Exit0**, [Log](../evidence/08a-boundaries-20261014.log): zwei Replacement-Parameter und ein Nummernvarianten-Einzeltest.
- Acht read-only synthetische Identitätsassertions, Exit0, [Script](../evidence/08a-identity-diagnostic.py)/[Log](../evidence/08a-identity-diagnostic.log). Bestätigte Disposable-DB, Head0123. Keine finanziellen Schreiboperationen in dieser Diagnose.

## Originalplan → konkreter Fall

| Anforderung | Code/Test/Assertion | Nachweistiefe |
|---|---|---|
| gleicher Lieferant/Nummer, anderer Hash | `backend/tests/test_allocation_adoptions.py::test_adoption_exact_pair_resolution_and_retained_duplicate_claim` (`same_bytes=False`): zunächst blocked; explizites distinct_documents-Urteil; konkurrierend201/409, danach zweite Adoption erlaubt, Kosten4 als zwei ausdrücklich verschiedene Rechnungen. `same_bytes=True`: Gewinner201, Verlierer409, Kosten2; aktuelle Reviewänderung löscht konsumierte Identität nicht. | 2 Parameterfälle im bg_426; vollständige Adoptionsequenzen, nicht beide Fälle pauschal „gleiche Bytes“ |
| Betrag und Zeitraum Bestandteil der Businessidentität | `backend/migrations/versions/0109_allocation_adoptions.py:137–141`: seller/number/kind/currency/amount/from/to; Kandidaten nach Hash **oder** voller Identität. Synthetisch gleicher Hashanteil ignoriert, geändertes gross_amount/start/end ergibt andere Identität. | 4 konkrete SELECT-Assertions im neuen Diagnoselog; **kein** zusätzlicher end-to-end Kosten-/Zeitraumtest und kein bestätigter Finanzfehler |
| normale Adoption vs Replacement | `backend/tests/test_allocation_adoptions.py::test_adoption_confirms_complete_split_once_and_preserves_exact_source`: Replay gleich, Kosten2/Dokument1. `backend/tests/test_allocation_source_replacements.py::test_replacement_consumes_new_source_and_retains_old_claim`: zwei Parameter (`changed_amount=False/True`), Quelle anderer Hash, supersedes/version2/alterClaim erhalten; Replaygleich, normale Neuadoption derselben neuen Quelle verboten; aktive Geldsumme119 bzw149, **nicht** alte+neue Summe. | bg_426 normale Adoption; bg_618 zwei durchgängige Replacementsequenzen |
| Org-/Scope-/Uploadergrenzen | `backend/tests/test_allocation_adoptions.py::test_adoption_scope_recipient_reads_all_member_sources_but_not_private_intake`: Scope-Empfänger darf Kosten/Quelle lesen, private Intake-Refs/Treatment404, andere Org404, private SQL permissiondenied. `test_adoption_requires_source_export_and_current_confirmation_even_for_retry`: aktuelle Export-/Confirm-Berechtigung auch bei Replay. SQL `require_adoption_authority`, `adoption_duplicate_snapshot`: orggebunden, scopegebunden und visibility/capability-geprüft. | 2 echte Einzeltests im bg_426 plus Codebeleg; keine Cross-Scope-Identitäts-/Metadatenoffenlegung behauptet |
| Nummernvarianten | `backend/tests/test_duplicate_comparisons.py::test_duplicate_identity_preserves_number_case_and_explicit_values`: andere Groß-/Kleinschreibung → Kandidatenleer, Vergleich422. Diagnose: äußere Leerzeichen gleich, Case/Punktuation unterschiedlich; sellerCase/Mehrfachwhitespace gleich. | Einzeltest bg_618 +4 SELECT-Assertions; keine aggressive Nummernnormalisierung eingeführt |
| atomare Effekte / Retry / Legacy | `test_adoption_atomic_failure_and_exact_concurrent_retry`: drei Failure-Stages; `test_adoption_duplicate_manual_without_attachment_in_both_orders`: zwei Reihenfolgen, Legacy-Vorkosten blockieren bzw nach Adoption Sourceconflict. | 3+2 Parameterfälle im bg_426; keine pauschale Ableitung aller Fälle aus17 |

## Ergebnis und begrenzte Restfragen

Die Businessidentität zählt Betrag und Zeitraum; geänderte Werte machen nicht automatisch dieselbe Rechnung aus. Die geprüften Same-bytes- und expliziten Paar-/Replacementpfade verhindern unbeabsichtigten zweiten finanziellen Effekt. Unterschiedliche Rechnungen nach expliziter Entscheidung dürfen getrennt adoptiert werden; das ist kein bestätigter Fehler.

Betrag-/Zeitraumvarianten sind durch Live-Helperassertions und Code qualifiziert, nicht durch neu erfundene End-to-End-Nodes. Ein weitergehender E2E-Finanzverdacht ist hier nicht bestätigt; bei neuem konkretem Verdachtsfall Regression vor Fix gemäß Originalplan. Diese begrenzte Tiefe ist keine zusätzliche erfundene Abnahme. Historische Raw-/Quellenbindungslücken bleiben sichtbar. WEG-V01 und unabhängiges Review separat offen; kein Produkt04b-Auftrag.
