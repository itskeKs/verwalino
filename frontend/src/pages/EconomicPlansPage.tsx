/**
 * Wirtschaftsplan list + create (M4). WEG/Misch properties only.
 */
import { useEffect, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { listEconomicPlans, listProperties, createEconomicPlan } from "../api/endpoints";
import type { EconomicPlan, EconomicPlanCreate, Property } from "../api/types";
import { ApiError } from "../api/client";
import { PreviousPlan } from "../components/PreviousPlan";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";

const STATUS_LABELS: Record<string, string> = { draft: "Entwurf", finalized: "Finalisiert" };
const STATUS_CSS: Record<string, string> = { draft: "badge--draft", finalized: "badge--confirmed" };

function emptyForm(): EconomicPlanCreate {
  const y = new Date().getFullYear();
  return {
    property_id: "",
    plan_year: y,
    period_start: `${y}-01-01`,
    period_end: `${y}-12-31`,
    reserve_contribution_total: "0.00",
    monthly_due_day: 1,
  };
}

export function EconomicPlansPage() {
  const navigate = useNavigate();
  const [plans, setPlans] = useState<EconomicPlan[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<EconomicPlanCreate>(emptyForm());
  const [saving, setSaving] = useState(false);

  const wegProps = properties.filter(
    (p) => p.management_type === "WEG" || p.management_type === "MISCH",
  );

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([listEconomicPlans(), listProperties()])
      .then(([pl, pr]) => {
        setPlans(pl);
        setProperties(pr);
      })
      .catch((err) =>
        setError(err instanceof ApiError && err.status === 401 ? "Nicht authentifiziert." : String(err)),
      )
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
      const created = await createEconomicPlan(form);
      setShowForm(false);
      navigate(`/wirtschaftsplaene/${created.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.5rem" }}>
        <h1 style={{ margin: 0 }}>Wirtschaftspläne</h1>
        <button className="btn btn--primary" onClick={() => { setForm(emptyForm()); setShowForm(true); }}>
          + Neuer Wirtschaftsplan
        </button>
      </div>

      <PreviousPlan properties={wegProps} />

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neuen Wirtschaftsplan anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              WEG-Objekt *
              <select aria-label="WEG-Objekt *" required value={form.property_id} onChange={(e) => setForm({ ...form, accounting_scope_id: null, property_id: e.target.value })}>
                <option value="">-- Objekt waehlen --</option>
                {wegProps.map((p) => (
                  <option key={p.id} value={p.id}>{p.name} ({p.management_type})</option>
                ))}
              </select>
            </label>
            <label>
              Wirtschaftsjahr *
              <input type="number" required value={form.plan_year}
                onChange={(e) => setForm({ ...form, plan_year: parseInt(e.target.value, 10) || form.plan_year })} />
            </label>
            <label>
              Zeitraum von *
              <input type="date" required value={form.period_start} onChange={(e) => setForm({ ...form, period_start: e.target.value })} />
            </label>
            <label>
              Zeitraum bis *
              <input type="date" required value={form.period_end} onChange={(e) => setForm({ ...form, period_end: e.target.value })} />
            </label>
            <label>
              Rücklagenzuführung (Jahr)
              <input type="text" inputMode="decimal" placeholder="0.00" value={form.reserve_contribution_total ?? ""}
                onChange={(e) => setForm({ ...form, reserve_contribution_total: e.target.value || "0.00" })} />
            </label>
            <label>
              Fälligkeitstag (monatl.)
              <input type="number" min="1" max="31" value={form.monthly_due_day ?? 1}
                onChange={(e) => setForm({ ...form, monthly_due_day: parseInt(e.target.value, 10) || null })} />
            </label>
            <AccountingScopeSelector key={form.property_id} propertyId={form.property_id} value={form.accounting_scope_id} onChange={id => setForm({ ...form, accounting_scope_id: id })} kind="WEG" />
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={saving}>{saving ? "Anlegen..." : "Anlegen"}</button>
              <button type="button" className="btn" onClick={() => setShowForm(false)}>Abbrechen</button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <p>Lade...</p>
      ) : plans.length === 0 ? (
        <p className="empty-hint">Keine Wirtschaftspläne vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr><th>Jahr</th><th>Objekt</th><th>Zeitraum</th><th>Rücklage</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {plans.map((p) => (
                <tr key={p.id}>
                  <td>{p.plan_year}</td>
                  <td>{propName(p.property_id)}</td>
                  <td>{p.period_start} bis {p.period_end}</td>
                  <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>{p.reserve_contribution_total} EUR</td>
                  <td><span className={`badge ${STATUS_CSS[p.status] ?? ""}`}>{STATUS_LABELS[p.status] ?? p.status}</span></td>
                  <td><Link to={`/wirtschaftsplaene/${p.id}`} className="btn btn--small">Details</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
