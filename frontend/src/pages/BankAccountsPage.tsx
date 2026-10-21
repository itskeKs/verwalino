/**
 * Bank accounts list page -- manage bank accounts per property.
 *
 * M6 prerequisite: payments require a bank_account (Vermoegenstrennungs-Anker).
 * Account types: LAUFEND (laufendes Konto), RUECKLAGE, KAUTION.
 */

import { useEffect, useState, useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import {
  listBankAccounts,
  createBankAccount,
} from "../api/endpoints";
import type { BankAccount, BankAccountCreate, BankAccountType } from "../api/types";
import { PropertySelector } from "../components/PropertySelector";
import { ApiError } from "../api/client";
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

  const propertyFilter = searchParams.get("property_id") ?? "";

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    listBankAccounts(propertyFilter || undefined)
      .then(setAccounts)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [propertyFilter]);

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

  function openCreate() {
    setForm(makeEmptyForm());
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createBankAccount(form);
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
        <button className="btn btn--primary" onClick={openCreate}>
          + Neues Bankkonto
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
        <PropertySelector label="Nach Objekt filtern" value={propertyFilter} onChange={value => setFilter("property_id", value)} />
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
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
                  <td><strong>{a.label}</strong></td>
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
                    {canImport && <Link className="btn" to={`/bankkonten/${a.id}/umsaetze?import=1`}>Kontoauszug importieren</Link>}
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
