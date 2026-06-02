/**
 * Open items page -- overview of offene Posten across all leases.
 *
 * M6 offene Posten: loads all leases, queries the open items endpoint
 * for each, and displays a summary per tenant/lease.
 * Rueckstand (debt) highlighted in red, Guthaben in green.
 */

import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  listLeases,
  listTenantProfiles,
  listPersons,
  listUnits,
  listProperties,
  leaseOpenItems,
} from "../api/endpoints";
import type {
  LeaseRecord,
  TenantProfile,
  Person,
  Unit,
  Property,
  OpenItem,
} from "../api/types";
import { ApiError } from "../api/client";

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

  const [results, setResults] = useState<LeaseOpenItems[]>([]);
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
    if (!periodStart || !periodEnd || leases.length === 0) return;
    setQuerying(true);
    setError(null);
    setResults([]);
    try {
      const allResults: LeaseOpenItems[] = [];
      // Query open items for each lease in parallel
      const promises = leases.map(async (lease) => {
        try {
          const items = await leaseOpenItems(lease.id, periodStart, periodEnd);
          if (items.length > 0) {
            const totalOpen = items.reduce(
              (sum, i) => sum + parseFloat(i.open_amount),
              0,
            );
            allResults.push({ lease, items, totalOpen });
          }
        } catch {
          // Skip leases that fail (e.g. no matching data)
        }
      });
      await Promise.all(promises);
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

      {error && <p className="error-text">{error}</p>}

      {/* Period selection */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Zeitraum waehlen</h2>
        <div className="form-grid">
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
        </div>
      </div>

      {/* Results */}
      {results.length === 0 && !querying && (
        <p className="empty-hint">
          Keine offenen Posten vorhanden oder noch nicht abgefragt.
        </p>
      )}

      {results.map((r) => (
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

          <div style={{ marginTop: "0.5rem" }}>
            <Link to={`/mietkonto`} className="btn btn--small">
              Zum Mietkonto
            </Link>
          </div>
        </div>
      ))}
    </div>
  );
}
