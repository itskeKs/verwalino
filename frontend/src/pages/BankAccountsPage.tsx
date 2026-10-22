/**
 * Bank accounts list page -- manage bank accounts per property.
 *
 * M6 prerequisite: payments require a bank_account (Vermoegenstrennungs-Anker).
 * Account types: LAUFEND (laufendes Konto), RUECKLAGE, KAUTION.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import {
  listBankAccounts,
  createBankAccount,
  updateBankAccount,
} from "../api/endpoints";
import type { BankAccount, BankAccountCreate, BankAccountType } from "../api/types";
import { PropertySelector } from "../components/PropertySelector";
import { ApiError } from "../api/client";
import { MasterDataEditor } from "../components/MasterDataEditor";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";

const ACCOUNT_TYPE_LABELS: Record<string, string> = {
  LAUFEND: "Laufendes Konto",
  RUECKLAGE: "Ruecklage",
  KAUTION: "Kaution",
};

const ACCOUNT_TYPE_CSS: Record<string, string> = {
  LAUFEND: "badge--confirmed",
  RUECKLAGE: "badge--misch",
  KAUTION: "badge--review",
};

const ACCOUNT_TYPE_OPTIONS: { value: BankAccountType; label: string }[] = [
  { value: "LAUFEND", label: "Laufendes Konto" },
  { value: "RUECKLAGE", label: "Ruecklage" },
  { value: "KAUTION", label: "Kaution" },
];

function makeEmptyForm(): BankAccountCreate {
  return {
    property_id: "",
    account_type: "LAUFEND",
    label: "",
    iban: null,
    bic: null,
    bank_name: null,
  };
}

export function BankAccountsPage() {
  const { session } = useAuth();
  const canImport = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [searchParams, setSearchParams] = useSearchParams();
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<BankAccountCreate>(makeEmptyForm());
  const [saving, setSaving] = useState(false);

  const requestVersion = useRef(0);
  const busyRef = useRef(false);

  const propertyFilter = searchParams.get("property_id") ?? "";

  const reload = useCallback(() => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    listBankAccounts(propertyFilter || undefined)
      .then(items => { if (version === requestVersion.current) setAccounts(items); })
      .catch((err) => {
        if (version !== requestVersion.current) return;
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
  }, [propertyFilter]);

  const invalidateRequests = useCallback(() => { requestVersion.current++; }, []);
  useEffect(() => {
    reload();
    return invalidateRequests;
  }, [reload, invalidateRequests]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  }

  function openCreate() {
    if (busyRef.current) return;
    setError(null);
    setForm(makeEmptyForm());
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canImport || busyRef.current) return;
    busyRef.current = true;
    const version = requestVersion.current;
    setSaving(true);
    setError(null);
    try {
      await createBankAccount(form);
      if (version !== requestVersion.current) return;
      setShowForm(false);
      reload();
    } catch (err) {
      if (version !== requestVersion.current) return;
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      busyRef.current = false;
      setSaving(false);
    }
  }

  return (
    <div className="paged-list">
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
        <h1 style={{ margin: 0 }}>Bankkonten</h1>
        {canImport && <button className="btn btn--primary" onClick={openCreate}>
          + Neues Bankkonto
        </button>}
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
        <PropertySelector label="Nach Objekt filtern" value={propertyFilter} onChange={value => setFilter("property_id", value)} />
      </div>

      {error && <p role="alert" className="error-text">{error} <button className="btn" onClick={reload}>Neu laden</button></p>}

      {canImport && showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neues Bankkonto anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <PropertySelector label="Objekt *" required value={form.property_id} emptyLabel="Objekt wählen" onChange={value => setForm({ ...form, accounting_scope_id: null, property_id: value })} />
            <label>
              Kontotyp *
              <select
                aria-label="Kontotyp *"
                required
                value={form.account_type}
                onChange={(e) =>
                  setForm({ ...form, accounting_scope_id: null, account_type: e.target.value as BankAccountType })
                }
              >
                {ACCOUNT_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Bezeichnung *
              <input
                type="text"
                required
                value={form.label}
                onChange={(e) =>
                  setForm({ ...form, label: e.target.value })
                }
                placeholder="z.B. Girokonto Mietobjekt"
              />
            </label>
            <label>
              IBAN
              <input
                type="text"
                value={form.iban ?? ""}
                onChange={(e) =>
                  setForm({ ...form, iban: e.target.value || null })
                }
                placeholder="DE89370400440532013000"
              />
            </label>
            <label>
              BIC
              <input
                type="text"
                value={form.bic ?? ""}
                onChange={(e) =>
                  setForm({ ...form, bic: e.target.value || null })
                }
                placeholder="COBADEFFXXX"
              />
            </label>
            <label>
              Bank
              <input
                type="text"
                value={form.bank_name ?? ""}
                onChange={(e) =>
                  setForm({ ...form, bank_name: e.target.value || null })
                }
                placeholder="Commerzbank"
              />
            </label>
            <AccountingScopeSelector key={`${form.property_id}:${form.account_type}`} kind={form.account_type === "KAUTION" ? "MIET" : form.account_type === "RUECKLAGE" ? "WEG" : undefined} propertyId={form.property_id} value={form.accounting_scope_id} onChange={id => setForm({ ...form, accounting_scope_id: id })} />
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
                disabled={saving}
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
      ) : accounts.length === 0 ? (
        <p className="empty-hint">Keine Bankkonten vorhanden.</p>
      ) : (
        <div className="card paged-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>Bezeichnung</th>
                <th>Objekt</th>
                <th>Kontotyp</th>
                <th>IBAN</th>
                <th>Bank</th>
                <th>Aktionen</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id}>
                  <td><strong>{a.label}</strong>{a.is_active === false && <span className="badge">Beendet</span>}</td>
                  <td>{a.property_name ?? "Objekt nicht verfügbar"}</td>
                  <td>
                    <span className={`badge ${ACCOUNT_TYPE_CSS[a.account_type] ?? ""}`}>
                      {ACCOUNT_TYPE_LABELS[a.account_type] ?? a.account_type}
                    </span>
                  </td>
                  <td style={{ fontFamily: "var(--mono)", fontSize: "0.85rem" }}>
                    {a.iban ?? "-"}
                  </td>
                  <td>{a.bank_name ?? "-"}</td>
                  <td>
                    <Link className="btn" to={`/bankkonten/${a.id}/umsaetze`}>Umsätze</Link>
                    {canImport && <MasterDataEditor key={`${propertyFilter}:${a.id}`} title="Bankkonto bearbeiten"
                      initial={{ label: a.label, iban: a.iban, bic: a.bic, bank_name: a.bank_name }}
                      fields={[{ key: "label", label: "Bezeichnung", maxLength: 255 }, { key: "iban", label: "IBAN", maxLength: 34, nullable: true }, { key: "bic", label: "BIC", maxLength: 11, nullable: true }, { key: "bank_name", label: "Bank", maxLength: 255, nullable: true }]}
                      save={changes => updateBankAccount(a.id, changes)} onSaved={reload}
                      active={a.is_active !== false} endLabel="Bankkonto beenden" setActive={is_active => updateBankAccount(a.id, { is_active })}
                      description="Bezeichnung und Bankdaten bearbeiten. Bereits verwendete IBAN/BIC können nicht geändert werden; eine Umbenennung ändert keine SEPA-Daten." />}

                    {canImport && a.is_active !== false && <Link className="btn" to={`/bankkonten/${a.id}/umsaetze?import=1`}>Kontoauszug importieren</Link>}
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
