/**
 * Invoice document detail page -- view, verify integrity, extraction review,
 * create cost entry from extraction, new version upload.
 *
 * M2 GoBD: no delete. Released documents are immutable.
 * Extraction payload is shown with per-field confidence.
 * From here the user can pre-fill a cost_entry draft.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  getInvoiceDocument,
  retryInvoiceExtraction,
  verifyInvoiceDocument,
  createInvoiceDocumentVersion,
  invoiceDocumentContentUrl,
  createCostEntry,
  listCostCategories,
  listProperties,
} from "../api/endpoints";
import type {
  InvoiceDocument,
  VerifyResult,
  CostCategory,
  Property,
  CostEntryCreate,
} from "../api/types";
import { ApiError } from "../api/client";
import { InvoiceProcessingStatus } from "../components/InvoiceProcessingStatus";
import { Badge } from "../components/ui/badge";

/** Human-readable short label for source_format values from the e-invoice parser. */
const FORMAT_LABELS: Record<string, string> = {
  XRECHNUNG_UBL: "XRechnung",
  XRECHNUNG_CII: "XRechnung",
  ZUGFERD: "ZUGFeRD",
};

function confidenceColor(c: number): string {
  if (c >= 0.8) return "#2e7d32";
  if (c >= 0.5) return "#e65100";
  return "#b71c1c";
}

