/**
 * Payments list page -- Zahlungen with filters, creation form, and navigation
 * to detail view.
 *
 * M6 Zahlungen: payment CRUD + book/reverse lifecycle.
 * Status badges: Entwurf (draft) / Gebucht (booked).
 * Storno chain visible in the list (reversed-by / reverses links).
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import {
  exportPaymentsCsv,
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
import type { PaymentListQuery } from "../api/endpoints";
import { ApiError } from "../api/client";
import { ReserveEvidenceFields } from "../components/ReserveEvidenceFields";
import { SepaExport } from "../components/SepaExport";

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
  { value: "NK_VORAUSZAHLUNG", label: "Betriebskosten-Vorauszahlung" },
  { value: "HK_VORAUSZAHLUNG", label: "Heizkosten-Vorauszahlung" },
  { value: "KAUTION", label: "Kaution" },
  { value: "HAUSGELD_VORSCHUSS", label: "Hausgeld-Vorschuss" },
  { value: "RUECKLAGENANTEIL", label: "Ruecklagenanteil" },
  { value: "RUECKLAGENENTNAHME", label: "Rücklagenentnahme" },
  { value: "SONDERZAHLUNG", label: "Sonder-/Nachzahlung" },
];

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "Betriebskosten-Vorauszahlung", HK_VORAUSZAHLUNG: "Heizkosten-Vorauszahlung",
  KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss",
  RUECKLAGENANTEIL: "Ruecklagenanteil",
  RUECKLAGENENTNAHME: "Rücklagenentnahme",
  SONDERZAHLUNG: "Sonder-/Nachzahlung",
};

const DIRECTION_LABELS: Record<string, string> = {
  IN: "Eingang",
  OUT: "Ausgang",
};

// Tenant-bound types: counterparty = tenant
const TENANT_TYPES = new Set(["KALTMIETE", "NK_VORAUSZAHLUNG", "HK_VORAUSZAHLUNG", "KAUTION"]);

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

// API amounts are decimal strings; never round list aggregates through Number.
function amountCents(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  return BigInt(`${whole}${fraction.padEnd(2, "0")}`);
}

function formatCents(value: bigint): string {
  const absolute = value < 0n ? -value : value;
  const whole = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${value < 0n ? "-" : ""}${whole},${(absolute % 100n).toString().padStart(2, "0")} EUR`;
}

export function PaymentsPage() {
  const [showSepa, setShowSepa] = useState(false);
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
  const [loaded, setLoaded] = useState<{ query: PaymentListQuery; key: string } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const lifecycle = useRef({ mounted: false, generation: 0, downloading: false });

  useEffect(() => {
    const state = lifecycle.current;
    state.mounted = true;
    return () => { state.mounted = false; state.generation++; };
  }, []);

  const statusFilter = searchParams.get("status") ?? "";
  const accountFilter = searchParams.get("bank_account_id") ?? "";
  const typeFilter = searchParams.get("payment_type") ?? "";
  const queryKey = JSON.stringify([statusFilter, accountFilter, typeFilter]);

  const reload = useCallback(() => {
    const generation = ++lifecycle.current.generation;
    const current = () => lifecycle.current.mounted && lifecycle.current.generation === generation;
    lifecycle.current.downloading = false;
    setDownloading(false);
    setDownloadError(null);
    setLoaded(null);
    setLoading(true);
    setError(null);
    const params: PaymentListQuery = {};
    if (statusFilter) params.status = statusFilter;
    if (accountFilter) params.bank_account_id = accountFilter;
    if (typeFilter) params.payment_type = typeFilter;
    Promise.all([
      listPayments(params),
      listBankAccounts(),
      listProperties(),
      listLeases(),
      listTenantProfiles(),
      listOwnerProfiles(),
      listPersons(),
    ])
      .then(([pay, ba, prop, lease, tp, op, per]) => {
        if (!current()) return;
        setLoaded({ query: params, key: queryKey });
        setPayments(pay);
        setBankAccounts(ba);
        setProperties(prop);
        setLeases(lease);
        setTenantProfiles(tp);
        setOwnerProfiles(op);
        setPersons(per);
      })
      .catch((err) => {
        if (!current()) return;
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => { if (current()) setLoading(false); });
  }, [statusFilter, accountFilter, typeFilter, queryKey]);

  useEffect(() => {
    reload();
  }, [reload]);

  function setFilter(key: string, value: string) {
    if ((searchParams.get(key) ?? "") === value) return;
    // Invalidate synchronously, before the URL update's next effect can run.
    lifecycle.current.generation++;
    lifecycle.current.downloading = false;
    setLoaded(null);
    setLoading(true);
    setDownloading(false);
    setDownloadError(null);
    setError(null);
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  }

  const currentDataset = loaded !== null && loaded.key === queryKey && !loading && !error;

  async function downloadCsv() {
    if (!currentDataset || !loaded || !lifecycle.current.mounted || lifecycle.current.downloading) return;
    const generation = lifecycle.current.generation;
    const current = () => lifecycle.current.mounted && lifecycle.current.generation === generation;
    lifecycle.current.downloading = true;
    setDownloading(true);
    setDownloadError(null);
    try {
      const blob = await exportPaymentsCsv(loaded.query);
      if (!current()) return;
      const anchor = document.createElement("a");
      const url = URL.createObjectURL(blob);
      try {
        anchor.href = url;
        anchor.download = "zahlungen.csv";
        document.body.append(anchor);
        anchor.click();
      } finally {
        anchor.remove();
        URL.revokeObjectURL(url);
      }
    } catch (reason) {
      if (current()) setDownloadError(reason instanceof Error ? reason.message : "CSV konnte nicht geladen werden.");
    } finally {
      if (current()) { lifecycle.current.downloading = false; setDownloading(false); }
    }
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
  const reserveProperty = bankAccounts.find(account => account.id === form.bank_account_id)?.property_id;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (form.payment_type === 'RUECKLAGENENTNAHME' && (!form.reserve_cost_entry_id || !form.reserve_register_entry_id)) {
      setError('Bitte verknüpfen Sie einen bestätigten Kostenbeleg und einen Beschluss aus der Beschluss-Sammlung.'); return;
    }
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

      <button type="button" className="btn" aria-expanded={showSepa} aria-controls="payments-sepa" onClick={() => setShowSepa(value => !value)}>
        {showSepa ? "SEPA-Lastschrift schließen" : "SEPA-Lastschrift öffnen"}
      </button>
      {showSepa && <div id="payments-sepa"><SepaExport bankAccounts={bankAccounts} properties={properties} /></div>}

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
          aria-label="Statusfilter"
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
          aria-label="Bankkontofilter"
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
          aria-label="Zahlungstypfilter"
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

      <button type="button" className="btn" disabled={!currentDataset || downloading} onClick={() => void downloadCsv()}>CSV herunterladen</button>
      {downloading && <p>CSV wird geladen…</p>}
      {downloadError && <p role="alert" className="error-text">{downloadError} Bitte CSV herunterladen erneut versuchen.</p>}
      {error && <p className="error-text">{error} <button type="button" onClick={reload}>Liste erneut laden</button></p>}
      {currentDataset && <div>
        <p>Listensaldo: <output aria-label="Listensaldo">{formatCents(payments.reduce((total, payment) => total + (payment.direction === "OUT" ? -amountCents(payment.amount) : amountCents(payment.amount)), 0n))}</output></p>
        <p>Eingänge minus Ausgänge aller angezeigten Zeilen, einschließlich Entwürfen, verworfenen Zahlungen und Stornos je Filter. Kein tatsächlicher Kontostand.</p>
      </div>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neue Zahlung anlegen</h2>
          <form onSubmit={handleSubmit} className={`form-grid ${form.payment_type === 'RUECKLAGENENTNAHME' ? 'reserve-payment-form' : ''}`}>
            <label>
              Bankkonto *
              <select
                aria-label="Bankkonto *"
                required
                value={form.bank_account_id}
                onChange={(e) =>
                  setForm({ ...form, bank_account_id: e.target.value, reserve_cost_entry_id: null, reserve_register_entry_id: null })
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
                aria-label="Zahlungstyp *"
                required
                value={form.payment_type}
                onChange={(e) =>
                  setForm({
                    ...form,
                    payment_type: e.target.value,
                    reserve_cost_entry_id: null, reserve_register_entry_id: null,
                    direction: e.target.value === 'RUECKLAGENENTNAHME' ? 'OUT' : form.direction,
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
                aria-label="Richtung *"
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
            {form.payment_type === 'RUECKLAGENENTNAHME' ? <p className="form-full-width">Eine Rücklagenentnahme ist eine Auszahlung ohne Mieter- oder Eigentümerzuordnung.</p> : isTenantType ? (
              <label>
                Mieter
                <select
                  aria-label="Mieter"
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
                  aria-label="Eigentuemer"
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
                  aria-label="Mietvertrag"
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
            {form.payment_type === 'RUECKLAGENENTNAHME' && <ReserveEvidenceFields propertyId={reserveProperty}
              cost={form.reserve_cost_entry_id || ''} register={form.reserve_register_entry_id || ''}
              onChange={(cost, register) => setForm({ ...form, reserve_cost_entry_id: cost || null, reserve_register_entry_id: register || null })} />}
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

      {loading || loaded?.key !== queryKey ? (
        <p>{error ? "Liste nicht verfügbar." : "Lade..."}</p>
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
