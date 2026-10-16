# Skip-/Suppression-Befund: test_declaration_runtime_config.py:544

**Datum:** 2025-10-13
**Status:** Geprüft, unverändert gelassen, keine Abschwächung
**Budget-Kategorie:** Dokumentation/Prüfung, kein Code geändert

---

## 1. Geprüfte Stelle

**Datei:** `backend/tests/test_declaration_runtime_config.py`
**Zeile:** ~544 ( pytest.mark.skipif-Decorator)

**Code-Ausschnitt:**

```python
@pytest.mark.skipif(
    not os.environ.get("HV_DECLARATION_RUNTIME_IMAGE"),
    reason="set HV_DECLARATION_RUNTIME_IMAGE after building the local runtime image",
)
def test_built_runtime_image_has_ocr_capabilities_and_nonroot_user() -> None:
    image = os.environ["HV_DECLARATION_RUNTIME_IMAGE"]
    result = _run_built_image_smoke(image)
    assert result.returncode == 0, result.stderr
```

---

## 2. Befund

### 2.1 Art des Skips

- **Typ:** `pytest.mark.skipif` (markiert, nicht gelöscht)
- **Bedingung:** `not os.environ.get("HV_DECLARATION_RUNTIME_IMAGE")`
- **Begründung im Code:** "set HV_DECLARATION_RUNTIME_IMAGE after building the local runtime image"

### 2.2 Funktion

- Der Test erfordert ein lokal gebautes Docker-Image (`HV_DECLARATION_RUNTIME_IMAGE`).
- Ohne gesetzte Umgebungsvariable wird der Test **lokal übersprungen** (`skip`), nicht ignoriert (`ignore`).
- Der Test ist **vollständig erhalten** und wird bei gesetzter Umgebungsvariable **vollständig ausgeführt**.

### 2.3 Bewertung

- **Keine Abschwächung:** Der Check wurde nicht gelöscht, nicht abgeschwächt, nicht hardcoded.
- **Intentional:** Der Skip ist dokumentiert und begründet.
- **Lokale Ausführbarkeit:** Lokal ohne Docker-Image korrekt übersprungen; bei gesetztem Image vollständig prüfend.

---

## 3. Weitere Suppressions

Keine weiteren `pytest.mark.skip`, `pytest.mark.xfail` oder `@suppress`-Dekoratoren in geprüften Testdateien gefunden.

---

## 4. Fazit

Die einzige markierte Skip-Stelle `test_declaration_runtime_config.py:544` ist:

1. **Intentional** und dokumentiert.
2. **Nicht gelöscht** oder abgeschwächt.
3. **Lokal korrekt** übersprungen bei fehlendem Docker-Image.
4. **Vollständig prüfend** bei gesetztem `HV_DECLARATION_RUNTIME_IMAGE`.

**Keine Änderungen am Testcode durchgeführt.**

---

*Dokument erstellt: 2025-10-13*
*Geprüfte Datei: `backend/tests/test_declaration_runtime_config.py`*
*Keine weiteren Skip-/Suppression-Stellen gefunden.*
