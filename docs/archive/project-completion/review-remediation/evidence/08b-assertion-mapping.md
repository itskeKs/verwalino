# 08b — Originalplan → echte Assertion → Laufbeleg

Stand: 2026-10-14. Quelle Originalplan §8b und §9, kein erweitertes Recoverygate. [Aktueller Lauf](08b-regression-20261014.log): `bg_616`, **12 passed in 10.20s, Exit 0**. Vor Edit: [Ausgangstest](08b-baseline-20261014.log), `bg_614`, **1 passed in 7.39s, Exit 0**. Quellbindung/Kommandos: `09-audit-manifest.json`; eigener [Delta](08b-owned-test.delta.patch), [Originalbytes](08b-test-intake-batches.before.txt).

## Konkrete Zuordnung (alle folgenden Nodes im neuen Lauf PASSED)

| Anforderung | Datei / echte Node-ID | Tatsächliche Assertion / Sequenz | Art |
|---|---|---|---|
| unavailable/timeout fail-closed | `backend/tests/test_intake_inspection.py::test_scanner_outcomes_precede_parsing` | Ergebnisstatus gemäß fünf Parametern; Parser darf nicht erreicht werden. `unavailable/timeout/failed → blocked`, detected→rejected, clean→clean. | 1 Funktion, 5 Parameterfälle, keine DB-Recoverysequenz |
| Erschöpfte Versuche + Scanner wieder verfügbar | `backend/tests/test_intake_batches.py::test_blocked_inspection_retries_same_original_with_bounded_replay` | Drei Inspections unavailable→blocked; Retry1/2→202, identischer Key identisches Receipt; Retry3→409. **Frischer Key danach ebenfalls409; bereitstehender Clean-Stub nicht aufgerufen; Worker liefert False.** Attempts3, keine document_refs, received1/inspected3. | Ein durchgängiger DB-Test über diese Sequenz, kleinste ergänzte Negativabdeckung |
| Autorisierte Recovery vor Ausschöpfung | `backend/tests/test_intake_batches.py::test_retry_can_recover_without_restarting_completed_work` | Timeout→blocked; aktueller Fence/Grund/Key→202; Clean-Inspector verarbeitet Job; processing1/failed0. Original exportiert exakt; Replay dasselbe Receipt, kein neuer Workerclaim. | Ein durchgängiger DB-Test, **getrennt** von endgültiger Budgetgrenze |
| Stale Fence | `backend/tests/test_intake_batches.py::test_stale_worker_cannot_complete_reclaimed_generation` | Claim verliert Lease; neue Fence; Abschluss mit alter False, neuer True. | Negativer Fencing-Einzeltest |
| Aktuelle Rechte | `backend/tests/test_intake_batches.py::test_scope_revocation_cancels_work_and_hides_status` | Widerruf→Workercontext None; Scanner darf nicht ausgeführt werden; Jobcancelled, Status404, Retry404. | Negativer Rechtetest; nicht bloß Endpoint vorhanden |
| Rollback / einmaliger Effekt | `backend/tests/test_outbox_delivery.py::test_delivery_transaction_rollback_retains_pending_work_and_retry_is_atomic` | Simulierter Crash vor Commit: Dokument0, pending1; erneute Anwendung: pending0, Dokument1. | Durchgängiger Rollback-/Retrytest |
| Altes blocked Event kein späterer clean-Effekt | `backend/tests/test_outbox_delivery.py::test_old_blocked_event_cannot_adopt_later_clean_inspection` | unavailable→Retry202→clean; erstes Event verarbeitet, Dokument0; neues Event verarbeitet, Dokument1. | Durchgängiger Event-/Recoverytest vor Ausschöpfung |
| Outbox-Rechte / Versuchsevidenz | `backend/tests/test_outbox_delivery.py::test_outbox_attempt_budget_and_raw_runtime_boundary` | Raw-clerk-Aufrufe permission denied/runtime required; drei Leaseverluste, kein vierter Claim, statusblocked, drei beendete Attempts, Dokument0. | Negativer Rechte-/Budgettest, **Outboxbudget** nicht Inspectionbudget |

## Code- und Policygrenze

`backend/migrations/versions/0093_intake_quarantine.py:406–430`, `retry_intake_inspection`: Access-Recheck **vor** Replay, gleicher Key bei anderem Payload konfliktbehaftet, neue Retryrequests nur blocked/cancelled, aktuelle Fence, `attempt_number < 3`. Endpoint ist autorisierter Recoverypfad innerhalb des Budgets. **Nach drei Versuchen ist kein neuer Recoverypfad über diesen Endpoint vorhanden**, auch wenn Scanner wieder verfügbar ist. Kein Budgetreset/clean-Bypass/Produktionscode geändert.

Der Plan fordert Prüfung dieser Grenze, **nicht** „endgültiges409→neuer202→clean“. Die frühere Pflichtsequenz war erfunden und wird zurückgenommen. Ob ein zusätzlich autorisierter Recoverypfad nach dem Limit gewünscht ist, ist eine **konkrete Policyentscheidung** gemäß §8b, kein bestätigter Codefehler und kein zusätzlich erfundenes Fachgate. Aktuelle Grenze ist durch den neuen Test belegt; Policy nicht eigenmächtig erweitern.

## Zählung / historische Grenzen

[AST](09-test-nodes.json): Inspection9 Funktionen/27 Fälle (malformed11), Batches15/17, Outbox9/9. 53 vorhandene Fälle sind **nicht** 53 heute gelaufene Fälle. Aktueller Lauf: 5 Scannerparameter +4 Batchtests +3 Outboxtests =12. Historische Laufzeit-/Summenclaims ohne Rohlog: [08b-test-verification.md](08b-test-verification.md). Fehlende Rohlogs bleiben fehlend; keine Garantie sämtlicher Sicherheitsgrenzen aus wenigen Tests.
