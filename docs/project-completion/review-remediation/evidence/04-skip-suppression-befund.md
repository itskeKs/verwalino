# Skip-/Suppression-Befund — begrenzt und korrigiert

Stand: 2026-10-14. Geprüft: `backend/tests/test_allocation_adoptions.py`, `test_intake_inspection.py`, `test_intake_batches.py`, `test_outbox_delivery.py`, `test_m4_weg.py`, `test_m4_weg_list_api.py` und `backend/tests/engine/test_golden_weg.py`, `test_invariants_weg.py`. Suche nach `suppress|skip|xfail|filterwarnings|noqa` lieferte:

- `test_allocation_adoptions.py:5`, `test_intake_batches.py:5`, `test_outbox_delivery.py:8`: `# noqa: F401` am Import von `worker_tenant, scope_seed`. Das sind Fixture-Imports; der Kommentar unterdrückt eine Unused-import-Lintwarnung, **keine** Testausführung, Assertion oder Fehler im Test.
- `test_outbox_delivery.py:53,65`: `skips` im Testnamen und der geprüfte fachliche Status `skipped`, kein pytest-Skip.
- Keine pytest-Skip-/XFail-/filterwarnings-Marker in diesem begrenzten Dateisatz gefunden. Keine repositoryweite Aussage über „einzigen Skip“.

Die Marker sind vorbestehend. Die drei betroffenen Dateien sind hashgleich zur gespeicherten Workspace-Baseline; in dieser Reparatur wurden weder Marker ergänzt noch Erwartungen geändert. Die Aussage „keine Suppressionsmarker“ war zu weitgehend und wird zurückgenommen. Aus einer Textsuche folgt keine vollständige Laufabnahme; erfolgreicher Testoutput wird getrennt zugeordnet. Veraltete Summen wie 33/33 und 51 werden nicht fortgeschrieben.
