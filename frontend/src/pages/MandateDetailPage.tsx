/**
 * Mandate detail -- Stammdaten des Mandats + Dokumente (Verträge/Unterlagen).
 *
 * Dokumente: Upload (Schreibrolle), Liste mit Download und Löschen. Belegt z. B.
 * Verwaltervertrag, Bankkonto-Unterlagen, Vollmachten, Versicherungen.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Upload, Download, Trash2, FileText } from "lucide-react";
import {
  getMandate,
  updateMandate,
  listMandateDocuments,
  uploadMandateDocument,
  mandateDocumentDownloadUrl,
  deleteMandateDocument,
  archiveMandateDocument,
} from "../api/endpoints";
import type { Mandate, MandateDocument, MandateDocType } from "../api/types";
import { MasterDataEditor } from "../components/MasterDataEditor";
import { SearchChoice } from "../components/SearchChoice";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Card, CardHeader, CardTitle, CardContent } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge, type BadgeTone } from "../components/ui/badge";

const MANDATE_TYPE_LABELS: Record<Mandate["mandate_type"], string> = {
  WEG: "WEG-Verwaltung",
  MIET: "Mietverwaltung",
  SEV: "Sondereigentum (SEV)",
};

const DOC_TYPE_OPTIONS: { value: MandateDocType; label: string }[] = [
  { value: "VERWALTERVERTRAG", label: "Verwaltervertrag" },
  { value: "BANKKONTO", label: "Bankkonto-Unterlagen" },
  { value: "VOLLMACHT", label: "Vollmacht" },
  { value: "VERSICHERUNG", label: "Versicherung" },
  { value: "SONSTIGES", label: "Sonstiges" },
];

const DOC_TYPE_LABELS: Record<MandateDocType, string> = {
  VERWALTERVERTRAG: "Verwaltervertrag",
  BANKKONTO: "Bankkonto-Unterlagen",
  VOLLMACHT: "Vollmacht",
  VERSICHERUNG: "Versicherung",
  SONSTIGES: "Sonstiges",
};

const DOC_TYPE_TONES: Record<MandateDocType, BadgeTone> = {
  VERWALTERVERTRAG: "primary",
  BANKKONTO: "success",
  VOLLMACHT: "warning",
  VERSICHERUNG: "info",
  SONSTIGES: "default",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("de-DE");
}

interface DocumentScope { id: string; label: string; scope_type: string; valid_from: string; valid_to: string | null }

export function MandateDetailPage() {
  const { mandateId } = useParams<{ mandateId: string }>();
  return mandateId ? <MandateDetail key={mandateId} mandateId={mandateId} /> : <p>Mandat nicht gefunden.</p>;
}

function MandateDetail({ mandateId }: { mandateId: string }) {
  const { session } = useAuth();
  const isWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";

  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [docs, setDocs] = useState<MandateDocument[]>([]);
  const [scope, setScope] = useState<DocumentScope | null>(null);
  const [scopeReason, setScopeReason] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [archiveDoc, setArchiveDoc] = useState<string | null>(null);
  const [archiveReason, setArchiveReason] = useState("");
  const [archiving, setArchiving] = useState(false);
  const requestVersion = useRef<symbol | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [docType, setDocType] = useState<MandateDocType>("VERWALTERVERTRAG");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!mandateId) return;
    const version = Symbol();
    requestVersion.current = version;
    setLoading(true);
    setError(null);
    setDocs([]);
    Promise.all([getMandate(mandateId), listMandateDocuments(mandateId, includeArchived)])
      .then(([m, d]) => {
        if (version !== requestVersion.current) return;
        setMandate(m);
        setDocs(d);
      })
      .catch((err) => { if (version === requestVersion.current) setError(err instanceof ApiError ? err.message : String(err)); })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
  }, [mandateId, includeArchived]);

  useEffect(() => {
    reload();
    return () => { requestVersion.current = null; };
  }, [reload]);

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!mandateId || !file || (!scope || !scopeReason.trim())) return;
    setUploading(true);
    setUploadError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("doc_type", docType);
      fd.append("title", title);
      if (scope) { fd.append("accounting_scope_id", scope.id); fd.append("scope_reason", scopeReason.trim()); }
      await uploadMandateDocument(mandateId, fd);
      setTitle("");
      setFile(null);
      setDocType("VERWALTERVERTRAG");
      // reset the native file input
      const input = document.getElementById("mandate-file") as HTMLInputElement | null;
      if (input) input.value = "";
      reload();
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(docId: string) {
    if (!mandateId) return;
    if (!window.confirm("Dokument wirklich löschen?")) return;
    try {
      await deleteMandateDocument(mandateId, docId);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function handleArchive(e: React.FormEvent) {
    e.preventDefault();
    if (!archiveDoc || !archiveReason.trim() || archiving) return;
    setArchiving(true); setError(null);
    try {
      await archiveMandateDocument(mandateId, archiveDoc, archiveReason.trim());
      setArchiveDoc(null); setArchiveReason(""); reload();
    } catch (err) { setError(String(err)); } finally { setArchiving(false); }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Lade…</p>;
  if (error && !mandate) return <p className="error-text">{error}</p>;
  if (!mandate) return <p className="error-text">Mandat nicht gefunden.</p>;

  return (
    <div className="space-y-5">
      <Link to="/mandate" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="h-4 w-4" /> Alle Mandate
      </Link>

      <div className="flex flex-wrap items-center gap-3">
        <h1 className="m-0">{mandate.label || "Mandat"}</h1>
        <Badge tone={mandate.mandate_type === "WEG" ? "primary" : mandate.mandate_type === "MIET" ? "success" : "warning"}>
          {MANDATE_TYPE_LABELS[mandate.mandate_type]}
        </Badge>
      </div>

      {error && <p className="error-text">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>Stammdaten</CardTitle>
        </CardHeader>
        <CardContent>
          {session?.role === "OWNER_ADMIN" && <MasterDataEditor title="Mandat bearbeiten"
            initial={{ label: mandate.label, mandate_type: mandate.mandate_type, start_date: mandate.start_date, end_date: mandate.end_date }}
            fields={[{ key: "label", label: "Bezeichnung", maxLength: 255 }, { key: "mandate_type", label: "Verwaltungsart", options: Object.entries(MANDATE_TYPE_LABELS).map(([value, label]) => ({ value, label })) }, { key: "start_date", label: "Beginn", type: "date" }, { key: "end_date", label: "Ende", type: "date", nullable: true }]}
            save={changes => updateMandate(mandate.id, changes)} onSaved={reload}
            confirmChanges={changes => changes.end_date ? "Mandat zum angegebenen Datum beenden? Historische Daten bleiben erhalten." : null}
            description="Mandatsstammdaten ändern. Gebundene Abrechnungskreise werden nicht verändert. Bereits gebundene Zeiträume oder Verwaltungsarten können gesperrt sein." />}
          <table className="info-table">
            <tbody>
              <tr><td>Art</td><td>{MANDATE_TYPE_LABELS[mandate.mandate_type]}</td></tr>
              <tr><td>Bezeichnung</td><td>{mandate.label || "—"}</td></tr>
              <tr><td>Beginn</td><td>{formatDate(mandate.start_date)}</td></tr>
              <tr><td>Ende</td><td>{mandate.end_date ? formatDate(mandate.end_date) : "laufend"}</td></tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Zugängliche Dokumente</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p>Diese Liste enthält nur Unterlagen mit Ihren aktuellen Leserechten und ist keine vollständige Mandatsakte. Alte Unterlagen ohne geprüfte Bereichszuordnung bleiben in Quarantäne und sind hier nicht zugänglich.</p>
          <label><input type="checkbox" checked={includeArchived} onChange={e => setIncludeArchived(e.target.checked)} /> Archivierte Unterlagen anzeigen</label>
          <Button type="button" onClick={reload}>Unterlagen neu laden</Button>
          {isWrite && (
            <form onSubmit={handleUpload} className="rounded-md border border-border bg-secondary/40 p-4">
              <p className="mb-3 text-sm font-medium">Dokument hochladen</p>
              <p className="mb-3 text-sm">Laden Sie ein eigenständig vorliegendes Original in einen ausdrücklich berechtigten Bereich hoch. Alte Unterlagen ohne geprüfte Zuordnung bleiben in Quarantäne; historische Upload-Angaben belegen keine Herkunft.</p>
              {uploadError && <p className="error-text mb-2">{uploadError}</p>}
              <fieldset className="form-grid" disabled={uploading} style={{ border: 0, padding: 0, minWidth: 0 }}>
                <SearchChoice<DocumentScope> endpoint={`/api/accounting-scopes/page?management_mandate_id=${mandateId}&capability=prepare`}
                  value={scope} onChange={setScope} required label="Bereich für neuen Upload *"
                  display={item => `${item.label} · ${item.scope_type} · ${item.valid_from} – ${item.valid_to || "offen"}`} />
                {scope && <label>Begründung der Bereichszuordnung<input required maxLength={2000} value={scopeReason} onChange={e => setScopeReason(e.target.value)} /></label>}
                {scope && <p className="form-full-width">Prüfen Sie, dass der gesamte Dateiinhalt für diesen Bereich bestimmt ist. Die Zuordnung und das Original werden unveränderlich gespeichert.</p>}
                <label>
                  Art *
                  <select
                    required
                    value={docType}
                    onChange={(e) => setDocType(e.target.value as MandateDocType)}
                  >
                    {DOC_TYPE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Bezeichnung
                  <input
                    type="text"
                    placeholder="z. B. Verwaltervertrag 2024"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </label>
                <label className="form-full-width">
                  Datei *
                  <input
                    id="mandate-file"
                    type="file"
                    required
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                </label>
                <div className="form-actions">
                  <Button type="submit" disabled={uploading || !file || !scope || !scopeReason.trim()}>
                    <Upload className="h-4 w-4" />
                    {uploading ? "Lade hoch…" : "Hochladen"}
                  </Button>
                </div>
              </fieldset>
            </form>
          )}

          {archiveDoc && <form onSubmit={handleArchive}>
            <label>Begründung der Archivierung<input required maxLength={2000} disabled={archiving} value={archiveReason} onChange={e => setArchiveReason(e.target.value)} /></label>
            <Button type="submit" disabled={archiving || !archiveReason.trim()}>Archivierung speichern</Button>
            <Button type="button" disabled={archiving} onClick={() => setArchiveDoc(null)}>Abbrechen</Button>
            <p>Original und Zuordnung bleiben erhalten. Archivierte Unterlagen können weiterhin mit Exportrecht geöffnet werden.</p>
          </form>}
          {error ? <p role="alert">Unterlagen derzeit nicht verfügbar. Bitte neu laden.</p> : docs.length === 0 ? (
            <p className="empty-hint">Keine zugänglichen Unterlagen für diese Auswahl.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Art</th>
                    <th>Bezeichnung / Datei</th>
                    <th>Größe</th>
                    <th>Hochgeladen</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id}>
                      <td>
                        <Badge tone={DOC_TYPE_TONES[d.doc_type]}>
                          {DOC_TYPE_LABELS[d.doc_type]}
                        </Badge>
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <span className="font-medium">
                            {d.title || d.original_filename || "Dokument"}
                          </span>
                        </div>
                        {d.title && d.original_filename && (
                          <span className="text-xs text-muted-foreground">{d.original_filename}</span>
                        )}
                      </td>
                      <td className="tabular-nums">{formatSize(d.content_byte_size)}<br />{d.accounting_scope_id ? "Bereichsgebunden" : "Ohne Bereich"}{d.archived_at && <span><br />Archiviert · {d.archive_reason}</span>}</td>
                      <td className="tabular-nums">{formatDate(d.uploaded_at)}</td>
                      <td>
                        <div className="flex items-center justify-end gap-2">
                          <a
                            href={mandateDocumentDownloadUrl(mandate.id, d.id)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="btn btn--small"
                          >
                            <Download className="h-3.5 w-3.5" /> Öffnen
                          </a>
                          {isWrite && d.accounting_scope_id && !d.archived_at && <Button type="button" onClick={() => { setArchiveDoc(d.id); setArchiveReason(""); }}>Archivieren</Button>}
                          {isWrite && !d.accounting_scope_id && (
                            <button
                              className="btn btn--small btn--danger"
                              onClick={() => handleDelete(d.id)}
                              aria-label="Löschen"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
