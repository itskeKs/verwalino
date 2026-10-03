import { FinancialCoverageReview } from "../components/FinancialCoverageReview";
import { FinancialCorrectionControls, PlanSuccessorSelector } from "../components/FinancialCorrectionControls";
/**
 * WEG-Jahresabrechnung detail (M4): reserve inputs, compute, finalize, PDF.
 * Per-owner breakdown with Abrechnungsspitze (Ist - Soll, WEMoG) highlighted.
 */
import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getWegRun,
  getEconomicPlan,
  computeWegRun,
  finalizeWegRun,
  wegRunPdfUrl,
  listProperties,
  listOwnerProfiles,
  listPersons,
} from "../api/endpoints";
import type { WegRun, WegLine, WegComputeRequest, Property, OwnerProfile, Person } from "../api/types";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = { draft: "Entwurf", finalized: "Finalisiert" };
const STATUS_CSS: Record<string, string> = { draft: "badge--draft", finalized: "badge--confirmed" };

const LINE_LABELS: Record<string, string> = {
  OWNER_COST_TOTAL: "Summe Ist-Kosten (Ihr Anteil)",
  PLAN_ADVANCE: "Soll-Vorschüsse (Wirtschaftsplan)",
  ABRECHNUNGSSPITZE: "Abrechnungsspitze",
  RESERVE_CONTRIBUTION: "Zuführung Erhaltungsrücklage",
};

