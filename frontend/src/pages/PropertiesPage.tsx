/**
 * Properties list -- shows all Objekte (properties) with inline creation.
 */

import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { listProperties, createProperty, updateProperty } from "../api/endpoints";
import type { Property, PropertyCreate, ManagementType } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

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
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<PropertyCreate>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    listProperties()
      .then(setProperties)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { void Promise.resolve().then(reload); }, [reload]);

  function openCreate() {
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
        await createProperty(form);
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
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h1 style={{ margin: 0 }}>Objekte</h1>
        {isWrite && <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
          <Link className="btn" to="/teilungserklaerung/import">
            Teilungserklärung importieren
          </Link>
          <button className="btn btn--primary" onClick={openCreate}>
            + Neues Objekt
          </button>
        </div>}
      </div>

      {error && <p className="error-text">{error}</p>}

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

      {loading ? (
        <p>Lade...</p>
      ) : properties.length === 0 ? (
        <p className="empty-hint">Keine Objekte vorhanden.</p>
      ) : (
        <div className="card">
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
    </div>
  );
}
