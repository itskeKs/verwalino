import { FinancialCoverageReview } from "../components/FinancialCoverageReview";
/**
 * Settlement detail page -- view, compute, preview, finalize, PDF download, correction.
 *
 * M3 Betriebskostenabrechnung:
 * - Draft: edit advance payments, enter structured inputs (external amounts,
 *   unit metrics), compute, preview. Engine 422 errors are shown clearly
 *   so the user can supply missing values.
 * - Finalized: read-only with GoBD notice, snapshot SHA-256, PDF download.
 *   Only correction (Storno/Neu) is allowed after finalization.
 * - Lines display all BGH-Pflichtangaben: total cost per category,
 *   distribution key, base share, time share, tenant amount, then
 *   sum / advance payment / balance.
 *
 * Money values are displayed as-is from the backend (string decimals, no float).
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  getSettlement,
  setAdvancePayments,
  setAdvancePaymentsDerived,
  computeSettlement,
  previewSettlement,
  finalizeSettlement,
  createSettlementCorrection,
  settlementPdfUrl,
  listProperties,
  listLeases,
  listUnits,
  listTenantProfiles,
  listPersons,
  listCostEntries,
  listCostCategories,
} from "../api/endpoints";
import type {
  SettlementRun,
  SettlementLine,
  ComputeRequest,
  ExternalAmountIn,
  ManualAmountIn,
  UnitMetricIn,
  Property,
  LeaseRecord,
  Unit,
  TenantProfile,
  Person,
  CostEntry,
  CostCategory,
} from "../api/types";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = {
  draft: "Entwurf",
  finalized: "Finalisiert",
};

const STATUS_CSS: Record<string, string> = {
  draft: "badge--draft",
  finalized: "badge--confirmed",
};

const DISTRIBUTION_KEY_LABELS: Record<string, string> = {
  MEA: "MEA",
  AREA: "Flaeche",
  HEATED_AREA: "Beheizte Flaeche",
  UNIT_COUNT: "Einheiten",
  PERSON_COUNT: "Personen",
  CONSUMPTION: "Verbrauch",
  EXTERNAL: "Extern",
  MANUAL: "Manuell",
};

/**
 * Determine which cost entries need structured input (EXTERNAL, CONSUMPTION,
 * PERSON_COUNT, MANUAL distribution keys).
 */
export function needsExternalInput(entry: CostEntry): boolean {
  return entry.tenant_distribution_key === "EXTERNAL";
}

export function needsManualInput(entry: CostEntry): boolean {
  return entry.tenant_distribution_key === "MANUAL";
}

export function needsConsumption(entries: CostEntry[]): boolean {
  return entries.some((e) => e.tenant_distribution_key === "CONSUMPTION");
}

export function needsPersonCount(entries: CostEntry[]): boolean {
  return entries.some((e) => e.tenant_distribution_key === "PERSON_COUNT");
}

