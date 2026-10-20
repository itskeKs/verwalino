import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getBankAccount, importBankTransactions, listBankTransactions } from "../api/endpoints";
import type { BankAccount, BankImportResult, BankTransaction, BankTransactionStatus } from "../api/types";
import { useAuth } from "../auth/useAuth";

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
  const [items, setItems] = useState<BankTransaction[]>([]);
  const [status, setStatus] = useState<BankTransactionStatus | "">("");
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
    listBankTransactions(id, status || undefined, offset).then(result => {
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
          <thead><tr><th>Buchung</th><th>Wertstellung</th><th>Gegenpartei</th><th>IBAN</th><th>Verwendungszweck</th><th>Betrag</th><th>Status</th></tr></thead>
          <tbody>{items.map(item => <tr key={item.id}>
            <td>{date(item.booking_date)}</td><td>{date(item.value_date)}</td><td>{item.counterparty_name ?? "–"}</td><td>{item.counterparty_iban ?? "–"}</td><td>{item.purpose ?? "–"}</td><td>{money(item.amount)}</td><td>{STATUS_LABELS[item.status]}</td>
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
