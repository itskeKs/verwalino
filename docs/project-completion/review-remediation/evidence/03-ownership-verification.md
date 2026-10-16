# Ownership-Verifikation — korrigiert

Stand: 2026-10-14. [Gespeicherte ursprüngliche Baseline](01-baseline.json) gegen aktuelle Datei-Bytes, **nicht gegen HEAD**: [09-baseline-comparison.json](09-baseline-comparison.json).

Vor der eigenen Testergänzung waren 54/54 `protected_worktree_sha256`-Einträge identisch: die 52 tracked Dirty-Dateien und die zwei ursprünglich untracked Testhelfer. Jetzt sind 53/54 identisch; einzig `backend/tests/test_intake_batches.py` hat den eigenen minimalen Delta. [Gesicherte Originalbytes](08b-test-intake-batches.before.txt) matchen den Baseline-Hash, [eigener Patch](08b-owned-test.delta.patch) grenzt die Ergänzung ab. Kein Vollaudit fremder Änderungen erforderlich.

`existing_chain_sha256` enthält bereits vorhandene Reparaturdateien. README, Vertrag und Reparatur-State wurden geändert; 04b-Closure, 04b-Receipt und Operatorartefakt sind hashgleich. Daher waren „keine Reparaturdatei wurde verändert“ und „die Kette existierte vorher nicht“ falsch. Untracked-Dateien werden durch `git diff --check` ohne zusätzliche Erfassung nicht geprüft.

Der Baseline-Statehash gehört zum **Reparatur-State**. Der ursprüngliche Produkt-State ist aus diesen Quellen nicht rekonstruierbar; aktuelle Inhalte und historische 04b-Grenzen sind in [05-product-04b-separation.md](05-product-04b-separation.md) konkret beschrieben. Fehlende historische Ownership wird nicht durch Agentenbehauptungen ersetzt.

Keine Änderung fremder Schutzdateien in dieser Fortsetzung. Der historische Git-Dateilistenumfang ist kein Nachweis über alle fremden Dirty-Dateien; ein solcher Vollaudit ist nicht beauftragt.
