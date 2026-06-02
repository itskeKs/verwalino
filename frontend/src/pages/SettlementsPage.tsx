/**
 * Settlement runs list page -- Abrechnungen (BK) with filter by property and status.
 *
 * M3 Betriebskostenabrechnung: list / create / navigate to detail.
 * Status badges: Entwurf (draft) / Finalisiert (finalized).
 */

import { useEffect, useState, useCallback } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import {
  listSettlements,
  listProperties,
  listLeases,
  listUnits,
  listTenantProfiles,
  listPersons,
  createSettlement,
} from "../api/endpoints";
import type {
  SettlementRun,
  SettlementRunCreate,
  Property,
  LeaseRecord,
  Unit,
  TenantProfile,
  Person,
} from "../api/types";
import { ApiError } from "../api/client";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Alle Status" },
  { value: "draft", label: "Entwurf" },
  { value: "finalized", label: "Finalisiert" },
];

const STATUS_LABELS: Record<string, string> = {
  draft: "Entwurf",
  finalized: "Finalisiert",
};

const STATUS_CSS: Record<string, string> = {
  draft: "badge--draft",
  finalized: "badge--confirmed",
};

function makeEmptyForm(): SettlementRunCreate {
  return {
    property_id: "",
    lease_agreement_id: "",
    period_start: "",
    period_end: "",
    advance_payments_total: null,
    management_mandate_id: null,
  };
}

export function SettlementsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<SettlementRun[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<SettlementRunCreate>(makeEmptyForm());
  const [saving, setSaving] = useState(false);

  const statusFilter = searchParams.get("status") ?? "";
  const propertyFilter = searchParams.get("property_id") ?? "";

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      listSettlements(propertyFilter || undefined),
      listProperties(),
      listLeases(),
      listUnits(),
      listTenantProfiles(),
      listPersons(),
    ])
      .then(([s, p, l, u, tp, per]) => {
        let filtered = s;
        if (statusFilter) {
          filtered = s.filter((r) => r.status === statusFilter);
        }
        setRuns(filtered);
        setProperties(p);
        setLeases(l);
        setUnits(u);
        setTenantProfiles(tp);
        setPersons(per);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [statusFilter, propertyFilter]);

  useEffect(() => {
    reload();
  }, [reload]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  }

  function getPropertyName(id: string): string {
    const prop = properties.find((p) => p.id === id);
    return prop ? prop.name : id.slice(0, 8);
  }

  function getTenantName(tenantProfileId: string): string {
    const tp = tenantProfiles.find((t) => t.id === tenantProfileId);
    if (!tp) return "—";
    const person = persons.find((p) => p.id === tp.person_id);
    if (!person) return "—";
    const parts = [person.first_name, person.last_name].filter(Boolean);
    return parts.length > 0 ? parts.join(" ") : "—";
  }

  function getUnitLabel(unitId: string): string {
    const unit = units.find((u) => u.id === unitId);
    if (!unit) return unitId.slice(0, 8);
    return unit.location ? `${unit.number} ${unit.location}` : unit.number;
  }

  function getLeaseLabel(id: string): string {
    const lease = leases.find((l) => l.id === id);
    if (!lease) return id.slice(0, 8);
    const unitLabel = getUnitLabel(lease.unit_id);
    const tenantName = getTenantName(lease.tenant_profile_id);
    const period = `ab ${lease.valid_from}${lease.valid_to ? ` bis ${lease.valid_to}` : " (laufend)"}`;
    return `${unitLabel} — ${tenantName} (${period})`;
  }

  // All leases are shown when a property is selected; the backend validates
  // the property<->lease relationship on create.

  function openCreate() {
    setForm(makeEmptyForm());
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createSettlement(form);
      setShowForm(false);
      navigate(`/abrechnungen/${created.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "1rem",
          flexWrap: "wrap",
          gap: "0.5rem",
        }}
      >
        <h1 style={{ margin: 0 }}>Abrechnungen</h1>
        <button className="btn btn--primary" onClick={openCreate}>
          + Neue Abrechnung
        </button>
      </div>

      {/* Filters */}
      <div
        style={{
          display: "flex",
          gap: "0.75rem",
          marginBottom: "1rem",
          flexWrap: "wrap",
        }}
      >
        <select
          value={statusFilter}
          onChange={(e) => setFilter("status", e.target.value)}
          style={{ padding: "0.4rem 0.5rem", fontSize: "0.85rem" }}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          value={propertyFilter}
          onChange={(e) => setFilter("property_id", e.target.value)}
          style={{ padding: "0.4rem 0.5rem", fontSize: "0.85rem" }}
        >
          <option value="">Alle Objekte</option>
          {properties.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neue Abrechnung anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              Objekt *
              <select
                required
                value={form.property_id}
                onChange={(e) =>
                  setForm({ ...form, property_id: e.target.value, lease_agreement_id: "" })
                }
              >
                <option value="">-- Objekt waehlen --</option>
                {properties.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Mietvertrag *
              <select
                required
                value={form.lease_agreement_id}
                onChange={(e) =>
                  setForm({ ...form, lease_agreement_id: e.target.value })
                }
              >
                <option value="">-- Mietvertrag waehlen --</option>
                {(form.property_id ? leases : []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {getLeaseLabel(l.id)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Abrechnungszeitraum von *
              <input
                type="date"
                required
                value={form.period_start}
                onChange={(e) =>
                  setForm({ ...form, period_start: e.target.value })
                }
              />
            </label>
            <label>
              Abrechnungszeitraum bis *
              <input
                type="date"
                required
                value={form.period_end}
                onChange={(e) =>
                  setForm({ ...form, period_end: e.target.value })
                }
              />
            </label>
            <label>
              Vorauszahlungssumme
              <input
                type="text"
                inputMode="decimal"
                placeholder="0.00"
                value={form.advance_payments_total ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    advance_payments_total: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Abrechnungstyp
              <input type="text" value="BK (Betriebskosten)" disabled />
            </label>
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={saving}
              >
                {saving ? "Anlegen..." : "Anlegen"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setShowForm(false)}
              >
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <p>Lade...</p>
      ) : runs.length === 0 ? (
        <p className="empty-hint">Keine Abrechnungen vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Zeitraum</th>
                <th>Objekt</th>
                <th>Mietvertrag</th>
                <th>Typ</th>
                <th>Ergebnis</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    {run.period_start} bis {run.period_end}
                  </td>
                  <td>{getPropertyName(run.property_id)}</td>
                  <td>
                    {run.lease_agreement_id
                      ? getLeaseLabel(run.lease_agreement_id)
                      : "-"}
                  </td>
                  <td>{run.run_type}</td>
                  <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}>
                    {run.balance != null ? `${run.balance} EUR` : "-"}
                  </td>
                  <td>
                    <span
                      className={`badge ${STATUS_CSS[run.status] ?? ""}`}
                    >
                      {STATUS_LABELS[run.status] ?? run.status}
                    </span>
                  </td>
                  <td>
                    <Link
                      to={`/abrechnungen/${run.id}`}
                      className="btn btn--small"
                    >
                      Details
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
