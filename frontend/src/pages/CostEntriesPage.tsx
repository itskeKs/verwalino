/**
 * Cost entries list page -- shows all Kostenpositionen with status/property filter.
 *
 * M2 GoBD-Belegfluss: cost_entry CRUD + state machine.
 * Status badges for draft_extracted / needs_review / confirmed / rejected / superseded.
 * Inline creation with category defaults pre-populated.
 */

import { useEffect, useState, useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  listCostEntries,
  listCostCategories,
  listProperties,
  listVendors,
  createCostEntry,
} from "../api/endpoints";
import type {
  CostEntry,
  CostCategory,
  CostEntryCreate,
  CostEntryStatus,
  Property,
  Vendor,
} from "../api/types";
import { ApiError } from "../api/client";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { SourceBadge } from "../components/SourceBadge";

const STATUS_OPTIONS: { value: CostEntryStatus | ""; label: string }[] = [
  { value: "", label: "Alle Status" },
  { value: "draft_extracted", label: "Entwurf (extrahiert)" },
  { value: "needs_review", label: "Pruefung noetig" },
  { value: "confirmed", label: "Bestaetigt" },
  { value: "rejected", label: "Verworfen" },
  { value: "superseded", label: "Ersetzt" },
];

const STATUS_LABELS: Record<string, string> = {
  draft_extracted: "Entwurf",
  needs_review: "Pruefung",
  confirmed: "Bestaetigt",
  rejected: "Verworfen",
  superseded: "Ersetzt",
};

const STATUS_CSS: Record<string, string> = {
  draft_extracted: "badge--draft",
  needs_review: "badge--review",
  confirmed: "badge--confirmed",
  rejected: "badge--rejected",
  superseded: "badge--superseded",
};

const APPORTIONABILITY_OPTIONS = [
  { value: "full", label: "Voll umlagefaehig" },
  { value: "partial", label: "Teilweise umlagefaehig" },
  { value: "none", label: "Nicht umlagefaehig" },
];

const DISTRIBUTION_KEY_OPTIONS = [
  { value: "", label: "(kein)" },
  { value: "MEA", label: "MEA" },
  { value: "AREA", label: "Flaeche" },
  { value: "HEATED_AREA", label: "Beheizte Flaeche" },
  { value: "UNIT_COUNT", label: "Einheiten" },
  { value: "PERSON_COUNT", label: "Personen" },
  { value: "CONSUMPTION", label: "Verbrauch" },
  { value: "EXTERNAL", label: "Extern" },
  { value: "MANUAL", label: "Manuell" },
];

function makeEmptyForm(): CostEntryCreate {
  return {
    property_id: "",
    cost_category_id: "",
    cost_category_plan_version: 1,
    vendor_id: null,
    invoice_date: "",
    service_period_from: "",
    service_period_to: "",
    gross_amount: "",
    vat_amount: null,
    vat_rate: null,
    document_number: null,
    owner_distribution_key: null,
    tenant_distribution_key: null,
    tenant_apportionable: "full",
    apportionable_tenant_amount: null,
    source: "MANUAL",
    status: "needs_review",
  };
}