function confidenceLabel(c: number): string {
  if (c >= 0.8) return "Hoch";
  if (c >= 0.5) return "Mittel";
  if (c > 0) return "Niedrig";
  return "-";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function InvoiceDocumentDetailPage() {
  const { docId } = useParams<{ docId: string }>();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<InvoiceDocument | null>(null);
  const [categories, setCategories] = useState<CostCategory[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Verify state
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);

  // Version upload
  const versionFileRef = useRef<HTMLInputElement>(null);

  const costAttempt = useRef<{ payload: string; key: string } | null>(null);

  // Cost entry creation form (pre-filled from extraction)
  const [showCostForm, setShowCostForm] = useState(false);
  const [costForm, setCostForm] = useState<CostEntryCreate>({
    property_id: "",
    cost_category_id: "",
    invoice_date: "",
    service_period_from: "",
    service_period_to: "",
    gross_amount: "",
    tenant_apportionable: "UMLAGEFAEHIG",
    source: "PDF_IMPORT",
    status: "draft_extracted",
  });

  const reload = useCallback(() => {
    if (!docId) return;
    setLoading(true);
    setError(null);
    setVerifyResult(null);
    Promise.all([
      getInvoiceDocument(docId),
      listCostCategories(),
      listProperties(),
    ])
      .then(([d, c, p]) => {
        setDoc(d);
        setCategories(c);
        setProperties(p);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [docId]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    if (!docId || !["queued", "running"].includes(doc?.processing_status ?? "")) return;
    let active = true;
    const timer = setTimeout(() => {
      getInvoiceDocument(docId).then((next) => { if (active) setDoc(next); })
        .catch(() => { if (active) setActionError("Status konnte nicht aktualisiert werden. Bitte neu laden."); });
    }, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [docId, doc]);

  async function handleRetry() {
    if (!docId) return;
    setActionLoading(true);
    setActionError(null);
    try { setDoc(await retryInvoiceExtraction(docId)); }
    catch (err) { setActionError(err instanceof ApiError ? err.message : String(err)); }
    finally { setActionLoading(false); }
  }

  async function handleVerify() {
    if (!docId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const result = await verifyInvoiceDocument(docId);
      setVerifyResult(result);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleVersionUpload() {
    if (!docId) return;
    const file = versionFileRef.current?.files?.[0];
    if (!file) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const newDoc = await createInvoiceDocumentVersion(docId, file);
      // Navigate to the new version
      navigate(`/belege/${newDoc.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  function openCostForm() {
    if (!doc) return;
    const ext = doc.extraction_payload;
    // Find category by suggested code
    let categoryId = "";
    if (ext?.suggested_category_code) {
      const match = categories.find(
        (c) => c.code === ext.suggested_category_code,
      );
      if (match) categoryId = match.id;
    }

    // Pre-fill defaults from category
    const cat = categories.find((c) => c.id === categoryId);

    setCostForm({
      property_id: "",
      cost_category_id: categoryId,
      cost_category_plan_version: cat?.plan_version ?? 1,
      vendor_id: null,
      invoice_date: ext?.invoice_date ?? "",
      service_period_from: ext?.service_period_from ?? "",
      service_period_to: ext?.service_period_to ?? "",
      gross_amount: ext?.gross_amount ?? "",
      vat_amount: ext?.vat_amount ?? null,
      vat_rate: ext?.vat_rate ?? null,
      document_number: ext?.document_number ?? null,
      owner_distribution_key: cat?.owner_distribution_key_default ?? null,
      tenant_distribution_key: cat?.tenant_distribution_key_default ?? null,
      tenant_apportionable: cat?.tenant_apportionability_default ?? "UMLAGEFAEHIG",
      apportionable_tenant_amount: null,
      source: ext?.is_einvoice ? "E_INVOICE" : "PDF_IMPORT",
      status: "draft_extracted",
    });
    setShowCostForm(true);
    setActionError(null);
  }

  async function handleCostSubmit(e: React.FormEvent) {
    e.preventDefault();
    setActionLoading(true);
    setActionError(null);
    try {
      const body = { ...costForm, source_document_id: docId };
      const payload = JSON.stringify(body);
      if (costAttempt.current?.payload !== payload) {
        costAttempt.current = { payload, key: crypto.randomUUID() };
      }
      const entry = await createCostEntry(body, costAttempt.current.key);
      // Navigate to the new cost entry
      navigate(`/kosten/${entry.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  if (loading) return <p>Lade...</p>;
  if (error && !doc) return <p className="error-text">{error}</p>;
  if (!doc) return <p className="error-text">Beleg nicht gefunden.</p>;

  const ext = doc.extraction_payload;
  const isReleased = doc.released_at !== null;

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/belege" className="link">
          &larr; Alle Belege
        </Link>
      </p>
      <p><InvoiceProcessingStatus document={doc} /></p>
      {["legacy", "needs_review", "unsupported", "unavailable", "timeout", "failed"].includes(doc.processing_status) && !doc.released_at && doc.status === "active" && (
        <button className="btn" onClick={handleRetry} disabled={actionLoading}>Texterkennung erneut starten</button>
      )}
      <h1>
        Beleg: {doc.original_filename ?? "Ohne Name"}{" "}
        <span
          className={`badge ${doc.status === "active" ? "badge--confirmed" : "badge--superseded"}`}
          style={{ fontSize: "0.8rem", verticalAlign: "middle" }}
        >
          {doc.status}
        </span>
        {isReleased && (
          <span
            className="badge badge--weg"
            style={{
              fontSize: "0.8rem",
              verticalAlign: "middle",
              marginLeft: "0.25rem",
            }}
          >
            freigegeben
          </span>
        )}
      </h1>

      {error && <p className="error-text">{error}</p>}
      {actionError && <p className="error-text">{actionError}</p>}

      {/* GoBD notice for released documents */}
      {isReleased && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#fff8e1",
            borderColor: "#ffcc02",
          }}
        >
          <strong>GoBD:</strong> Dieser Beleg ist freigegeben und unveraenderbar.
          Korrekturen erzeugen eine neue Version mit Bezug zum Original.
        </div>
      )}

      {/* Document metadata */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Dokumenteigenschaften</h2>
        <table className="info-table">
          <tbody>
            <tr>
              <td>Dateiname</td>
              <td>{doc.original_filename ?? "-"}</td>
            </tr>
            <tr>
              <td>MIME-Typ</td>
              <td>{doc.mime_type}</td>
            </tr>
            <tr>
              <td>Groesse</td>
              <td>{formatBytes(doc.content_byte_size)}</td>
            </tr>
            <tr>
              <td>Version</td>
              <td>v{doc.version}</td>
            </tr>
            <tr>
              <td>SHA-256</td>
              <td>
                <code style={{ fontSize: "0.78rem", wordBreak: "break-all" }}>
                  {doc.content_sha256}
                </code>
              </td>
            </tr>
            <tr>
              <td>Ketten-Root</td>
              <td>
                <code>{doc.chain_root_id.slice(0, 12)}...</code>
              </td>
            </tr>
            {doc.supersedes_id && (
              <tr>
                <td>Ersetzt</td>
                <td>
                  <Link to={`/belege/${doc.supersedes_id}`} className="link">
                    Vorgaengerversion
                  </Link>
                </td>
              </tr>
            )}
            {doc.cost_entry_id && (
              <tr>
                <td>Kostenposition</td>
                <td>
                  <Link
                    to={`/kosten/${doc.cost_entry_id}`}
                    className="link"
                  >
                    Verknuepfte Kostenposition
                  </Link>
                </td>
              </tr>
            )}
            {doc.released_at && (
              <tr>
                <td>Freigegeben am</td>
                <td>{doc.released_at}</td>
              </tr>
            )}
            {doc.retention_until && (
              <tr>
                <td>Aufbewahrung bis</td>
                <td>{doc.retention_until}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Actions */}
      <div
        style={{
          display: "flex",
          gap: "0.5rem",
          marginBottom: "1.5rem",
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <a
          href={invoiceDocumentContentUrl(doc.id)}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn--primary"
        >
          Dokument ansehen
        </a>
        <button
          className="btn"
          onClick={handleVerify}
          disabled={actionLoading}
        >
          Integritaet pruefen
        </button>
        {!doc.cost_entry_id && doc.status === "active" && !doc.released_at && (
          <button
            className="btn btn--primary"
            onClick={openCostForm}
            disabled={actionLoading}
          >
            Kostenposition erstellen
          </button>
        )}
      </div>

      {/* Verify result */}
      {verifyResult && (
        <div
          className="card"
          style={{
            marginBottom: "1.5rem",
            background: verifyResult.integrity_ok ? "#e8f5e9" : "#ffebee",
            borderColor: verifyResult.integrity_ok ? "#4caf50" : "#f44336",
          }}
        >
          <strong>
            Integritaetspruefung:{" "}
            {verifyResult.integrity_ok ? "OK" : "FEHLGESCHLAGEN"}
          </strong>
          <br />
          <code style={{ fontSize: "0.78rem" }}>
            SHA-256: {verifyResult.content_sha256}
          </code>
        </div>
      )}

      {/* Extraction payload review */}
      {ext && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
            <h2 style={{ margin: 0 }}>Extrahierte Felder</h2>
            {ext.is_einvoice && (
              <Badge tone="primary">
                Als E-Rechnung erkannt
                {ext.source_format && (
                  <span style={{ opacity: 0.7, marginLeft: "0.25em" }}>
                    ({FORMAT_LABELS[ext.source_format] ?? ext.source_format})
                  </span>
                )}
              </Badge>
            )}
          </div>
          <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "0.75rem" }}>
            Gesamtkonfidenz:{" "}
            <span style={{ color: confidenceColor(ext.confidence), fontWeight: 600 }}>
              {confidenceLabel(ext.confidence)} ({(ext.confidence * 100).toFixed(0)}%)
            </span>
          </p>
          <table className="info-table">
            <tbody>
              <ExtractionRow
                label="Lieferant"
                value={ext.vendor_name}
                fieldConf={ext.field_confidences?.vendor_name}
              />
              <ExtractionRow
                label="Rechnungsdatum"
                value={ext.invoice_date}
                fieldConf={ext.field_confidences?.invoice_date}
              />
              <ExtractionRow
                label="Belegnummer"
                value={ext.document_number}
                fieldConf={ext.field_confidences?.document_number}
              />
              <ExtractionRow
                label="Bruttobetrag"
                value={ext.gross_amount}
                fieldConf={ext.field_confidences?.gross_amount}
              />
              <ExtractionRow
                label="USt-Betrag"
                value={ext.vat_amount}
                fieldConf={ext.field_confidences?.vat_amount}
              />
              <ExtractionRow
                label="USt-Satz"
                value={ext.vat_rate}
                fieldConf={ext.field_confidences?.vat_rate}
              />
              <ExtractionRow
                label="Leistungszeitraum von"
                value={ext.service_period_from}
                fieldConf={ext.field_confidences?.service_period_from}
              />
              <ExtractionRow
                label="Leistungszeitraum bis"
                value={ext.service_period_to}
                fieldConf={ext.field_confidences?.service_period_to}
              />
              <ExtractionRow
                label="Kategorievorschlag"
                value={ext.suggested_category_code}
                fieldConf={ext.field_confidences?.suggested_category_code}
              />
            </tbody>
          </table>
        </div>
      )}

      {/* New version upload */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Neue Version hochladen</h2>
        <p
          style={{
            fontSize: "0.85rem",
            color: "var(--text-muted)",
            marginBottom: "0.75rem",
          }}
        >
          Die bestehende Version bleibt erhalten (GoBD). Es wird eine neue
          Version in der Belegkette angelegt.
        </p>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <input
            ref={versionFileRef}
            type="file"
            accept="application/pdf,application/xml,text/xml"
            style={{ fontSize: "0.85rem" }}
          />
          <button
            className="btn"
            onClick={handleVersionUpload}
            disabled={actionLoading}
          >
            {actionLoading ? "Hochladen..." : "Version erstellen"}
          </button>
        </div>
      </div>

      {/* Cost entry creation form (pre-filled from extraction) */}
      {showCostForm && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>Kostenposition aus Beleg erstellen</h2>
          <p
            style={{
              fontSize: "0.85rem",
              color: "var(--text-muted)",
              marginBottom: "0.75rem",
            }}
          >
            Die erkannten Felder wurden vorbelegt. Bitte pruefen und
            ergaenzen Sie die Daten, bevor Sie den Entwurf speichern.
          </p>
          <form onSubmit={handleCostSubmit} className="form-grid">
            <label>
              Objekt *
              <select
                required
                value={costForm.property_id}
                onChange={(e) =>
                  setCostForm({ ...costForm, property_id: e.target.value })
                }
              >
                <option value="">-- Objekt waehlen --</option>
                {properties.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Kategorie *
              <select
                required
                value={costForm.cost_category_id}
                onChange={(e) => {
                  const cat = categories.find((c) => c.id === e.target.value);
                  setCostForm({
                    ...costForm,
                    cost_category_id: e.target.value,
                    cost_category_plan_version: cat?.plan_version ?? 1,
                    tenant_apportionable:
                      cat?.tenant_apportionability_default ?? costForm.tenant_apportionable,
                    owner_distribution_key:
                      cat?.owner_distribution_key_default ?? costForm.owner_distribution_key,
                    tenant_distribution_key:
                      cat?.tenant_distribution_key_default ?? costForm.tenant_distribution_key,
                  });
                }}
              >
                <option value="">-- Kategorie waehlen --</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.code} {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Rechnungsdatum *
              <input
                type="date"
                required
                value={costForm.invoice_date}
                onChange={(e) =>
                  setCostForm({ ...costForm, invoice_date: e.target.value })
                }
              />
            </label>
            <label>
              Leistungszeitraum von *
              <input
                type="date"
                required
                value={costForm.service_period_from}
                onChange={(e) =>
                  setCostForm({
                    ...costForm,
                    service_period_from: e.target.value,
                  })
                }
              />
            </label>
            <label>
              Leistungszeitraum bis *
              <input
                type="date"
                required
                value={costForm.service_period_to}
                onChange={(e) =>
                  setCostForm({
                    ...costForm,
                    service_period_to: e.target.value,
                  })
                }
              />
            </label>
            <label>
              Bruttobetrag *
              <input
                type="text"
                required
                inputMode="decimal"
                value={costForm.gross_amount}
                onChange={(e) =>
                  setCostForm({ ...costForm, gross_amount: e.target.value })
                }
              />
            </label>
            <label>
              USt-Betrag
              <input
                type="text"
                inputMode="decimal"
                value={costForm.vat_amount ?? ""}
                onChange={(e) =>
                  setCostForm({
                    ...costForm,
                    vat_amount: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              USt-Satz (%)
              <input
                type="text"
                inputMode="decimal"
                value={costForm.vat_rate ?? ""}
                onChange={(e) =>
                  setCostForm({
                    ...costForm,
                    vat_rate: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Belegnummer
              <input
                type="text"
                value={costForm.document_number ?? ""}
                onChange={(e) =>
                  setCostForm({
                    ...costForm,
                    document_number: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Umlagefaehigkeit *
              <select
                required
                value={costForm.tenant_apportionable}
                onChange={(e) =>
                  setCostForm({
                    ...costForm,
                    tenant_apportionable: e.target.value,
                  })
                }
              >
                <option value="full">Voll umlagefaehig</option>
                <option value="partial">Teilweise umlagefaehig</option>
                <option value="none">Nicht umlagefaehig</option>
              </select>
            </label>
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={actionLoading}
              >
                {actionLoading ? "Erstellen..." : "Entwurf erstellen"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setShowCostForm(false)}
              >
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

/** Helper component: one row of extraction data with confidence indicator. */
function ExtractionRow({
  label,
  value,
  fieldConf,
}: {
  label: string;
  value: string | null;
  fieldConf?: number;
}) {
  return (
    <tr>
      <td>{label}</td>
      <td>
        {value ?? (
          <span style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
            nicht erkannt
          </span>
        )}
        {fieldConf !== undefined && fieldConf > 0 && (
          <span
            style={{
              marginLeft: "0.5rem",
              fontSize: "0.75rem",
              color: confidenceColor(fieldConf),
              fontWeight: 500,
            }}
          >
            ({(fieldConf * 100).toFixed(0)}%)
          </span>
        )}
      </td>
    </tr>
  );
}
