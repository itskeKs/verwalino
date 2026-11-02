import { HeatingInputs } from "../components/HeatingInputs";
import { heatingPayload, useHeatingCandidates } from "../lib/heatingInputs";
import { FinancialCoverageReview } from "../components/FinancialCoverageReview";
/**
 * Settlement detail page -- view, compute, preview, finalize, PDF download, correction.
 *
 * M3 Betriebskostenabrechnung:
 * - Draft: edit advance payments, enter structured inputs (external amounts,
 *   unit metrics), compute, preview. Engine 422 errors are shown clearly
 *   so the user can supply missing values.
 * - Finalized: read-only with GoBD notice, snapshot SHA-256, PDF download.
 *   Corrections use Storno/Neu; accepting an advance proposal appends rent history
 *   without changing the lease or finalized settlement.
 * - Lines display all BGH-Pflichtangaben: total cost per category,
 *   distribution key, base share, time share, tenant amount, then
 *   sum / advance payment / balance.
 *
 * Money values are displayed as-is from the backend (string decimals, no float).
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  getSettlement,
  getConsumptionSuggestions,
  getAdvanceProposal,
  applyAdvanceProposal,
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
  AdvanceProposal,
  ConsumptionSuggestion,
  LeaseRentChange,
  SettlementRun,
  SettlementLine,
  ComputeRequest,
  HeatingConfig,
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
import { useAuth } from "../auth/useAuth";
import { ApiError } from "../api/client";
import { MailDispatch } from "../components/MailDispatch";
import { fromDbDistributionKey } from "../lib/costVocabulary";
import {
  needsExternalInput,
  needsManualInput,
  needsConsumption,
  needsPersonCount,
} from "../lib/settlementInputNeeds";

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

function formatProposalMoney(value: string): string {
  // Group the decimal text directly: converting to Number can lose cents.
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value);
  if (!match) return "Nicht verfügbar";
  const integer = match[2].replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${match[1]}${integer},${(match[3] ?? "").padEnd(2, "0")} €`;
}

function isProposalDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function nextProposalDate(periodEnd: string): string {
  if (!isProposalDate(periodEnd)) return "";
  const next = new Date(new Date(`${periodEnd}T00:00:00Z`).getTime() + 86_400_000).toISOString().slice(0, 10);
  return isProposalDate(next) ? next : "";
}

function AdvanceProposalSection({ runId }: { runId: string }) {
  const { session } = useAuth();
  const canApply = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [proposal, setProposal] = useState<AdvanceProposal | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [validFrom, setValidFrom] = useState("");
  const [saving, setSaving] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [result, setResult] = useState<LeaseRentChange | null>(null);
  const busy = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);
    getAdvanceProposal(runId)
      .then((value) => {
        if (!active) return;
        setProposal(value);
        setValidFrom(nextProposalDate(value.period_end));
      })
      .catch((err: unknown) => {
        if (active) setLoadError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [runId, retry]);

  async function handleApply(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canApply || !proposal || busy.current || result) return;
    setApplyError(null);
    if (!isProposalDate(validFrom)) {
      setApplyError("Bitte ein gültiges Datum angeben.");
      return;
    }
    busy.current = true;
    setSaving(true);
    try {
      const change = await applyAdvanceProposal(runId, { valid_from: validFrom });
      if (mounted.current) setResult(change);
    } catch (err) {
      if (mounted.current) setApplyError(err instanceof Error ? err.message : String(err));
    } finally {
      busy.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  return (
    <section className="card" aria-labelledby="advance-proposal-heading" style={{ marginBottom: "1.5rem" }}>
      <h2 id="advance-proposal-heading">Vorauszahlungsvorschlag</h2>
      {loading && <p role="status">Lade Vorauszahlungsvorschlag...</p>}
      {loadError && <>
        <p role="alert">{loadError}</p>
        <button className="btn" onClick={() => setRetry((value) => value + 1)} disabled={loading}>Erneut versuchen</button>
      </>}
      {proposal && <>
        <p>Betriebskosten-Vorauszahlung: <strong>{formatProposalMoney(proposal.monthly_amount)}</strong> monatlich</p>
        <p>Heizkosten-Vorauszahlung: <strong>{formatProposalMoney(proposal.heating_monthly_amount)}</strong> monatlich</p>
        <p>Grundlage Betriebskosten: {formatProposalMoney(proposal.tenant_cost)} Mieteranteil / {proposal.months.replace(".", ",")} Monate
          {" "}({proposal.period_start} bis {proposal.period_end}).</p>
        <p>Grundlage Heizkosten: {formatProposalMoney(proposal.heating_tenant_cost)} Mieteranteil / {proposal.months.replace(".", ",")} Monate.</p>
        <p>Beide Vorauszahlungen werden geändert. Die Kaltmiete bleibt die am gewählten Tag gültige Rate.
          Der ursprüngliche Mietvertrag und die finalisierte Abrechnung bleiben unverändert.</p>
        {applyError && <p role="alert">{applyError}</p>}
        {result && <p role="status">Miethistorie ergänzt: ab {result.valid_from}, Kaltmiete {formatProposalMoney(result.base_rent)},
          {" "}Betriebskosten-Vorauszahlung {formatProposalMoney(result.advance_payment)}, Heizkosten-Vorauszahlung {formatProposalMoney(result.heating_advance_payment)} monatlich.</p>}
        {canApply && !result && <form onSubmit={handleApply} noValidate>
          <label>Übernehmen ab
            <input type="date" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} disabled={saving} required />
          </label>
          <button className="btn btn--primary" type="submit" disabled={saving || loading || !!loadError}>
            Vorauszahlungen übernehmen
          </button>
        </form>}
      </>}
    </section>
  );
}

export function SettlementDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const { session } = useAuth();
  const canEditHeating = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";

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
  const [heatingAdvanceValue, setHeatingAdvanceValue] = useState("");

  // Structured inputs for compute
  const [externalAmounts, setExternalAmounts] = useState<ExternalAmountIn[]>([]);
  const [manualAmounts, setManualAmounts] = useState<ManualAmountIn[]>([]);
  const [unitMetrics, setUnitMetrics] = useState<UnitMetricIn[]>([]);
  const [heatingConfigs, setHeatingConfigs] = useState<HeatingConfig[]>([]);
  const [showInputs, setShowInputs] = useState(false);
  const [consumptionSuggestions, setConsumptionSuggestions] = useState<ConsumptionSuggestion[]>([]);
  const [manualConsumptionIds, setManualConsumptionIds] = useState<Set<string>>(new Set());
  const [consumptionLoading, setConsumptionLoading] = useState(false);
  const [consumptionError, setConsumptionError] = useState<string | null>(null);
  const loadGeneration = useRef(0);

  // Preview lines (not persisted)
  const [previewRun, setPreviewRun] = useState<SettlementRun | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  const singleAdvanceBlock = run?.lines.some(line =>
    line.line_type === "ADVANCE_PAYMENT" && line.label === "Geleistete Vorauszahlungen");
  const isDraft = run?.status === "draft";
  const isFinalized = run?.status === "finalized";

  const heatingCandidates = useHeatingCandidates(runId ?? "", heatingConfigs, canEditHeating && isDraft && run?.id === runId);
  const heatingBlocked = heatingConfigs.length > 0 && heatingCandidates.blocked;
  const genericEntries = costEntries.filter(entry => !heatingConfigs.some(config => config.cost_entry_id === entry.id));
  const consumptionBlocked = needsConsumption(genericEntries) && (consumptionLoading || Boolean(consumptionError));
  const heatingOptions = costEntries.flatMap(entry => {
    const category = categories.find(category => category.id === entry.cost_category_id);
    if (category?.code !== "HEIZUNG_ZENTRAL" && category?.code !== "WARMWASSER_ZENTRAL") return [];
    return [{ id: entry.id, label: category.label, kind: category.code === "HEIZUNG_ZENTRAL" ? "HEATING" as const : "HOT_WATER" as const }];
  });

  function initStructuredInputs(entries: CostEntry[], r: SettlementRun) {
    setHeatingConfigs(r.extra_inputs?.heating_inputs ?? []);
    setManualConsumptionIds(new Set());
    setConsumptionSuggestions([]);
    setConsumptionError(null);
    setConsumptionLoading(false);
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
    setExternalAmounts(externals.map(row => r.extra_inputs?.external_amounts?.find(saved => saved.cost_entry_id === row.cost_entry_id && saved.unit_id === row.unit_id) ?? row));

    // Manual amounts: one entry per cost_entry that uses MANUAL key
    const manuals: ManualAmountIn[] = entries
      .filter(needsManualInput)
      .map((e) => ({
        cost_entry_id: e.id,
        unit_id: unitId,
        amount: "",
      }));
    setManualAmounts(manuals.map(row => r.extra_inputs?.manual_amounts?.find(saved => saved.cost_entry_id === row.cost_entry_id && saved.unit_id === row.unit_id) ?? row));

    // Unit metrics: if any entry needs consumption or person count
    const hasConsumption = needsConsumption(entries);
    const hasPersons = needsPersonCount(entries);
    if (hasConsumption || hasPersons) {
      setUnitMetrics(r.extra_inputs?.unit_metrics?.length ? r.extra_inputs.unit_metrics : [
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

  const loadConsumption = useCallback(async (r: SettlementRun, entries: CostEntry[], generation: number) => {
    entries = entries.filter(entry => !r.extra_inputs?.heating_inputs?.some(config => config.cost_entry_id === entry.id));
    if (r.status !== "draft" || !needsConsumption(entries)) {
      const saved = r.extra_inputs?.unit_metrics ?? [];
      setManualConsumptionIds(new Set(saved.filter(row => row.consumption != null).map(row => row.unit_id)));
      return;
    }
    setConsumptionLoading(true);
    setConsumptionError(null);
    try {
      const sources = await getConsumptionSuggestions(r.id);
      if (generation !== loadGeneration.current) return;
      setConsumptionSuggestions(sources);
      const saved = r.extra_inputs?.unit_metrics ?? [];
      setManualConsumptionIds(new Set(saved.filter(row => row.consumption != null).map(row => row.unit_id)));
      setUnitMetrics(previous => sources.length ? sources.map(source => ({
        unit_id: source.unit_id, consumption: saved.find(row => row.unit_id === source.unit_id)?.consumption ?? source.consumption ?? "",
        person_count: needsPersonCount(entries)
          ? previous.find(row => row.unit_id === source.unit_id)?.person_count ?? undefined : null,
      })) : previous);
    } catch (err) {
      if (generation === loadGeneration.current) setConsumptionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (generation === loadGeneration.current) setConsumptionLoading(false);
    }
  }, []);

  const reload = useCallback(() => {
    if (!runId) return;
    const generation = ++loadGeneration.current;
    let active = true;
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
      .then(async ([r, p, l, u, tp, per, c]) => {
        if (!active || generation !== loadGeneration.current) return;
        setRun(r);
        setProperties(p);
        setLeases(l);
        setUnits(u);
        setTenantProfiles(tp);
        setPersons(per);
        setCategories(c);
        setAdvanceValue(r.advance_payments_total ?? "");
        setHeatingAdvanceValue(r.heating_advance_payments_total ?? "");
        // Load cost entries for the property
        if (r.property_id) {
          const entries = await listCostEntries({ property_id: r.property_id, status: "confirmed" });
          if (!active || generation !== loadGeneration.current) return;
          setCostEntries(entries);
          initStructuredInputs(entries, r);
          await loadConsumption(r, entries, generation);
        }
      })
      .catch((err) => {
        if (!active || generation !== loadGeneration.current) return;
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => { if (active && generation === loadGeneration.current) setLoading(false); });
    return () => { active = false; if (generation === loadGeneration.current) loadGeneration.current++; };
  }, [runId, loadConsumption]);

  useEffect(() => {
    return reload();
  }, [reload]);

  function getPropertyName(id: string): string {
    const prop = properties.find((p) => p.id === id);
    return prop ? prop.name : id.slice(0, 8);
  }

  function getUnitLabel(id: string): string {
    const unit = units.find((u) => u.id === id);
    if (unit) return unit.location ? `${unit.number} (${unit.location})` : unit.number;
    return consumptionSuggestions.find(source => source.unit_id === id)?.unit_label ?? id.slice(0, 8);
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
      heating_inputs: heatingPayload(heatingConfigs),
      external_amounts: externalAmounts.filter((e) => e.tenant_amount !== "" && !heatingConfigs.some(config => config.cost_entry_id === e.cost_entry_id)),
      manual_amounts: manualAmounts.filter((m) => m.amount !== "" && !heatingConfigs.some(config => config.cost_entry_id === m.cost_entry_id)),
      unit_metrics: unitMetrics
        .filter((u) => (needsConsumption(genericEntries) && manualConsumptionIds.has(u.unit_id) && u.consumption) || u.person_count != null)
        .map((u) => ({
          ...u,
          consumption: needsConsumption(genericEntries) && manualConsumptionIds.has(u.unit_id) ? u.consumption?.replace(",", ".") || null : null,
          person_count: u.person_count ?? null,
        })),
    };
  }

  // --- Actions ---

  async function handleSaveAdvance() {
    if (!runId || (advanceValue === (run?.advance_payments_total ?? "") && heatingAdvanceValue === (run?.heating_advance_payments_total ?? ""))) {
      setEditingAdvance(false);
      return;
    }
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await setAdvancePayments(runId, {
        ...(advanceValue !== (run?.advance_payments_total ?? "") ? { advance_payments_total: advanceValue.replace(",", ".") } : {}),
        ...(heatingAdvanceValue !== (run?.heating_advance_payments_total ?? "") ? { heating_advance_payments_total: heatingAdvanceValue.replace(",", ".") } : {}),
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
      `Betriebskosten- und Heizkosten-Vorauszahlungen aus erfassten Zahlungen für ${year} ableiten?`,
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await setAdvancePaymentsDerived(runId, year);
      setRun(updated);
      setAdvanceValue(updated.advance_payments_total ?? "");
      setHeatingAdvanceValue(updated.heating_advance_payments_total ?? "");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  function handleConsumptionRetry() {
    if (run) void loadConsumption(run, costEntries, loadGeneration.current);
  }

  async function handleCompute() {
    if (!runId || consumptionBlocked || heatingBlocked) return;
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
    if (!runId || consumptionBlocked || heatingBlocked) return;
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
    if (!runId || consumptionBlocked || heatingBlocked) return;
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
      (l) => l.line_type === "ADVANCE_PAYMENT" || l.line_type === "BALANCE" || l.line_type === "SUBTOTAL",
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
                      {ln.note?.startsWith("HeizkostenV intern:")
                        ? "HeizkostenV intern"
                        : ln.distribution_key
                        ? DISTRIBUTION_KEY_LABELS[fromDbDistributionKey(ln.distribution_key)] ??
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
                    <td colSpan={ln.line_type === "SUBTOTAL" && !ln.note ? 2 : 1}>{ln.label}</td>
                    {(ln.line_type !== "SUBTOTAL" || ln.note) && <td
                      style={{
                        fontFamily: "var(--mono)",
                        textAlign: "right",
                      }}
                    >
                      {ln.amount} EUR
                    </td>}
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
    const visibleExternals = externalAmounts.filter(row => !heatingConfigs.some(config => config.cost_entry_id === row.cost_entry_id));
    const hasExternals = visibleExternals.length > 0;
    const hasManuals = manualAmounts.length > 0;
    const hasMetrics = unitMetrics.length > 0;

    if (!hasExternals && !hasManuals && !hasMetrics) return null;

    return (
      <div className="card" style={{ marginBottom: "1rem" }}>
        <h2>Strukturierte Eingaben</h2>
        {needsConsumption(genericEntries) && consumptionLoading && <p>Zählerbasis wird geladen …</p>}
        {needsConsumption(genericEntries) && consumptionError && <p role="alert">{consumptionError} <button type="button" className="btn"
          onClick={handleConsumptionRetry}>Zählerbasis erneut laden</button></p>}
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
              {visibleExternals.map((ext, idx) => (
                <label key={`ext-${idx}`}>
                  {getCostEntryLabel(ext.cost_entry_id)}
                  <input
                    type="text"
                    inputMode="decimal"
                    placeholder="0.00"
                    value={ext.tenant_amount}
                    onChange={(e) => {
                      setExternalAmounts(previous => previous.map(row => row === ext ? { ...row, tenant_amount: e.target.value } : row));
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
            {needsConsumption(genericEntries) && (
              <p id="settlement-consumption-period-hint" className="hint">
                Verbrauch der Einheit im gesamten Objekt-Abrechnungsjahr eingeben, auch bei gekürztem Mietzeitraum.
              </p>
            )}
            {needsPersonCount(genericEntries) && (
              <p id="settlement-person-period-hint" className="hint">
                Personenzahlen je Einheit für das gesamte Objekt-Abrechnungsjahr in allen zugehörigen Abrechnungen gleich eingeben.
              </p>
            )}
            <div className="form-grid">
              {unitMetrics.map((m, idx) => (
                <div key={`metric-${idx}`} style={{ display: "contents" }}>
                  {needsConsumption(genericEntries) && (
                    <label>
                      Verbrauch ({getUnitLabel(m.unit_id)})
                      <input
                        type="text"
                        inputMode="decimal"
                        placeholder="0.000"
                        aria-label={`Verbrauch (${getUnitLabel(m.unit_id)})`}
                        aria-describedby="settlement-consumption-period-hint"
                        disabled={consumptionLoading || actionLoading}
                        value={m.consumption ?? ""}
                        onChange={(e) => {
                          const value = e.target.value;
                          const next = [...unitMetrics];
                          next[idx] = {
                            ...m,
                            consumption: value,
                          };
                          setUnitMetrics(next);
                          setManualConsumptionIds(previous => {
                            const updated = new Set(previous);
                            if (value) updated.add(m.unit_id); else updated.delete(m.unit_id);
                            return updated;
                          });
                        }}
                      />
                      {consumptionSuggestions.filter(source => source.unit_id === m.unit_id).map(source =>
                        <small key={source.unit_id} style={{ display: "block" }}>
                          {source.start_reading && source.end_reading
                            ? `Zählerbasis: ${source.start_reading.value} (${source.start_reading.reading_date}) → ${source.end_reading.value} (${source.end_reading.reading_date}), jeweils ±31 Tage. ${manualConsumptionIds.has(m.unit_id) ? "Handwert wird verwendet." : "Wird bei Berechnung frisch geprüft."}`
                            : source.reason}
                          {source.start_reading && source.end_reading && source.reason && ` ${source.reason}`}
                        </small>)}
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
                        aria-describedby="settlement-person-period-hint"
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
          role="alert"
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

      {isFinalized && run.id === runId && <MailDispatch kind="settlement" documentId={run.id} />}

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
              <td>{singleAdvanceBlock ? "Geleistete Vorauszahlungen" : "Betriebskosten-Vorauszahlungen"}</td>
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
                      title="Beide Vorauszahlungssummen aus erfassten NK- und HK-Zahlungen ableiten"
                    >
                      Aus Zahlungen ableiten
                    </button>
                  </>
                )}
              </td>
            </tr>
            {!singleAdvanceBlock && <tr><td>Heizkosten-Vorauszahlungen</td><td>
              <span style={{ fontFamily: "var(--mono)" }}>{run.heating_advance_payments_total ?? "-"} EUR</span>
              <span style={{ marginLeft: "0.5rem" }}>({run.heating_advance_payments_source})</span>
            </td></tr>}
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

      {run.id === runId && isFinalized && !run.superseded_by_id && run.run_type === "BK_MIETER" && (
        <AdvanceProposalSection key={run.id} runId={run.id} />
      )}

      {/* Advance payment edit */}
      {editingAdvance && isDraft && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Vorauszahlungssummen ändern</h2>
          <div className="form-grid">
            <label>
              Betriebskosten-Vorauszahlung (EUR)
              <input
                type="text"
                inputMode="decimal"
                placeholder="0.00"
                value={advanceValue}
                onChange={(e) => setAdvanceValue(e.target.value)}
              />
            </label>
            <label>Heizkosten-Vorauszahlung (EUR)
              <input type="text" inputMode="decimal" value={heatingAdvanceValue}
                onChange={(e) => setHeatingAdvanceValue(e.target.value)} />
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

      {isDraft && canEditHeating && heatingOptions.length > 0 && <HeatingInputs options={heatingOptions} configs={heatingConfigs}
        onChange={setHeatingConfigs} candidates={heatingCandidates} disabled={actionLoading} />}
      {isDraft && canEditHeating && heatingBlocked && <p>Interne Heizkosten: gültige Angaben und passende, vollständig geladene Zeiträume sind vor Berechnen, Vorschau und Finalisieren erforderlich.</p>}

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

        {run.status === "draft" && <FinancialCoverageReview kind="settlement" subjectId={run.id} values={{ advance_payments_total: run.advance_payments_total, advance_payments_source: run.advance_payments_source, heating_advance_payments_total: run.heating_advance_payments_total, heating_advance_payments_source: run.heating_advance_payments_source }} />}
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
              disabled={actionLoading || consumptionBlocked || heatingBlocked}
            >
              {actionLoading ? "Berechnen..." : "Berechnen"}
            </button>
            <button
              className="btn"
              onClick={handlePreview}
              disabled={actionLoading || consumptionBlocked || heatingBlocked}
            >
              Vorschau
            </button>
            <button
              className="btn btn--primary"
              onClick={handleFinalize}
              disabled={actionLoading || consumptionBlocked || heatingBlocked}
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
