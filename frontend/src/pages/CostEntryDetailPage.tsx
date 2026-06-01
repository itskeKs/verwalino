/**
 * Cost entry detail page -- view, edit (drafts only), confirm, reject, correct.
 *
 * GoBD: confirmed entries are read-only. Correction = new version (POST correction).
 * Shows version chain when superseded_by_id is set.
 * 422 validation errors on confirm are displayed clearly.
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  getCostEntry,
  updateCostEntry,
  confirmCostEntry,
  rejectCostEntry,
  createCostEntryCorrection,
  listCostCategories,
  listProperties,
  listInvoiceDocuments,
} from "../api/endpoints";
import type {
  CostEntry,
  CostEntryUpdate,
  CostCategory,
  Property,
  InvoiceDocument,
} from "../api/types";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = {
  draft_extracted: "Entwurf (extrahiert)",
  needs_review: "Pruefung noetig",
  confirmed: "Bestaetigt",
  rejected: "Verworfen",
  superseded: "Ersetzt",
};

const STATUS_CSS: Record<string, string> = {
  draft_extracted: "badge--draft",
  needs_review: "badge--review",
  confirmed: "badge--confirmed",
  rejected: "badge--rejected",
  superseded: "badge--superseded",
};

const APPORTIONABILITY_OPTIONS = [
  { value: "full", label: "Voll umlagefaehig" },
  { value: "partial", label: "Teilweise umlagefaehig" },
  { value: "none", label: "Nicht umlagefaehig" },
];

const DISTRIBUTION_KEY_OPTIONS = [
  { value: "", label: "(kein)" },
  { value: "MEA", label: "MEA" },
  { value: "AREA", label: "Flaeche" },
  { value: "HEATED_AREA", label: "Beheizte Flaeche" },
  { value: "UNIT_COUNT", label: "Einheiten" },
  { value: "PERSON_COUNT", label: "Personen" },
  { value: "CONSUMPTION", label: "Verbrauch" },
  { value: "EXTERNAL", label: "Extern" },
  { value: "MANUAL", label: "Manuell" },
];

function isEditable(status: string): boolean {
  return status === "draft_extracted" || status === "needs_review";
}

function isConfirmable(status: string): boolean {
  return status === "draft_extracted" || status === "needs_review";
}

function isCorrectable(entry: CostEntry): boolean {
  return entry.status === "confirmed" && entry.superseded_by_id === null;
}

export function CostEntryDetailPage() {
  const { entryId } = useParams<{ entryId: string }>();
  const navigate = useNavigate();
  const [entry, setEntry] = useState<CostEntry | null>(null);
  const [categories, setCategories] = useState<CostCategory[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [documents, setDocuments] = useState<InvoiceDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Edit mode
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<CostEntryUpdate>({});

  // Correction mode
  const [correcting, setCorrecting] = useState(false);
  const [correctionForm, setCorrectionForm] = useState<CostEntryUpdate>({});

  const reload = useCallback(() => {
    if (!entryId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      getCostEntry(entryId),
      listCostCategories(),
      listProperties(),
      listInvoiceDocuments(entryId),
    ])
      .then(([e, c, p, d]) => {
        setEntry(e);
        setCategories(c);
        setProperties(p);
        setDocuments(d);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [entryId]);

  useEffect(() => {
    reload();
  }, [reload]);

  function getCategoryLabel(id: string): string {
    const cat = categories.find((c) => c.id === id);
    return cat ? `${cat.code} ${cat.label}` : id.slice(0, 8);
  }

  function getPropertyName(id: string): string {
    const prop = properties.find((p) => p.id === id);
    return prop ? prop.name : id.slice(0, 8);
  }

  function startEdit() {
    if (!entry) return;
    setEditForm({
      gross_amount: entry.gross_amount,
      vat_amount: entry.vat_amount,
      vat_rate: entry.vat_rate,
      invoice_date: entry.invoice_date,
      service_period_from: entry.service_period_from,
      service_period_to: entry.service_period_to,
      cost_category_id: entry.cost_category_id,
      vendor_id: entry.vendor_id,
      document_number: entry.document_number,
      owner_distribution_key: entry.owner_distribution_key,
      tenant_distribution_key: entry.tenant_distribution_key,
      tenant_apportionable: entry.tenant_apportionable,
      apportionable_tenant_amount: entry.apportionable_tenant_amount,
    });
    setEditing(true);
    setActionError(null);
  }

  async function handleEditSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!entryId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await updateCostEntry(entryId, editForm);
      setEntry(updated);
      setEditing(false);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleConfirm() {
    if (!entryId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const confirmed = await confirmCostEntry(entryId);
      setEntry(confirmed);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleReject() {
    if (!entryId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const rejected = await rejectCostEntry(entryId);
      setEntry(rejected);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  function startCorrection() {
    if (!entry) return;
    setCorrectionForm({
      gross_amount: entry.gross_amount,
      vat_amount: entry.vat_amount,
      vat_rate: entry.vat_rate,
      invoice_date: entry.invoice_date,
      service_period_from: entry.service_period_from,
      service_period_to: entry.service_period_to,
      cost_category_id: entry.cost_category_id,
      vendor_id: entry.vendor_id,
      document_number: entry.document_number,
      owner_distribution_key: entry.owner_distribution_key,
      tenant_distribution_key: entry.tenant_distribution_key,
      tenant_apportionable: entry.tenant_apportionable,
      apportionable_tenant_amount: entry.apportionable_tenant_amount,
    });
    setCorrecting(true);
    setActionError(null);
  }

  async function handleCorrectionSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!entryId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const newVersion = await createCostEntryCorrection(
        entryId,
        correctionForm,
      );
      // Navigate to the new correction version
      navigate(`/kosten/${newVersion.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  if (loading) return <p>Lade...</p>;
  if (error && !entry) return <p className="error-text">{error}</p>;
  if (!entry) return <p className="error-text">Kostenposition nicht gefunden.</p>;

  const editable = isEditable(entry.status);
  const confirmable = isConfirmable(entry.status);
  const correctable = isCorrectable(entry);

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/kosten" className="link">
          &larr; Alle Kostenpositionen
        </Link>
      </p>
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
        <h1 style={{ margin: 0 }}>
          Kostenposition{" "}
          <span
            className={`badge ${STATUS_CSS[entry.status] ?? ""}`}
            style={{ fontSize: "0.8rem", verticalAlign: "middle" }}
          >
            {STATUS_LABELS[entry.status] ?? entry.status}
          </span>
        </h1>
      </div>

      {error && <p className="error-text">{error}</p>}
      {actionError && <p className="error-text">{actionError}</p>}

      {/* GoBD notice for confirmed entries */}
      {entry.status === "confirmed" && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#fff8e1",
            borderColor: "#ffcc02",
          }}
        >
          <strong>GoBD:</strong> Diese Kostenposition ist bestaetigt und
          unveraenderbar. Aenderungen sind nur als Korrekturversion moeglich.
        </div>
      )}

      {/* Superseded notice */}
      {entry.superseded_by_id && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#f3e5f5",
            borderColor: "#ce93d8",
          }}
        >
          Diese Position wurde ersetzt durch{" "}
          <Link to={`/kosten/${entry.superseded_by_id}`} className="link">
            Korrekturversion
          </Link>
          .
        </div>
      )}

      {/* Detail card (read-only view) */}
      {!editing && !correcting && (
        <>
          <div className="card" style={{ marginBottom: "1.5rem" }}>
            <h2>Details</h2>
            <table className="info-table">
              <tbody>
                <tr>
                  <td>Objekt</td>
                  <td>
                    <Link
                      to={`/objekte/${entry.property_id}`}
                      className="link"
                    >
                      {getPropertyName(entry.property_id)}
                    </Link>
                  </td>
                </tr>
                <tr>
                  <td>Kategorie</td>
                  <td>{getCategoryLabel(entry.cost_category_id)}</td>
                </tr>
                <tr>
                  <td>Rechnungsdatum</td>
                  <td>{entry.invoice_date}</td>
                </tr>
                <tr>
                  <td>Leistungszeitraum</td>
                  <td>
                    {entry.service_period_from} bis {entry.service_period_to}
                  </td>
                </tr>
                <tr>
                  <td>Bruttobetrag</td>
                  <td style={{ fontFamily: "var(--mono)" }}>
                    {entry.gross_amount}
                  </td>
                </tr>
                {entry.vat_amount && (
                  <tr>
                    <td>USt-Betrag</td>
                    <td style={{ fontFamily: "var(--mono)" }}>
                      {entry.vat_amount}
                    </td>
                  </tr>
                )}
                {entry.vat_rate && (
                  <tr>
                    <td>USt-Satz</td>
                    <td>{entry.vat_rate}%</td>
                  </tr>
                )}
                {entry.document_number && (
                  <tr>
                    <td>Belegnummer</td>
                    <td>{entry.document_number}</td>
                  </tr>
                )}
                <tr>
                  <td>Verteilerschl. Eigentuemer</td>
                  <td>{entry.owner_distribution_key ?? "-"}</td>
                </tr>
                <tr>
                  <td>Verteilerschl. Mieter</td>
                  <td>{entry.tenant_distribution_key ?? "-"}</td>
                </tr>
                <tr>
                  <td>Umlagefaehigkeit</td>
                  <td>{entry.tenant_apportionable}</td>
                </tr>
                {entry.apportionable_tenant_amount && (
                  <tr>
                    <td>Umlagef. Betrag</td>
                    <td style={{ fontFamily: "var(--mono)" }}>
                      {entry.apportionable_tenant_amount}
                    </td>
                  </tr>
                )}
                <tr>
                  <td>Quelle</td>
                  <td>{entry.source}</td>
                </tr>
                {entry.confirmed_at && (
                  <tr>
                    <td>Bestaetigt am</td>
                    <td>{entry.confirmed_at}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Action buttons */}
          <div
            style={{
              display: "flex",
              gap: "0.5rem",
              marginBottom: "1.5rem",
              flexWrap: "wrap",
            }}
          >
            {editable && (
              <button className="btn btn--primary" onClick={startEdit}>
                Bearbeiten
              </button>
            )}
            {confirmable && (
              <button
                className="btn btn--primary"
                onClick={handleConfirm}
                disabled={actionLoading}
              >
                Bestaetigen
              </button>
            )}
            {confirmable && (
              <button
                className="btn"
                onClick={handleReject}
                disabled={actionLoading}
                style={{ borderColor: "#b71c1c", color: "#b71c1c" }}
              >
                Verwerfen
              </button>
            )}
            {correctable && (
              <button className="btn" onClick={startCorrection}>
                Korrektur erstellen
              </button>
            )}
          </div>
        </>
      )}

      {/* Edit form (drafts only) */}
      {editing && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>Entwurf bearbeiten</h2>
          <form onSubmit={handleEditSubmit} className="form-grid">
            <label>
              Kategorie
              <select
                value={editForm.cost_category_id ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    cost_category_id: e.target.value || null,
                  })
                }
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
              Rechnungsdatum
              <input
                type="date"
                value={editForm.invoice_date ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    invoice_date: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Leistungszeitraum von
              <input
                type="date"
                value={editForm.service_period_from ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    service_period_from: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Leistungszeitraum bis
              <input
                type="date"
                value={editForm.service_period_to ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    service_period_to: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Bruttobetrag
              <input
                type="text"
                inputMode="decimal"
                value={editForm.gross_amount ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    gross_amount: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              USt-Betrag
              <input
                type="text"
                inputMode="decimal"
                value={editForm.vat_amount ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
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
                value={editForm.vat_rate ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    vat_rate: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Belegnummer
              <input
                type="text"
                value={editForm.document_number ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    document_number: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Verteilerschl. Eigentuemer
              <select
                value={editForm.owner_distribution_key ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    owner_distribution_key: e.target.value || null,
                  })
                }
              >
                {DISTRIBUTION_KEY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Verteilerschl. Mieter
              <select
                value={editForm.tenant_distribution_key ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    tenant_distribution_key: e.target.value || null,
                  })
                }
              >
                {DISTRIBUTION_KEY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Umlagefaehigkeit
              <select
                value={editForm.tenant_apportionable ?? "full"}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    tenant_apportionable: e.target.value || null,
                  })
                }
              >
                {APPORTIONABILITY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Umlagef. Betrag (Mieter)
              <input
                type="text"
                inputMode="decimal"
                value={editForm.apportionable_tenant_amount ?? ""}
                onChange={(e) =>
                  setEditForm({
                    ...editForm,
                    apportionable_tenant_amount: e.target.value || null,
                  })
                }
              />
            </label>
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={actionLoading}
              >
                {actionLoading ? "Speichern..." : "Speichern"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setEditing(false)}
              >
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Correction form (confirmed entries only) */}
      {correcting && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>Korrekturversion erstellen</h2>
          <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "0.75rem" }}>
            Die bestehende Kostenposition bleibt unveraendert erhalten (GoBD).
            Es wird eine neue Korrekturversion angelegt.
          </p>
          <form onSubmit={handleCorrectionSubmit} className="form-grid">
            <label>
              Kategorie
              <select
                value={correctionForm.cost_category_id ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    cost_category_id: e.target.value || null,
                  })
                }
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
              Rechnungsdatum
              <input
                type="date"
                value={correctionForm.invoice_date ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    invoice_date: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Leistungszeitraum von
              <input
                type="date"
                value={correctionForm.service_period_from ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    service_period_from: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Leistungszeitraum bis
              <input
                type="date"
                value={correctionForm.service_period_to ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    service_period_to: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Bruttobetrag
              <input
                type="text"
                inputMode="decimal"
                value={correctionForm.gross_amount ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    gross_amount: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              USt-Betrag
              <input
                type="text"
                inputMode="decimal"
                value={correctionForm.vat_amount ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
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
                value={correctionForm.vat_rate ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    vat_rate: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Belegnummer
              <input
                type="text"
                value={correctionForm.document_number ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    document_number: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Verteilerschl. Eigentuemer
              <select
                value={correctionForm.owner_distribution_key ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    owner_distribution_key: e.target.value || null,
                  })
                }
              >
                {DISTRIBUTION_KEY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Verteilerschl. Mieter
              <select
                value={correctionForm.tenant_distribution_key ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    tenant_distribution_key: e.target.value || null,
                  })
                }
              >
                {DISTRIBUTION_KEY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Umlagefaehigkeit
              <select
                value={correctionForm.tenant_apportionable ?? "full"}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    tenant_apportionable: e.target.value || null,
                  })
                }
              >
                {APPORTIONABILITY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Umlagef. Betrag (Mieter)
              <input
                type="text"
                inputMode="decimal"
                value={correctionForm.apportionable_tenant_amount ?? ""}
                onChange={(e) =>
                  setCorrectionForm({
                    ...correctionForm,
                    apportionable_tenant_amount: e.target.value || null,
                  })
                }
              />
            </label>
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={actionLoading}
              >
                {actionLoading ? "Erstellen..." : "Korrektur erstellen"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setCorrecting(false)}
              >
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Linked documents */}
      <div className="card">
        <h2>Verknuepfte Belege</h2>
        {documents.length === 0 ? (
          <p className="empty-hint">Keine Belege verknuepft.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Dateiname</th>
                <th>Version</th>
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
                  <td>
                    <span
                      className={`badge ${doc.status === "active" ? "badge--confirmed" : "badge--superseded"}`}
                    >
                      {doc.status}
                    </span>
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
                      Ansehen
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
