/**
 * Vermoegensbericht (§28 IV WEG) list + create (M4).
 */
import { useEffect, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { listWegRuns, listProperties, createAssetReport } from "../api/endpoints";
import type { WegRun, AssetReportCreate, Property } from "../api/types";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = { draft: "Entwurf", finalized: "Finalisiert" };
const STATUS_CSS: Record<string, string> = { draft: "badge--draft", finalized: "badge--confirmed" };

function emptyForm(): AssetReportCreate {
  const y = new Date().getFullYear() - 1;
  return { property_id: "", period_start: `${y}-01-01`, period_end: `${y}-12-31` };
}

export function AssetReportsPage() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<WegRun[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<AssetReportCreate>(emptyForm());
  const [saving, setSaving] = useState(false);

  const wegProps = properties.filter((p) => p.management_type === "WEG" || p.management_type === "MISCH");

  const reload = useCallback(() => {
    setLoading(true);
    Promise.all([listWegRuns("VERMOEGENSBERICHT"), listProperties()])
      .then(([r, pr]) => { setRuns(r); setProperties(pr); })
      .catch((err) => setError(err instanceof ApiError && err.status === 401 ? "Nicht authentifiziert." : String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => reload(), [reload]);

  function propName(id: string): string {
    return properties.find((p) => p.id === id)?.name ?? id.slice(0, 8);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createAssetReport(form);
      setShowForm(false);
      navigate(`/vermoegensberichte/${created.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.5rem" }}>
        <h1 style={{ margin: 0 }}>Vermögensberichte</h1>
        <button className="btn btn--primary" onClick={() => { setForm(emptyForm()); setShowForm(true); }}>+ Neuer Vermögensbericht</button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neuen Vermögensbericht anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              WEG-Objekt *
              <select required value={form.property_id} onChange={(e) => setForm({ ...form, property_id: e.target.value })}>
                <option value="">-- Objekt waehlen --</option>
                {wegProps.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.management_type})</option>)}
              </select>
            </label>
            <label>Zeitraum von *
              <input type="date" required value={form.period_start} onChange={(e) => setForm({ ...form, period_start: e.target.value })} />
            </label>
            <label>Stichtag (bis) *
              <input type="date" required value={form.period_end} onChange={(e) => setForm({ ...form, period_end: e.target.value })} />
            </label>
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={saving}>{saving ? "Anlegen..." : "Anlegen"}</button>
              <button type="button" className="btn" onClick={() => setShowForm(false)}>Abbrechen</button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <p>Lade...</p>
      ) : runs.length === 0 ? (
        <p className="empty-hint">Keine Vermögensberichte vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead><tr><th>Stichtag</th><th>Objekt</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.period_end}</td>
                  <td>{propName(r.property_id)}</td>
                  <td><span className={`badge ${STATUS_CSS[r.status] ?? ""}`}>{STATUS_LABELS[r.status] ?? r.status}</span></td>
                  <td><Link to={`/vermoegensberichte/${r.id}`} className="btn btn--small">Details</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
