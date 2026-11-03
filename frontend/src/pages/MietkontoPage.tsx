/**
 * Mietkonto page -- Soll/Ist/Saldo + offene Posten per lease or owner.
 *
 * M6 Mietkonto: select a lease or owner, date range, then view the
 * account statement. Rueckstand (open debt) shown in red, Guthaben in green.
 */

import { useEffect, useState, useCallback } from "react";
import {
  listLeases,
  listTenantProfiles,
  listOwnerProfiles,
  listPersons,
  listUnits,
  listProperties,
  leaseStatement,
  ownerStatement,
  listEconomicPlans,
} from "../api/endpoints";
import type {
  LeaseRecord,
  TenantProfile,
  OwnerProfile,
  Person,
  Unit,
  Property,
  MietkontoResult,
  EconomicPlan,
} from "../api/types";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { ApiError } from "../api/client";

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "Betriebskosten-Vorauszahlung", HK_VORAUSZAHLUNG: "Heizkosten-Vorauszahlung",
  KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss",
  RUECKLAGENANTEIL: "Ruecklagenanteil",
  SONDERZAHLUNG: "Sonder-/Nachzahlung",
};

type ViewMode = "lease" | "owner";

export function MietkontoPage() {
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [economicPlans, setEconomicPlans] = useState<EconomicPlan[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Selection
  const [viewMode, setViewMode] = useState<ViewMode>("lease");
  const [selectedLeaseId, setSelectedLeaseId] = useState("");
  const [selectedOwnerId, setSelectedOwnerId] = useState("");
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [scopeId, setScopeId] = useState<string | null>(null);
  const scopePropertyId = viewMode === "lease"
    ? units.find(unit => unit.id === leases.find(lease => lease.id === selectedLeaseId)?.unit_id)?.property_id
    : economicPlans.find(plan => plan.id === selectedPlanId)?.property_id;
  const currentYear = new Date().getFullYear();
  const [periodStart, setPeriodStart] = useState(`${currentYear}-01-01`);
  const [periodEnd, setPeriodEnd] = useState(`${currentYear}-12-31`);

  // Result
  const [result, setResult] = useState<MietkontoResult | null>(null);
  const [querying, setQuerying] = useState(false);

  const loadLookups = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      listLeases(),
      listTenantProfiles(),
      listOwnerProfiles(),
      listPersons(),
      listUnits(),
      listProperties(),
      listEconomicPlans(),
    ])
      .then(([l, tp, op, per, u, prop, ep]) => {
        setLeases(l);
        setTenantProfiles(tp);
        setOwnerProfiles(op);
        setPersons(per);
        setUnits(u);
        setProperties(prop);
        setEconomicPlans(ep);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadLookups();
  }, [loadLookups]);

  function getPersonName(personId: string): string {
    const person = persons.find((p) => p.id === personId);
    if (!person) return "-";
    const parts = [person.first_name, person.last_name].filter(Boolean);
    return parts.length > 0 ? parts.join(" ") : "-";
  }

  function getLeaseLabel(lease: LeaseRecord): string {
    const tp = tenantProfiles.find((t) => t.id === lease.tenant_profile_id);
    const name = tp ? getPersonName(tp.person_id) : "?";
    const unit = units.find((u) => u.id === lease.unit_id);
    const unitLabel = unit ? `${unit.number}` : "";
    const prop = unit ? properties.find((p) => p.id === unit.property_id) : null;
    const propName = prop ? `${prop.name} / ` : "";
    return `${propName}${unitLabel} - ${name} (ab ${lease.valid_from}${lease.valid_to ? ` bis ${lease.valid_to}` : ""})`;
  }

  function getOwnerLabel(op: OwnerProfile): string {
    return getPersonName(op.person_id);
  }

  async function handleQuery() {
    if (viewMode === "lease" && !selectedLeaseId) return;
    if (viewMode === "owner" && !selectedOwnerId) return;
    if (!periodStart || !periodEnd) return;

    setQuerying(true);
    setError(null);
    setResult(null);
    try {
      if (viewMode === "lease") {
        const r = await leaseStatement(selectedLeaseId, periodStart, periodEnd, scopeId || undefined);
        setResult(r);
      } else {
        const r = await ownerStatement(
          selectedOwnerId,
          periodStart,
          periodEnd,
          selectedPlanId || undefined,
          scopeId || undefined,
        );
        setResult(r);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setQuerying(false);
    }
  }

  function balanceColor(balance: string): string | undefined {
    const val = parseFloat(balance);
    if (val > 0) return "#b71c1c"; // Soll - Ist > 0: Rueckstand
    if (val < 0) return "#2e7d32"; // Guthaben
    return undefined;
  }

  function openAmountColor(amount: string): string | undefined {
    const val = parseFloat(amount);
    if (val > 0) return "#b71c1c"; // open debt = red
    if (val < 0) return "#2e7d32"; // overpayment = green
    return undefined;
  }

  if (loading) return <p>Lade...</p>;

  return (
    <div>
      <h1>Mietkonto</h1>

      {error && <p className="error-text">{error}</p>}

      {/* Selection form */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Kontoauszug abrufen</h2>
        <fieldset disabled={querying} className="form-grid [&>*]:min-w-0 [&_input]:min-w-0 [&_select]:min-w-0" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }} onChangeCapture={() => { setResult(null); setError(null); }}>
          <label>
            Ansicht
            <select
              aria-label="Ansicht"
              value={viewMode}
              onChange={(e) => {
                setViewMode(e.target.value as ViewMode);
                setResult(null);
                setScopeId(null);
              }}
            >
              <option value="lease">Mietvertrag (Mieter)</option>
              <option value="owner">Eigentuemer (Hausgeld)</option>
            </select>
          </label>

          {viewMode === "lease" ? (
            <label>
              <span id="statement-lease-label">Mietvertrag *</span>
              <select
                aria-labelledby="statement-lease-label"
                required
                value={selectedLeaseId}
                onChange={(e) => { setSelectedLeaseId(e.target.value); setScopeId(null); }}
              >
                <option value="">-- Mietvertrag waehlen --</option>
                {leases.map((l) => (
                  <option key={l.id} value={l.id}>
                    {getLeaseLabel(l)}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <label>
                Eigentuemer *
                <select
                  required
                  aria-label="Eigentuemer *"
                  value={selectedOwnerId}
                  onChange={(e) => { setSelectedOwnerId(e.target.value); setScopeId(null); }}
                >
                  <option value="">-- Eigentuemer waehlen --</option>
                  {ownerProfiles.map((op) => (
                    <option key={op.id} value={op.id}>
                      {getOwnerLabel(op)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Wirtschaftsplan (fuer Soll)
                <select
                  value={selectedPlanId}
                  onChange={(e) => { setSelectedPlanId(e.target.value); setScopeId(null); }}
                >
                  <option value="">(kein -- Sonderumlagen und Ist)</option>
                  {economicPlans
                    .filter((ep) => ep.status === "finalized")
                    .map((ep) => {
                      const prop = properties.find((p) => p.id === ep.property_id);
                      return (
                        <option key={ep.id} value={ep.id}>
                          {prop?.name ?? ""} - {ep.plan_year}
                        </option>
                      );
                    })}
                </select>
              </label>
            </>
          )}

          <AccountingScopeSelector key={`${viewMode}:${selectedLeaseId}:${selectedOwnerId}:${selectedPlanId}`}
            propertyId={scopePropertyId} kind={viewMode === "lease" ? "MIET" : "WEG"}
            capability="read" value={scopeId} onChange={setScopeId} />
          {viewMode === "owner" && selectedPlanId && <p className="muted">Ohne Auswahl wird der Abrechnungskreis des Wirtschaftsplans verwendet.</p>}
          <label>
            Zeitraum von *
            <input
              type="date"
              required
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
            />
          </label>
          <label>
            Zeitraum bis *
            <input
              type="date"
              required
              value={periodEnd}
              onChange={(e) => setPeriodEnd(e.target.value)}
            />
          </label>

          <div className="form-actions">
            <button
              className="btn btn--primary"
              onClick={handleQuery}
              disabled={querying}
            >
              {querying ? "Laden..." : "Abrufen"}
            </button>
          </div>
        </fieldset>
      </div>

      {/* Result */}
      {result && (
        <div className="card">
          <h2>Kontoauszug</h2>

          {/* Summary */}
          <table className="info-table" style={{ marginBottom: "1.25rem" }}>
            <tbody>
              <tr>
                <td>Soll gesamt</td>
                <td style={{ fontFamily: "var(--mono)" }}>
                  {result.soll_total} EUR
                </td>
              </tr>
              <tr>
                <td>Ist gesamt</td>
                <td style={{ fontFamily: "var(--mono)" }}>
                  {result.ist_total} EUR
                </td>
              </tr>
              <tr>
                <td>
                  <strong>Saldo</strong>
                </td>
                <td
                  style={{
                    fontFamily: "var(--mono)",
                    fontWeight: 700,
                    fontSize: "1.05rem",
                    color: balanceColor(result.balance),
                  }}
                >
                  {result.balance} EUR
                  {parseFloat(result.balance) > 0 && " (Rueckstand)"}
                  {parseFloat(result.balance) < 0 && " (Guthaben)"}
                </td>
              </tr>
            </tbody>
          </table>

          {/* Open items detail */}
          <h3>Offene Posten je Periode/Typ</h3>
          {result.open_items.length === 0 ? (
            <p className="empty-hint">Keine Posten in diesem Zeitraum.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Periode</th>
                    <th>Typ</th>
                    <th style={{ textAlign: "right" }}>Soll</th>
                    <th style={{ textAlign: "right" }}>Ist</th>
                    <th style={{ textAlign: "right" }}>Offen</th>
                  </tr>
                </thead>
                <tbody>
                  {result.open_items.map((item, idx) => (
                    <tr key={idx}>
                      <td>
                        {item.period_year
                          ? `${item.period_year}${item.period_month ? "/" + String(item.period_month).padStart(2, "0") : ""}`
                          : "-"}
                      </td>
                      <td>
                        {PAYMENT_TYPE_LABELS[item.payment_type] ?? item.payment_type}
                      </td>
                      <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}>
                        {item.soll} EUR
                      </td>
                      <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}>
                        {item.ist} EUR
                      </td>
                      <td
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--mono)",
                          fontWeight: 600,
                          color: openAmountColor(item.open_amount),
                        }}
                      >
                        {item.open_amount} EUR
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