export function WegSettlementDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<WegRun | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [paidOwners, setPaidOwners] = useState<string[]>([]);
  const [paidValues, setPaidValues] = useState<Record<string, string>>({});
  const [opening, setOpening] = useState("");
  const [withdrawals, setWithdrawals] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [planBusy, setPlanBusy] = useState(false);
  const [generation, setGeneration] = useState(0);
  const pageGeneration = useRef(0);

  const isDraft = run?.status === "draft";
  const isFinalized = run?.status === "finalized";

  useEffect(() => {
    if (!runId) return;
    let active = true;
    pageGeneration.current += 1; setBusy(false);
    setLoading(true); setRun(null); setError(null); setActionError(null);
    setPaidOwners([]); setPaidValues({}); setOpening(''); setWithdrawals('');
    Promise.all([getWegRun(runId), listProperties(), listOwnerProfiles(), listPersons()])
      .then(async ([r, pr, op, per]) => {
        const plan = r.economic_plan_id ? await getEconomicPlan(r.economic_plan_id) : null;
        if (!active) return;
        setRun(r); setProperties(pr); setOwnerProfiles(op); setPersons(per);
        setPaidOwners([...new Set((plan?.lines ?? []).map((line) => line.owner_profile_id).filter((id): id is string => Boolean(id)))]);
      })
      .catch((err) => { if (active) setError(err instanceof ApiError && err.status === 401 ? "Nicht authentifiziert." : String(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; pageGeneration.current += 1; };
  }, [runId, generation]);

  function propName(id: string): string {
    return properties.find((p) => p.id === id)?.name ?? id.slice(0, 8);
  }
  function ownerName(opId: string | null): string {
    if (!opId) return "Objektweit";
    const op = ownerProfiles.find((o) => o.id === opId);
    const per = op ? persons.find((p) => p.id === op.person_id) : undefined;
    if (!per) return opId.slice(0, 8);
    return [per.first_name, per.last_name].filter(Boolean).join(" ") || "—";
  }

  function req(): WegComputeRequest {
    return { reserve_opening_balance: opening, reserve_withdrawals_total: withdrawals, paid_advances: paidOwners.map((id) => ({ owner_profile_id: id, paid_total: paidValues[id] ?? "" })) };
  }

  async function run_(fn: () => Promise<WegRun>) {
    const current = pageGeneration.current;
    setBusy(true);
    setActionError(null);
    try {
      const result = await fn();
      if (pageGeneration.current === current) setRun(result);
    } catch (err) {
      if (pageGeneration.current === current) setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (pageGeneration.current === current) setBusy(false);
    }
  }

  async function handleFinalize() {
    if (!runId) return;
    if (!window.confirm("WEG-Jahresabrechnung finalisieren? Danach unveränderbar (GoBD).")) return;
    await run_(() => finalizeWegRun(runId, req()));
  }

  if (loading) return <p>Lade...</p>;
  if (!run) return <p className="error-text">{error ?? "WEG-Abrechnung nicht gefunden."}</p>;

  // Group lines by owner.
  const ownerIds = Array.from(new Set(run.lines.map((l) => l.addressee_owner_profile_id ?? "—")));

  function ownerLines(oid: string): WegLine[] {
    return run!.lines.filter((l) => (l.addressee_owner_profile_id ?? "—") === oid);
  }
  function spitzeColor(amount: string): string | undefined {
    const v = parseFloat(amount);
    return v > 0 ? "#b71c1c" : v < 0 ? "#2e7d32" : undefined;
  }

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/weg-abrechnungen" className="link">&larr; Alle WEG-Abrechnungen</Link>
      </p>
      <h1 style={{ marginTop: 0 }}>
        WEG-Jahresabrechnung{" "}
        <span className={`badge ${STATUS_CSS[run.status] ?? ""}`} style={{ fontSize: "0.8rem", verticalAlign: "middle" }}>
          {STATUS_LABELS[run.status] ?? run.status}
        </span>
      </h1>

      {actionError && (
        <div className="card" style={{ marginBottom: "1rem", background: "#ffebee", borderColor: "#ef9a9a" }}>
          <strong>Fehler:</strong> {actionError}
        </div>
      )}
      {isFinalized && (
        <div className="card" style={{ marginBottom: "1rem", background: "#fff8e1", borderColor: "#ffcc02" }}>
          <strong>GoBD:</strong> Finalisiert und unveränderbar.
          {run.snapshot_sha256 && <div style={{ marginTop: "0.5rem", fontSize: "0.82rem" }}>Snapshot SHA-256: <code>{run.snapshot_sha256}</code></div>}
        </div>
      )}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Übersicht</h2>
        <table className="info-table"><tbody>
          <tr><td>Objekt</td><td><Link to={`/objekte/${run.property_id}`} className="link">{propName(run.property_id)}</Link></td></tr>
          <tr><td>Zeitraum</td><td>{run.period_start} bis {run.period_end}</td></tr>
          {run.total_cost != null && <tr><td>Gesamtkosten (Ist, ohne Rücklage)</td><td style={{ fontFamily: "var(--mono)" }}>{run.total_cost} EUR</td></tr>}
          {run.balance != null && <tr><td>Σ Abrechnungsspitze</td><td style={{ fontFamily: "var(--mono)", fontWeight: 700 }}>{run.balance} EUR</td></tr>}
          <tr><td>Engine-Version</td><td><code>{run.engine_version}</code></td></tr>
          {run.finalized_at && <tr><td>Finalisiert am</td><td>{run.finalized_at}</td></tr>}
        </tbody></table>
      </div>

      {isDraft && (
        <fieldset disabled={busy || planBusy} className="card" style={{ marginBottom: "1rem" }}>
          <h2>Rücklage & Berechnung</h2>
          {paidOwners.map((id) => <label key={id}>Tatsächlich gezahltes Hausgeld · {ownerName(id)}
            <input inputMode="decimal" placeholder="Unbekannt" value={paidValues[id] ?? ""} onChange={(e) => setPaidValues({ ...paidValues, [id]: e.target.value })} />
          </label>)}
          <div className="form-grid">
            <label>Rücklagen-Anfangsbestand
              <input type="text" inputMode="decimal" placeholder="0.00" value={opening} onChange={(e) => setOpening(e.target.value)} />
            </label>
            <label>Rücklagen-Entnahmen (Jahr)
              <input type="text" inputMode="decimal" placeholder="0.00" value={withdrawals} onChange={(e) => setWithdrawals(e.target.value)} />
            </label>
          </div>
      {run.status === "draft" && <FinancialCoverageReview kind="settlement" subjectId={run.id} values={req()} />}
          <div className="form-actions" style={{ marginTop: "1rem" }}>
            <button className="btn btn--primary" disabled={busy} onClick={() => runId && run_(() => computeWegRun(runId, req()))}>
              {busy ? "Berechnen..." : "Berechnen"}
            </button>
            <button className="btn btn--primary" style={{ background: "#2e7d32", borderColor: "#2e7d32" }} disabled={busy} onClick={handleFinalize}>
              Finalisieren
            </button>
          </div>
        </fieldset>
      )}

      {isDraft && <PlanSuccessorSelector key={`plan-${run.id}`} runId={run.id} disabled={busy} onBlockedChange={setPlanBusy} onApplied={() => setGeneration(value => value + 1)} />}
      <FinancialCorrectionControls key={`correction-${run.id}`} run={run} />

      {isFinalized && (
        <div style={{ marginBottom: "1.5rem" }}>
          <button className="btn btn--primary" onClick={() => runId && window.open(wegRunPdfUrl(runId), "_blank")}>PDF herunterladen</button>
        </div>
      )}

      {run.lines.length === 0 ? (
        <p className="empty-hint">Noch nicht berechnet. Klicken Sie „Berechnen".</p>
      ) : (
        ownerIds.map((oid) => (
          <div className="card" key={oid} style={{ marginBottom: "1rem" }}>
            <h3 style={{ marginTop: 0 }}>{ownerName(oid === "—" ? null : oid)}</h3>
            <table className="data-table">
              <thead><tr><th>Position</th><th>Schlüssel</th><th style={{ textAlign: "right" }}>Gesamt</th><th style={{ textAlign: "right" }}>Betrag</th></tr></thead>
              <tbody>
                {ownerLines(oid).map((l) => {
                  const isSummary = l.line_type !== "COST" && l.line_type !== "RESERVE_CONTRIBUTION";
                  const isSpitze = l.line_type === "ABRECHNUNGSSPITZE";
                  return (
                    <tr key={l.id} style={isSummary ? { fontWeight: isSpitze ? 700 : 600 } : undefined}>
                      <td>
                        {LINE_LABELS[l.line_type] ?? l.label}
                        {l.note ? <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", fontStyle: "italic" }}>{l.note}</div> : null}
                      </td>
                      <td>{l.distribution_key ?? ""}</td>
                      <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>{l.total_amount != null ? `${l.total_amount} EUR` : ""}</td>
                      <td style={{ fontFamily: "var(--mono)", textAlign: "right", color: isSpitze ? spitzeColor(l.amount) : undefined }}>
                        {l.amount} EUR
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))
      )}
    </div>
  );
}