export function CostEntriesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [entries, setEntries] = useState<CostEntry[]>([]);
  const [categories, setCategories] = useState<CostCategory[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<CostEntryCreate>(makeEmptyForm());
  const [saving, setSaving] = useState(false);

  const statusFilter = searchParams.get("status") ?? "";
  const propertyFilter = searchParams.get("property_id") ?? "";

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    const params: { status?: string; property_id?: string } = {};
    if (statusFilter) params.status = statusFilter;
    if (propertyFilter) params.property_id = propertyFilter;

    Promise.all([
      listCostEntries(params),
      listCostCategories(),
      listProperties(),
      listVendors(),
    ])
      .then(([e, c, p, v]) => {
        setEntries(e);
        setCategories(c);
        setProperties(p);
        setVendors(v);
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

  function getCategoryLabel(id: string): string {
    const cat = categories.find((c) => c.id === id);
    return cat ? `${cat.code} ${cat.label}` : id.slice(0, 8);
  }

  function getPropertyName(id: string): string {
    const prop = properties.find((p) => p.id === id);
    return prop ? prop.name : id.slice(0, 8);
  }

  function onCategoryChange(categoryId: string) {
    const cat = categories.find((c) => c.id === categoryId);
    if (cat) {
      setForm({
        ...form,
        cost_category_id: categoryId,
        cost_category_plan_version: cat.plan_version,
        tenant_apportionable: cat.tenant_apportionability_default,
        owner_distribution_key: cat.owner_distribution_key_default ?? null,
        tenant_distribution_key: cat.tenant_distribution_key_default ?? null,
      });
    } else {
      setForm({ ...form, cost_category_id: categoryId });
    }
  }

  function openCreate() {
    setForm(makeEmptyForm());
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createCostEntry(form);
      setShowForm(false);
      reload();
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
        <h1 style={{ margin: 0 }}>Kostenpositionen</h1>
        <button className="btn btn--primary" onClick={openCreate}>
          + Neue Kostenposition
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
          <h2>Neue Kostenposition anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              Objekt *
              <select
                required
                value={form.property_id}
                onChange={(e) =>
                  setForm({ ...form, accounting_scope_id: null, property_id: e.target.value })
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
              Kategorie *
              <select
                required
                value={form.cost_category_id}
                onChange={(e) => onCategoryChange(e.target.value)}
              >
                <option value="">-- Kategorie waehlen --</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.code} {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Dienstleister
              <select
                value={form.vendor_id ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    vendor_id: e.target.value || null,
                  })
                }
              >
                <option value="">(kein)</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Rechnungsdatum *
              <input
                type="date"
                required
                value={form.invoice_date}
                onChange={(e) =>
                  setForm({ ...form, invoice_date: e.target.value })
                }
              />
            </label>
            <label>
              Leistungszeitraum von *
              <input
                type="date"
                required
                value={form.service_period_from}
                onChange={(e) =>
                  setForm({ ...form, service_period_from: e.target.value })
                }
              />
            </label>
            <label>
              Leistungszeitraum bis *
              <input
                type="date"
                required
                value={form.service_period_to}
                onChange={(e) =>
                  setForm({ ...form, service_period_to: e.target.value })
                }
              />
            </label>
            <label>
              Bruttobetrag *
              <input
                type="text"
                required
                inputMode="decimal"
                placeholder="0.00"
                value={form.gross_amount}
                onChange={(e) =>
                  setForm({ ...form, gross_amount: e.target.value })
                }
              />
            </label>
            <label>
              USt-Betrag
              <input
                type="text"
                inputMode="decimal"
                placeholder="0.00"
                value={form.vat_amount ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    vat_amount: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              USt-Satz (%)
              <input
                type="text"
                inputMode="decimal"
                placeholder="19.00"
                value={form.vat_rate ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    vat_rate: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Belegnummer
              <input
                type="text"
                value={form.document_number ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    document_number: e.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Verteilerschl. Eigentuemer
              <select
                value={form.owner_distribution_key ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    owner_distribution_key: e.target.value || null,
                  })
                }
              >
                {DISTRIBUTION_KEY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Verteilerschl. Mieter
              <select
                value={form.tenant_distribution_key ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    tenant_distribution_key: e.target.value || null,
                  })
                }
              >
                {DISTRIBUTION_KEY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Umlagefaehigkeit *
              <select
                required
                value={form.tenant_apportionable}
                onChange={(e) =>
                  setForm({ ...form, tenant_apportionable: e.target.value })
                }
              >
                {APPORTIONABILITY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Umlagefaehiger Betrag (Mieter)
              <input
                type="text"
                inputMode="decimal"
                placeholder="0.00"
                value={form.apportionable_tenant_amount ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    apportionable_tenant_amount: e.target.value || null,
                  })
                }
              />
            </label>
            <AccountingScopeSelector key={form.property_id} propertyId={form.property_id} value={form.accounting_scope_id} onChange={id => setForm({ ...form, accounting_scope_id: id })} />
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={saving}
              >
                {saving ? "Speichern..." : "Speichern"}
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
      ) : entries.length === 0 ? (
        <p className="empty-hint">Keine Kostenpositionen vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Datum</th>
                <th>Kategorie</th>
                <th>Objekt</th>
                <th>Brutto</th>
                <th>Status</th>
                <th>Quelle</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.invoice_date}</td>
                  <td>{getCategoryLabel(entry.cost_category_id)}</td>
                  <td>{getPropertyName(entry.property_id)}</td>
                  <td style={{ textAlign: "right", fontFamily: "var(--mono)" }}>
                    {entry.gross_amount}
                  </td>
                  <td>
                    <span
                      className={`badge ${STATUS_CSS[entry.status] ?? ""}`}
                    >
                      {STATUS_LABELS[entry.status] ?? entry.status}
                    </span>
                  </td>
                  <td><SourceBadge source={entry.source} /></td>
                  <td>
                    <Link
                      to={`/kosten/${entry.id}`}
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
