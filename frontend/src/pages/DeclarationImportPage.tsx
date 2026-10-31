import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  createDeclarationImport,
  getDeclarationImport,
  listDeclarationDocuments,
  pollDeclarationImport,
  retryDeclarationImport,
  startDeclarationImport,
  updateDeclarationDocument,
  uploadDeclarationDocument,
} from "../api/endpoints";
import { ApiError } from "../api/client";
import { MAX_UPLOAD_BYTES } from "../lib/uploads";
import type {
  DeclarationDocument,
  DeclarationDocumentRole,
  DeclarationImport,
  DeclarationImportMode,
} from "../api/types";

const RETRYABLE_FAILURE_CODES = new Set([
  "PROVIDER_TIMEOUT",
  "PROVIDER_INCOMPLETE",
  "PROVIDER_CONNECTION_ERROR",
  "JOB_PRINCIPAL_UNVERIFIED",
  "JOB_PERMISSION_CHANGED",
  "PROVIDER_RATE_LIMIT",
  "PROVIDER_SERVER_ERROR",
  "OCR_TIMEOUT",
  "WORKER_INTERRUPTED",
]);

const FAILURE_MESSAGES: Record<string, string> = {
  ORGANIZATION_AI_BUDGET_EXCEEDED: "Das monatliche KI-Budget ist ausgeschöpft. Bitte wenden Sie sich an den Betreiber.",
  JOB_PRINCIPAL_UNVERIFIED: "Für diesen älteren Auftrag ist kein bestätigter Auslöser gespeichert. Ein berechtigter Benutzer kann ihn erneut starten.",
  JOB_PERMISSION_CHANGED: "Die Berechtigung des auslösenden Benutzers gilt nicht mehr. Ein berechtigter Benutzer kann den Import erneut starten.",
  PROVIDER_TIMEOUT: "Zeitüberschreitung beim Dienstleister. Bitte versuchen Sie den Import erneut.",
  PROVIDER_RATE_LIMIT: "Der Dienst ist gerade ausgelastet. Bitte versuchen Sie den Import erneut.",
  PROVIDER_SERVER_ERROR: "Der Dienstleister hat einen Serverfehler gemeldet. Bitte versuchen Sie es später erneut.",
  OCR_TIMEOUT: "Die Dokumentaufbereitung hat zu lange gedauert. Bitte versuchen Sie den Import erneut.",
  WORKER_INTERRUPTED: "Die Verarbeitung wurde unterbrochen. Bitte starten Sie den Import erneut.",
};

type UploadStatus = "ready" | "uploading" | "uploaded" | "error";

interface UploadItem {
  localId: string;
  file?: File;
  document?: DeclarationDocument;
  title: string;
  role: DeclarationDocumentRole;
  status: UploadStatus;
  error?: string;
}

function safeErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Der Vorgang konnte nicht abgeschlossen werden.";
}

function stageLabel(item: DeclarationImport | null): string {
  if (!item) return "Vorbereitung";
  if (item.status === "DRAFT") return "Dokumente vorbereiten";
  if (item.status === "NEEDS_REVIEW") return "Fachliche Prüfung";
  if (item.status === "FAILED") return "Verarbeitung fehlgeschlagen";
  if (item.status === "APPLIED") return "Übernommen";
  return item.current_stage || "Verarbeitung läuft";
}

function uploadStatusLabel(status: UploadStatus): string {
  return {
    ready: "Bereit zum Hochladen",
    uploading: "Wird hochgeladen",
    uploaded: "Hochgeladen",
    error: "Upload fehlgeschlagen",
  }[status];
}

function uploadedItem(document: DeclarationDocument): UploadItem {
  return {
    localId: `server-${document.id}`,
    document,
    title: document.title,
    role: document.document_role ?? document.doc_type,
    status: "uploaded",
  };
}

