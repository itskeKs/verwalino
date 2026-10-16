# Kontextbudget — Nachweisgrenze und Arbeitsweise

Stand: 2026-10-14. [Vertrag](../contract.md) und Originalplan §Ausführung mit 49k: 49k Gesamtkontext als Ziel, höchstens etwa 28k Arbeitsziel und Kontextwechsel spätestens etwa 32k oder bei wiederholt verdrängten Reads. Dies sind **Planungsbudgets, keine Messwerte**.

Für die frühere Sitzung sind tatsächliche Tokenbelegung, Reserve und effektives Backendfenster nicht gespeichert. Die frühere frei geschätzte Summe 12.500 und davon abgeleitete Reserve/Sicherheitsabstands- oder Konformitätsbehauptungen werden zurückgenommen. **Historische Einhaltung nicht nachweisbar**, keine nachträgliche Zertifizierung durch Dateizählung.

Zu Beginn dieser Fortsetzung lag eine echte vom System gelieferte Kompaktierungszusammenfassung vor. Sie belegt eine erfolgte Kompaktierung, aber weder Tokenzahl noch Kontextwechsel nach jedem früheren Paket. Für diese Fortsetzung bleiben Messwerte nicht verfügbar. Lektüre wird abschnittsweise begrenzt und benötigte Ergebnisse gepinnt; aktuelle Übergabe und State werden vor einem weiteren verfügbaren `compact_context`-Aufruf persistent verifiziert. Erst dessen tatsächliches Toolergebnis darf als neue Kompaktierung dokumentiert werden.

Keine Coddy-Konfiguration, Providerpolicy oder Backendfenster geändert. Qwen bleibt Zielmodell der Umsetzung; dessen verfügbares Fenster und tatsächliche Auslastung kann nur die Laufzeitoberfläche/Backendkonfiguration bestätigen. Fehlende historische Messungen erzeugen keine zusätzlichen Fach-/Reviewer-Abnahmen.
