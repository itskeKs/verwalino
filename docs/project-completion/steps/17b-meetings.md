# 17b-meetings — Package 17

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 17a-dunning. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Agenda/Einladung/Proxy/Vote/Protokoll/Register mit V09-Methodengates; Beschluss aktiviert keine Regel automatisch.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/meeting_service.py`
- `backend/app/api/meetings.py`
- `backend/tests/test_m8_meetings.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**E5 — Meetings and immutable resolution history.** P4.5 after E4. Extend
`models/owners_meeting.py`, `agenda_item.py`, `resolution.py`,
`resolution_register_entry.py`, `services/meeting_service.py`, `meeting_pdf.py`,
`api/meetings.py`, `MeetingsPage.tsx`, `MeetingDetailPage.tsx`,
`ResolutionRegisterPage.tsx`; add **new** attendance/proxy/vote records as needed.
Build agenda/document → invitation → attendance/proxy → supported voting → minutes
→ register amendment journey. A resolution can propose a D2 rule interpretation,
never activate calculation automatically. Additive history migration; rollback
keeps numbered originals and append-only amendments. A22; authenticated voting
coverage absent. Extend `test_m8_meetings.py` with approved voting/proxy fixtures,
invalid proxy/duplicate vote, register sequence, invitation audience and correction
PDF. V09/V01 gate supported voting/circular-resolution methods and interpretation.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/17b-meetings.md` schreiben; in `../state.md`
`current: steps/18a-rent-adjustment.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/18a-rent-adjustment.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/18a-rent-adjustment.md` anhand von state.md und contract.md weiter.“
