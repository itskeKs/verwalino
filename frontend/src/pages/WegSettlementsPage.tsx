/**
 * WEG-Jahresabrechnung list + create (M4). Requires a linked Wirtschaftsplan
 * (Soll source for the Abrechnungsspitze).
 */
import { useEffect, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { listWegRuns, listProperties, listEconomicPlans, createWegRun } from "../api/endpoints";
import type { WegRun, WegRunCreate, Property, EconomicPlan } from "../api/types";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = { draft: "Entwurf", finalized: "Finalisiert" };
const STATUS_CSS: Record<string, string> = { draft: "badge--draft", finalized: "badge--confirmed" };

function emptyForm(): WegRunCreate {
  const y = new Date().getFullYear() - 1;
  return { property_id: "", economic_plan_id: "", period_start: `${y}-01-01`, period_end: `${y}-12-31` };
}

export function WegSettlementsPage() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<WegRun[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [plans, setPlans] = useState<EconomicPlan[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<WegRunCreate>(emptyForm());
  const [saving, setSaving] = useState(false);

  const wegProps = properties.filter((p) => p.management_type === "WEG" || p.management_type === "MISCH");
  const plansForProperty = plans.filter((p) => p.property_id === form.property_id);

  const reload = useCallback(() => {
    setLoading(true);
    Promise.all([listWegRuns("WEG_JAHRESABRECHNUNG"), listProperties(), listEconomicPlans()])
      .then(([r, pr, pl]) => { setRuns(r); setProperties(pr); setPlans(pl); })
      .catch((err) => setError(err instanceof ApiError && err.status === 401 ? "Nicht authentifiziert." : String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => reload(), [reload]);

  function propName(id: string): string {
    return properties.find((p) => p.id === id)?.name ?? id.slice(0, 8);
  }
  function planLabel(p: EconomicPlan): string {
    return `Wirtschaftsplan ${p.plan_year} (${p.status === "finalized" ? "finalisiert" : "Entwurf"})`;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createWegRun(form);
      setShowForm(false);
      navigate(`/weg-abrechnungen/${created.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.5rem" }}>
        <h1 style={{ margin: 0 }}>WEG-Jahresabrechnungen</h1>
        <button className="btn btn--primary" onClick={() => { setForm(emptyForm()); setShowForm(true); }}>+ Neue WEG-Abrechnung</button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neue WEG-Jahresabrechnung anlegen</h2>
          {wegProps.length > 0 && plansForProperty.length === 0 && form.property_id && (
            <p className="empty-hint">Für dieses Objekt gibt es noch keinen Wirtschaftsplan. Bitte zuerst einen Wirtschaftsplan anlegen (Soll-Quelle für die Abrechnungsspitze).</p>
          )}
          <form onSubmit={handleSubmit} className="form-grid">
            <p>Der Abrechnungskreis wird aus dem gewählten Wirtschaftsplan übernommen.</p>
            <label>
              WEG-Objekt *
              <select aria-label="WEG-Objekt *" required value={form.property_id} onChange={(e) => setForm({ ...form, property_id: e.target.value, economic_plan_id: "" })}>
                <option value="">-- Objekt waehlen --</option>
                {wegProps.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.management_type})</option>)}
              </select>
            </label>
            <label>
              Wirtschaftsplan (Soll) *
              <select aria-label="Wirtschaftsplan (Soll) *" required value={form.economic_plan_id} onChange={(e) => setForm({ ...form, economic_plan_id: e.target.value })}>
                <option value="">-- Wirtschaftsplan waehlen --</option>
                {(form.property_id ? plansForProperty : []).map((p) => <option key={p.id} value={p.id}>{planLabel(p)}</option>)}
              </select>
            </label>
            <label>
              Zeitraum von *
              <input type="date" required value={form.period_start} onChange={(e) => setForm({ ...form, period_start: e.target.value })} />
            </label>
            <label>
              Zeitraum bis *
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
        <p className="empty-hint">Keine WEG-Abrechnungen vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead><tr><th>Zeitraum</th><th>Objekt</th><th style={{ textAlign: "right" }}>Σ Abrechnungsspitze</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.period_start} bis {r.period_end}</td>
                  <td>{propName(r.property_id)}</td>
                  <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>{r.balance != null ? `${r.balance} EUR` : "-"}</td>
                  <td><span className={`badge ${STATUS_CSS[r.status] ?? ""}`}>{STATUS_LABELS[r.status] ?? r.status}</span></td>
                  <td><Link to={`/weg-abrechnungen/${r.id}`} className="btn btn--small">Details</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
