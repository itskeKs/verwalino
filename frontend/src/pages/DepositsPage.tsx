import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDeposits, listLeases } from "../api/endpoints";
import type { DepositAccount, LeaseRecord } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";

export function DepositsPage() {
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [scopeValid, setScopeValid] = useState(true);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ accounts: DepositAccount[]; leases: LeaseRecord[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let current = true;
    void Promise.resolve().then(async () => {
      if (!current) return;
      setResult(null);
      setError(null);
      setLoading(true);
      if (!scopeValid) {
        setLoading(false);
        return;
      }
      try {
        const [leases, accounts] = await Promise.all([listLeases(), listDeposits(scopeId || undefined)]);
        if (current) setResult({ leases, accounts });
      } catch (err) {
        if (current) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (current) setLoading(false);
      }
    });
    return () => { current = false; };
  }, [scopeId, scopeValid, revision]);

  function changeScope(id: string | null) {
    setResult(null);
    setError(null);
    setLoading(true);
    setScopeId(id);
  }

  function leaseLabel(id: string): string {
    const lease = result?.leases.find(item => item.id === id);
    return lease
      ? `Mietvertrag ab ${lease.valid_from}${lease.valid_to ? ` bis ${lease.valid_to}` : " (laufend)"}`
      : "Vertragszeitraum nicht verfügbar";
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <h1>Kautionen</h1>
        {canWrite && <Link className="btn btn--primary" to="/zahlungen">Zahlung erfassen</Link>}
      </div>
      <div className="card mb-4">
        <AccountingScopeSelector capability="read" value={scopeId} onChange={changeScope} onValidityChange={setScopeValid} />
        <p className="muted">Kautionen werden getrennt vom Mietsaldo geführt. Zahlungen und Rückzahlungen werden unter Zahlungen mit Typ Kaution erfasst.</p>
        <button className="btn" disabled={loading || !scopeValid} onClick={() => {
          setResult(null);
          setError(null);
          setLoading(true);
          setRevision(value => value + 1);
        }}>Aktualisieren</button>
      </div>
      {!scopeValid && <p role="alert" className="error-text">Bitte einen verfügbaren Abrechnungskreis wählen.</p>}
      {error && <p role="alert" className="error-text">{error}</p>}
      {loading && <p role="status">Lade Kautionen...</p>}
      {!loading && !error && scopeValid && result && (
        result.accounts.length === 0 ? <p className="empty-hint">Keine Kautionen vorhanden.</p> : (
          <div className="card" style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead><tr>
                <th>Mieter</th><th>Objekt</th><th>Einheit</th>
                {["Soll", "Gezahlt", "Zurück", "Bestand"].map(label => <th key={label} style={{ textAlign: "right" }}>{label}</th>)}
              </tr></thead>
              <tbody>{result.accounts.map(account => (
                <tr key={account.lease_agreement_id}>
                  <td>{account.tenant_name}<div className="muted text-xs">{leaseLabel(account.lease_agreement_id)}</div></td>
                  <td>{account.property_name}</td><td>{account.unit_label}</td>
                  {[account.expected_amount, account.paid_amount, account.refunded_amount, account.held_amount].map((amount, index) => (
                    <td key={index} style={{ textAlign: "right", fontFamily: "var(--mono)" }}>{amount} EUR</td>
                  ))}
                </tr>
              ))}</tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}
