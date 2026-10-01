import { FinancialCoverageReview } from "../components/FinancialCoverageReview";
/**
 * Wirtschaftsplan detail (M4): planned-cost editor, compute (per-owner monthly
 * Hausgeld), finalize, PDF, correction.
 */
import { useEffect, useState, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  getEconomicPlan,
  computeEconomicPlan,
  finalizeEconomicPlan,
  createEconomicPlanCorrection,
  economicPlanPdfUrl,
  listProperties,
  listCostCategories,
  listOwnerProfiles,
  listPersons,
} from "../api/endpoints";
import type {
  EconomicPlan,
  EconomicPlanLine,
  PlannedCostIn,
  Property,
  CostCategory,
  OwnerProfile,
  Person,
} from "../api/types";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = { draft: "Entwurf", finalized: "Finalisiert" };
const STATUS_CSS: Record<string, string> = { draft: "badge--draft", finalized: "badge--confirmed" };

interface CostRow {
  category_code: string;
  label: string;
  planned_amount: string;
  distribution_key: string;
  cost_group: string;
  category_id: string | null;
}

export function EconomicPlanDetailPage() {
  const { planId } = useParams<{ planId: string }>();
  const navigate = useNavigate();
  const [plan, setPlan] = useState<EconomicPlan | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [categories, setCategories] = useState<CostCategory[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [rows, setRows] = useState<CostRow[]>([]);
  const [reserve, setReserve] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isDraft = plan?.status === "draft";
  const isFinalized = plan?.status === "finalized";

  const reload = useCallback(() => {
    if (!planId) return;
    setLoading(true);
    Promise.all([getEconomicPlan(planId), listProperties(), listCostCategories(), listOwnerProfiles(), listPersons()])
      .then(([pl, pr, c, op, per]) => {
        setPlan(pl);
        setProperties(pr);
        setCategories(c);
        setOwnerProfiles(op);
        setPersons(per);
        setReserve(pl.status === "finalized" ? pl.reserve_contribution_total : "");
      })
      .catch((err) => setError(err instanceof ApiError && err.status === 401 ? "Nicht authentifiziert." : String(err)))
      .finally(() => setLoading(false));
  }, [planId]);

  useEffect(() => reload(), [reload]);

  function propName(id: string): string {
    return properties.find((p) => p.id === id)?.name ?? id.slice(0, 8);
  }
  function ownerName(opId: string | null): string {
    if (!opId) return "—";
    const op = ownerProfiles.find((o) => o.id === opId);
    const per = op ? persons.find((p) => p.id === op.person_id) : undefined;
    if (!per) return opId.slice(0, 8);
    return [per.first_name, per.last_name].filter(Boolean).join(" ") || "—";
  }

  function addRow() {
    setRows([...rows, { category_code: "", label: "", planned_amount: "", distribution_key: "MEA", cost_group: "UMLAGEFAEHIG", category_id: null }]);
  }
  function setRow(idx: number, code: string) {
    const cat = categories.find((c) => c.code === code);
    const next = [...rows];
    next[idx] = {
      ...next[idx],
      category_code: code,
      label: cat?.label ?? code,
      distribution_key: cat?.owner_distribution_key_default ?? "MEA",
      cost_group: cat?.cost_group ?? "UMLAGEFAEHIG",
      category_id: cat?.id ?? null,
    };
    setRows(next);
  }
  function setRowAmount(idx: number, amount: string) {
    const next = [...rows];
    next[idx] = { ...next[idx], planned_amount: amount };
    setRows(next);
  }
  function removeRow(idx: number) {
    setRows(rows.filter((_, i) => i !== idx));
  }

  function buildRequest() {
    const planned_costs: PlannedCostIn[] = rows
      .map((r) => ({
        category_code: r.category_code,
        label: r.label,
        planned_amount: r.planned_amount,
        distribution_key: r.distribution_key,
        cost_group: r.cost_group,
        category_id: r.category_id,
      }));
    return { planned_costs, reserve_contribution_total: reserve };
  }

  async function run(fn: () => Promise<EconomicPlan>) {
    setBusy(true);
    setActionError(null);
    try {
      setPlan(await fn());
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleFinalize() {
    if (!planId) return;
    if (!window.confirm("Wirtschaftsplan finalisieren? Danach unveränderbar (GoBD); Änderung nur als Korrektur.")) return;
    await run(() => finalizeEconomicPlan(planId, buildRequest()));
  }

  async function handleCorrection() {
    if (!planId) return;
    if (!window.confirm("Korrektur-Wirtschaftsplan anlegen? Der bestehende Plan bleibt erhalten.")) return;
    setBusy(true);
    try {
      const n = await createEconomicPlanCorrection(planId);
      navigate(`/wirtschaftsplaene/${n.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p>Lade...</p>;
  if (!plan) return <p className="error-text">{error ?? "Wirtschaftsplan nicht gefunden."}</p>;

  const ownerLines = plan.lines.filter((l) => l.owner_profile_id);
  const costLines = plan.lines.filter((l) => !l.owner_profile_id);

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/wirtschaftsplaene" className="link">&larr; Alle Wirtschaftspläne</Link>
      </p>
      <h1 style={{ marginTop: 0 }}>
        Wirtschaftsplan {plan.plan_year}{" "}
        <span className={`badge ${STATUS_CSS[plan.status] ?? ""}`} style={{ fontSize: "0.8rem", verticalAlign: "middle" }}>
          {STATUS_LABELS[plan.status] ?? plan.status}
        </span>
      </h1>

      {actionError && (
        <div className="card" style={{ marginBottom: "1rem", background: "#ffebee", borderColor: "#ef9a9a" }}>
          <strong>Fehler:</strong> {actionError}
        </div>
      )}

      {isFinalized && (
        <div className="card" style={{ marginBottom: "1rem", background: "#fff8e1", borderColor: "#ffcc02" }}>
          <strong>GoBD:</strong> Finalisiert und unveränderbar. Änderungen nur als Korrektur.
          {plan.snapshot_sha256 && (
            <div style={{ marginTop: "0.5rem", fontSize: "0.82rem" }}>Snapshot SHA-256: <code>{plan.snapshot_sha256}</code></div>
          )}
        </div>
      )}
      {plan.superseded_by_id && (
        <div className="card" style={{ marginBottom: "1rem", background: "#f3e5f5", borderColor: "#ce93d8" }}>
          Ersetzt durch <Link to={`/wirtschaftsplaene/${plan.superseded_by_id}`} className="link">Korrektur</Link>.
        </div>
      )}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Übersicht</h2>
        <table className="info-table"><tbody>
          <tr><td>Objekt</td><td><Link to={`/objekte/${plan.property_id}`} className="link">{propName(plan.property_id)}</Link></td></tr>
          <tr><td>Zeitraum</td><td>{plan.period_start} bis {plan.period_end}</td></tr>
          <tr><td>Rücklagenzuführung</td><td style={{ fontFamily: "var(--mono)" }}>{plan.reserve_contribution_total} EUR</td></tr>
          <tr><td>Engine-Version</td><td><code>{plan.engine_version}</code></td></tr>
          {plan.finalized_at && <tr><td>Finalisiert am</td><td>{plan.finalized_at}</td></tr>}
        </tbody></table>
      </div>

      {isDraft && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Geplante Kosten</h2>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead><tr><th>Kategorie</th><th>Verteilerschlüssel</th><th>Geplant (Jahr)</th><th></th></tr></thead>
              <tbody>
                {rows.map((r, idx) => (
                  <tr key={idx}>
                    <td>
                      <select value={r.category_code} onChange={(e) => setRow(idx, e.target.value)}>
                        <option value="">-- Kategorie --</option>
                        {categories.map((c) => <option key={c.id} value={c.code}>{c.code} {c.label}</option>)}
                      </select>
                    </td>
                    <td>{r.distribution_key}</td>
                    <td>
                      <input type="text" inputMode="decimal" placeholder="0.00" value={r.planned_amount}
                        onChange={(e) => setRowAmount(idx, e.target.value)} />
                    </td>
                    <td><button className="btn btn--small" onClick={() => removeRow(idx)}>×</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: "0.5rem" }}>
            <button className="btn btn--small" onClick={addRow}>+ Position</button>
          </div>
          <div className="form-grid" style={{ marginTop: "1rem" }}>
            <label>
              Rücklagenzuführung (Jahr)
              <input type="text" inputMode="decimal" placeholder="0.00" value={reserve} onChange={(e) => setReserve(e.target.value)} />
            </label>
          </div>
      {plan.status === "draft" && <FinancialCoverageReview kind="economic_plan" subjectId={plan.id} values={buildRequest()} />}
          <div className="form-actions" style={{ marginTop: "1rem" }}>
            <button className="btn btn--primary" disabled={busy} onClick={() => planId && run(() => computeEconomicPlan(planId, buildRequest()))}>
              {busy ? "Berechnen..." : "Berechnen"}
            </button>
            <button className="btn btn--primary" style={{ background: "#2e7d32", borderColor: "#2e7d32" }} disabled={busy} onClick={handleFinalize}>
              Finalisieren
            </button>
          </div>
        </div>
      )}

      {isFinalized && !plan.superseded_by_id && (
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1.5rem", flexWrap: "wrap" }}>
          <button className="btn btn--primary" onClick={() => planId && window.open(economicPlanPdfUrl(planId), "_blank")}>PDF herunterladen</button>
          <button className="btn" disabled={busy} onClick={handleCorrection}>Korrektur erstellen</button>
        </div>
      )}

      {ownerLines.length > 0 && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Hausgeld-Vorschüsse je Eigentümer</h2>
          <table className="data-table">
            <thead><tr><th>Eigentümer</th><th>Anteilsbasis</th><th style={{ textAlign: "right" }}>Jahresvorschuss</th><th style={{ textAlign: "right" }}>monatlich</th></tr></thead>
            <tbody>
              {ownerLines.map((l: EconomicPlanLine) => (
                <tr key={l.id}>
                  <td>{ownerName(l.owner_profile_id)}</td>
                  <td style={{ fontFamily: "var(--mono)" }}>
                    {l.distribution_base_share != null && l.distribution_base_total != null ? `${l.distribution_base_share} / ${l.distribution_base_total}` : "-"}
                  </td>
                  <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>{l.planned_amount} EUR</td>
                  <td style={{ fontFamily: "var(--mono)", textAlign: "right", fontWeight: 600 }}>{l.monthly_amount ? `${l.monthly_amount} EUR` : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {costLines.length > 0 && (
        <div className="card">
          <h2>Geplante Kosten (berechnet)</h2>
          <table className="data-table">
            <thead><tr><th>Position</th><th>Verteilerschlüssel</th><th style={{ textAlign: "right" }}>Geplant</th></tr></thead>
            <tbody>
              {costLines.map((l) => (
                <tr key={l.id}>
                  <td>{l.label}{l.note ? <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", fontStyle: "italic" }}>{l.note}</div> : null}</td>
                  <td>{l.distribution_key ?? "-"}</td>
                  <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>{l.planned_amount} EUR</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
