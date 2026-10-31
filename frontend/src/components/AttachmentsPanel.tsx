import { MAX_UPLOAD_BYTES } from "../lib/uploads";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { archiveAttachment, attachmentDownloadUrl, listAttachments, listProperties, uploadAttachment } from "../api/endpoints";
import { ApiError } from "../api/client";
import type { Attachment, AttachmentFilters, Property } from "../api/types";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

type Props = { canWrite: boolean; unitId?: string } & (
  { propertyId: string; personId?: string } | { propertyId?: undefined; personId: string }
);

const INLINE_TYPES = new Set(["application/pdf", "image/png", "image/jpeg"]);

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401: return "Nicht authentifiziert. Bitte erneut anmelden.";
      case 403: return "Keine Berechtigung für diese Dokumentaktion.";
      case 404: return "Dokument oder Zuordnung nicht verfügbar oder nicht zugänglich.";
      case 409: return "Dokument wurde bereits geändert oder seine Integrität konnte nicht bestätigt werden. Bitte Liste erneut laden.";
      case 413: return error.message;
      case 422: return "Ungültige Eingaben. Bitte Datei, Titel und Zuordnung prüfen.";
      default: return "Dokumentaktion fehlgeschlagen. Bitte erneut versuchen.";
    }
  }
  return "Netzwerkfehler. Bitte Verbindung prüfen und erneut versuchen.";
}

export function AttachmentsPanel(props: Props) {
  // Parent/role changes abandon all old drafts and asynchronous UI updates.
  return <AttachmentContext key={JSON.stringify([props.propertyId, props.unitId, props.personId, props.canWrite])} {...props} />;
}