export function DeclarationImportPage() {
  const { propertyId, importId } = useParams<{ propertyId: string; importId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const [item, setItem] = useState<DeclarationImport | null>(null);
  const [documents, setDocuments] = useState<UploadItem[]>([]);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [loadedImportId, setLoadedImportId] = useState<string | null>(null);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [uploadingBatch, setUploadingBatch] = useState(false);
  const [reordering, setReordering] = useState(false);
  const queueKeys = useRef<Record<string, string>>({});
  const creationStarted = useRef(false);
  const reviewNavigated = useRef(false);
  const uploadInFlight = useRef(false);
  const reorderInFlight = useRef(false);

  const routeMode: DeclarationImportMode = propertyId ? "EXISTING_PROPERTY" : "NEW_PROPERTY";
  const isReviewRoute = location.pathname.endsWith("/review");
  const activeImportId = importId || item?.id;

  const applyImportUpdate = useCallback((updated: DeclarationImport) => {
    setItem(updated);
    if (updated.status === "NEEDS_REVIEW" && !isReviewRoute && !reviewNavigated.current) {
      reviewNavigated.current = true;
      navigate(`/teilungserklaerung/import/${updated.id}/review`, { replace: true });
    }
  }, [isReviewRoute, navigate]);

  useEffect(() => {
    if (importId) return;
    if (creationStarted.current) return;
    creationStarted.current = true;
    setCreating(true);
    createDeclarationImport(
      routeMode === "EXISTING_PROPERTY"
        ? { mode: "EXISTING_PROPERTY", target_property_id: propertyId! }
        : { mode: "NEW_PROPERTY" },
    )
      .then((created) => {
        applyImportUpdate(created);
        navigate(`/teilungserklaerung/import/${created.id}`, { replace: true });
      })
      .catch((error) => setStatusError(safeErrorMessage(error)))
      .finally(() => setCreating(false));
  }, [applyImportUpdate, importId, navigate, propertyId, routeMode]);

  useEffect(() => {
    if (!importId) return;
    let active = true;
    Promise.all([getDeclarationImport(importId), listDeclarationDocuments(importId)])
      .then(([loadedImport, loadedDocuments]) => {
        if (!active) return;
        applyImportUpdate(loadedImport);
        setDocuments(loadedDocuments.map(uploadedItem));
        setLoadedImportId(importId);
      })
      .catch((error) => {
        if (active) setStatusError(safeErrorMessage(error));
      });
    return () => { active = false; };
  }, [applyImportUpdate, importId]);

  useEffect(() => {
    if (!importId || loadedImportId !== importId) return;
    return pollDeclarationImport(importId, {
      intervalMs: 5_000,
      onUpdate: applyImportUpdate,
      onError: (error) => setStatusError(safeErrorMessage(error)),
    });
  }, [applyImportUpdate, importId, loadedImportId, pollGeneration]);

  const selectFiles = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    const errors: string[] = [];
    const accepted: UploadItem[] = [];
    for (const file of files) {
      const hasKnownMimeType = file.type.length > 0;
      const isPdf = hasKnownMimeType
        ? file.type === "application/pdf"
        : file.name.toLowerCase().endsWith(".pdf");
      if (!isPdf) {
        errors.push(`Nur PDF-Dateien können hochgeladen werden: „${file.name}“ ist keine PDF-Datei.`);
      } else if (file.size > MAX_UPLOAD_BYTES) {
        errors.push(`„${file.name}“ ist größer als maximal 25 MiB.`);
      } else {
        accepted.push({
          localId: `${file.name}-${file.size}-${crypto.randomUUID()}`,
          file,
          title: file.name,
          role: "DECLARATION",
          status: "ready",
        });
      }
    }
    setSelectionError(errors.join(" ") || null);
    if (accepted.length) setDocuments((current) => [...current, ...accepted]);
    event.target.value = "";
  };

  const uploadReadyDocuments = async () => {
    if (!activeImportId || loadedImportId !== activeImportId || item?.status !== "DRAFT" || uploadInFlight.current) return;
    const ready = documents.filter((document) => document.status === "ready" && document.file);
    if (!ready.length) return;
    uploadInFlight.current = true;
    setUploadingBatch(true);
    let nextSequence = Math.max(0, ...documents.map((document) => document.document?.sequence_no ?? 0)) + 1;
    try {
      for (const current of ready) {
        setDocuments((items) => items.map((document) => (
          document.localId === current.localId ? { ...document, status: "uploading", error: undefined } : document
        )));
        const formData = new FormData();
        const file = current.file!;
        const uploadFile = file.type
          ? file
          : new File([file], file.name, { type: "application/pdf", lastModified: file.lastModified });
        formData.append("file", uploadFile);
        formData.append("document_role", current.role);
        formData.append("sequence_no", String(nextSequence));
        nextSequence += 1;
        try {
          const uploaded = await uploadDeclarationDocument(activeImportId, formData);
          setDocuments((items) => items.map((document) => (
            document.localId === current.localId
              ? { ...document, document: uploaded, title: uploaded.title, role: uploaded.document_role ?? current.role, status: "uploaded" }
              : document
          )));
        } catch (error) {
          setDocuments((items) => items.map((document) => (
            document.localId === current.localId ? { ...document, status: "error", error: safeErrorMessage(error) } : document
          )));
        }
      }
    } finally {
      uploadInFlight.current = false;
      setUploadingBatch(false);
    }
  };

  const persistDocument = async (localId: string, change: { title?: string; role?: DeclarationDocumentRole }) => {
    const current = documents.find((document) => document.localId === localId);
    if (!activeImportId || item?.status !== "DRAFT" || !current?.document) return;
    setDocuments((items) => items.map((document) => (
      document.localId === localId
        ? { ...document, title: change.title ?? document.title, role: change.role ?? document.role }
        : document
    )));
    try {
      const updated = await updateDeclarationDocument(activeImportId, current.document.id, {
        title: change.title,
        document_role: change.role,
      });
      setDocuments((items) => items.map((document) => (
        document.localId === localId ? { ...document, document: updated, title: updated.title, role: updated.document_role ?? document.role } : document
      )));
    } catch (error) {
      setStatusError(safeErrorMessage(error));
    }
  };

  const reorderDocuments = async (localId: string, direction: -1 | 1) => {
    const currentIndex = documents.findIndex((document) => document.localId === localId);
    const targetIndex = currentIndex + direction;
    if (
      currentIndex < 0 || targetIndex < 0 || targetIndex >= documents.length || !activeImportId
      || item?.status !== "DRAFT" || reorderInFlight.current
      || documents[currentIndex].status !== "uploaded" || documents[targetIndex].status !== "uploaded"
    ) return;
    reorderInFlight.current = true;
    setReordering(true);
    const reordered = [...documents];
    [reordered[currentIndex], reordered[targetIndex]] = [reordered[targetIndex], reordered[currentIndex]];
    setDocuments(reordered);
    const uploaded = reordered.filter((document) => document.status === "uploaded" && document.document);
    const maxSequence = Math.max(0, ...uploaded.map((document) => document.document!.sequence_no ?? 0));
    try {
      for (let index = 0; index < uploaded.length; index += 1) {
        await updateDeclarationDocument(activeImportId, uploaded[index].document!.id, { sequence_no: maxSequence + uploaded.length + index + 1 });
      }
      for (let index = 0; index < uploaded.length; index += 1) {
        await updateDeclarationDocument(activeImportId, uploaded[index].document!.id, { sequence_no: index + 1 });
      }
      setDocuments((items) => items.map((document) => {
        const position = uploaded.findIndex((candidate) => candidate.localId === document.localId);
        return position >= 0 && document.document
          ? { ...document, document: { ...document.document, sequence_no: position + 1 } }
          : document;
      }));
    } catch (error) {
      const reorderError = safeErrorMessage(error);
      try {
        const persistedDocuments = await listDeclarationDocuments(activeImportId);
        setDocuments((items) => [
          ...persistedDocuments.map(uploadedItem),
          ...items.filter((document) => document.status !== "uploaded"),
        ]);
        setStatusError(`${reorderError} Die gespeicherte Reihenfolge wurde neu geladen.`);
      } catch {
        setStatusError(`${reorderError} Bitte laden Sie die Seite neu, bevor Sie die Reihenfolge erneut ändern.`);
      }
    } finally {
      reorderInFlight.current = false;
      setReordering(false);
    }
  };

  const startImport = async () => {
    if (!activeImportId) return;
    setStarting(true);
    setStatusError(null);
    try {
      const command = `${activeImportId}:start`;
      const key = queueKeys.current[command] ??= crypto.randomUUID();
      applyImportUpdate(await startDeclarationImport(activeImportId, key));
      delete queueKeys.current[command];
    } catch (error) {
      setStatusError(safeErrorMessage(error));
    } finally {
      setStarting(false);
    }
  };

  const retryImport = async () => {
    if (!activeImportId) return;
    setRetrying(true);
    setStatusError(null);
    try {
      const command = `${activeImportId}:retry`;
      const key = queueKeys.current[command] ??= crypto.randomUUID();
      applyImportUpdate(await retryDeclarationImport(activeImportId, key));
      delete queueKeys.current[command];
      setPollGeneration((generation) => generation + 1);
    } catch (error) {
      setStatusError(safeErrorMessage(error));
    } finally {
      setRetrying(false);
    }
  };

  const hasPendingUpload = documents.some((document) => document.status === "ready" || document.status === "uploading");
  const hasUploadedDocument = documents.some((document) => document.status === "uploaded");
  const canStart = loadedImportId === activeImportId && hasUploadedDocument && !hasPendingUpload && item?.status === "DRAFT";
  const failureMessage = item?.error_code ? FAILURE_MESSAGES[item.error_code] : undefined;
  const isRetryable = !!item?.error_code && RETRYABLE_FAILURE_CODES.has(item.error_code);
  const draftEditable = loadedImportId === activeImportId && item?.status === "DRAFT";
  const targetMode = item?.mode ?? routeMode;
  const targetPropertyId = item?.target_property_id ?? propertyId;

  return (
    <main className="declaration-import-page">
      <div className="declaration-import-heading">
        <div>
          <h1>Teilungserklärung einlesen</h1>
          <p className="empty-hint">Der Import erstellt zunächst nur einen prüfbaren Entwurf. Daten werden erst nach fachlicher Freigabe übernommen.</p>
        </div>
        {item && <span className="badge badge--draft">{stageLabel(item)}</span>}
      </div>

      {statusError && <p className="error-text" role="alert">{statusError}</p>}
      <p className="declaration-import-live" aria-live="polite">
        {creating ? "Importentwurf wird vorbereitet." : item ? `Importstatus: ${stageLabel(item)}.` : "Importentwurf wird vorbereitet."}
      </p>

      <ol className="declaration-import-steps" aria-label="Importschritte">
        <li>Ziel</li><li>Dokumente</li><li>Verarbeitung</li><li>Ergebnis</li>
      </ol>

      <section className="card declaration-import-section" aria-labelledby="declaration-target-heading">
        <h2 id="declaration-target-heading">1. Ziel</h2>
        {targetMode === "EXISTING_PROPERTY" ? (
          <p><strong>Nachtrag für das aktuelle Objekt einlesen</strong><br />Objekt-ID: {targetPropertyId}. Das Ziel ist über diese Seite festgelegt.</p>
        ) : (
          <p><strong>Neues Objekt aus einer Teilungserklärung anlegen</strong><br />Die erkannten Angaben werden später vor der Übernahme fachlich geprüft.</p>
        )}
      </section>

      <section className="card declaration-import-section" aria-labelledby="declaration-documents-heading">
        <h2 id="declaration-documents-heading">2. Dokumente</h2>
        <fieldset disabled={!activeImportId || creating || !draftEditable}>
          <legend>PDF-Dateien auswählen</legend>
          <label htmlFor="declaration-files">PDF-Dateien auswählen</label>
          <input id="declaration-files" aria-label="PDF-Dateien auswählen" type="file" accept="application/pdf,.pdf" multiple onChange={selectFiles} />
          <p className="empty-hint">Erlaubt sind PDFs bis maximal 25 MiB pro Datei. Die serverseitige Prüfung bleibt maßgeblich.</p>
        </fieldset>
        {selectionError && <p className="error-text" role="alert">{selectionError}</p>}

        {documents.length > 0 && (
          <div className="declaration-document-list" aria-label="Ausgewählte Dokumente">
            {documents.map((document, index) => (
              <article key={document.localId} className="declaration-document-item">
                <div className="declaration-document-main">
                  <label>
                    Dokumenttitel
                    <input
                      value={document.title}
                      disabled={document.status !== "uploaded" || !draftEditable}
                      onChange={(event) => setDocuments((items) => items.map((candidate) => candidate.localId === document.localId ? { ...candidate, title: event.target.value } : candidate))}
                      onBlur={() => void persistDocument(document.localId, { title: document.title })}
                    />
                  </label>
                  <label>
                    Dokumenttyp
                    <select
                      aria-label="Dokumenttyp"
                      value={document.role}
                      disabled={document.status !== "uploaded" || !draftEditable}
                      onChange={(event) => void persistDocument(document.localId, { role: event.target.value as DeclarationDocumentRole })}
                    >
                      <option value="DECLARATION">Teilungserklärung</option>
                      <option value="AMENDMENT">Nachtrag</option>
                      <option value="ANNEX">Anlage</option>
                      <option value="OTHER">Sonstiges</option>
                    </select>
                  </label>
                </div>
                <p className="empty-hint" aria-live="polite">{uploadStatusLabel(document.status)}{document.error ? `: ${document.error}` : ""}</p>
                <div className="form-actions declaration-document-actions">
                  {document.status === "ready" && <button type="button" className="btn btn--small" disabled={!draftEditable || uploadingBatch} onClick={() => setDocuments((items) => items.filter((candidate) => candidate.localId !== document.localId))}>Vor Upload entfernen</button>}
                  {document.status === "error" && <button type="button" className="btn btn--small" disabled={!draftEditable || uploadingBatch} onClick={() => setDocuments((items) => items.map((candidate) => candidate.localId === document.localId ? { ...candidate, status: "ready", error: undefined } : candidate))}>Upload erneut vorbereiten</button>}
                  <button type="button" className="btn btn--small" disabled={!draftEditable || reordering || index === 0 || document.status !== "uploaded" || documents[index - 1]?.status !== "uploaded"} aria-label={`${document.title} nach oben verschieben`} onClick={() => void reorderDocuments(document.localId, -1)}>Nach oben</button>
                  <button type="button" className="btn btn--small" disabled={!draftEditable || reordering || index === documents.length - 1 || document.status !== "uploaded" || documents[index + 1]?.status !== "uploaded"} aria-label={`${document.title} nach unten verschieben`} onClick={() => void reorderDocuments(document.localId, 1)}>Nach unten</button>
                </div>
              </article>
            ))}
          </div>
        )}
        <button type="button" className="btn" disabled={!draftEditable || uploadingBatch || !documents.some((document) => document.status === "ready") || !activeImportId} onClick={() => void uploadReadyDocuments()}>
          {uploadingBatch ? "PDFs werden hochgeladen…" : "Ausgewählte PDFs hochladen"}
        </button>
      </section>

      <section className="card declaration-import-section" aria-labelledby="declaration-processing-heading">
        <h2 id="declaration-processing-heading">3. Verarbeitung</h2>
        <p>Für die Extraktion werden die benötigten Dokumentabschnitte über die OpenAI API verarbeitet. Die Originaldateien bleiben dem Importentwurf zugeordnet und werden nicht automatisch als Stammdaten übernommen.</p>
        {item && <>
          <progress value={item.progress_percent} max="100">{item.progress_percent}%</progress>
          <p className="empty-hint">{item.progress_percent}% – {stageLabel(item)}. Sie können diese Seite schließen; der serverseitige Importstatus bleibt erhalten.</p>
        </>}
        {!canStart && item?.status === "DRAFT" && <p className="empty-hint">Import starten ist noch nicht möglich: Mindestens eine PDF muss vollständig hochgeladen sein und es dürfen keine Uploads ausstehen.</p>}
        <button type="button" className="btn btn--primary" disabled={!canStart || starting} onClick={() => void startImport()}>
          {starting ? "Import wird gestartet…" : "Import starten"}
        </button>
      </section>

      <section className="card declaration-import-section" aria-labelledby="declaration-result-heading">
        <h2 id="declaration-result-heading">4. Ergebnis</h2>
        {item?.status === "NEEDS_REVIEW" && <p><strong>Bereit für die fachliche Prüfung</strong><br />Die erkannten Angaben liegen als Entwurf zur Prüfung vor. Die eigentliche Prüfoberfläche folgt im nächsten Schritt.</p>}
        {item?.status === "APPLIED" && <p><strong>Übernommen</strong><br />Der freigegebene Entwurf wurde in das Objekt übernommen.</p>}
        {item?.status === "CANCELLED" && <><p><strong>Abgebrochen</strong><br />{failureMessage ?? "Der Import wurde abgebrochen."}</p>{isRetryable && <button type="button" className="btn btn--primary" disabled={retrying} onClick={() => void retryImport()}>{retrying ? "Wird erneut gestartet…" : "Erneut versuchen"}</button>}</>}
        {item?.status === "FAILED" && <>
          <p className="error-text" role="alert"><strong>Verarbeitung fehlgeschlagen.</strong><br />{failureMessage ?? item.error_message ?? "Der Import kann nicht fortgesetzt werden."}</p>
          {isRetryable && <button type="button" className="btn btn--primary" disabled={retrying} onClick={() => void retryImport()}>{retrying ? "Wird erneut gestartet…" : "Erneut versuchen"}</button>}
        </>}
        {!item || ["DRAFT", "QUEUED", "PREFLIGHT", "EXTRACTING", "RECONCILING", "RETRY_WAIT", "APPLYING"].includes(item.status) ? <p className="empty-hint">Noch kein abschließendes Ergebnis.</p> : null}
      </section>
    </main>
  );
}
