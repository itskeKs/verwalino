/**
 * Payments list page -- Zahlungen with filters, creation form, and navigation
 * to detail view.
 *
 * M6 Zahlungen: payment CRUD + book/reverse lifecycle.
 * Status badges: Entwurf (draft) / Gebucht (booked).
 * Storno chain visible in the list (reversed-by / reverses links).
 */

import { useEffect, useState, useCallback } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import {
  listPayments,
  listBankAccounts,
  listProperties,
  listLeases,
  listTenantProfiles,
  listOwnerProfiles,
  listPersons,
  createPayment,
} from "../api/endpoints";
import type {
  Payment,
  PaymentCreate,
  PaymentDirection,
  BankAccount,
  Property,
  LeaseRecord,
  TenantProfile,
  OwnerProfile,
  Person,
} from "../api/types";
import { ApiError } from "../api/client";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Alle Status" },
  { value: "draft", label: "Entwurf" },
  { value: "booked", label: "Gebucht" },
  { value: "cancelled", label: "Verworfen" },
];

const STATUS_LABELS: Record<string, string> = {
  draft: "Entwurf",
  booked: "Gebucht",
  cancelled: "Verworfen",
};

const STATUS_CSS: Record<string, string> = {
  draft: "badge--draft",
  booked: "badge--confirmed",
  cancelled: "badge--superseded",
};

const PAYMENT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "KALTMIETE", label: "Kaltmiete" },
  { value: "NK_VORAUSZAHLUNG", label: "NK-Vorauszahlung" },
  { value: "KAUTION", label: "Kaution" },
  { value: "HAUSGELD_VORSCHUSS", label: "Hausgeld-Vorschuss" },
  { value: "RUECKLAGENANTEIL", label: "Ruecklagenanteil" },
  { value: "SONDERZAHLUNG", label: "Sonder-/Nachzahlung" },
];

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "NK-Vorauszahlung",
  KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss",
  RUECKLAGENANTEIL: "Ruecklagenanteil",
  SONDERZAHLUNG: "Sonder-/Nachzahlung",
};

const DIRECTION_LABELS: Record<string, string> = {
  IN: "Eingang",
  OUT: "Ausgang",
};

// Tenant-bound types: counterparty = tenant
const TENANT_TYPES = new Set(["KALTMIETE", "NK_VORAUSZAHLUNG", "KAUTION"]);

function makeEmptyForm(): PaymentCreate {
  return {
    bank_account_id: "",
    direction: "IN",
    amount: "",
    value_date: "",
    payment_type: "KALTMIETE",
    tenant_profile_id: null,
    owner_profile_id: null,
    lease_agreement_id: null,
    period_year: null,
    period_month: null,
    note: null,
  };
}

