import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  getBankAccount, importBankTransactions, listBankMatchingTransactions,
  assignBankTransaction, ignoreBankTransaction, listUnits, listLeases,
  listOwnership, listTenantProfiles, listOwnerProfiles, listPersons,
} from "../api/endpoints";
import type {
  BankAccount, BankImportResult, BankTransactionWithSuggestions, BankTransactionStatus,
  BankAssignmentLine, BankMatchSuggestion, BankMatchLine, LeaseRecord,
  OwnerProfile, TenantProfile, Person, Unit, OwnershipRecord,
} from "../api/types";
import { useAuth } from "../auth/useAuth";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "../components/ui/dialog";

const STATUS_LABELS: Record<BankTransactionStatus, string> = {
  NEU: "Neu",
  ZUGEORDNET: "Zugeordnet",
  IGNORIERT: "Ignoriert",
};

// Decimal strings are formatted without converting monetary values to floating point.
function money(amount: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
  if (!match) return amount;
  const [, sign, integer, fraction = ""] = match;
  return `${sign}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ".")},${fraction.padEnd(2, "0")} €`;
}

function date(value: string | null): string {
  if (!value) return "–";
  return value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, "$3.$2.$1");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function BankTransactionsPage() {
  const { id } = useParams<{ id: string }>();
  return id ? <AccountTransactions key={id} id={id} /> : <p className="error-text">Bankkonto fehlt.</p>;
}