function AttachmentContext({ propertyId, unitId, personId, canWrite }: Props) {
  const headingId = useId();
  const alive = useRef(false);
  const listVersion = useRef(0);
  const propertyVersion = useRef(0);
  const mutationLock = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<Attachment[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [propertiesLoading, setPropertiesLoading] = useState(false);
  const [propertiesError, setPropertiesError] = useState<string | null>(null);
  const [selectedProperty, setSelectedProperty] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<Attachment | null>(null);
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async (offset = 0) => {
    const version = ++listVersion.current;
    setLoading(true);
    setListError(null);
    if (offset === 0) { setItems([]); setNextOffset(null); }
    const filters: AttachmentFilters = { include_archived: includeArchived, offset, limit: 50 };
    if (propertyId) filters.property_id = propertyId;
    if (unitId) filters.unit_id = unitId;
    if (personId) filters.person_id = personId;
    try {
      const page = await listAttachments(filters);
      if (!alive.current || version !== listVersion.current) return;
      setItems((previous) => offset === 0 ? page.items : [...previous, ...page.items.filter((item) => !previous.some((old) => old.id === item.id))]);
      setNextOffset(page.next_offset);
    } catch (error) {
      if (alive.current && version === listVersion.current) setListError(errorMessage(error));
    } finally {
      if (alive.current && version === listVersion.current) setLoading(false);
    }
  }, [propertyId, unitId, personId, includeArchived]);

  const loadProperties = useCallback(async () => {
    const version = ++propertyVersion.current;
    setPropertiesLoading(true);
    setPropertiesError(null);
    try {
      const choices = await listProperties();
      if (alive.current && version === propertyVersion.current) setProperties(choices);
    } catch (error) {
      if (alive.current && version === propertyVersion.current) setPropertiesError(errorMessage(error));
    } finally {
      if (alive.current && version === propertyVersion.current) setPropertiesLoading(false);
    }
  }, []);

  const invalidateList = useCallback(() => { listVersion.current++; }, []);
  const invalidateProperties = useCallback(() => { propertyVersion.current++; }, []);
  const unmount = useCallback(() => {
    alive.current = false;
    invalidateList(); invalidateProperties();
  }, [invalidateList, invalidateProperties]);
  useEffect(() => { alive.current = true; return unmount; }, [unmount]);
  useEffect(() => { void load(); return invalidateList; }, [load, invalidateList]);
  useEffect(() => {
    if (!propertyId && canWrite) void loadProperties();
    return invalidateProperties;
  }, [propertyId, canWrite, loadProperties, invalidateProperties]);

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!canWrite || mutationLock.current) return;
    setStatus(null);
    setUploadError(null);
    const uploadProperty = propertyId || selectedProperty;
    if (!uploadProperty || (!propertyId && !properties.some((p) => p.id === uploadProperty))) {
      setUploadError("Bitte ein Objekt auswählen."); return;
    }
    if (!file || file.size === 0) { setUploadError("Bitte eine nicht leere Dokumentdatei auswählen."); return; }
    if (file.size > MAX_UPLOAD_BYTES) { setUploadError("Die Dokumentdatei darf höchstens 25 MiB groß sein."); return; }
    if (title.length > 255 || title.includes("\0")) { setUploadError("Der Dokumenttitel darf höchstens 255 Zeichen und kein Nullzeichen enthalten."); return; }
    mutationLock.current = true;
    setPending(true);
    try {
      await uploadAttachment(file, { property_id: uploadProperty, title, ...(unitId ? { unit_id: unitId } : {}), ...(personId ? { person_id: personId } : {}) });
      if (!alive.current) return;
      setFile(null); setTitle("");
      if (fileInput.current) fileInput.current.value = "";
      setStatus("Dokument hochgeladen.");
      void load();
    } catch (error) {
      if (alive.current) setUploadError(errorMessage(error));
    } finally {
      mutationLock.current = false;
      if (alive.current) setPending(false);
    }
  }

  function closeArchive() {
    if (mutationLock.current) return;
    setArchiveTarget(null); setReason(""); setArchiveError(null);
  }
  async function archive(event: React.FormEvent) {
    event.preventDefault();
    if (!canWrite || !archiveTarget || mutationLock.current) return;
    setStatus(null); setArchiveError(null);
    const trimmed = reason.trim();
    if (!trimmed || trimmed.length > 2000 || trimmed.includes("\0")) { setArchiveError("Bitte einen Archivierungsgrund mit 1 bis 2000 Zeichen angeben."); return; }
    mutationLock.current = true;
    setPending(true);
    try {
      await archiveAttachment(archiveTarget.id, trimmed);
      if (!alive.current) return;
      setArchiveTarget(null); setReason(""); setStatus("Dokument archiviert.");
      void load();
    } catch (error) {
      if (alive.current) setArchiveError(errorMessage(error));
    } finally {
      mutationLock.current = false;
      if (alive.current) setPending(false);
    }
  }

  return <section className="card" aria-labelledby={headingId} style={{ marginBottom: "1.5rem" }}>
    <h2 id={headingId}>Dokumente</h2>
    <p>Originale bleiben erhalten. Archivieren löscht keine Datei. Maximal 25 MiB je Dokument.</p>
    {status && <p role="status">{status}</p>}
    {canWrite && <form onSubmit={upload} noValidate aria-label="Dokument hochladen" className="form-grid">
      {!propertyId && <>
        <label>Objekt *<select required value={selectedProperty} disabled={pending || propertiesLoading || !!propertiesError} onChange={(e) => {
          if (mutationLock.current) return;
          setSelectedProperty(e.target.value); setUploadError(null); setStatus(null);
        }}>
          <option value="">Bitte Objekt auswählen</option>
          {properties.map((property) => <option key={property.id} value={property.id}>{[property.name, property.street, [property.zip_code, property.city].filter(Boolean).join(" ")].filter(Boolean).join(" · ")}</option>)}
        </select></label>
        {propertiesLoading && <p role="status">Lade Objekte…</p>}
        {propertiesError && <div role="alert">{propertiesError} <button type="button" className="btn" onClick={() => void loadProperties()} disabled={pending}>Objekte erneut laden</button></div>}
        {!propertiesLoading && !propertiesError && properties.length === 0 && <p>Keine zugänglichen Objekte für den Upload vorhanden.</p>}
      </>}
      <label>Dokumentdatei<input ref={fileInput} type="file" required disabled={pending} onChange={(e) => {
        if (mutationLock.current) return;
        setFile(e.target.files?.[0] ?? null); setUploadError(null); setStatus(null);
      }} /></label>
      <label>Dokumenttitel<input type="text" maxLength={255} value={title} disabled={pending} onChange={(e) => {
        if (mutationLock.current) return;
        setTitle(e.target.value); setUploadError(null); setStatus(null);
      }} /></label>
      {uploadError && <p role="alert" className="error-text">{uploadError}</p>}
      <button type="submit" className="btn btn--primary" disabled={pending || (!propertyId && (propertiesLoading || !!propertiesError || properties.length === 0))}>Dokument hochladen</button>
    </form>}
    <label><input type="checkbox" checked={includeArchived} disabled={pending} onChange={(e) => {
      if (mutationLock.current) return;
      setIncludeArchived(e.target.checked); setStatus(null);
    }} /> Archivierte anzeigen</label>
    {loading && <p role="status">Lade Dokumente…</p>}
    {listError && <div role="alert">{listError} <button type="button" className="btn" onClick={() => void load()} disabled={pending || loading}>Erneut versuchen</button></div>}
    {!loading && !listError && items.length === 0 && <p>Keine Dokumente vorhanden.</p>}
    {items.length > 0 && <ul aria-label="Dokumentenliste">
      {items.map((item) => <li key={item.id}>
        <strong>{item.title || item.original_filename}</strong>
        {item.title && <span> · {item.original_filename}</span>}
        <span> · {new Intl.NumberFormat("de-DE").format(item.content_byte_size)} Bytes · {new Date(item.created_at).toLocaleDateString("de-DE")}</span>
        {item.archived_at && <span> · Archiviert: {item.archive_reason}</span>}
        <div className="form-actions">
          <button type="button" className="btn" onClick={() => window.open(attachmentDownloadUrl(item.id, INLINE_TYPES.has(item.mime_type)), "_blank", "noopener,noreferrer")}>Öffnen</button>
          {canWrite && !item.archived_at && <button type="button" className="btn" disabled={pending} onClick={() => {
            if (mutationLock.current) return;
            setArchiveTarget(item); setReason(""); setArchiveError(null); setStatus(null);
          }}>Archivieren</button>}
        </div>
      </li>)}
    </ul>}
    {nextOffset !== null && !listError && <button type="button" className="btn" disabled={loading || pending} onClick={() => { if (!loading) void load(nextOffset); }}>Weitere Dokumente laden</button>}
    <Dialog open={archiveTarget !== null} onOpenChange={(open) => { if (!open) closeArchive(); }}>
      <DialogContent onEscapeKeyDown={(event) => { if (mutationLock.current) event.preventDefault(); }} onPointerDownOutside={(event) => { if (mutationLock.current) event.preventDefault(); }}>
        <DialogTitle>Dokument archivieren</DialogTitle>
        <DialogDescription>{archiveTarget?.title || archiveTarget?.original_filename} wird archiviert. Das Original bleibt erhalten.</DialogDescription>
        <form onSubmit={archive} noValidate>
          <label>Archivierungsgrund<textarea required maxLength={2000} value={reason} disabled={pending} onChange={(e) => {
            if (mutationLock.current) return;
            setReason(e.target.value); setArchiveError(null);
          }} /></label>
          {archiveError && <p role="alert" className="error-text">{archiveError}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn--primary" disabled={pending}>Dokument archivieren</button>
            <button type="button" className="btn" disabled={pending} onClick={closeArchive}>Abbrechen</button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </section>;
}
