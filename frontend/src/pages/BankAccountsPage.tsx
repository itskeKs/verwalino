/**
 * Bank accounts list page -- manage bank accounts per property.
 *
 * M6 prerequisite: payments require a bank_account (Vermoegenstrennungs-Anker).
 * Account types: LAUFEND (laufendes Konto), RUECKLAGE, KAUTION.
 */

import { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import {
  listBankAccounts,
  listProperties,
  createBankAccount,
} from "../api/endpoints";
import type { BankAccount, BankAccountCreate, BankAccountType, Property } from "../api/types";
import { ApiError } from "../api/client";

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
  const [searchParams, setSearchParams] = useSearchParams();
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<BankAccountCreate>(makeEmptyForm());
  const [saving, setSaving] = useState(false);

  const propertyFilter = searchParams.get("property_id") ?? "";

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      listBankAccounts(propertyFilter || undefined),
      listProperties(),
    ])
      .then(([a, p]) => {
        setAccounts(a);
        setProperties(p);
      })
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

  function getPropertyName(id: string): string {
    const prop = properties.find((p) => p.id === id);
    return prop ? prop.name : id.slice(0, 8);
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
          <h2>Neues Bankkonto anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              Objekt *
              <select
                required
                value={form.property_id}
                onChange={(e) =>
                  setForm({ ...form, property_id: e.target.value })
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
              Kontotyp *
              <select
                required
                value={form.account_type}
                onChange={(e) =>
                  setForm({ ...form, account_type: e.target.value as BankAccountType })
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
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Bezeichnung</th>
                <th>Objekt</th>
                <th>Kontotyp</th>
                <th>IBAN</th>
                <th>Bank</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id}>
                  <td><strong>{a.label}</strong></td>
                  <td>{getPropertyName(a.property_id)}</td>
                  <td>
                    <span className={`badge ${ACCOUNT_TYPE_CSS[a.account_type] ?? ""}`}>
                      {ACCOUNT_TYPE_LABELS[a.account_type] ?? a.account_type}
                    </span>
                  </td>
                  <td style={{ fontFamily: "var(--mono)", fontSize: "0.85rem" }}>
                    {a.iban ?? "-"}
                  </td>
                  <td>{a.bank_name ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
