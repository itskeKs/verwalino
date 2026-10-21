/**
 * Cost entries list page -- shows all Kostenpositionen with status/property filter.
 *
 * M2 GoBD-Belegfluss: cost_entry CRUD + state machine.
 * Status badges for draft_extracted / needs_review / confirmed / rejected / superseded.
 * Inline creation with category defaults pre-populated.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  exportCostEntriesCsv,
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
import type { CostEntryListQuery } from "../api/endpoints";
import { ApiError } from "../api/client";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { SourceBadge } from "../components/SourceBadge";
import {
  toDbApportionability,
  toDbDistributionKey,
  fromDbApportionability,
  fromDbDistributionKey,
  DISTRIBUTION_KEY_LABELS,
  APPORTIONABILITY_LABELS,
} from "../lib/costVocabulary";

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
  const [loaded, setLoaded] = useState<{ query: CostEntryListQuery; key: string } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const lifecycle = useRef({ mounted: false, generation: 0, downloading: false });

  useEffect(() => {
    const state = lifecycle.current;
    state.mounted = true;
    return () => { state.mounted = false; state.generation++; };
  }, []);

  const statusFilter = searchParams.get("status") ?? "";
  const propertyFilter = searchParams.get("property_id") ?? "";
  const queryKey = JSON.stringify([statusFilter, propertyFilter]);

  const reload = useCallback(() => {
    const generation = ++lifecycle.current.generation;
    const current = () => lifecycle.current.mounted && lifecycle.current.generation === generation;
    lifecycle.current.downloading = false;
    setDownloading(false);
    setDownloadError(null);
    setLoaded(null);
    setLoading(true);
    setError(null);
    const params: CostEntryListQuery = {};
    if (statusFilter) params.status = statusFilter;
    if (propertyFilter) params.property_id = propertyFilter;
    Promise.all([
      listCostEntries(params),
      listCostCategories(),
      listProperties(),
      listVendors(),
    ])
      .then(([e, c, p, v]) => {
        if (!current()) return;
        setLoaded({ query: params, key: queryKey });
        setEntries(e);
        setCategories(c);
        setProperties(p);
        setVendors(v);
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
  }, [statusFilter, propertyFilter, queryKey]);

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
      const blob = await exportCostEntriesCsv(loaded.query);
      if (!current()) return;
      const anchor = document.createElement("a");
      const url = URL.createObjectURL(blob);
      try {
        anchor.href = url;
        anchor.download = "kosten.csv";
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
        tenant_apportionable: fromDbApportionability(cat.tenant_apportionability_default),
        owner_distribution_key: cat.owner_distribution_key_default
          ? fromDbDistributionKey(cat.owner_distribution_key_default)
          : null,
        tenant_distribution_key: cat.tenant_distribution_key_default
          ? fromDbDistributionKey(cat.tenant_distribution_key_default)
          : null,
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
      // Convert UI values to DB values before sending
      const payload: CostEntryCreate = {
        ...form,
        tenant_apportionable: toDbApportionability(form.tenant_apportionable),
        owner_distribution_key: form.owner_distribution_key
          ? toDbDistributionKey(form.owner_distribution_key)
          : null,
        tenant_distribution_key: form.tenant_distribution_key
          ? toDbDistributionKey(form.tenant_distribution_key)
          : null,
      };
      await createCostEntry(payload);
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
          aria-label="Objektfilter"
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

      <button type="button" className="btn" disabled={!currentDataset || downloading} onClick={() => void downloadCsv()}>CSV herunterladen</button>
      {downloading && <p>CSV wird geladen…</p>}
      {downloadError && <p role="alert" className="error-text">{downloadError} Bitte CSV herunterladen erneut versuchen.</p>}
      {error && <p className="error-text">{error} <button type="button" onClick={reload}>Liste erneut laden</button></p>}
      {currentDataset && <div>
        <p>Listensumme Brutto: <output aria-label="Listensumme Brutto">{formatCents(entries.reduce((total, entry) => total + amountCents(entry.gross_amount), 0n))}</output></p>
        <p>Bruttosumme aller angezeigten Zeilen, einschließlich Entwürfen, verworfenen und ersetzten Kosten sowie negativen Korrekturen je Filter.</p>
      </div>}

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
                {Object.entries(DISTRIBUTION_KEY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
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
                {Object.entries(DISTRIBUTION_KEY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
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
                {Object.entries(APPORTIONABILITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
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

      {loading || loaded?.key !== queryKey ? (
        <p>{error ? "Liste nicht verfügbar." : "Lade..."}</p>
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
