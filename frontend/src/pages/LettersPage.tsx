import { useEffect, useRef, useState } from "react";
import { listProperties } from "../api/endpoints";
import {
  createLetterTemplate, issueLetters, listLetterDocuments, listLetterTemplates, updateLetterTemplate,
  type LetterDocument, type LetterTemplate, type LetterTemplateInput,
} from "../api/letters";
import type { Property } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../components/ui/dialog";

const placeholders = ["{{mieter.name}}", "{{mieter.adresse}}", "{{objekt.adresse}}", "{{objekt.name}}", "{{datum}}",
  "{{vermieter.name}}", "{{vermieter.adresse}}", "{{einheit.nummer}}", "{{einheit.lage}}", "{{einheit.flaeche}}",
  "{{vertrag.beginn}}", "{{vertrag.ende}}", "{{vertrag.kaltmiete}}", "{{vertrag.vorauszahlung}}", "{{vertrag.kaution}}",
  "{{vertrag.uebergabe}}", "{{vertrag.vereinbarungen}}"];
const emptyTemplate = (): LetterTemplateInput => ({ name: "", subject: "", body: "" });
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function LettersPage() {
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [templates, setTemplates] = useState<LetterTemplate[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [propertyId, setPropertyId] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [asOf, setAsOf] = useState("");
  const [historyReload, setHistoryReload] = useState(0);
  const [history, setHistory] = useState<{ propertyId: string; documents: LetterDocument[]; loading: boolean; error: string | null }>({ propertyId: "", documents: [], loading: false, error: null });
  const [editor, setEditor] = useState<{ id: string | null; form: LetterTemplateInput } | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [issueError, setIssueError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"save" | "issue" | null>(null);
  const lock = useRef<"save" | "issue" | null>(null);
  const requestId = useRef<string | null>(null);
  const alive = useRef(false);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    Promise.all([listProperties(), listLetterTemplates(controller.signal)])
      .then(([nextProperties, nextTemplates]) => {
        if (active) { setProperties(nextProperties); setTemplates(nextTemplates); }
      })
      .catch(error => { if (active) setLoadError(message(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [reload]);

  useEffect(() => {
    if (!propertyId) return;
    let active = true;
    const controller = new AbortController();
    listLetterDocuments(propertyId, controller.signal)
      .then(documents => { if (active) setHistory({ propertyId, documents, loading: false, error: null }); })
      .catch(error => { if (active) setHistory({ propertyId, documents: [], loading: false, error: message(error) }); });
    return () => { active = false; controller.abort(); };
  }, [propertyId, historyReload]);

  const template = templates.find(item => item.id === templateId);
  const property = properties.find(item => item.id === propertyId);
  const ready = canWrite && !!property && !!template && /^\d{4}-\d{2}-\d{2}$/.test(asOf) && !busy && !editor && !loading && !loadError;
  function invalidateIssue() {
    requestId.current = null; setIssueError(null); setResult(null); setConfirming(false);
  }
  function changeProperty(id: string) {
    if (lock.current) return;
    invalidateIssue(); setPropertyId(id);
    setHistory({ propertyId: id, documents: [], loading: !!id, error: null });
  }
  function openEditor(id: string | null) {
    if (!canWrite || lock.current) return;
    setConfirming(false); setSaveError(null);
    setEditor({ id, form: id && template ? { name: template.name, subject: template.subject, body: template.body } : emptyTemplate() });
  }
  async function saveTemplate(event: React.FormEvent) {
    event.preventDefault();
    if (!canWrite || !editor || lock.current) return;
    lock.current = "save"; setBusy("save"); setSaveError(null);
    try {
      const saved = editor.id ? await updateLetterTemplate(editor.id, editor.form) : await createLetterTemplate(editor.form);
      if (!alive.current) return;
      setTemplates(items => [...items.filter(item => item.id !== saved.id), saved]);
      setTemplateId(saved.id); setEditor(null); invalidateIssue();
    } catch (error) {
      if (alive.current) setSaveError(message(error));
    } finally {
      lock.current = null; if (alive.current) setBusy(null);
    }
  }
  async function issue() {
    if (!ready || !confirming || lock.current) return;
    lock.current = "issue"; setBusy("issue"); setIssueError(null); setResult(null);
    requestId.current ??= crypto.randomUUID();
    try {
      const documents = await issueLetters({ request_id: requestId.current, property_id: propertyId, template_id: templateId, as_of: asOf });
      if (!alive.current) return;
      requestId.current = null; setConfirming(false);
      setResult(documents.length ? `${documents.length} Briefe erstellt. Die PDFs bleiben unverändert gespeichert.` : "Keine aktuellen Mieter am gewählten Stichtag; keine Briefe erstellt.");
      setHistory(previous => ({ propertyId, documents: [...documents, ...previous.documents.filter(item => !documents.some(next => next.id === item.id))], loading: false, error: null }));
      setHistoryReload(value => value + 1);
    } catch (error) {
      if (!alive.current) return;
      // A network/5xx failure may have happened after commit: retry with the same key.
      const uncertain = !(error instanceof ApiError) || error.status >= 500 || error.status === 408;
      if (!uncertain) requestId.current = null;
      setConfirming(false);
      setIssueError(`${message(error)}${uncertain ? " Ergebnis unklar. Bei unveränderten Eingaben wird derselbe Auftrag erneut abgefragt; bitte erneut bestätigen. Änderungen starten einen neuen Auftrag." : ""}`);
    } finally {
      lock.current = null; if (alive.current) setBusy(null);
    }
  }

  return <div>
    <h1>Briefe</h1>
    <p>Vorlagen verwalten und Serienbriefe als PDF für alle am Stichtag aktuellen Mieter eines Objekts erstellen. Jede Person erhält genau einen Brief, auch bei mehreren Mietverhältnissen. Kein E-Mail-Versand.</p>
    {loading && <p role="status">Vorlagen und Objekte werden geladen…</p>}
    {loadError && <div role="alert"><p className="error-text">{loadError}</p><button className="btn" disabled={!!busy || loading} onClick={() => { setLoading(true); setLoadError(null); setReload(value => value + 1); }}>Erneut laden</button></div>}
    <section className="card" style={{ marginBottom: "1rem" }} aria-label="Briefauswahl">
      <div className="form-grid">
        <label>Objekt *<select aria-label="Objekt *" value={propertyId} disabled={!!busy || loading || !!loadError} onChange={event => changeProperty(event.target.value)}>
          <option value="">-- Objekt wählen --</option>{properties.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label>Vorlage *<select aria-label="Vorlage *" value={templateId} disabled={!!busy || !!editor || loading || !!loadError} onChange={event => { if (!lock.current) { invalidateIssue(); setTemplateId(event.target.value); } }}>
          <option value="">-- Vorlage wählen --</option>{templates.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label>Stichtag *<input type="date" aria-label="Stichtag *" required value={asOf} disabled={!!busy} onChange={event => { if (!lock.current) { invalidateIssue(); setAsOf(event.target.value); } }} /></label>
      </div>
      {!loading && !loadError && templates.length === 0 && <p>Noch keine Vorlagen vorhanden.</p>}
      {canWrite ? <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "1rem" }}>
        <button className="btn" disabled={!!busy || !!editor || loading || !!loadError} onClick={() => openEditor(null)}>Neue Vorlage</button>
        <button className="btn" disabled={!template || !!busy || !!editor || loading || !!loadError} onClick={() => openEditor(templateId)}>Vorlage bearbeiten</button>
        <button className="btn btn--primary" disabled={!ready} onClick={() => { if (ready && !lock.current) setConfirming(true); }}>Briefe erstellen</button>
      </div> : <p>Nur Lesezugriff. Vorlagen und bereits erstellte Briefe können eingesehen werden.</p>}
    </section>

    <section className="card" style={{ marginBottom: "1rem" }} aria-label="Platzhalterhilfe">
      <h2>Platzhalter und Klartext</h2>
      <p>Diese Platzhalter exakt einschließlich der doppelten geschweiften Klammern in Betreff oder Brieftext verwenden:</p>
      <ul>{placeholders.map(value => <li key={value}><code>{value}</code></li>)}</ul>
      <p>Das Datum ist der gewählte Stichtag. Kein HTML und keine Skripte: Alle Inhalte werden ausschließlich als Klartext behandelt. Andere Platzhalter werden nicht unterstützt.</p>
      <p>Vertragsdaten beziehen sich auf den ausgewählten Originalvertrag des Empfängers. Bei mehreren Verträgen wird nur einer verwendet. Übergabedatum und Vereinbarungen werden über „Mietvertrag erstellen“ in der Einheit ergänzt. Vorlage; rechtliche Prüfung erforderlich.</p>
      <p>Änderungen an einer gespeicherten Vorlage gelten nur für neue Briefe. Bereits erstellte PDFs bleiben unverändert.</p>
    </section>

    {canWrite && editor && <section className="card" style={{ marginBottom: "1rem" }}>
      <h2>{editor.id ? "Gespeicherte Vorlage bearbeiten" : "Vorlage anlegen"}</h2>
      <form onSubmit={saveTemplate}>
        <fieldset disabled={!!busy} style={{ border: 0, padding: 0 }} className="form-grid">
          <label>Vorlagenname *<input required value={editor.form.name} onChange={event => setEditor({ ...editor, form: { ...editor.form, name: event.target.value } })} /></label>
          <label>Betreff *<input required value={editor.form.subject} onChange={event => setEditor({ ...editor, form: { ...editor.form, subject: event.target.value } })} /></label>
          <label style={{ gridColumn: "1 / -1" }}>Brieftext *<textarea required rows={10} value={editor.form.body} onChange={event => setEditor({ ...editor, form: { ...editor.form, body: event.target.value } })} /></label>
        </fieldset>
        {saveError && <p className="error-text" role="alert">{saveError}</p>}
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
          <button className="btn btn--primary" disabled={!!busy || !editor.form.name.trim() || !editor.form.subject.trim() || !editor.form.body.trim()} type="submit">{busy === "save" ? "Speichert…" : "Vorlage speichern"}</button>
          <button className="btn" type="button" disabled={!!busy} onClick={() => { if (!lock.current) { setEditor(null); setSaveError(null); } }}>Abbrechen</button>
        </div>
      </form>
    </section>}
    {template && !editor && <section className="card" style={{ marginBottom: "1rem" }} aria-label="Gespeicherte Vorlage">
      <h2>Gespeicherte Vorlage: {template.name}</h2>
      <p>Betreff: {template.subject}</p><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontFamily: "inherit" }}>{template.body}</pre>
    </section>}
    {issueError && <p className="error-text" role="alert">{issueError}</p>}
    {result && <p role="status">{result}</p>}

    <section className="card" aria-label="Briefhistorie">
      <h2>Erstellte Briefe</h2>
      {!propertyId ? <p>Bitte ein Objekt wählen, um die gespeicherten Briefe zu sehen.</p> : history.propertyId === propertyId && <>
        {history.loading && <p role="status">Historie wird geladen…</p>}
        {history.error && <div role="alert"><p className="error-text">{history.error}</p><button className="btn" disabled={!!busy} onClick={() => { setHistory({ propertyId, documents: [], loading: true, error: null }); setHistoryReload(value => value + 1); }}>Historie erneut laden</button></div>}
        {!history.loading && !history.error && history.documents.length === 0 && <p>Noch keine Briefe für dieses Objekt.</p>}
        {history.documents.length > 0 && <div style={{ overflowX: "auto" }}><table className="data-table"><thead><tr><th>Empfänger</th><th>Betreff</th><th>Stichtag</th><th>Erstellt</th><th>Dokument</th></tr></thead><tbody>
          {history.documents.map(item => <tr key={item.id}><td>{item.recipient_name}</td><td>{item.subject}</td><td>{item.as_of}</td><td>{new Date(item.created_at).toLocaleString("de-DE")}</td><td><a href={item.pdf_url} target="_blank" rel="noopener noreferrer" aria-label={`PDF für ${item.recipient_name}`}>PDF öffnen</a></td></tr>)}
        </tbody></table></div>}
      </>}
    </section>

    <Dialog open={confirming} onOpenChange={open => { if (!lock.current) setConfirming(open); }}>
      <DialogContent onEscapeKeyDown={event => { if (lock.current) event.preventDefault(); }} onPointerDownOutside={event => { if (lock.current) event.preventDefault(); }}>
        <DialogHeader><DialogTitle>Briefe verbindlich erstellen?</DialogTitle><DialogDescription>Für alle am Stichtag aktuellen Mieter wird je Person ein unveränderliches PDF gespeichert. Es wird nichts versendet.</DialogDescription></DialogHeader>
        <p>Objekt: {property?.name} · Vorlage: {template?.name} · Stichtag: {asOf}</p>
        <DialogFooter><button className="btn" disabled={!!busy} onClick={() => { if (!lock.current) setConfirming(false); }}>Abbrechen</button><button className="btn btn--primary" disabled={!ready} onClick={() => void issue()}>{busy === "issue" ? "Erstellt…" : "Jetzt erstellen"}</button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}
