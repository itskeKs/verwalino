import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { ApiError } from "../api/client";
import {
  applyMasterImport, previewMasterImport, readMasterImportFile,
  type MasterImportApplyInput, type MasterImportInput, type MasterImportPreview,
  type MasterImportResult, type MasterImportCounts,
} from "../api/masterImport";

const CSV_HEADER = "kind;key;property_key;name;street;zip_code;city;management_type;number;area;heated_area;usage_type;first_name;last_name;email;phone;company_name";

function countsText(counts: MasterImportCounts) {
  return `${counts.properties} Objekte · ${counts.units} Einheiten · ${counts.persons} Kontakte`;
}

export function MasterImport({ onImported }: { onImported: () => void }) {
  const { session } = useAuth();
  const isWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [csvText, setCsvText] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");
  const [withAudience, setWithAudience] = useState(false);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<MasterImportPreview | null>(null);
  const [result, setResult] = useState<MasterImportResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<"file" | "preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [applyStarted, setApplyStarted] = useState(false);
  const generation = useRef(0);
  const busyRef = useRef(false);
  const request = useRef<AbortController | null>(null);
  const previewInput = useRef<MasterImportInput | null>(null);
  const pendingApply = useRef<MasterImportApplyInput | null>(null);

  useEffect(() => () => {
    generation.current += 1;
    request.current?.abort();
  }, []);

  function invalidate() {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    busyRef.current = false;
    pendingApply.current = null;
    previewInput.current = null;
    setBusy(null);
    setPreview(null);
    setResult(null);
    setConfirmed(false);
    setError(null);
    setUncertain(false);
    setApplyStarted(false);
    return generation.current;
  }

  async function selectFile(file: File | undefined) {
    const current = invalidate();
    setCsvText(null);
    setFileName(file?.name ?? "");
    if (!file) return;
    busyRef.current = true;
    setBusy("file");
    try {
      const text = await readMasterImportFile(file);
      if (generation.current === current) setCsvText(text);
    } catch (failure) {
      if (generation.current === current) setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      if (generation.current === current) { busyRef.current = false; setBusy(null); }
    }
  }

  const audienceValid = !withAudience || (session?.role === "OWNER_ADMIN" && !!reason.trim());

  async function loadPreview() {
    if (!isWrite || busyRef.current || csvText === null || !audienceValid || pendingApply.current) return;
    const current = invalidate();
    const controller = new AbortController();
    request.current = controller;
    busyRef.current = true;
    setBusy("preview");
    const input: MasterImportInput = {
      csv_text: csvText,
      data_audience: withAudience && session?.role === "OWNER_ADMIN" ? {
        label: "CSV-Stammdatenimport", reason: reason.trim(), grants: [{
          user_id: session.user_id, capabilities: ["read", "prepare", "export"], reason: reason.trim(),
        }],
      } : null,
    };
    try {
      const response = await previewMasterImport(input, controller.signal);
      if (generation.current !== current || controller.signal.aborted) return;
      previewInput.current = input;
      setPreview(response);
    } catch (failure) {
      if (generation.current === current && !controller.signal.aborted) setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      if (generation.current === current) { busyRef.current = false; setBusy(null); }
    }
  }

  async function apply() {
    if (!isWrite || busyRef.current || !confirmed || !preview || preview.errors.length || !preview.rows.length || !previewInput.current || result) return;
    // Keep this exact request after a lost/5xx reply: the server may already have committed it.
    const input = pendingApply.current ?? {
      ...previewInput.current, expected_digest: preview.source_digest, request_id: crypto.randomUUID(),
    };
    pendingApply.current = input;
    setApplyStarted(true);
    const current = generation.current;
    const controller = new AbortController();
    request.current = controller;
    busyRef.current = true;
    setBusy("apply");
    setError(null);
    setUncertain(false);
    try {
      const response = await applyMasterImport(input, controller.signal);
      if (generation.current !== current || controller.signal.aborted) return;
      setResult(response);
    } catch (failure) {
      if (generation.current === current && !controller.signal.aborted) {
        setError(failure instanceof Error ? failure.message : String(failure));
        setUncertain(!(failure instanceof ApiError) || failure.status >= 500);
      }
      return;
    } finally {
      if (generation.current === current) { busyRef.current = false; setBusy(null); }
    }
    if (generation.current === current && !controller.signal.aborted) onImported();
  }

  function resultPath(row: MasterImportResult["rows"][number]): string | null {
    if (row.kind === "property") return `/objekte/${row.id}`;
    if (row.kind === "person") return `/personen/${row.id}`;
    const propertyKey = preview?.rows.find(item => item.kind === "unit" && item.key === row.key)?.values.property_key;
    const propertyId = result?.rows.find(item => item.kind === "property" && item.key === propertyKey)?.id ?? propertyKey;
    return propertyId ? `/objekte/${propertyId}/einheiten/${row.id}` : null;
  }

  if (!isWrite) return null;
  return <section className="card" aria-label="CSV-Stammdatenimport" style={{ marginBottom: "1rem" }}>
    <h2>CSV-Stammdatenimport</h2>
    <p>Objekte (property), Einheiten (unit) und Kontakte (person) neu anlegen. Bestehende Datensätze werden nicht verändert.
      Kontakte werden ausschließlich als Personen angelegt, ohne Profile, Mietverträge oder geerbte Objektfreigaben.</p>
    <p>UTF-8 (BOM erlaubt), höchstens 256 KiB und 500 Datenzeilen. Semikolon als Trennzeichen, exakte Kopfzeile wie in der Vorlage.
      key muss je Datei eindeutig sein. property_key verweist bei Einheiten auf den CSV-Schlüssel eines Objekts oder ausdrücklich auf die UUID eines bestehenden Objekts; bei Kontakten bleibt es leer.
      Flächen als einfache Dezimalzahlen mit Komma oder Punkt, ohne Tausendertrennzeichen.</p>
    <details><summary>CSV-Kopfzeile anzeigen</summary><code style={{ overflowWrap: "anywhere" }}>{CSV_HEADER}</code></details>
    <p><a className="btn" href="/api/properties/import/template.csv" download="master-data-template.csv">CSV-Vorlage herunterladen</a></p>
    <label>CSV-Datei
      <input type="file" accept=".csv,text/csv" onChange={event => void selectFile(event.target.files?.[0])} />
    </label>
    {fileName && <p>Ausgewählte Datei: {fileName}</p>}
    {session?.role === "OWNER_ADMIN" && <fieldset>
      <legend>Datenfreigabe für neue Objekte</legend>
      <label style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start" }}>
        <input type="checkbox" checked={withAudience} disabled={busy === "file"} onChange={event => { invalidate(); setWithAudience(event.target.checked); }} />
        <span>Datenbereich anlegen und mir Lesen, Bearbeiten und Exportieren erlauben</span>
      </label>
      {withAudience && <label>Begründung der Freigabe
        <input required maxLength={2000} value={reason} disabled={busy === "file"} onChange={event => { invalidate(); setReason(event.target.value); }} />
      </label>}
      <p className="muted">Nur nach ausdrücklicher Auswahl für neue Objekte; keine automatische Freigabe für Kontakte oder bestehende Objekte.
        Weitere Teamfreigaben unter Gebäudedaten-Rechte einrichten. Für Echtdaten kann der Server eine Datenfreigabe verlangen.</p>
    </fieldset>}
    {session?.role === "CLERK" && <p className="muted">Keine administrative Selbstfreigabe. Für Echtdaten kann eine durch die Administration eingerichtete Datenfreigabe erforderlich sein.</p>}
    <p>Änderungen an Datei oder Datenfreigabe verwerfen Vorschau und Wiederholungsschlüssel. Bei unklarem Importausgang zuerst denselben Import erneut versuchen, statt die Eingaben zu ändern.</p>
    <button type="button" className="btn" disabled={!!busy || csvText === null || !audienceValid || applyStarted || !!result} onClick={() => void loadPreview()}>Vorschau prüfen</button>
    {busy && <p role="status">{busy === "file" ? "CSV-Datei wird gelesen…" : busy === "preview" ? "Vorschau wird geprüft…" : "Import läuft…"}</p>}
    {error && <div role="alert" className="error-text"><p>{error}</p>
      {uncertain && <p>Der Importausgang ist unklar. Erneut versuchen verwendet dieselbe Anfrage und denselben Wiederholungsschlüssel; es wird kein zweiter Import gestartet.</p>}
    </div>}
    {preview && !result && <section aria-label="Importvorschau">
      <h3>Vorschau</h3><p>{countsText(preview.counts)}</p>
      <div style={{ overflowX: "auto" }}><table className="data-table" aria-label="CSV-Vorschau">
        <thead><tr><th>Zeile</th><th>Art</th><th>Schlüssel</th><th>Werte</th></tr></thead>
        <tbody>{preview.rows.map(row => <tr key={`${row.line}-${row.kind}-${row.key}`}>
          <td>{row.line}</td><td>{row.kind}</td><td>{row.key}</td>
          <td><dl>{Object.entries(row.values).map(([field, value]) => <div key={field}><dt>{field}</dt><dd>{value}</dd></div>)}</dl></td>
        </tr>)}</tbody>
      </table></div>
      {preview.errors.length > 0 ? <section aria-label="Zeilenfehler"><h3>Zeilenfehler</h3>
        <ul>{preview.errors.map((row, index) => <li key={index}>Zeile {row.line} · {row.field}: {row.message}</li>)}</ul>
        <p>Bitte die CSV-Datei korrigieren und erneut auswählen. Import ist bei Zeilenfehlern gesperrt.</p>
      </section> : preview.rows.length > 0 ? <>
        <label style={{ display: "flex", gap: "0.5rem", margin: "1rem 0" }}>
          <input type="checkbox" checked={confirmed} disabled={busy === "apply"} onChange={event => setConfirmed(event.target.checked)} />
          <span>Ich bestätige, dass die geprüften CSV-Zeilen als neue Stammdaten importiert werden sollen.</span>
        </label>
        <button type="button" className="btn btn--primary" disabled={!confirmed || !!busy} onClick={() => void apply()}>
          {applyStarted ? "Import erneut versuchen" : "Stammdaten importieren"}
        </button>
      </> : <p>Keine importierbaren Zeilen vorhanden.</p>}
    </section>}
    {result && <section aria-label="Importergebnis"><h3>Import abgeschlossen</h3><p>{countsText(result.counts)}</p>
      <ul>{result.rows.map(row => {
        const path = resultPath(row);
        const label = `${row.kind} ${row.key}: ${row.id}`;
        return <li key={`${row.kind}-${row.key}`}>Zeile {row.line} · {path ? <Link to={path}>{label}</Link> : label}</li>;
      })}</ul>
    </section>}
  </section>;
}
