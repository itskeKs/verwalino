import { ReserveLedgerPanel } from "../components/ReserveLedgerPanel";
import { FinancialCoverageReview } from "../components/FinancialCoverageReview";
import { FinancialCorrectionControls } from "../components/FinancialCorrectionControls";
/**
 * Vermoegensbericht detail (M4, §28 IV WEG): manual reserve / receivables /
 * payables inputs (clearly labelled "Verwalter-Angabe"), compute, finalize, PDF.
 * Reserve closing = opening + contributions - withdrawals (auto).
 */
import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getWegRun,
  listBankAccounts,
  computeAssetReport,
  finalizeAssetReport,
  assetReportPdfUrl,
  listProperties,
} from "../api/endpoints";
import type { WegRun, AssetItemIn, AssetReportComputeRequest, Property, BankAccount } from "../api/types";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = { draft: "Entwurf", finalized: "Finalisiert" };
const STATUS_CSS: Record<string, string> = { draft: "badge--draft", finalized: "badge--confirmed" };

const LINE_LABELS: Record<string, string> = {
  RESERVE_OPENING: "Erhaltungsrücklage Anfangsbestand",
  RESERVE_CONTRIBUTION: "Zuführung",
  RESERVE_WITHDRAWAL: "Entnahme",
  RESERVE_CLOSING: "Erhaltungsrücklage Endbestand",
  BANK_BALANCE: "Bankkonto",
  RECEIVABLE: "Forderung",
  PAYABLE: "Verbindlichkeit",
};