function AccountTransactions({ id }: { id: string }) {
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [searchParams, setSearchParams] = useSearchParams();
  const [account, setAccount] = useState<BankAccount | null>(null);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [items, setItems] = useState<BankTransactionWithSuggestions[]>([]);
  const [status, setStatus] = useState<BankTransactionStatus | "">("NEU");
  const [offsets, setOffsets] = useState([0]);
  const offset = offsets[offsets.length - 1];
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BankImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<BankTransactionWithSuggestions | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);
  const mutationLock = useRef(false);
  const importLock = useRef(false);
  const fileVersion = useRef(0);
  const mounted = useRef(false);
  const showImport = canWrite && searchParams.get("import") === "1";

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getBankAccount(id).then(value => {
      if (!cancelled) setAccount(value);
    }).catch(error => {
      if (!cancelled) setAccountError(errorMessage(error));
    });
    return () => { cancelled = true; };
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    listBankMatchingTransactions(id, status === "" ? "ALL" : status, offset).then(result => {
      if (cancelled) return;
      setItems(result.items);
      setNextOffset(result.next_offset);
    }).catch(error => {
      if (!cancelled) setListError(errorMessage(error));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [id, status, offset, revision]);

  function resetList() {
    setLoading(true);
    setListError(null);
    setItems([]);
    setNextOffset(null);
  }

  function changeFile(value: File | null) {
    fileVersion.current += 1;
    setFile(value);
    setPreview(null);
    setImportError(null);
    setSuccess(null);
  }

  async function runImport(dryRun: boolean) {
    if (!canWrite || !file || importLock.current || (!dryRun && !preview)) return;
    importLock.current = true;
    setBusy(true);
    setImportError(null);
    setSuccess(null);
    if (dryRun) setPreview(null);
    const version = fileVersion.current;
    try {
      const result = await importBankTransactions(id, file, dryRun);
      if (!mounted.current) return;
      if (dryRun) {
        if (version === fileVersion.current) setPreview(result);
      } else {
        if (version === fileVersion.current) {
          setPreview(null);
          setSuccess(`${result.imported_count} neue Umsätze importiert.`);
        }
        resetList();
        setOffsets([0]);
        setRevision(value => value + 1);
      }
    } catch (error) {
      if (mounted.current && version === fileVersion.current) setImportError(errorMessage(error));
    } finally {
      importLock.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function mutate(item: BankTransactionWithSuggestions, lines?: BankAssignmentLine[]) {
    if (!canWrite || item.status !== "NEU" || mutationLock.current) return;
    mutationLock.current = true;
    setMutating(true);
    setMutationError(null);
    setSuccess(null);
    try {
      if (lines) await assignBankTransaction(item.id, lines);
      else await ignoreBankTransaction(item.id);
      if (!mounted.current) return;
      setEditing(null);
      setSuccess(lines ? "Umsatz zugeordnet." : "Umsatz ignoriert.");
      resetList();
      setRevision(value => value + 1);
    } catch (error) {
      if (mounted.current) setMutationError(errorMessage(error));
    } finally {
      mutationLock.current = false;
      if (mounted.current) setMutating(false);
    }
  }

  function openSplit(item: BankTransactionWithSuggestions) {
    if (!canWrite || mutationLock.current) return;
    setMutationError(null);
    setSuccess(null);
    setEditing(item);
  }

  function acceptSuggestion(item: BankTransactionWithSuggestions) {
    if (!canWrite || mutationLock.current) return;
    const candidates = item.suggestions ?? [];
    const candidate = candidates[0];
    const lines = candidate && suggestionLines(candidate, item.amount);
    if (candidates.length === 1 && (candidate.confidence === "HIGH" || candidate.confidence === "MEDIUM") && candidate.requires_review === false && cents(candidate.unallocated_amount) === 0n && lines) {
      void mutate(item, lines);
    } else openSplit(item);
  }

  return (
    <div className="paged-list">
      <Link to="/bankkonten">← Bankkonten</Link>
      <h1>Umsätze{account ? ` – ${account.label}` : ""}</h1>
      {account?.iban && <p>{account.iban}</p>}
      {accountError && <p role="alert" className="error-text">{accountError}</p>}
      {canWrite && !showImport && <button className="btn btn--primary" onClick={() => {
        const params = new URLSearchParams(searchParams);
        params.set("import", "1");
        setSearchParams(params);
      }}>Kontoauszug importieren</button>}

      {showImport && <section className="card" style={{ marginBottom: "1rem" }} aria-label="Kontoauszug importieren">
        <h2>Kontoauszug importieren</h2>
        <p>Datei auswählen, Vorschau prüfen und anschließend ausdrücklich übernehmen.</p>
        <label>Kontoauszug<input type="file" accept=".csv,text/csv,.xml,application/xml,text/xml,.zip,application/zip" disabled={busy && preview !== null} onChange={event => changeFile(event.target.files?.[0] ?? null)} /></label>
        <button type="button" className="btn" disabled={!file || busy} onClick={() => void runImport(true)}>{busy ? "Bitte warten…" : "Vorschau erstellen"}</button>
        {importError && <p role="alert" className="error-text">{importError}</p>}
        {success && <p role="status">{success}</p>}
        {preview && <div>
          <h3>Importvorschau</h3>
          <p>{preview.count} Umsätze: {preview.new_count} neu, {preview.duplicate_count} Dubletten.</p>
          <p>Zeitraum: {date(preview.date_from)} bis {date(preview.date_to)}</p>
          <p>Gesamtbetrag: {money(preview.total_amount)} · Davon neu: {money(preview.new_total_amount)}</p>
          {preview.preview_truncated && <p>Vorschau gekürzt. Die Übernahme umfasst alle oben gezählten Umsätze der Datei.</p>}
          <div className="paged-table"><table className="data-table">
            <thead><tr><th>Buchung</th><th>Wertstellung</th><th>Gegenpartei</th><th>IBAN</th><th>Verwendungszweck</th><th>Betrag</th><th>Importstatus</th></tr></thead>
            <tbody>{preview.transactions.map((item, index) => <tr key={`${item.import_hash}:${index}`}>
              <td>{date(item.booking_date)}</td><td>{date(item.value_date)}</td><td>{item.counterparty_name ?? "–"}</td><td>{item.counterparty_iban ?? "–"}</td><td>{item.purpose ?? "–"}</td><td>{money(item.amount)}</td><td>{item.is_duplicate ? "Dublette" : "Neu"}</td>
            </tr>)}</tbody>
          </table></div>
          <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void runImport(false)}>Übernehmen</button>
        </div>}
      </section>}

      {!editing && mutationError && <p role="alert" className="error-text">{mutationError}</p>}
      {!showImport && success && <p role="status">{success}</p>}
      {editing && canWrite && <SplitDialog transaction={editing} account={account} busy={mutating} error={mutationError}
        onClose={() => { if (!mutationLock.current) { setEditing(null); setMutationError(null); } }}
        onSubmit={lines => void mutate(editing, lines)} />}
      <label htmlFor="bank-transaction-status">Status</label>
      <select id="bank-transaction-status" value={status} onChange={event => {
        resetList();
        setStatus(event.target.value as BankTransactionStatus | "");
        setOffsets([0]);
      }}>
        <option value="">Alle</option>
        {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      {loading ? <p>Lade Umsätze…</p> : listError ? <div>
        <p role="alert" className="error-text">{listError}</p>
        <button className="btn" onClick={() => { resetList(); setRevision(value => value + 1); }}>Erneut laden</button>
      </div> : items.length === 0 ? <p className="empty-hint">Keine Umsätze vorhanden.</p> : <div className="card paged-table">
        <table className="data-table">
          <thead><tr><th>Buchung</th><th>Wertstellung</th><th>Gegenpartei</th><th>IBAN</th><th>Verwendungszweck</th><th>Betrag</th><th>Status</th><th>Vorschläge / Aktionen</th></tr></thead>
          <tbody>{items.map(item => <tr key={item.id}>
            <td>{date(item.booking_date)}</td><td>{date(item.value_date)}</td><td>{item.counterparty_name ?? "–"}</td><td>{item.counterparty_iban ?? "–"}</td><td>{item.purpose ?? "–"}</td><td>{money(item.amount)}</td><td>{STATUS_LABELS[item.status]}</td>
            <td>{(item.suggestions ?? []).map((candidate, index) => <SuggestionDetails key={index} suggestion={candidate} />)}
              {canWrite && item.status === "NEU" && <div className="form-actions">
                {(item.suggestions ?? []).length > 0 && <button className="btn" disabled={mutating} onClick={() => acceptSuggestion(item)}>Vorschlag übernehmen</button>}
                <button className="btn" disabled={mutating} onClick={() => openSplit(item)}>Aufteilen</button>
                <button className="btn" disabled={mutating} onClick={() => void mutate(item)}>Ignorieren</button>
              </div>}
            </td>
          </tr>)}</tbody>
        </table>
      </div>}
      <div className="form-actions">
        <button className="btn" disabled={loading || offsets.length <= 1} onClick={() => { resetList(); setOffsets(value => value.slice(0, -1)); }}>Zurück</button>
        <button className="btn" disabled={loading || listError !== null || nextOffset === null} onClick={() => {
          if (nextOffset !== null) { resetList(); setOffsets(value => [...value, nextOffset]); }
        }}>Weiter</button>
      </div>
    </div>
  );
}

const PAYMENT_TYPES: Record<BankAssignmentLine["payment_type"], string> = {
  KALTMIETE: "Kaltmiete", NK_VORAUSZAHLUNG: "NK-Vorauszahlung", KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss", RUECKLAGENANTEIL: "Rücklagenanteil",
  RUECKLAGENENTNAHME: "Rücklagenentnahme",
  SONDERZAHLUNG: "Sonder-/Nachzahlung", NACHZAHLUNG: "Nachzahlung",
};
const TENANT_TYPES = new Set(["KALTMIETE", "NK_VORAUSZAHLUNG", "KAUTION"]);
const OWNER_TYPES = new Set(["HAUSGELD_VORSCHUSS", "RUECKLAGENANTEIL", "SONDERZAHLUNG"]);

// No Number/parseFloat conversion: bank and user decimal strings stay exact.
function cents(value: string): bigint | null {
  if (typeof value !== "string") return null;
  const match = /^(-?)(\d+)(?:[.,](\d{1,2}))?$/.exec(value.trim());
  if (!match) return null;
  const amount = BigInt(match[2]) * 100n + BigInt((match[3] ?? "").padEnd(2, "0"));
  return match[1] ? -amount : amount;
}
function decimal(value: bigint): string {
  const absolute = value < 0n ? -value : value;
  return `${value < 0n ? "-" : ""}${absolute / 100n}.${String(absolute % 100n).padStart(2, "0")}`;
}
function positiveAmount(value: string): string | null {
  const amount = cents(value);
  return amount !== null && amount > 0n && amount <= 99999999999999n ? decimal(amount) : null;
}
function validLine(line: BankAssignmentLine): boolean {
  const lease = Boolean(line.lease_agreement_id);
  const owner = Boolean(line.owner_profile_id);
  return Object.hasOwn(PAYMENT_TYPES, line.payment_type) && positiveAmount(line.amount) !== null
    && Number.isInteger(line.period_year) && line.period_year >= 1 && line.period_year <= 9999
    && Number.isInteger(line.period_month) && line.period_month >= 1 && line.period_month <= 12
    && lease !== owner && !(owner && line.tenant_profile_id)
    && (!TENANT_TYPES.has(line.payment_type) || lease) && (!OWNER_TYPES.has(line.payment_type) || owner);
}
function coversTransaction(lines: BankAssignmentLine[], amount: string): boolean {
  const total = cents(amount);
  return total !== null && lines.length > 0 && lines.length <= 100 && lines.every(validLine)
    && lines.reduce((sum, line) => sum + cents(line.amount)!, 0n) === (total < 0n ? -total : total);
}
function suggestionLines(suggestion: BankMatchSuggestion, amount: string): BankAssignmentLine[] | null {
  const lines = suggestion.lines.map(line => ({
    payment_type: line.payment_type, amount: line.amount === null ? "" : positiveAmount(line.amount) ?? "",
    period_year: line.period_year, period_month: line.period_month,
    ...(line.lease_agreement_id ? { lease_agreement_id: line.lease_agreement_id } : {}),
    ...(line.tenant_profile_id ? { tenant_profile_id: line.tenant_profile_id } : {}),
    ...(line.owner_profile_id ? { owner_profile_id: line.owner_profile_id } : {}),
  }));
  return coversTransaction(lines, amount) ? lines : null;
}
function SuggestionDetails({ suggestion }: { suggestion: BankMatchSuggestion }) {
  return <div>
    <strong>{suggestion.target_name} · {suggestion.confidence}</strong>
    <ul>{suggestion.lines.map((line, index) => <li key={index}>
      {PAYMENT_TYPES[line.payment_type] ?? line.payment_type} · {line.period_month}/{line.period_year} · {line.amount === null ? "Betrag festlegen" : money(line.amount)} (offen: {money(line.open_amount)})
    </li>)}</ul>
    <ul>{suggestion.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
    {(suggestion.requires_review || suggestion.confidence === "LOW") && <p>Prüfung erforderlich – Beträge und Ziele vor dem Buchen prüfen.</p>}
    <p>Nicht aufgeteilt: {money(suggestion.unallocated_amount)}</p>
  </div>;
}

interface SplitRow {
  key: number;
  payment_type: BankAssignmentLine["payment_type"];
  amount: string;
  period_year: string;
  period_month: string;
  targetKind: "lease" | "owner";
  target: string;
  note: string;
}
interface TargetData {
  leases: LeaseRecord[];
  owners: OwnerProfile[];
  tenants: TenantProfile[];
  persons: Person[];
  units: Unit[];
  ownership: OwnershipRecord[];
}
function SplitDialog({ transaction, account, busy, error, onClose, onSubmit }: {
  transaction: BankTransactionWithSuggestions;
  account: BankAccount | null;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (lines: BankAssignmentLine[]) => void;
}) {
  function row(key: number, line?: BankMatchLine): SplitRow {
    const [year, month] = transaction.value_date.split("-");
    const payment_type = line?.payment_type ?? "KALTMIETE";
    const targetKind = OWNER_TYPES.has(payment_type) ? "owner" : TENANT_TYPES.has(payment_type) ? "lease" : line?.owner_profile_id ? "owner" : "lease";
    return { key, payment_type, amount: line?.amount ?? "",
      period_year: String(line?.period_year ?? year), period_month: String(line?.period_month ?? Number(month)),
      targetKind, target: (targetKind === "owner" ? line?.owner_profile_id : line?.lease_agreement_id) ?? "", note: "" };
  }
  const [rows, setRows] = useState<SplitRow[]>(() => {
    const lines = transaction.suggestions?.length === 1 ? transaction.suggestions[0].lines : [];
    return lines.length > 0 ? lines.slice(0, 100).map((line, index) => row(index, line)) : [row(0)];
  });
  const [targets, setTargets] = useState<TargetData | null>(null);
  const [targetError, setTargetError] = useState<string | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [targetRevision, setTargetRevision] = useState(0);
  useEffect(() => {
    if (!account) return;
    let cancelled = false;
    Promise.all([listUnits(account.property_id), listLeases(), listOwnerProfiles(), listTenantProfiles(), listPersons(), listOwnership()])
      .then(([units, leases, owners, tenants, persons, ownership]) => {
        if (cancelled) return;
        const scopedUnits = units.filter(unit => unit.property_id === account.property_id);
        const unitIds = new Set(scopedUnits.map(unit => unit.id));
        const scopedOwnership = ownership.filter(item => unitIds.has(item.unit_id));
        const ownerIds = new Set(scopedOwnership.map(item => item.owner_profile_id));
        setTargets({ units: scopedUnits, leases: leases.filter(lease => unitIds.has(lease.unit_id)), owners: owners.filter(owner => ownerIds.has(owner.id)), tenants, persons, ownership: scopedOwnership });
        setTargetError(null);
      }).catch(cause => { if (!cancelled) setTargetError(errorMessage(cause)); });
    return () => { cancelled = true; };
  }, [account, targetRevision]);

  function name(personId?: string) {
    const person = targets?.persons.find(person => person.id === personId);
    return person ? person.company_name || [person.first_name, person.last_name].filter(Boolean).join(" ") : "Name nicht verfügbar";
  }
  function update(key: number, patch: Partial<SplitRow>) {
    setRows(current => current.map(item => item.key === key ? { ...item, ...patch } : item));
    setValidation(null);
  }
  function submit() {
    if (busy || !targets) return;
    const lines: BankAssignmentLine[] = [];
    for (const item of rows) {
      const amount = positiveAmount(item.amount);
      const lease = item.targetKind === "lease" ? targets.leases.find(lease => lease.id === item.target) : undefined;
      const owner = item.targetKind === "owner" ? targets.owners.find(owner => owner.id === item.target) : undefined;
      if (!amount || !/^\d{1,4}$/.test(item.period_year) || !/^\d{1,2}$/.test(item.period_month) || (!lease && !owner)) {
        setValidation("Bitte positive Beträge (max. 12 Vorkommastellen, 2 Nachkommastellen), Periode und ein gültiges Ziel im Bankobjekt angeben.");
        return;
      }
      const line: BankAssignmentLine = {
        payment_type: item.payment_type, amount, period_year: Number(item.period_year), period_month: Number(item.period_month),
        ...(lease ? { lease_agreement_id: lease.id, tenant_profile_id: lease.tenant_profile_id } : { owner_profile_id: owner!.id }),
        ...(item.note.trim() ? { note: item.note.trim() } : {}),
      };
      if (!validLine(line)) { setValidation("Bitte Zahlungsart, Ziel und gültige Periode prüfen."); return; }
      lines.push(line);
    }
    if (!coversTransaction(lines, transaction.amount)) { setValidation("Die Summe der positiven Splitbeträge muss den Umsatz vollständig abdecken."); return; }
    setValidation(null);
    onSubmit(lines);
  }
  const total = cents(transaction.amount);
  const entered = rows.map(item => cents(item.amount));
  const remaining = total !== null && entered.every(amount => amount !== null)
    ? (total < 0n ? -total : total) - entered.reduce<bigint>((sum, amount) => sum + amount!, 0n) : null;

  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto" onEscapeKeyDown={event => { if (busy) event.preventDefault(); }} onPointerDownOutside={event => { if (busy) event.preventDefault(); }}>
      <DialogTitle>Umsatz aufteilen</DialogTitle>
      <DialogDescription>{transaction.counterparty_name ?? "Unbekannte Gegenpartei"} · {money(transaction.amount)} · {transaction.amount.startsWith("-") ? "Ausgang (OUT)" : "Eingang (IN)"}. Splitbeträge immer positiv, z. B. 700,00. Die Buchungsberechtigung wird serverseitig geprüft.</DialogDescription>
      {(transaction.suggestions ?? []).map((suggestion, index) => <div key={index}>
        <SuggestionDetails suggestion={suggestion} />
        {transaction.suggestions.length > 1 && <button type="button" className="btn" disabled={busy} onClick={() => { setRows(suggestion.lines.length ? suggestion.lines.slice(0, 100).map((line, index) => row(index, line)) : [row(0)]); setValidation(null); }}>Vorschlag {index + 1} laden: {suggestion.target_name}</button>}
      </div>)}
      {targetError && <div><p role="alert" className="error-text">{targetError}</p><button className="btn" onClick={() => setTargetRevision(value => value + 1)}>Ziele erneut laden</button></div>}
      {!targets && !targetError && <p>Lade Ziele…</p>}
      {validation && <p role="alert" className="error-text">{validation}</p>}
      {error && <p role="alert" className="error-text">{error}</p>}
      <form onSubmit={event => { event.preventDefault(); submit(); }} noValidate>
        <fieldset disabled={busy || !targets}>
          {rows.map((item, index) => <fieldset key={item.key} className="card">
            <legend>Splitzeile {index + 1}</legend>
            <label>Zahlungsart<select aria-label="Zahlungsart" value={item.payment_type} onChange={event => {
              const payment_type = event.target.value as BankAssignmentLine["payment_type"];
              const targetKind = OWNER_TYPES.has(payment_type) ? "owner" : TENANT_TYPES.has(payment_type) ? "lease" : item.targetKind;
              update(item.key, { payment_type, targetKind, target: targetKind === item.targetKind ? item.target : "" });
            }}>{Object.entries(PAYMENT_TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Betrag (EUR)<input inputMode="decimal" value={item.amount} onChange={event => update(item.key, { amount: event.target.value })} /></label>
            <label>Periode Jahr<input type="number" min="1" max="9999" value={item.period_year} onChange={event => update(item.key, { period_year: event.target.value })} /></label>
            <label>Periode Monat<input type="number" min="1" max="12" value={item.period_month} onChange={event => update(item.key, { period_month: event.target.value })} /></label>
            {item.payment_type === "NACHZAHLUNG" && <label>Zielart<select aria-label="Zielart" value={item.targetKind} onChange={event => update(item.key, { targetKind: event.target.value as SplitRow["targetKind"], target: "" })}><option value="lease">Mietvertrag</option><option value="owner">Eigentümer</option></select></label>}
            {item.targetKind === "lease" ? <label>Mietvertrag<select aria-label="Mietvertrag" value={item.target} onChange={event => update(item.key, { target: event.target.value })}>
              <option value="">Mietvertrag wählen</option>
              {targets?.leases.map(lease => <option key={lease.id} value={lease.id}>{name(targets.tenants.find(tenant => tenant.id === lease.tenant_profile_id)?.person_id)} · Einheit {targets.units.find(unit => unit.id === lease.unit_id)?.number} · ab {lease.valid_from}{lease.valid_to ? ` bis ${lease.valid_to}` : " (laufend)"}</option>)}
            </select></label> : <label>Eigentümer<select aria-label="Eigentümer" value={item.target} onChange={event => update(item.key, { target: event.target.value })}>
              <option value="">Eigentümer wählen</option>
              {targets?.owners.map(owner => <option key={owner.id} value={owner.id}>{name(owner.person_id)} · Einheit {targets.ownership.filter(record => record.owner_profile_id === owner.id).map(record => targets.units.find(unit => unit.id === record.unit_id)?.number).join(", ")}</option>)}
            </select></label>}
            <label>Notiz<input maxLength={500} value={item.note} onChange={event => update(item.key, { note: event.target.value })} /></label>
            <button type="button" className="btn" disabled={rows.length <= 1} onClick={() => { setRows(current => current.filter(other => other.key !== item.key)); setValidation(null); }}>Zeile entfernen</button>
          </fieldset>)}
          <button type="button" className="btn" disabled={rows.length >= 100} onClick={() => setRows(current => [...current, row(Math.max(...current.map(item => item.key)) + 1)])}>Zeile hinzufügen</button>
          <p>Restbetrag: {remaining === null ? "Beträge eingeben" : money(decimal(remaining))}</p>
          <button type="submit" className="btn btn--primary">{busy ? "Bitte warten…" : "Zuordnung buchen"}</button>
        </fieldset>
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Abbrechen</button>
      </form>
    </DialogContent>
  </Dialog>;
}