export function SettlementDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();

  const [run, setRun] = useState<SettlementRun | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [costEntries, setCostEntries] = useState<CostEntry[]>([]);
  const [categories, setCategories] = useState<CostCategory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Advance payments edit
  const [editingAdvance, setEditingAdvance] = useState(false);
  const [advanceValue, setAdvanceValue] = useState("");

  // Structured inputs for compute
  const [externalAmounts, setExternalAmounts] = useState<ExternalAmountIn[]>([]);
  const [manualAmounts, setManualAmounts] = useState<ManualAmountIn[]>([]);
  const [unitMetrics, setUnitMetrics] = useState<UnitMetricIn[]>([]);
  const [showInputs, setShowInputs] = useState(false);

  // Preview lines (not persisted)
  const [previewRun, setPreviewRun] = useState<SettlementRun | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  const isDraft = run?.status === "draft";
  const isFinalized = run?.status === "finalized";

  function initStructuredInputs(entries: CostEntry[], r: SettlementRun) {
    const unitId = r.unit_id;
    if (!unitId) return;

    // External amounts: one entry per cost_entry that uses EXTERNAL key
    const externals: ExternalAmountIn[] = entries
      .filter(needsExternalInput)
      .map((e) => ({
        cost_entry_id: e.id,
        unit_id: unitId,
        tenant_amount: "",
      }));
    setExternalAmounts(externals);

    // Manual amounts: one entry per cost_entry that uses MANUAL key
    const manuals: ManualAmountIn[] = entries
      .filter(needsManualInput)
      .map((e) => ({
        cost_entry_id: e.id,
        unit_id: unitId,
        amount: "",
      }));
    setManualAmounts(manuals);

    // Unit metrics: if any entry needs consumption or person count
    const hasConsumption = needsConsumption(entries);
    const hasPersons = needsPersonCount(entries);
    if (hasConsumption || hasPersons) {
      setUnitMetrics([
        {
          unit_id: unitId,
          consumption: hasConsumption ? "" : null,
          person_count: hasPersons ? undefined : null,
        },
      ]);
    } else {
      setUnitMetrics([]);
    }
  }

  const reload = useCallback(() => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      getSettlement(runId),
      listProperties(),
      listLeases(),
      listUnits(),
      listTenantProfiles(),
      listPersons(),
      listCostCategories(),
    ])
      .then(([r, p, l, u, tp, per, c]) => {
        setRun(r);
        setProperties(p);
        setLeases(l);
        setUnits(u);
        setTenantProfiles(tp);
        setPersons(per);
        setCategories(c);
        setAdvanceValue(r.advance_payments_total ?? "");
        // Load cost entries for the property
        if (r.property_id) {
          listCostEntries({ property_id: r.property_id, status: "confirmed" }).then(
            (entries) => {
              setCostEntries(entries);
              // Initialize structured inputs for entries that need them
              initStructuredInputs(entries, r);
            },
          );
        }
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [runId]);

  useEffect(() => {
    reload();
  }, [reload]);

  function getPropertyName(id: string): string {
    const prop = properties.find((p) => p.id === id);
    return prop ? prop.name : id.slice(0, 8);
  }

  function getUnitLabel(id: string): string {
    const unit = units.find((u) => u.id === id);
    return unit ? `${unit.number} (${unit.location})` : id.slice(0, 8);
  }

  function getTenantName(tenantProfileId: string | null): string {
    if (!tenantProfileId) return "—";
    const tp = tenantProfiles.find((t) => t.id === tenantProfileId);
    if (!tp) return "—";
    const person = persons.find((p) => p.id === tp.person_id);
    if (!person) return "—";
    const parts = [person.first_name, person.last_name].filter(Boolean);
    return parts.length > 0 ? parts.join(" ") : "—";
  }

  function getLeaseInfo(): string {
    if (!run?.lease_agreement_id) return "-";
    const lease = leases.find((l) => l.id === run.lease_agreement_id);
    if (!lease) return run.lease_agreement_id.slice(0, 8);
    return `ab ${lease.valid_from}${lease.valid_to ? ` bis ${lease.valid_to}` : " (laufend)"}`;
  }

  function getCategoryLabel(code: string | null): string {
    if (!code) return "-";
    const cat = categories.find((c) => c.code === code);
    return cat ? `${cat.code} ${cat.label}` : code;
  }

  function getCostEntryLabel(id: string): string {
    const entry = costEntries.find((e) => e.id === id);
    if (!entry) return id.slice(0, 8);
    const cat = categories.find((c) => c.id === entry.cost_category_id);
    return cat ? `${cat.code} ${cat.label}` : id.slice(0, 8);
  }

  function buildComputeRequest(): ComputeRequest {
    return {
      external_amounts: externalAmounts.filter((e) => e.tenant_amount !== ""),
      manual_amounts: manualAmounts.filter((m) => m.amount !== ""),
      unit_metrics: unitMetrics
        .filter((u) => u.consumption || u.person_count != null)
        .map((u) => ({
          ...u,
          consumption: u.consumption || null,
          person_count: u.person_count ?? null,
        })),
    };
  }

  // --- Actions ---

  async function handleSaveAdvance() {
    if (!runId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await setAdvancePayments(runId, {
        advance_payments_total: advanceValue,
      });
      setRun(updated);
      setEditingAdvance(false);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleDeriveAdvance() {
    if (!runId || !run) return;
    const year = run.period_start ? parseInt(run.period_start.slice(0, 4), 10) : null;
    if (!year) return;
    const confirmed = window.confirm(
      `Vorauszahlungssumme aus erfassten NK-Zahlungen fuer ${year} ableiten?`,
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await setAdvancePaymentsDerived(runId, year);
      setRun(updated);
      setAdvanceValue(updated.advance_payments_total ?? "");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleCompute() {
    if (!runId) return;
    setActionLoading(true);
    setActionError(null);
    setShowPreview(false);
    try {
      const result = await computeSettlement(runId, buildComputeRequest());
      setRun(result);
    } catch (err) {
      if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function handlePreview() {
    if (!runId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const result = await previewSettlement(runId, buildComputeRequest());
      setPreviewRun(result);
      setShowPreview(true);
    } catch (err) {
      if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function handleFinalize() {
    if (!runId) return;
    const confirmed = window.confirm(
      "Abrechnung finalisieren? Nach der Finalisierung ist die Abrechnung " +
        "unveraenderbar (GoBD). Aenderungen sind nur als Korrekturabrechnung moeglich.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const result = await finalizeSettlement(runId, buildComputeRequest());
      setRun(result);
      setShowPreview(false);
    } catch (err) {
      if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function handleCorrection() {
    if (!runId) return;
    const confirmed = window.confirm(
      "Korrekturabrechnung erstellen? Die bestehende Abrechnung bleibt " +
        "unveraendert erhalten (Storno/Neu). Ein neuer Entwurf wird angelegt.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const newRun = await createSettlementCorrection(runId);
      navigate(`/abrechnungen/${newRun.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  function handleDownloadPdf() {
    if (!runId) return;
    window.open(settlementPdfUrl(runId), "_blank");
  }

  // --- Render helpers ---

  function renderLines(lines: SettlementLine[]) {
    const costLines = lines.filter((l) => l.line_type === "COST");
    const summaryLines = lines.filter(
      (l) => l.line_type === "ADVANCE_PAYMENT" || l.line_type === "BALANCE",
    );

    return (
      <div>
        <h3 style={{ margin: "0 0 0.75rem" }}>Kostenaufstellung</h3>
        {costLines.length === 0 ? (
          <p className="empty-hint">
            Keine Kostenpositionen berechnet. Bitte zuerst berechnen.
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Kostenart</th>
                  <th>Gesamtkosten</th>
                  <th>Umlageschluessel</th>
                  <th>Anteilsbasis</th>
                  <th>Zeitanteil</th>
                  <th style={{ textAlign: "right" }}>Mieteranteil</th>
                </tr>
              </thead>
              <tbody>
                {costLines.map((ln, idx) => (
                  <tr key={ln.id || idx}>
                    <td>
                      <strong>{ln.label}</strong>
                      {ln.cost_entry_id && <div><Link to={`/kosten/${ln.cost_entry_id}`} className="link">Kostenposition und Beleg ansehen</Link></div>}
                      {ln.cost_category_code && (
                        <div
                          style={{
                            fontSize: "0.78rem",
                            color: "var(--text-muted)",
                          }}
                        >
                          {getCategoryLabel(ln.cost_category_code)}
                        </div>
                      )}
                      {ln.note && (
                        <div
                          style={{
                            fontSize: "0.78rem",
                            color: "var(--text-muted)",
                            fontStyle: "italic",
                          }}
                        >
                          {ln.note}
                        </div>
                      )}
                    </td>
                    <td style={{ fontFamily: "var(--mono)", textAlign: "right" }}>
                      {ln.total_amount != null ? `${ln.total_amount} EUR` : "-"}
                    </td>
                    <td>
                      {ln.distribution_key
                        ? DISTRIBUTION_KEY_LABELS[ln.distribution_key] ??
                          ln.distribution_key
                        : "-"}
                    </td>
                    <td style={{ fontFamily: "var(--mono)" }}>
                      {ln.distribution_base_share != null &&
                      ln.distribution_base_total != null
                        ? `${ln.distribution_base_share} / ${ln.distribution_base_total}`
                        : "-"}
                    </td>
                    <td style={{ fontFamily: "var(--mono)" }}>
                      {ln.time_share_days != null &&
                      ln.time_share_total_days != null
                        ? `${ln.time_share_days} / ${ln.time_share_total_days} Tage`
                        : "-"}
                    </td>
                    <td
                      style={{
                        fontFamily: "var(--mono)",
                        textAlign: "right",
                        fontWeight: 600,
                      }}
                    >
                      {ln.amount} EUR
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Summary lines */}
        {summaryLines.length > 0 && (
          <div style={{ marginTop: "1rem" }}>
            <table className="data-table">
              <tbody>
                {summaryLines.map((ln, idx) => (
                  <tr
                    key={ln.id || `summary-${idx}`}
                    style={{
                      fontWeight: ln.line_type === "BALANCE" ? 700 : 600,
                      fontSize:
                        ln.line_type === "BALANCE" ? "1.05rem" : undefined,
                    }}
                  >
                    <td>{ln.label}</td>
                    <td
                      style={{
                        fontFamily: "var(--mono)",
                        textAlign: "right",
                      }}
                    >
                      {ln.amount} EUR
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

  function renderStructuredInputs() {
    const hasExternals = externalAmounts.length > 0;
    const hasManuals = manualAmounts.length > 0;
    const hasMetrics = unitMetrics.length > 0;

    if (!hasExternals && !hasManuals && !hasMetrics) return null;

    return (
      <div className="card" style={{ marginBottom: "1rem" }}>
        <h2>Strukturierte Eingaben</h2>
        <p
          style={{
            fontSize: "0.85rem",
            color: "var(--text-muted)",
            marginBottom: "0.75rem",
          }}
        >
          Einige Kostenpositionen erfordern zusaetzliche Angaben fuer die
          Berechnung. Fehlende Werte fuehren zu einer Fehlermeldung beim
          Berechnen.
        </p>

        {hasExternals && (
          <>
            <h3 style={{ fontSize: "0.95rem", margin: "0.75rem 0 0.5rem" }}>
              Externe Heizkosten (je Einheit)
            </h3>
            <div className="form-grid">
              {externalAmounts.map((ext, idx) => (
                <label key={`ext-${idx}`}>
                  {getCostEntryLabel(ext.cost_entry_id)}
                  <input
                    type="text"
                    inputMode="decimal"
                    placeholder="0.00"
                    value={ext.tenant_amount}
                    onChange={(e) => {
                      const next = [...externalAmounts];
                      next[idx] = { ...ext, tenant_amount: e.target.value };
                      setExternalAmounts(next);
                    }}
                  />
                </label>
              ))}
            </div>
          </>
        )}

        {hasManuals && (
          <>
            <h3 style={{ fontSize: "0.95rem", margin: "0.75rem 0 0.5rem" }}>
              Manuelle Betraege (je Einheit)
            </h3>
            <div className="form-grid">
              {manualAmounts.map((man, idx) => (
                <label key={`man-${idx}`}>
                  {getCostEntryLabel(man.cost_entry_id)}
                  <input
                    type="text"
                    inputMode="decimal"
                    placeholder="0.00"
                    value={man.amount}
                    onChange={(e) => {
                      const next = [...manualAmounts];
                      next[idx] = { ...man, amount: e.target.value };
                      setManualAmounts(next);
                    }}
                  />
                </label>
              ))}
            </div>
          </>
        )}

        {hasMetrics && (
          <>
            <h3 style={{ fontSize: "0.95rem", margin: "0.75rem 0 0.5rem" }}>
              Verbrauch / Personenzahl
            </h3>
            <div className="form-grid">
              {unitMetrics.map((m, idx) => (
                <div key={`metric-${idx}`} style={{ display: "contents" }}>
                  {m.consumption !== null && (
                    <label>
                      Verbrauch ({getUnitLabel(m.unit_id)})
                      <input
                        type="text"
                        inputMode="decimal"
                        placeholder="0.000"
                        value={m.consumption ?? ""}
                        onChange={(e) => {
                          const next = [...unitMetrics];
                          next[idx] = {
                            ...m,
                            consumption: e.target.value || null,
                          };
                          setUnitMetrics(next);
                        }}
                      />
                    </label>
                  )}
                  {m.person_count !== null && (
                    <label>
                      Personenzahl ({getUnitLabel(m.unit_id)})
                      <input
                        type="number"
                        min="0"
                        step="1"
                        placeholder="0"
                        value={m.person_count ?? ""}
                        onChange={(e) => {
                          const next = [...unitMetrics];
                          next[idx] = {
                            ...m,
                            person_count: e.target.value
                              ? parseInt(e.target.value, 10)
                              : undefined,
                          };
                          setUnitMetrics(next);
                        }}
                      />
                    </label>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    );
  }

  // --- Rendering ---

  if (loading) return <p>Lade...</p>;
  if (error && !run) return <p className="error-text">{error}</p>;
  if (!run) return <p className="error-text">Abrechnung nicht gefunden.</p>;

  return (
    <div className="settlement-detail">
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/abrechnungen" className="link">
          &larr; Alle Abrechnungen
        </Link>
      </p>

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
      <h1 style={{ margin: 0 }}>
          Betriebskostenabrechnung{" "}
          <span
            className={`badge ${STATUS_CSS[run.status] ?? ""}`}
            style={{ fontSize: "0.8rem", verticalAlign: "middle" }}
          >
            {STATUS_LABELS[run.status] ?? run.status}
          </span>
        </h1>
      </div>

      {actionError && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#ffebee",
            borderColor: "#ef9a9a",
          }}
        >
          <strong>Fehler:</strong> {actionError}
          {actionError.includes("missing") ||
          actionError.includes("Missing") ||
          actionError.includes("required") ||
          actionError.includes("Required") ||
          actionError.includes("fehlt") ||
          actionError.includes("Fehlend") ? (
            <p
              style={{
                fontSize: "0.85rem",
                marginTop: "0.5rem",
                marginBottom: 0,
              }}
            >
              Bitte tragen Sie die fehlenden Werte im Abschnitt
              &quot;Strukturierte Eingaben&quot; nach und versuchen Sie es
              erneut.
            </p>
          ) : null}
        </div>
      )}

      {/* GoBD notice for finalized runs */}
      {isFinalized && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#fff8e1",
            borderColor: "#ffcc02",
          }}
        >
          <strong>GoBD:</strong> Diese Abrechnung ist finalisiert und
          unveraenderbar. Aenderungen sind nur als Korrekturabrechnung
          (Storno/Neu) moeglich.
          {run.snapshot_sha256 && (
            <div style={{ marginTop: "0.5rem", fontSize: "0.82rem" }}>
              Snapshot SHA-256:{" "}
              <code title={run.snapshot_sha256}>
                {run.snapshot_sha256}
              </code>
            </div>
          )}
        </div>
      )}

      {/* Superseded notice */}
      {run.superseded_by_id && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#f3e5f5",
            borderColor: "#ce93d8",
          }}
        >
          Diese Abrechnung wurde ersetzt durch{" "}
          <Link
            to={`/abrechnungen/${run.superseded_by_id}`}
            className="link"
          >
            Korrekturabrechnung
          </Link>
          .
        </div>
      )}

      {/* Overview card */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Uebersicht</h2>
        <table className="info-table">
          <tbody>
            <tr>
              <td>Objekt</td>
              <td>
                <Link
                  to={`/objekte/${run.property_id}`}
                  className="link"
                >
                  {getPropertyName(run.property_id)}
                </Link>
              </td>
            </tr>
            <tr>
              <td>Einheit</td>
              <td>
                {run.unit_id ? getUnitLabel(run.unit_id) : "-"}
              </td>
            </tr>
            <tr>
              <td>Mieter</td>
              <td>{getTenantName(run.tenant_profile_id)}</td>
            </tr>
            <tr>
              <td>Mietvertrag</td>
              <td>{getLeaseInfo()}</td>
            </tr>
            <tr>
              <td>Zeitraum</td>
              <td>
                {run.period_start} bis {run.period_end}
              </td>
            </tr>
            <tr>
              <td>Abrechnungstyp</td>
              <td>{run.run_type}</td>
            </tr>
            <tr>
              <td>Vorauszahlungen</td>
              <td>
                <span style={{ fontFamily: "var(--mono)" }}>
                  {run.advance_payments_total ?? "-"} EUR
                </span>
                <span
                  style={{
                    fontSize: "0.78rem",
                    color: "var(--text-muted)",
                    marginLeft: "0.5rem",
                  }}
                >
                  ({run.advance_payments_source})
                </span>
                {isDraft && !editingAdvance && (
                  <>
                    <button
                      className="btn btn--small"
                      style={{ marginLeft: "0.5rem" }}
                      onClick={() => setEditingAdvance(true)}
                    >
                      Aendern
                    </button>
                    <button
                      className="btn btn--small"
                      style={{ marginLeft: "0.25rem" }}
                      onClick={handleDeriveAdvance}
                      disabled={actionLoading}
                      title="Vorauszahlungssumme aus erfassten NK-Zahlungen ableiten"
                    >
                      Aus Zahlungen ableiten
                    </button>
                  </>
                )}
              </td>
            </tr>
            {run.total_cost != null && (
              <tr>
                <td>Gesamtkosten</td>
                <td style={{ fontFamily: "var(--mono)" }}>
                  {run.total_cost} EUR
                </td>
              </tr>
            )}
            {run.balance != null && (
              <tr>
                <td>
                  {parseFloat(run.balance) > 0
                    ? "Nachzahlung"
                    : parseFloat(run.balance) < 0
                      ? "Guthaben"
                      : "Saldo"}
                </td>
                <td
                  style={{
                    fontFamily: "var(--mono)",
                    fontWeight: 700,
                    color:
                      parseFloat(run.balance) > 0
                        ? "#b71c1c"
                        : parseFloat(run.balance) < 0
                          ? "#2e7d32"
                          : undefined,
                  }}
                >
                  {run.balance} EUR
                </td>
              </tr>
            )}
            <tr>
              <td>Engine-Version</td>
              <td>
                <code>{run.engine_version}</code>
              </td>
            </tr>
            {run.finalized_at && (
              <tr>
                <td>Finalisiert am</td>
                <td>{run.finalized_at}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Advance payment edit */}
      {editingAdvance && isDraft && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Vorauszahlungssumme aendern</h2>
          <div className="form-grid">
            <label>
              Vorauszahlungssumme (manuell)
              <input
                type="text"
                inputMode="decimal"
                placeholder="0.00"
                value={advanceValue}
                onChange={(e) => setAdvanceValue(e.target.value)}
              />
            </label>
            <div className="form-actions">
              <button
                className="btn btn--primary"
                onClick={handleSaveAdvance}
                disabled={actionLoading}
              >
                {actionLoading ? "Speichern..." : "Speichern"}
              </button>
              <button
                className="btn"
                onClick={() => setEditingAdvance(false)}
              >
                Abbrechen
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Structured inputs (draft only) */}
      {isDraft && (
        <>
          <div style={{ marginBottom: "1rem" }}>
            <button
              className="btn"
              onClick={() => setShowInputs(!showInputs)}
            >
              {showInputs
                ? "Strukturierte Eingaben ausblenden"
                : "Strukturierte Eingaben anzeigen"}
            </button>
          </div>
          {showInputs && renderStructuredInputs()}
        </>
      )}

        {run.status === "draft" && <FinancialCoverageReview kind="settlement" subjectId={run.id} values={{ advance_payments_total: run.advance_payments_total, advance_payments_source: run.advance_payments_source }} />}
      {/* Action buttons */}
      <div
        style={{
          display: "flex",
          gap: "0.5rem",
          marginBottom: "1.5rem",
          flexWrap: "wrap",
        }}
      >
        {isDraft && (
          <>
            <button
              className="btn btn--primary"
              onClick={handleCompute}
              disabled={actionLoading}
            >
              {actionLoading ? "Berechnen..." : "Berechnen"}
            </button>
            <button
              className="btn"
              onClick={handlePreview}
              disabled={actionLoading}
            >
              Vorschau
            </button>
            <button
              className="btn btn--primary"
              onClick={handleFinalize}
              disabled={actionLoading}
              style={{ background: "#2e7d32", borderColor: "#2e7d32" }}
            >
              Finalisieren
            </button>
          </>
        )}
        {isFinalized && !run.superseded_by_id && (
          <>
            <button
              className="btn btn--primary"
              onClick={handleDownloadPdf}
            >
              PDF herunterladen
            </button>
            <button
              className="btn"
              onClick={handleCorrection}
              disabled={actionLoading}
            >
              Korrekturabrechnung erstellen
            </button>
          </>
        )}
      </div>

      {/* Preview result */}
      {showPreview && previewRun && (
        <div
          className="card"
          style={{
            marginBottom: "1.5rem",
            background: "#e3f2fd",
            borderColor: "#90caf9",
          }}
        >
          <h2>Vorschau (nicht gespeichert)</h2>
          <table className="info-table" style={{ marginBottom: "1rem" }}>
            <tbody>
              <tr>
                <td>Gesamtkosten</td>
                <td style={{ fontFamily: "var(--mono)" }}>
                  {previewRun.total_cost} EUR
                </td>
              </tr>
              <tr>
                <td>
                  {previewRun.balance && parseFloat(previewRun.balance) > 0
                    ? "Nachzahlung"
                    : previewRun.balance &&
                        parseFloat(previewRun.balance) < 0
                      ? "Guthaben"
                      : "Saldo"}
                </td>
                <td style={{ fontFamily: "var(--mono)", fontWeight: 700 }}>
                  {previewRun.balance} EUR
                </td>
              </tr>
            </tbody>
          </table>
          {renderLines(previewRun.lines)}
          <div style={{ marginTop: "0.75rem" }}>
            <button
              className="btn"
              onClick={() => setShowPreview(false)}
            >
              Vorschau schliessen
            </button>
          </div>
        </div>
      )}

      {/* Computed lines (persisted) */}
      <div className="card">
        <h2>
          {isFinalized
            ? "Abrechnungsergebnis"
            : "Berechnete Kostenpositionen"}
        </h2>
        {run.lines.length === 0 ? (
          <p className="empty-hint">
            {isDraft
              ? 'Noch nicht berechnet. Klicken Sie "Berechnen" um die Abrechnung zu erstellen.'
              : "Keine Positionen vorhanden."}
          </p>
        ) : (
          renderLines(run.lines)
        )}
      </div>
    </div>
  );
}
