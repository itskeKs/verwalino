import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { getMailDispatch, sendMailDispatch } from "../api/mail";
import type { MailAttempt, MailDispatchHistory, MailKind } from "../api/mail";
import { useAuth } from "../auth/useAuth";

interface Props { kind: MailKind; documentId: string }
interface PendingRequest { personId: string; requestId: string }
const labels: Record<MailAttempt["status"], string> = {
  accepted: "SMTP angenommen (kein Zustellnachweis)",
  failed: "Fehlgeschlagen",
  unknown: "Ausgang unklar – vor erneutem Versand prüfen",
  prepared: "Ausgang unklar – vor erneutem Versand prüfen (vorbereitet)",
};
function errorMessage(error: unknown): string {
  return error instanceof ApiError ? `HTTP ${error.status}: ${error.message}` : String(error);
}
function unresolved(attempt: MailAttempt) {
  return attempt.status === "prepared" || attempt.status === "unknown" || !attempt.completed_at;
}

export function MailDispatch(props: Props) {
  const { session, loading } = useAuth();
  if (loading) return <p role="status">Lade Versandberechtigung...</p>;
  if (!session) return null;
  const canSend = session.role === "OWNER_ADMIN" || session.role === "CLERK";
  // A new document/session must never inherit a selection or an idempotency key.
  return <MailDispatchPanel key={`${session.organization_id}:${session.user_id}:${props.kind}:${props.documentId}`} {...props} canSend={canSend} />;
}

function MailDispatchPanel({ kind, documentId, canSend }: Props & { canSend: boolean }) {
  const selectId = useId();
  const [data, setData] = useState<MailDispatchHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [personId, setPersonId] = useState("");
  const [pending, setPending] = useState<PendingRequest | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const acknowledged = useRef<MailAttempt[]>([]);

  const reload = useCallback(async () => {
    const current = ++generation.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setLoading(true);
    setLoadError(null);
    try {
      const history = await getMailDispatch(kind, documentId, abort.signal);
      if (!mounted.current || current !== generation.current) return;
      // Retain POST acknowledgements if a refreshed history has not caught up yet.
      const ids = new Set(history.attempts.map(attempt => attempt.id));
      setData({ ...history, attempts: [...history.attempts, ...acknowledged.current.filter(attempt => !ids.has(attempt.id))] });
    } catch (error) {
      if (!mounted.current || current !== generation.current) return;
      setLoadError(errorMessage(error));
    } finally {
      if (mounted.current && current === generation.current) setLoading(false);
    }
  }, [kind, documentId]);

  useEffect(() => {
    mounted.current = true;
    void reload();
    return () => { mounted.current = false; controller.current?.abort(); };
  }, [reload]);

  const recipient = data?.recipients.find(item => item.person_id === personId);
  const blocked = data?.attempts.some(attempt => attempt.person_id === personId && unresolved(attempt));
  const disabled = loading || !!loadError || sending || !recipient?.email?.trim() || blocked;

  async function send() {
    if (busy.current || disabled || !canSend || !recipient) return;
    busy.current = true;
    try {
      if (!window.confirm(`E-Mail für ${recipient.name} an ${recipient.email} versenden? Der Versuch wird lokal protokolliert. SMTP-Annahme ist kein Zustellnachweis; Zugang und Fristen gesondert prüfen.`)) return;
      const request = pending ?? { personId: recipient.person_id, requestId: crypto.randomUUID() };
      setPending(request);
      setSending(true);
      setSendError(null);
      try {
        const attempt = await sendMailDispatch(kind, documentId, request.personId, request.requestId);
        if (!mounted.current) return;
        acknowledged.current = [...acknowledged.current.filter(item => item.id !== attempt.id), attempt];
        setData(previous => previous && ({ ...previous, attempts: [...previous.attempts.filter(item => item.id !== attempt.id), attempt] }));
        setPending(null);
      } catch (error) {
        if (!mounted.current) return;
        // Keep the exact request for an explicit retry, never start a new attempt on a transport error.
        setSendError(errorMessage(error));
      }
      if (mounted.current) await reload();
    } catch (error) {
      if (mounted.current) setSendError(errorMessage(error));
    } finally {
      busy.current = false;
      if (mounted.current) setSending(false);
    }
  }

  return <section className="card" aria-label="E-Mail-Versand" style={{ marginBottom: "1.5rem" }}>
    <h2>E-Mail-Versand</h2>
    <p>Versandversuche werden lokal protokolliert. SMTP-Annahme ist kein Zustellnachweis. Zugang und Fristen sind gesondert zu prüfen.</p>
    {loading && <p role="status">Lade Versandhistorie...</p>}
    {loadError && <p role="alert" className="error-text">Versandhistorie konnte nicht geladen werden: {loadError}</p>}
    {data && (loading || loadError) && <p>Die angezeigte Versandhistorie ist möglicherweise veraltet. Versand ist bis zur Aktualisierung gesperrt.</p>}
    {sendError && <p role="alert" className="error-text">Versandversuch: {sendError} Vor einem erneuten Versuch die Historie prüfen.</p>}
    <button type="button" className="btn" disabled={loading || sending} onClick={() => void reload()}>Versandhistorie neu laden</button>
    {data && canSend && <div style={{ marginTop: "1rem" }}>
      <label htmlFor={selectId}>E-Mail-Empfänger</label>{" "}
      <select id={selectId} value={personId} disabled={loading || !!loadError || sending || !!pending} onChange={event => setPersonId(event.target.value)}>
        <option value="">Bitte Person auswählen</option>
        {data.recipients.map(item => <option key={item.person_id} value={item.person_id}>{item.name}</option>)}
      </select>
      {recipient && <p>E-Mail: {recipient.email || "Keine E-Mail-Adresse hinterlegt – Versand nicht möglich."}</p>}
      {!data.recipients.length && <p>Keine Empfänger für dieses Dokument vorhanden.</p>}
      {blocked && <p>Ausgang unklar – vor erneutem Versand prüfen</p>}
      <button type="button" className="btn btn--primary" disabled={!!disabled} onClick={() => void send()}>
        {sending ? "Versand läuft..." : pending ? "Denselben Versandversuch wiederholen" : "E-Mail-Versand bestätigen"}
      </button>
    </div>}
    <h3>Versandhistorie</h3>
    {data && !data.attempts.length && <p>Noch keine Versandversuche.</p>}
    {!!data?.attempts.length && <div className="table-responsive"><table className="data-table">
      <thead><tr><th>Person</th><th>E-Mail</th><th>Status</th><th>Vorbereitet am</th><th>Abgeschlossen am</th><th>Versand-PDF</th></tr></thead>
      <tbody>{[...data.attempts].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(attempt => <tr key={attempt.id}>
        <td>{data.recipients.find(item => item.person_id === attempt.person_id)?.name ?? attempt.person_id}</td>
        <td>{attempt.email}</td><td>{labels[attempt.status]}</td><td>{attempt.created_at}</td><td>{attempt.completed_at ?? "–"}</td>
        <td><a className="link" href={`/api/mail/${kind}/${encodeURIComponent(documentId)}/${encodeURIComponent(attempt.id)}/pdf`} target="_blank" rel="noopener">Versand-PDF öffnen</a></td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
