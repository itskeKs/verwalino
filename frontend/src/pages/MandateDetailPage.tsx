/**
 * Mandate detail -- Stammdaten des Mandats + Dokumente (Verträge/Unterlagen).
 *
 * Dokumente: Upload (Schreibrolle), Liste mit Download und Löschen. Belegt z. B.
 * Verwaltervertrag, Bankkonto-Unterlagen, Vollmachten, Versicherungen.
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Upload, Download, Trash2, FileText } from "lucide-react";
import {
  getMandate,
  listMandateDocuments,
  uploadMandateDocument,
  mandateDocumentDownloadUrl,
  deleteMandateDocument,
} from "../api/endpoints";
import type { Mandate, MandateDocument, MandateDocType } from "../api/types";
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

export function MandateDetailPage() {
  const { mandateId } = useParams<{ mandateId: string }>();
  const { session } = useAuth();
  const isWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";

  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [docs, setDocs] = useState<MandateDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [docType, setDocType] = useState<MandateDocType>("VERWALTERVERTRAG");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!mandateId) return;
    setLoading(true);
    setError(null);
    Promise.all([getMandate(mandateId), listMandateDocuments(mandateId)])
      .then(([m, d]) => {
        setMandate(m);
        setDocs(d);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [mandateId]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!mandateId || !file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("doc_type", docType);
      fd.append("title", title);
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
          <CardTitle>Dokumente</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {isWrite && (
            <form onSubmit={handleUpload} className="rounded-md border border-border bg-secondary/40 p-4">
              <p className="mb-3 text-sm font-medium">Dokument hochladen</p>
              {uploadError && <p className="error-text mb-2">{uploadError}</p>}
              <div className="form-grid">
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
                  <Button type="submit" disabled={uploading || !file}>
                    <Upload className="h-4 w-4" />
                    {uploading ? "Lade hoch…" : "Hochladen"}
                  </Button>
                </div>
              </div>
            </form>
          )}

          {docs.length === 0 ? (
            <p className="empty-hint">Noch keine Dokumente zu diesem Mandat.</p>
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
                      <td className="tabular-nums">{formatSize(d.content_byte_size)}</td>
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
                          {isWrite && (
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
