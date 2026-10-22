/**
 * Open items page -- overview of offene Posten across all leases.
 *
 * Loads one complete scoped result and displays a summary per tenant/lease.
 * Rueckstand (debt) highlighted in red, Guthaben in green.
 */

import { useEffect, useState, useCallback, useContext } from "react";
import { Link } from "react-router-dom";
import {
  listLeases,
  listTenantProfiles,
  listPersons,
  listUnits,
  listProperties,
  allLeaseOpenItems,
} from "../api/endpoints";
import type {
  LeaseRecord,
  TenantProfile,
  Person,
  Unit,
  Property,
  OpenItem,
} from "../api/types";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { ApiError } from "../api/client";
import { AuthCtx } from "../auth/useAuth";
import { DunningBatch } from "../components/DunningBatch";

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "NK-Vorauszahlung",
  KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss",
  RUECKLAGENANTEIL: "Ruecklagenanteil",
  SONDERZAHLUNG: "Sonder-/Nachzahlung",
};

interface LeaseOpenItems {
  lease: LeaseRecord;
  items: OpenItem[];
  totalOpen: number;
}

export function OpenItemsPage() {
  const auth = useContext(AuthCtx);
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const currentYear = new Date().getFullYear();
  const [periodStart, setPeriodStart] = useState(`${currentYear}-01-01`);
  const [periodEnd, setPeriodEnd] = useState(`${currentYear}-12-31`);

  const [results, setResults] = useState<LeaseOpenItems[] | null>(null);
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);

  const loadLookups = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      listLeases(),
      listTenantProfiles(),
      listPersons(),
      listUnits(),
      listProperties(),
    ])
      .then(([l, tp, per, u, prop]) => {
        setLeases(l);
        setTenantProfiles(tp);
        setPersons(per);
        setUnits(u);
        setProperties(prop);
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

  function getTenantName(tenantProfileId: string): string {
    const tp = tenantProfiles.find((t) => t.id === tenantProfileId);
    return tp ? getPersonName(tp.person_id) : "-";
  }

  function getUnitLabel(unitId: string): string {
    const unit = units.find((u) => u.id === unitId);
    return unit ? unit.number : unitId.slice(0, 8);
  }

  function getPropertyName(unitId: string): string {
    const unit = units.find((u) => u.id === unitId);
    if (!unit) return "";
    const prop = properties.find((p) => p.id === unit.property_id);
    return prop ? prop.name : "";
  }

  async function handleLoadAll() {
    if (!periodStart || !periodEnd) return;
    setQuerying(true);
    setError(null);
    setResults(null);
    try {
      const response = await allLeaseOpenItems(periodStart, periodEnd, scopeId || undefined);
      const allResults = response.map(row => {
        const lease = leases.find(candidate => candidate.id === row.lease_agreement_id);
        if (!lease) throw new Error("Mietvertragsdaten haben sich geändert. Bitte Seite neu laden.");
        return { lease, items: row.items, totalOpen: row.items.reduce((sum, item) => sum + parseFloat(item.open_amount), 0) };
      });
      // Sort by total open amount (largest debt first)
      allResults.sort((a, b) => b.totalOpen - a.totalOpen);
      setResults(allResults);
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

  if (loading) return <p>Lade...</p>;

  return (
    <div>
      <h1>Offene Posten</h1>

      {error && <p role="alert" className="error-text">{error}</p>}

      {/* Period selection */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Zeitraum waehlen</h2>
        <fieldset disabled={querying} className="form-grid" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }} onChangeCapture={() => { setResults(null); setError(null); }}>
          <AccountingScopeSelector kind="MIET" capability="read" value={scopeId} onChange={setScopeId} />
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
              onClick={handleLoadAll}
              disabled={querying}
            >
              {querying ? "Laden..." : "Offene Posten laden"}
            </button>
          </div>
        </fieldset>
      </div>

      {auth && <DunningBatch periodStart={periodStart} periodEnd={periodEnd} properties={properties} />}

      {/* Results */}
      {results !== null && results.length === 0 && !querying && !error && (
        <p className="empty-hint">
          Keine offenen Posten im abgefragten Bereich und Zeitraum.
        </p>
      )}

      {(results ?? []).map((r) => (
        <div
          key={r.lease.id}
          className="card"
          style={{ marginBottom: "1rem" }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              marginBottom: "0.75rem",
              flexWrap: "wrap",
              gap: "0.5rem",
            }}
          >
            <h2 style={{ margin: 0 }}>
              {getTenantName(r.lease.tenant_profile_id)}
              <span
                style={{
                  fontSize: "0.85rem",
                  fontWeight: 400,
                  color: "var(--text-muted)",
                  marginLeft: "0.5rem",
                }}
              >
                {getPropertyName(r.lease.unit_id)} / Einheit{" "}
                {getUnitLabel(r.lease.unit_id)}
              </span>
            </h2>
            <span
              style={{
                fontFamily: "var(--mono)",
                fontWeight: 700,
                fontSize: "1.05rem",
                color: r.totalOpen > 0 ? "#b71c1c" : r.totalOpen < 0 ? "#2e7d32" : undefined,
              }}
            >
              {r.totalOpen.toFixed(2)} EUR offen
            </span>
          </div>

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
                {r.items.map((item, idx) => {
                  const openVal = parseFloat(item.open_amount);
                  return (
                    <tr key={idx}>
                      <td>
                        {item.period_year
                          ? `${item.period_year}${item.period_month ? "/" + String(item.period_month).padStart(2, "0") : ""}`
                          : "-"}
                      </td>
                      <td>
                        {PAYMENT_TYPE_LABELS[item.payment_type] ?? item.payment_type}
                      </td>
                      <td
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--mono)",
                        }}
                      >
                        {item.soll} EUR
                      </td>
                      <td
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--mono)",
                        }}
                      >
                        {item.ist} EUR
                      </td>
                      <td
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--mono)",
                          fontWeight: 600,
                          color: openVal > 0 ? "#b71c1c" : openVal < 0 ? "#2e7d32" : undefined,
                        }}
                      >
                        {item.open_amount} EUR
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <Link to={`/mietkonto`} className="btn btn--small">
              Zum Mietkonto
            </Link>
            {r.totalOpen > 0 && (
              <Link
                to={`/mahnungen?create=tenant&tenant_profile_id=${encodeURIComponent(r.lease.tenant_profile_id)}&lease_agreement_id=${encodeURIComponent(r.lease.id)}&period_start=${encodeURIComponent(periodStart)}&period_end=${encodeURIComponent(periodEnd)}${scopeId ? `&accounting_scope_id=${encodeURIComponent(scopeId)}` : ""}`}
                className="btn btn--small"
                style={{ color: "#b71c1c" }}
              >
                Mahnung erstellen
              </Link>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