export function AssetReportDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<WegRun | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [balances, setBalances] = useState<Record<string, string>>({});
  const [opening, setOpening] = useState("");
  const [contributions, setContributions] = useState("");
  const [withdrawals, setWithdrawals] = useState("");
  const [reserveReason, setReserveReason] = useState("");
  const [ledgerVersion, setLedgerVersion] = useState(0);
  const [ledgerRefresh, setLedgerRefresh] = useState(0);
  const [receivables, setReceivables] = useState<AssetItemIn[]>([]);
  const [payables, setPayables] = useState<AssetItemIn[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pageGeneration = useRef(0);

  const isDraft = run?.status === "draft";
  const isFinalized = run?.status === "finalized";

  useEffect(() => {
    if (!runId) return;
    let active = true;
    pageGeneration.current += 1; setBusy(false);
    setLoading(true); setRun(null); setError(null); setActionError(null);
    setAccounts([]); setBalances({}); setOpening(''); setContributions(''); setWithdrawals(''); setReserveReason(''); setReceivables([]); setPayables([]);
    Promise.all([getWegRun(runId), listProperties()])
      .then(async ([r, pr]) => {
        const accounts = await listBankAccounts(r.property_id);
        if (active) { setRun(r); setProperties(pr); setAccounts(accounts); }
      })
      .catch((err) => { if (active) setError(err instanceof ApiError && err.status === 401 ? "Nicht authentifiziert." : String(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; pageGeneration.current += 1; };
  }, [runId]);

  function propName(id: string): string {
    return properties.find((p) => p.id === id)?.name ?? id.slice(0, 8);
  }

  function req(): AssetReportComputeRequest {
    return {
      reserve_opening_balance: opening || null,
      reserve_override_reason: reserveReason,
      reserve_withdrawals_total: withdrawals || null,
      reserve_contributions_total: contributions || null,
      bank_balances: accounts.map((account) => ({ bank_account_id: account.id, label: account.label, account_type: account.account_type, balance: balances[account.id] ?? "" })),
      receivables: receivables,
      payables: payables,
    };
  }

  async function run_(fn: () => Promise<WegRun>) {
    const current = pageGeneration.current;
    setBusy(true);
    setActionError(null);
    try {
      const result = await fn();
      if (pageGeneration.current === current) { setRun(result); setLedgerRefresh(v => v + 1); }
    } catch (err) {
      if (pageGeneration.current === current) setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (pageGeneration.current === current) setBusy(false);
    }
  }

  async function handleFinalize() {
    if (!runId) return;
    if (!window.confirm("Vermögensbericht finalisieren? Danach unveränderbar (GoBD).")) return;
    await run_(() => finalizeAssetReport(runId, req()));
  }

  function itemEditor(items: AssetItemIn[], set: (v: AssetItemIn[]) => void, title: string) {
    return (
      <div style={{ marginTop: "1rem" }}>
        <h3 style={{ fontSize: "0.95rem", margin: "0 0 0.5rem" }}>{title} <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>(Verwalter-Angabe)</span></h3>
        {items.map((it, idx) => (
          <div key={idx} style={{ display: "flex", gap: "0.5rem", marginBottom: "0.4rem", flexWrap: "wrap" }}>
            <input type="text" placeholder="Bezeichnung" value={it.label}
              onChange={(e) => { const n = [...items]; n[idx] = { ...it, label: e.target.value }; set(n); }} />
            <input type="text" inputMode="decimal" placeholder="0.00" value={it.amount} style={{ maxWidth: "140px" }}
              onChange={(e) => { const n = [...items]; n[idx] = { ...it, amount: e.target.value }; set(n); }} />
            <button className="btn btn--small" onClick={() => set(items.filter((_, i) => i !== idx))}>×</button>
          </div>
        ))}
        <button className="btn btn--small" onClick={() => set([...items, { label: "", amount: "" }])}>+ Position</button>
      </div>
    );
  }

  if (loading) return <p>Lade...</p>;
  if (!run) return <p className="error-text">{error ?? "Vermögensbericht nicht gefunden."}</p>;

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/vermoegensberichte" className="link">&larr; Alle Vermögensberichte</Link>
      </p>
      <h1 style={{ marginTop: 0 }}>
        Vermögensbericht{" "}
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
          <tr><td>Stichtag</td><td>{run.period_end}</td></tr>
          <tr><td>Engine-Version</td><td><code>{run.engine_version}</code></td></tr>
          {run.finalized_at && <tr><td>Finalisiert am</td><td>{run.finalized_at}</td></tr>}
        </tbody></table>
      </div>

      <ReserveLedgerPanel key={`ledger-${run.id}`} runId={run.id} propertyId={run.property_id} status={run.status} disabled={busy} refreshKey={ledgerRefresh}
        onChanged={() => { setLedgerVersion(v => v + 1); setRun({ ...run, lines: [] }); }} />
      {isDraft && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Vermögenswerte erfassen</h2>
          <p style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
            Die Erhaltungsrücklage stammt aus dem Rücklagenbuch. Leere Rücklagenfelder übernehmen dessen Werte. Manuelle Gesamtwerte benötigen eine Begründung. Bitte alle Kontostände zum Stichtag prüfen.
            Forderungen/Verbindlichkeiten sind manuelle Verwalter-Angaben (kein Buchungssaldo).
          </p>
          <div className="form-grid">
            <label>Rücklage Anfangsbestand
              <input type="text" inputMode="decimal" placeholder="0.00" value={opening} onChange={(e) => setOpening(e.target.value)} />
            </label>
            <label>Rücklage Zuführung
              <input type="text" inputMode="decimal" placeholder="0.00" value={contributions} onChange={(e) => setContributions(e.target.value)} />
            </label>
            <label>Rücklage Entnahmen
              <input type="text" inputMode="decimal" placeholder="0.00" value={withdrawals} onChange={(e) => setWithdrawals(e.target.value)} />
            </label>
            <label className="form-full-width">Begründung der Rücklagenwerte
              <textarea rows={3} value={reserveReason} maxLength={2000} onChange={e => setReserveReason(e.target.value)} />
            </label>
          </div>
          {accounts.map((account) => <label key={account.id}>Kontostand · {account.label}
            <input inputMode="decimal" placeholder="Unbekannt" value={balances[account.id] ?? ""} onChange={(e) => setBalances({ ...balances, [account.id]: e.target.value })} />
          </label>)}
          {itemEditor(receivables, setReceivables, "Forderungen")}
          {itemEditor(payables, setPayables, "Verbindlichkeiten")}
      {run.status === "draft" && <FinancialCoverageReview key={ledgerVersion} kind="settlement" subjectId={run.id} values={req()} onSaved={() => setLedgerRefresh(v => v + 1)} />}
          <div className="form-actions" style={{ marginTop: "1rem" }}>
            <button className="btn btn--primary" disabled={busy} onClick={() => runId && run_(() => computeAssetReport(runId, req()))}>
              {busy ? "Berechnen..." : "Berechnen"}
            </button>
            <button className="btn btn--primary" style={{ background: "#2e7d32", borderColor: "#2e7d32" }} disabled={busy} onClick={handleFinalize}>
              Finalisieren
            </button>
          </div>
        </div>
      )}

      {isFinalized && (
        <div style={{ marginBottom: "1.5rem" }}>
          <button className="btn btn--primary" onClick={() => runId && window.open(assetReportPdfUrl(runId), "_blank")}>PDF herunterladen</button>
        </div>
      )}

      <FinancialCorrectionControls key={run.id} run={run} />

      {run.lines.length > 0 && (
        <div className="card">
          <h2>Vermögensaufstellung</h2>
          <table className="data-table">
            <thead><tr><th>Position</th><th style={{ textAlign: "right" }}>Betrag</th></tr></thead>
            <tbody>
              {run.lines.map((l) => {
                const isClosing = l.line_type === "RESERVE_CLOSING";
                return (
                  <tr key={l.id} style={isClosing ? { fontWeight: 700 } : undefined}>
                    <td>
                      {LINE_LABELS[l.line_type] ?? l.label}
                      {l.label && LINE_LABELS[l.line_type] && l.label !== LINE_LABELS[l.line_type]
                        ? <span style={{ color: "var(--text-muted)" }}> — {l.label}</span> : null}
                      {l.note ? <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", fontStyle: "italic" }}>{l.note}</div> : null}
                    </td>
                    <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>{l.amount} EUR</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
