/**
 * Properties list -- shows all Objekte (properties) with inline creation.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { createProperty, updateProperty } from "../api/endpoints";
import type { Property, PropertyCreate, ManagementType } from "../api/types";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { MasterImport } from "../components/MasterImport";

const MANAGEMENT_TYPES: { value: ManagementType; label: string }[] = [
  { value: "MIET", label: "Mietobjekt" },
  { value: "WEG", label: "WEG" },
  { value: "SEV", label: "SEV" },
  { value: "MISCH", label: "Mischobjekt" },
];

const EMPTY_FORM: PropertyCreate = {
  name: "",
  street: "",
  zip_code: "",
  city: "",
  management_type: "MIET",
  fiscal_year_start_month: 1,
};

export function PropertiesPage() {
  const { session } = useAuth();
  const isWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const page = usePagedList<Property>("/api/properties/page");
  const properties = page.items;
  const reload = page.reload;
  const loading = page.loading;
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [showMasterImport, setShowMasterImport] = useState(false);
  const [masterImportOpened, setMasterImportOpened] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<PropertyCreate>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [creationKey, setCreationKey] = useState(() => crypto.randomUUID());
  const [withAudience, setWithAudience] = useState(false);
  const [audienceReason, setAudienceReason] = useState("");

  function openCreate() {
    setCreationKey(crypto.randomUUID());
    setWithAudience(false);
    setAudienceReason("");
    setEditId(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  }

  function openEdit(p: Property) {
    setEditId(p.id);
    setForm({
      name: p.name,
      street: p.street,
      zip_code: p.zip_code,
      city: p.city,
      management_type: p.management_type,
      fiscal_year_start_month: p.fiscal_year_start_month,
    });
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      if (editId) {
        await updateProperty(editId, form);
      } else {
        await createProperty({ ...form, ...(withAudience && session ? { data_audience: {
          label: form.name.trim(), reason: audienceReason.trim(), grants: [{
            user_id: session.user_id, capabilities: ["read", "prepare", "approve", "export"], reason: audienceReason.trim(),
          }],
        } } : {}) }, creationKey);
      }
      setShowForm(false);
      setEditId(null);
      reload();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="paged-list">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h1 style={{ margin: 0 }}>Objekte</h1>
        {isWrite && <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
          <button className="btn" aria-expanded={showMasterImport} aria-controls="master-import-panel" onClick={() => { setMasterImportOpened(true); setShowMasterImport(value => !value); }}>
            CSV-Stammdaten importieren
          </button>
          <Link className="btn" to="/teilungserklaerung/import">
            Teilungserklärung importieren
          </Link>
          <button className="btn btn--primary" onClick={openCreate}>
            + Neues Objekt
          </button>
        </div>}
      </div>

      {isWrite && masterImportOpened && <div id="master-import-panel" hidden={!showMasterImport}><MasterImport onImported={reload} /></div>}
      <ListSearch label="Objekte suchen" onSearch={page.setQuery} />
      {(error || page.error) && <p className="error-text">{error || page.error}</p>}

      {isWrite && showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>{editId ? "Objekt bearbeiten" : "Neues Objekt anlegen"}</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              Name *
              <input
                type="text"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label>
              Verwaltungsart *
              <select
                aria-label="Verwaltungsart *"
                value={form.management_type}
                onChange={(e) => setForm({ ...form, management_type: e.target.value as ManagementType })}
              >
                {MANAGEMENT_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </label>
            <label>
              Strasse
              <input
                type="text"
                value={form.street ?? ""}
                onChange={(e) => setForm({ ...form, street: e.target.value })}
              />
            </label>
            <label>
              PLZ
              <input
                type="text"
                value={form.zip_code ?? ""}
                onChange={(e) => setForm({ ...form, zip_code: e.target.value })}
              />
            </label>
            <label>
              Stadt
              <input
                type="text"
                value={form.city ?? ""}
                onChange={(e) => setForm({ ...form, city: e.target.value })}
              />
            </label>
            <label>
              GJ-Startmonat
              <select
                value={form.fiscal_year_start_month ?? 1}
                onChange={(e) => setForm({ ...form, fiscal_year_start_month: Number(e.target.value) })}
              >
                {Array.from({ length: 12 }, (_, i) => (
                  <option key={i + 1} value={i + 1}>{i + 1}</option>
                ))}
              </select>
            </label>
            {!editId && session?.role === "OWNER_ADMIN" && <fieldset style={{ gridColumn: "1 / -1", minWidth: 0, display: "grid", gap: "0.65rem" }}>
              <legend>Datenfreigabe für das neue Objekt</legend>
              <label style={{ display: "flex", flexDirection: "row", alignItems: "flex-start", gap: "0.5rem" }}>
                <input type="checkbox" style={{ marginTop: "0.2rem", flexShrink: 0 }} checked={withAudience} onChange={event => setWithAudience(event.target.checked)} />
                <span>Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben</span>
              </label>
              {withAudience && <label>Begründung der Freigabe
                <input required maxLength={2000} value={audienceReason} onChange={event => setAudienceReason(event.target.value)} />
              </label>}
              <p className="muted">Weitere Teamfreigaben können anschließend unter Gebäudedaten-Rechte eingerichtet werden.</p>
            </fieldset>}
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? "Speichern..." : "Speichern"}
              </button>
              <button type="button" className="btn" onClick={() => setShowForm(false)}>
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {loading && properties.length === 0 ? (
        <p>Lade...</p>
      ) : properties.length === 0 ? (
        <p className="empty-hint">Keine Objekte vorhanden.</p>
      ) : (
        <div className="card paged-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Typ</th>
                <th>Adresse</th>
                <th>GJ</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {properties.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link to={`/objekte/${p.id}`} className="link">
                      {p.name}
                    </Link>
                  </td>
                  <td>
                    <span className={`badge badge--${p.management_type.toLowerCase()}`}>
                      {p.management_type}
                    </span>
                  </td>
                  <td>{[p.street, p.zip_code, p.city].filter(Boolean).join(", ") || "-"}</td>
                  <td>{p.fiscal_year_start_month}</td>
                  <td>{isWrite && <button className="btn btn--small" onClick={() => openEdit(p)}>Bearbeiten</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <MoreResults available={!!page.nextCursor} loading={loading} onMore={() => void page.loadMore()} />
    </div>
  );
}
