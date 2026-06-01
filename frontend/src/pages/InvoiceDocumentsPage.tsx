/**
 * Invoice documents list page -- upload, list, link to detail/review.
 *
 * M2 GoBD: no delete button (documents are immutable once released).
 * PDF upload via multipart form-data.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import {
  listInvoiceDocuments,
  uploadInvoiceDocument,
} from "../api/endpoints";
import type { InvoiceDocument } from "../api/types";
import { ApiError } from "../api/client";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function InvoiceDocumentsPage() {
  const [documents, setDocuments] = useState<InvoiceDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    listInvoiceDocuments()
      .then(setDocuments)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      await uploadInvoiceDocument(file);
      if (fileRef.current) fileRef.current.value = "";
      reload();
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "1rem",
          flexWrap: "wrap",
          gap: "0.5rem",
        }}
      >
        <h1 style={{ margin: 0 }}>Belege</h1>
      </div>

      {/* Upload area */}
      <div className="card" style={{ marginBottom: "1rem" }}>
        <h2>PDF hochladen</h2>
        <p
          style={{
            fontSize: "0.85rem",
            color: "var(--text-muted)",
            marginBottom: "0.75rem",
          }}
        >
          Laden Sie eine Rechnung (PDF) hoch. Das System extrahiert erkannte
          Felder und erstellt einen Kostenpositions-Entwurf.
        </p>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,application/xml,text/xml"
            style={{ fontSize: "0.85rem" }}
          />
          <button
            className="btn btn--primary"
            onClick={handleUpload}
            disabled={uploading}
          >
            {uploading ? "Hochladen..." : "Hochladen"}
          </button>
        </div>
        {uploadError && (
          <p className="error-text" style={{ marginTop: "0.5rem" }}>
            {uploadError}
          </p>
        )}
      </div>

      {error && <p className="error-text">{error}</p>}

      {loading ? (
        <p>Lade...</p>
      ) : documents.length === 0 ? (
        <p className="empty-hint">Keine Belege vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Dateiname</th>
                <th>Version</th>
                <th>Groesse</th>
                <th>Typ</th>
                <th>Status</th>
                <th>SHA-256</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>{doc.original_filename ?? "-"}</td>
                  <td>v{doc.version}</td>
                  <td>{formatBytes(doc.content_byte_size)}</td>
                  <td>{doc.mime_type}</td>
                  <td>
                    <span
                      className={`badge ${
                        doc.status === "active"
                          ? "badge--confirmed"
                          : "badge--superseded"
                      }`}
                    >
                      {doc.status}
                    </span>
                    {doc.released_at && (
                      <span
                        className="badge badge--weg"
                        style={{ marginLeft: "0.25rem" }}
                      >
                        freigegeben
                      </span>
                    )}
                  </td>
                  <td>
                    <code title={doc.content_sha256}>
                      {doc.content_sha256.slice(0, 12)}...
                    </code>
                  </td>
                  <td>
                    <Link
                      to={`/belege/${doc.id}`}
                      className="btn btn--small"
                    >
                      Details
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
