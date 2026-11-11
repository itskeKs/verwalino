/**
 * "Hausgeld buchen" on a finalized economic plan: one row per month of one owner, built by the
 * server from the plan lines and the ownership periods. The user picks the bank account once,
 * ticks the months that were paid, adjusts an amount or value date where it differs and confirms
 * once; the server creates and books all payments or none.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { bookHausgeld, getHausgeldBooking, listBankAccounts } from "../api/endpoints";
import type { BankAccount, EconomicPlan, HausgeldBookingRow, Payment } from "../api/types";
import { normalizePlanAmount } from "../api/previousPlan";
import { ApiError } from "../api/client";
import { centsFromDecimal, formatIban, formatMoney } from "../lib/format";

const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

interface Entry {
  amount: string;
  value_date: string;
  selected: boolean;
}

interface Result {
  count: number;
  total: bigint;
  bankAccountId: string;
}

function monthKey(row: Pick<HausgeldBookingRow, "period_year" | "period_month">): string {
  return `${row.period_year}-${row.period_month}`;
}

function cents(amount: string): bigint | null {
  const normalized = normalizePlanAmount(amount);
  return normalized === null ? null : centsFromDecimal(normalized);
}

function countLabel(count: number): string {
  return count === 1 ? "1 Zahlung" : `${count} Zahlungen`;
}

function message(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

export function HausgeldBooking({ plan, ownerLabel }: { plan: EconomicPlan; ownerLabel: (ownerProfileId: string) => string }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<HausgeldBookingRow[] | null>(null);
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [accountId, setAccountId] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  const load = useCallback(async () => {
    try {
      const [offer, bank] = await Promise.all([getHausgeldBooking(plan.id), listBankAccounts(plan.property_id)]);
      setRows(offer);
      setAccounts(bank.filter(account => account.is_active !== false));
      setLoadError(null);
    } catch (err) {
      setLoadError(message(err));
    }
  }, [plan.id, plan.property_id]);

  useEffect(() => {
    if (open && rows === null && loadError === null) void load();
  }, [open, rows, loadError, load]);

  const owners = useMemo(() => {
    const open = new Map<string, number>();
    for (const row of rows ?? []) open.set(row.owner_profile_id, (open.get(row.owner_profile_id) ?? 0) + (row.booked ? 0 : 1));
    return [...open.entries()];
  }, [rows]);
  const ownerRows = useMemo(() => (rows ?? []).filter(row => row.owner_profile_id === ownerId), [rows, ownerId]);

  // Start every open month of the chosen owner ticked, with the plan's amount and due day.
  useEffect(() => {
    setEntries(Object.fromEntries(ownerRows.map(row => [monthKey(row), {
      amount: row.amount.replace(".", ","), value_date: row.value_date, selected: !row.booked,
    }])));
  }, [ownerRows]);

  // Preselect the only owner.
  useEffect(() => {
    if (rows && ownerId === "" && owners.length === 1) setOwnerId(owners[0][0]);
  }, [rows, ownerId, owners]);

  function update(key: string, change: Partial<Entry>) {
    setEntries(current => ({ ...current, [key]: { ...current[key], ...change } }));
  }

  const chosen = ownerRows.filter(row => !row.booked && entries[monthKey(row)]?.selected);
  const invalidKeys = new Set(chosen.filter(row => {
    const entry = entries[monthKey(row)];
    const amount = cents(entry.amount);
    return amount === null || amount <= 0n || !entry.value_date;
  }).map(monthKey));
  const total = chosen.reduce((sum, row) => sum + (cents(entries[monthKey(row)].amount) ?? 0n), 0n);
  const canBook = !busy && accountId !== "" && chosen.length > 0 && invalidKeys.size === 0;

  async function handleBook() {
    if (!canBook) return;
    const confirmed = window.confirm(
      `${countLabel(chosen.length)} über zusammen ${formatMoney(total)} für ${ownerLabel(ownerId)} buchen? ` +
        "Nach der Buchung sind die Zahlungen unveränderbar (GoBD). Korrekturen sind nur als Stornobuchung möglich.",
    );
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const booked: Payment[] = await bookHausgeld(plan.id, {
        bank_account_id: accountId,
        rows: chosen.map(row => ({
          owner_profile_id: row.owner_profile_id, period_year: row.period_year, period_month: row.period_month,
          amount: normalizePlanAmount(entries[monthKey(row)].amount) as string, value_date: entries[monthKey(row)].value_date,
        })),
      });
      setResult({ count: booked.length, total: booked.reduce((sum, payment) => sum + centsFromDecimal(payment.amount), 0n), bankAccountId: accountId });
      await load();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: "1.5rem" }}>
      <button type="button" className="btn btn--primary" aria-expanded={open} aria-controls="hausgeld-booking" onClick={() => setOpen(value => !value)}>
        {open ? "Hausgeld-Buchung schließen" : "Hausgeld buchen"}
      </button>
      {open && (
        <div id="hausgeld-booking" style={{ marginTop: "1rem" }}>
          <h2>Hausgeld buchen</h2>
          <p style={{ marginBottom: "1rem" }}>
            Je Eigentümer und Monat des Plans {plan.plan_year}. Nach der Buchung sind die Zahlungen unveränderbar (GoBD); Korrekturen sind nur als Stornobuchung möglich.
          </p>
          {loadError && <p role="alert" className="error-text">{loadError}</p>}
          {rows && rows.length === 0 && <p>Der Plan enthält kein Hausgeld, das gebucht werden kann.</p>}
          {rows && rows.length > 0 && (
            <>
              <div className="form-grid">
                <label>
                  Bankkonto
                  <select aria-label="Bankkonto" value={accountId} onChange={e => setAccountId(e.target.value)}>
                    <option value="">-- Bankkonto wählen --</option>
                    {accounts.map(account => (
                      <option key={account.id} value={account.id}>{account.label}{account.iban ? ` (${formatIban(account.iban)})` : ""}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Eigentümer
                  <select aria-label="Eigentümer" value={ownerId} onChange={e => { setOwnerId(e.target.value); setResult(null); setError(null); }}>
                    <option value="">-- Eigentümer wählen --</option>
                    {owners.map(([id, openMonths]) => (
                      <option key={id} value={id}>{ownerLabel(id)} – {openMonths > 0 ? `${openMonths} offen` : "alle gebucht"}</option>
                    ))}
                  </select>
                </label>
              </div>
              {ownerRows.length > 0 && (
                <div className="overflow-x-auto" style={{ marginTop: "1rem" }}>
                  <table className="data-table">
                    <thead>
                      <tr><th><span className="sr-only">Auswahl</span></th><th>Monat</th><th>Betrag (EUR) und Wertstellung</th></tr>
                    </thead>
                    <tbody>
                      {ownerRows.map(row => {
                        const key = monthKey(row);
                        const entry = entries[key];
                        const label = `${MONTHS[row.period_month - 1]} ${row.period_year}`;
                        if (!entry) return null;
                        return (
                          <tr key={key}>
                            <td>
                              <input type="checkbox" aria-label={`${label} auswählen`} checked={entry.selected} disabled={row.booked}
                                onChange={e => update(key, { selected: e.target.checked })} />
                            </td>
                            <td>
                              <div>{label}</div>
                              {row.booked && <span className="badge badge--confirmed">gebucht</span>}
                            </td>
                            <td>
                              {!row.booked && (
                                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                                  <input type="text" inputMode="decimal" aria-label={`Betrag ${label}`} value={entry.amount}
                                    aria-invalid={invalidKeys.has(key) ? true : undefined} style={{ width: "6.5rem", fontSize: "0.9rem" }}
                                    onChange={e => update(key, { amount: e.target.value })} />
                                  <input type="date" aria-label={`Wertstellung ${label}`} value={entry.value_date}
                                    aria-invalid={invalidKeys.has(key) ? true : undefined} style={{ width: "9.25rem", fontSize: "0.9rem" }}
                                    onChange={e => update(key, { value_date: e.target.value })} />
                                </div>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {invalidKeys.size > 0 && (
                <p className="error-text">Bitte geben Sie für jeden ausgewählten Monat einen Betrag über 0,00 € und ein Wertstellungsdatum an.</p>
              )}
              {error && <p role="alert" className="error-text">{error}</p>}
              {result && (
                <p role="status">
                  {countLabel(result.count)} über {formatMoney(result.total)} gebucht.{" "}
                  <Link className="link" to={`/zahlungen?bank_account_id=${result.bankAccountId}&payment_type=HAUSGELD_VORSCHUSS`}>Zahlungen ansehen</Link>
                </p>
              )}
              <div className="form-actions" style={{ marginTop: "1rem" }}>
                <button type="button" className="btn btn--primary" disabled={!canBook} onClick={handleBook}>
                  {busy ? "Buchen..." : "Ausgewählte Monate buchen"}
                </button>
                {chosen.length > 0 && <span style={{ marginLeft: "0.75rem" }}>{countLabel(chosen.length)} · {formatMoney(total)}</span>}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