export function PaymentsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [payments, setPayments] = useState<Payment[]>([]);
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<PaymentCreate>(makeEmptyForm());
  const [saving, setSaving] = useState(false);

  const statusFilter = searchParams.get("status") ?? "";
  const accountFilter = searchParams.get("bank_account_id") ?? "";
  const typeFilter = searchParams.get("payment_type") ?? "";

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    const params: Record<string, string | number | undefined> = {};
    if (statusFilter) params.status = statusFilter;
    if (accountFilter) params.bank_account_id = accountFilter;
    if (typeFilter) params.payment_type = typeFilter;

    Promise.all([
      listPayments(params as Parameters<typeof listPayments>[0]),
      listBankAccounts(),
      listProperties(),
      listLeases(),
      listTenantProfiles(),
      listOwnerProfiles(),
      listPersons(),
    ])
      .then(([pay, ba, prop, lease, tp, op, per]) => {
        setPayments(pay);
        setBankAccounts(ba);
        setProperties(prop);
        setLeases(lease);
        setTenantProfiles(tp);
        setOwnerProfiles(op);
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
  }, [statusFilter, accountFilter, typeFilter]);

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

  function getAccountLabel(id: string): string {
    const a = bankAccounts.find((ba) => ba.id === id);
    if (!a) return id.slice(0, 8);
    const prop = properties.find((p) => p.id === a.property_id);
    const propName = prop ? prop.name : "";
    return `${a.label}${propName ? ` (${propName})` : ""}`;
  }

  function getPersonName(personId: string): string {
    const person = persons.find((p) => p.id === personId);
    if (!person) return "-";
    const parts = [person.first_name, person.last_name].filter(Boolean);
    return parts.length > 0 ? parts.join(" ") : "-";
  }

  function getCounterpartyName(payment: Payment): string {
    if (payment.tenant_profile_id) {
      const tp = tenantProfiles.find((t) => t.id === payment.tenant_profile_id);
      return tp ? getPersonName(tp.person_id) : "-";
    }
    if (payment.owner_profile_id) {
      const op = ownerProfiles.find((o) => o.id === payment.owner_profile_id);
      return op ? getPersonName(op.person_id) : "-";
    }
    return "-";
  }

  function openCreate() {
    setForm(makeEmptyForm());
    setShowForm(true);
  }

  const isTenantType = TENANT_TYPES.has(form.payment_type);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createPayment(form);
      setShowForm(false);
      navigate(`/zahlungen/${created.id}`);
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
        <h1 style={{ margin: 0 }}>Zahlungen</h1>
        <button className="btn btn--primary" onClick={openCreate}>
          + Neue Zahlung
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
          value={accountFilter}
          onChange={(e) => setFilter("bank_account_id", e.target.value)}
          style={{ padding: "0.4rem 0.5rem", fontSize: "0.85rem" }}
        >
          <option value="">Alle Konten</option>
          {bankAccounts.map((a) => (
            <option key={a.id} value={a.id}>
              {getAccountLabel(a.id)}
            </option>
          ))}
        </select>
        <select
          value={typeFilter}
          onChange={(e) => setFilter("payment_type", e.target.value)}
          style={{ padding: "0.4rem 0.5rem", fontSize: "0.85rem" }}
        >
          <option value="">Alle Typen</option>
          {PAYMENT_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neue Zahlung anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              Bankkonto *
              <select
                required
                value={form.bank_account_id}
                onChange={(e) =>
                  setForm({ ...form, bank_account_id: e.target.value })
                }
              >
                <option value="">-- Konto waehlen --</option>
                {bankAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {getAccountLabel(a.id)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Zahlungstyp *
              <select
                required
                value={form.payment_type}
                onChange={(e) =>
                  setForm({
                    ...form,
                    payment_type: e.target.value,
                    // Clear counterparty when switching between tenant/owner types
                    tenant_profile_id: null,
                    owner_profile_id: null,
                    lease_agreement_id: null,
                  })
                }
              >
                {PAYMENT_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Richtung *
              <select
                required
                value={form.direction}
                onChange={(e) =>
                  setForm({ ...form, direction: e.target.value as PaymentDirection })
                }
              >
                <option value="IN">Eingang (Einzahlung)</option>
                <option value="OUT">Ausgang (Auszahlung)</option>
              </select>
            </label>
            <label>
              Betrag *
              <input
                type="text"
                required
                inputMode="decimal"
                placeholder="0.00"
                value={form.amount}
                onChange={(e) =>
                  setForm({ ...form, amount: e.target.value })
                }
              />
            </label>
            <label>
              Wertstellungsdatum *
              <input
                type="date"
                required
                value={form.value_date}
                onChange={(e) =>
                  setForm({ ...form, value_date: e.target.value })
                }
              />
            </label>
            {isTenantType ? (
              <label>
                Mieter
                <select
                  value={form.tenant_profile_id ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      tenant_profile_id: e.target.value || null,
                      owner_profile_id: null,
                    })
                  }
                >
                  <option value="">-- Mieter waehlen --</option>
                  {tenantProfiles.map((tp) => {
                    const person = persons.find((p) => p.id === tp.person_id);
                    const name = person
                      ? [person.first_name, person.last_name].filter(Boolean).join(" ")
                      : tp.id.slice(0, 8);
                    return (
                      <option key={tp.id} value={tp.id}>
                        {name}
                      </option>
                    );
                  })}
                </select>
              </label>
            ) : (
              <label>
                Eigentuemer
                <select
                  value={form.owner_profile_id ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      owner_profile_id: e.target.value || null,
                      tenant_profile_id: null,
                    })
                  }
                >
                  <option value="">-- Eigentuemer waehlen --</option>
                  {ownerProfiles.map((op) => {
                    const person = persons.find((p) => p.id === op.person_id);
                    const name = person
                      ? [person.first_name, person.last_name].filter(Boolean).join(" ")
                      : op.id.slice(0, 8);
                    return (
                      <option key={op.id} value={op.id}>
                        {name}
                      </option>
                    );
                  })}
                </select>
              </label>
            )}
            {isTenantType && (
              <label>
                Mietvertrag
                <select
                  value={form.lease_agreement_id ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      lease_agreement_id: e.target.value || null,
                    })
                  }
                >
                  <option value="">(kein)</option>
                  {leases.map((l) => {
                    const tp = tenantProfiles.find((t) => t.id === l.tenant_profile_id);
                    const person = tp
                      ? persons.find((p) => p.id === tp.person_id)
                      : null;
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
            )}
            <label>
              Periode Jahr
              <input
                type="number"
                min="2000"
                max="2100"
                value={form.period_year ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    period_year: e.target.value ? parseInt(e.target.value, 10) : null,
                  })
                }
                placeholder="z.B. 2025"
              />
            </label>
            <label>
              Periode Monat
              <input
                type="number"
                min="1"
                max="12"
                value={form.period_month ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    period_month: e.target.value ? parseInt(e.target.value, 10) : null,
                  })
                }
                placeholder="1-12"
              />
            </label>
            <label className="form-full-width">
              Notiz
              <textarea
                value={form.note ?? ""}
                onChange={(e) =>
                  setForm({ ...form, note: e.target.value || null })
                }
                rows={2}
                placeholder="Optionale Bemerkung"
              />
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
      ) : payments.length === 0 ? (
        <p className="empty-hint">Keine Zahlungen vorhanden.</p>
      ) : (
        <div className="card">
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Datum</th>
                  <th>Typ</th>
                  <th>Richtung</th>
                  <th>Gegenpartei</th>
                  <th>Konto</th>
                  <th style={{ textAlign: "right" }}>Betrag</th>
                  <th>Periode</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p) => (
                  <tr
                    key={p.id}
                    style={{
                      opacity: p.reversed_by_id ? 0.55 : 1,
                      textDecoration: p.reversed_by_id ? "line-through" : undefined,
                    }}
                  >
                    <td>{p.value_date}</td>
                    <td>
                      {PAYMENT_TYPE_LABELS[p.payment_type] ?? p.payment_type}
                      {p.reverses_id && (
                        <div style={{ fontSize: "0.75rem", color: "#b71c1c" }}>
                          Storno
                        </div>
                      )}
                    </td>
                    <td>
                      <span
                        style={{
                          color: p.direction === "IN" ? "#2e7d32" : "#b71c1c",
                          fontWeight: 600,
                        }}
                      >
                        {DIRECTION_LABELS[p.direction] ?? p.direction}
                      </span>
                    </td>
                    <td>{getCounterpartyName(p)}</td>
                    <td style={{ fontSize: "0.85rem" }}>
                      {getAccountLabel(p.bank_account_id)}
                    </td>
                    <td
                      style={{
                        textAlign: "right",
                        fontFamily: "var(--mono)",
                        fontWeight: 600,
                      }}
                    >
                      {p.amount} EUR
                    </td>
                    <td>
                      {p.period_year
                        ? `${p.period_year}${p.period_month ? "/" + String(p.period_month).padStart(2, "0") : ""}`
                        : "-"}
                    </td>
                    <td>
                      <span className={`badge ${STATUS_CSS[p.status] ?? ""}`}>
                        {STATUS_LABELS[p.status] ?? p.status}
                      </span>
                    </td>
                    <td>
                      <Link
                        to={`/zahlungen/${p.id}`}
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
