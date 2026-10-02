/**
 * Dunning notices list page -- Mahnungen with filters, creation form,
 * and navigation to detail view.
 *
 * M7 Mahnwesen: dunning notice CRUD + issue lifecycle.
 * Status badges: Entwurf (draft) / Ausgestellt (issued).
 * Supports dunning for tenants (Mieter) and owners (Eigentuemer).
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import {
  listDunningNotices,
  createDunningForTenant,
  createDunningForOwner,
  listTenantProfiles,
  listOwnerProfiles,
  listPersons,
  listLeases,
  listUnits,
  listEconomicPlans,
} from "../api/endpoints";
import type {
  DunningNotice,
  DunningCreateTenant,
  DunningCreateOwner,
  TenantProfile,
  OwnerProfile,
  Person,
  LeaseRecord,
  Unit,
  EconomicPlan,
} from "../api/types";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { ApiError } from "../api/client";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Alle Status" },
  { value: "draft", label: "Entwurf" },
  { value: "issued", label: "Ausgestellt" },
  { value: "cancelled", label: "Verworfen" },
];

const STATUS_LABELS: Record<string, string> = {
  draft: "Entwurf",
  issued: "Ausgestellt",
  cancelled: "Verworfen",
};

const STATUS_CSS: Record<string, string> = {
  draft: "badge--draft",
  issued: "badge--confirmed",
};

const LEVEL_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Alle Stufen" },
  { value: "1", label: "Stufe 1 - Zahlungserinnerung" },
  { value: "2", label: "Stufe 2 - Mahnung" },
  { value: "3", label: "Stufe 3 - Letzte Mahnung" },
];

const LEVEL_LABELS: Record<number, string> = {
  1: "Zahlungserinnerung",
  2: "Mahnung",
  3: "Letzte Mahnung",
};

type RecipientType = "tenant" | "owner";

function makeEmptyTenantForm(): DunningCreateTenant {
  const currentYear = new Date().getFullYear();
  return {
    tenant_profile_id: "",
    lease_agreement_id: "",
    period_start: `${currentYear}-01-01`,
    period_end: `${currentYear}-12-31`,
    reference_date: null,
    grace_days: 0,
    override_fee: null,
  };
}

function makeEmptyOwnerForm(): DunningCreateOwner {
  const currentYear = new Date().getFullYear();
  return {
    owner_profile_id: "",
    period_start: `${currentYear}-01-01`,
    period_end: `${currentYear}-12-31`,
    economic_plan_id: null,
    reference_date: null,
    grace_days: 0,
    override_fee: null,
  };
}

export function DunningNoticesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [notices, setNotices] = useState<DunningNotice[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const requestId = useRef(0);
  const [listError, setListError] = useState<string | null>(null);
  const [economicPlans, setEconomicPlans] = useState<EconomicPlan[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [recipientType, setRecipientType] = useState<RecipientType>("tenant");
  const [tenantForm, setTenantForm] = useState<DunningCreateTenant>(makeEmptyTenantForm());
  const [ownerForm, setOwnerForm] = useState<DunningCreateOwner>(makeEmptyOwnerForm());
  const [scopeValid, setScopeValid] = useState(false);
  const [saving, setSaving] = useState(false);

  const statusFilter = searchParams.get("status") ?? "";
  const levelFilter = searchParams.get("level") ?? "";

  // Handle pre-fill from OpenItemsPage shortcut
  useEffect(() => {
    const createType = searchParams.get("create");
    if (createType === "tenant") {
      const prefillTenant = searchParams.get("tenant_profile_id") ?? "";
      const prefillLease = searchParams.get("lease_agreement_id") ?? "";
      const prefillStart = searchParams.get("period_start") ?? makeEmptyTenantForm().period_start;
      const prefillEnd = searchParams.get("period_end") ?? makeEmptyTenantForm().period_end;
      setRecipientType("tenant");
      setTenantForm({
        ...makeEmptyTenantForm(),
        tenant_profile_id: prefillTenant,
        lease_agreement_id: prefillLease,
        accounting_scope_id: searchParams.get("accounting_scope_id"),
        period_start: prefillStart,
        period_end: prefillEnd,
      });
      setShowForm(true);
      // Clean up URL params so they don't persist on reload
      const next = new URLSearchParams(searchParams);
      next.delete("accounting_scope_id");
      next.delete("create");
      next.delete("tenant_profile_id");
      next.delete("lease_agreement_id");
      next.delete("period_start");
      next.delete("period_end");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reload = useCallback(() => {
    const current = ++requestId.current;
    setLoading(true);
    setListError(null);
    setNotices([]);
    const params: Record<string, string | number | undefined> = {};
    if (statusFilter) params.status = statusFilter;
    if (levelFilter) params.level = parseInt(levelFilter, 10);

    Promise.all([
      listDunningNotices(params as Parameters<typeof listDunningNotices>[0]),
      listTenantProfiles(),
      listOwnerProfiles(),
      listPersons(),
      listLeases(),
      listEconomicPlans(),
      listUnits(),
    ])
      .then(([dn, tp, op, per, l, ep, u]) => {
        if (current !== requestId.current) return;
        setUnits(u);
        setNotices(dn);
        setTenantProfiles(tp);
        setOwnerProfiles(op);
        setPersons(per);
        setLeases(l);
        setEconomicPlans(ep);
      })
      .catch((err) => {
        if (current !== requestId.current) return;
        if (err instanceof ApiError && err.status === 401) {
          setListError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setListError(String(err));
        }
      })
      .finally(() => { if (current === requestId.current) setLoading(false); });
    return () => { requestId.current++; };
  }, [statusFilter, levelFilter]);

  useEffect(() => {
    return reload();
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

  function getPersonName(personId: string): string {
    const person = persons.find((p) => p.id === personId);
    if (!person) return "-";
    const parts = [person.first_name, person.last_name].filter(Boolean);
    return parts.length > 0 ? parts.join(" ") : "-";
  }

  function getRecipientName(notice: DunningNotice): string {
    if (notice.tenant_profile_id) {
      const tp = tenantProfiles.find((t) => t.id === notice.tenant_profile_id);
      return tp ? getPersonName(tp.person_id) : "-";
    }
    if (notice.owner_profile_id) {
      const op = ownerProfiles.find((o) => o.id === notice.owner_profile_id);
      return op ? getPersonName(op.person_id) : "-";
    }
    return "-";
  }

  function getRecipientType(notice: DunningNotice): string {
    if (notice.tenant_profile_id) return "Mieter";
    if (notice.owner_profile_id) return "Eigentuemer";
    return "-";
  }

  // Leases filtered by selected tenant
  const filteredLeases = tenantForm.tenant_profile_id
    ? leases.filter((l) => l.tenant_profile_id === tenantForm.tenant_profile_id)
    : leases;

  const selectedLease = leases.find(item => item.id === tenantForm.lease_agreement_id);
  const selectedPlan = economicPlans.find(item => item.id === ownerForm.economic_plan_id);
  const creationProperty = recipientType === "tenant"
    ? units.find(item => item.id === selectedLease?.unit_id)?.property_id
    : selectedPlan?.property_id;

  function openCreate() {
    setTenantForm(makeEmptyTenantForm());
    setOwnerForm(makeEmptyOwnerForm());
    setRecipientType("tenant");
    setShowForm(true);
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!scopeValid) return;
    setSaving(true);
    setError(null);
    try {
      let created: DunningNotice;
      if (recipientType === "tenant") {
        created = await createDunningForTenant(tenantForm);
      } else {
        created = await createDunningForOwner(ownerForm);
      }
      setShowForm(false);
      navigate(`/mahnungen/${created.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        // Handle "no overdue items" or other business errors gracefully
        if (err.status === 409 || err.status === 422 || err.status === 400) {
          setError(err.message);
        } else {
          setError(err.message);
        }
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
        <h1 style={{ margin: 0 }}>Mahnungen</h1>
        <button className="btn btn--primary" onClick={openCreate}>
          + Neue Mahnung
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
          value={levelFilter}
          onChange={(e) => setFilter("level", e.target.value)}
          style={{ padding: "0.4rem 0.5rem", fontSize: "0.85rem" }}
        >
          {LEVEL_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neue Mahnung anlegen</h2>
          <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "1rem" }}>
            Waehlen Sie den Empfaenger und den Zeitraum. Die ueberfaelligen Posten
            werden automatisch ermittelt. Gibt es keine offenen Posten, wird eine
            entsprechende Meldung angezeigt.
          </p>
          <form onSubmit={handleSubmit}>
            <fieldset disabled={saving} className="form-grid" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
            {/* Recipient type toggle */}
            <label>
              Empfaengertyp *
              <select
                required
                value={recipientType}
                onChange={(e) => {
                  setRecipientType(e.target.value as RecipientType);
                  setTenantForm(makeEmptyTenantForm());
                  setOwnerForm(makeEmptyOwnerForm());
                }}
              >
                <option value="tenant">Mieter</option>
                <option value="owner">Eigentuemer</option>
              </select>
            </label>

            {recipientType === "tenant" ? (
              <>
                <label>
                  Mieter *
                  <select
                    required
                    value={tenantForm.tenant_profile_id}
                    onChange={(e) =>
                      setTenantForm({
                        ...tenantForm,
                        tenant_profile_id: e.target.value,
                        lease_agreement_id: "",
                        accounting_scope_id: null,
                      })
                    }
                  >
                    <option value="">-- Mieter waehlen --</option>
                    {tenantProfiles.map((tp) => (
                      <option key={tp.id} value={tp.id}>
                        {getPersonName(tp.person_id)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Mietvertrag *
                  <select
                    required
                    value={tenantForm.lease_agreement_id}
                    onChange={(e) =>
                      setTenantForm({ ...tenantForm, lease_agreement_id: e.target.value, accounting_scope_id: null })
                    }
                  >
                    <option value="">-- Mietvertrag waehlen --</option>
                    {filteredLeases.map((l) => {
                      const tp = tenantProfiles.find((t) => t.id === l.tenant_profile_id);
                      const person = tp ? persons.find((p) => p.id === tp.person_id) : null;
                      const tenantName = person
                        ? [person.first_name, person.last_name].filter(Boolean).join(" ")
                        : "";
                      return (
                        <option key={l.id} value={l.id}>
                          {tenantName} ab {l.valid_from}
                          {l.valid_to ? ` bis ${l.valid_to}` : " (laufend)"}
                        </option>
                      );
                    })}
                  </select>
                </label>
                <label>
                  Zeitraum von *
                  <input
                    type="date"
                    required
                    value={tenantForm.period_start}
                    onChange={(e) =>
                      setTenantForm({ ...tenantForm, period_start: e.target.value })
                    }
                  />
                </label>
                <label>
                  Zeitraum bis *
                  <input
                    type="date"
                    required
                    value={tenantForm.period_end}
                    onChange={(e) =>
                      setTenantForm({ ...tenantForm, period_end: e.target.value })
                    }
                  />
                </label>
                <label>
                  Stichtag
                  <input
                    type="date"
                    value={tenantForm.reference_date ?? ""}
                    onChange={(e) =>
                      setTenantForm({
                        ...tenantForm,
                        reference_date: e.target.value || null,
                      })
                    }
                  />
                </label>
                <label>
                  Karenzzeit (Tage)
                  <input
                    type="number"
                    min="0"
                    value={tenantForm.grace_days ?? 0}
                    onChange={(e) =>
                      setTenantForm({
                        ...tenantForm,
                        grace_days: parseInt(e.target.value, 10) || 0,
                      })
                    }
                  />
                </label>
                <label>
                  Mahngebuehr (Override)
                  <input
                    type="text"
                    inputMode="decimal"
                    placeholder="Standard je Stufe"
                    value={tenantForm.override_fee ?? ""}
                    onChange={(e) =>
                      setTenantForm({
                        ...tenantForm,
                        override_fee: e.target.value || null,
                      })
                    }
                  />
                </label>
              </>
            ) : (
              <>
                <label>
                  Eigentuemer *
                  <select
                    required
                    value={ownerForm.owner_profile_id}
                    onChange={(e) =>
                      setOwnerForm({ ...ownerForm, owner_profile_id: e.target.value, economic_plan_id: null, accounting_scope_id: null })
                    }
                  >
                    <option value="">-- Eigentuemer waehlen --</option>
                    {ownerProfiles.map((op) => (
                      <option key={op.id} value={op.id}>
                        {getPersonName(op.person_id)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Zeitraum von *
                  <input
                    type="date"
                    required
                    value={ownerForm.period_start}
                    onChange={(e) =>
                      setOwnerForm({ ...ownerForm, period_start: e.target.value })
                    }
                  />
                </label>
                <label>
                  Zeitraum bis *
                  <input
                    type="date"
                    required
                    value={ownerForm.period_end}
                    onChange={(e) =>
                      setOwnerForm({ ...ownerForm, period_end: e.target.value })
                    }
                  />
                </label>
                <label>
                  Wirtschaftsplan
                  <select
                    value={ownerForm.economic_plan_id ?? ""}
                    onChange={(e) =>
                      setOwnerForm({
                        ...ownerForm,
                        economic_plan_id: e.target.value || null,
                        accounting_scope_id: null,
                      })
                    }
                  >
                    <option value="">Ohne Wirtschaftsplan (Altbestand)</option>
                    {economicPlans.map((ep) => (
                      <option key={ep.id} value={ep.id}>
                        {ep.plan_year} ({ep.period_start} - {ep.period_end})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Stichtag
                  <input
                    type="date"
                    value={ownerForm.reference_date ?? ""}
                    onChange={(e) =>
                      setOwnerForm({
                        ...ownerForm,
                        reference_date: e.target.value || null,
                      })
                    }
                  />
                </label>
                <label>
                  Karenzzeit (Tage)
                  <input
                    type="number"
                    min="0"
                    value={ownerForm.grace_days ?? 0}
                    onChange={(e) =>
                      setOwnerForm({
                        ...ownerForm,
                        grace_days: parseInt(e.target.value, 10) || 0,
                      })
                    }
                  />
                </label>
                <label>
                  Mahngebuehr (Override)
                  <input
                    type="text"
                    inputMode="decimal"
                    placeholder="Standard je Stufe"
                    value={ownerForm.override_fee ?? ""}
                    onChange={(e) =>
                      setOwnerForm({
                        ...ownerForm,
                        override_fee: e.target.value || null,
                      })
                    }
                  />
                </label>
              </>
            )}

            <AccountingScopeSelector key={`${recipientType}-${tenantForm.lease_agreement_id}-${ownerForm.owner_profile_id}-${ownerForm.economic_plan_id}`}
              onValidityChange={setScopeValid} propertyId={creationProperty} kind={recipientType === "tenant" ? "MIET" : "WEG"}
              value={recipientType === "tenant" ? tenantForm.accounting_scope_id : ownerForm.accounting_scope_id}
              onChange={accounting_scope_id => recipientType === "tenant"
                ? setTenantForm({ ...tenantForm, accounting_scope_id }) : setOwnerForm({ ...ownerForm, accounting_scope_id })} />
            <p className="form-hint">Für bereits zugeordnete Zahlungen einen Abrechnungskreis wählen. Eigentümermahnungen in einem Abrechnungskreis benötigen einen zugeordneten, finalisierten Wirtschaftsplan.</p>
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={saving || !scopeValid}
              >
                {saving ? "Erstellen..." : "Mahnung erstellen"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setShowForm(false)}
              >
                Abbrechen
              </button>
            </div>
            </fieldset>
          </form>
        </div>
      )}

      {loading ? (
        <p>Lade...</p>
      ) : listError ? (
        <p role="alert" className="error-text">Mahnungen nicht verfügbar: {listError} <button className="btn" onClick={reload}>Erneut laden</button></p>
      ) : notices.length === 0 ? (
        <p className="empty-hint">Keine Mahnungen vorhanden.</p>
      ) : (
        <div className="card">
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Datum</th>
                  <th>Empfaenger</th>
                  <th>Typ</th>
                  <th>Stufe</th>
                  <th style={{ textAlign: "right" }}>Hauptforderung</th>
                  <th style={{ textAlign: "right" }}>Gebuehr</th>
                  <th style={{ textAlign: "right" }}>Gesamt</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {notices.map((n) => (
                  <tr key={n.id}>
                    <td>{n.issue_date}</td>
                    <td>{getRecipientName(n)}</td>
                    <td style={{ fontSize: "0.85rem" }}>{getRecipientType(n)}</td>
                    <td>
                      Stufe {n.level}
                      <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                        {LEVEL_LABELS[n.level] ?? ""}
                      </div>
                    </td>
                    <td
                      style={{
                        textAlign: "right",
                        fontFamily: "var(--mono)",
                      }}
                    >
                      {n.principal_amount} EUR
                    </td>
                    <td
                      style={{
                        textAlign: "right",
                        fontFamily: "var(--mono)",
                      }}
                    >
                      {n.fee_amount} EUR
                    </td>
                    <td
                      style={{
                        textAlign: "right",
                        fontFamily: "var(--mono)",
                        fontWeight: 600,
                      }}
                    >
                      {n.total_amount} EUR
                    </td>
                    <td>
                      <span className={`badge ${STATUS_CSS[n.status] ?? ""}`}>
                        {STATUS_LABELS[n.status] ?? n.status}
                      </span>
                    </td>
                    <td>
                      <Link
                        to={`/mahnungen/${n.id}`}
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
        </div>
      )}
    </div>
  );
}
