/**
 * Persons list page -- shows all Personen with inline creation/editing.
 */

import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { listPersons, createPerson, updatePerson } from "../api/endpoints";
import type { Person, PersonCreate } from "../api/types";
import { ApiError } from "../api/client";

interface PersonFormData {
  first_name: string;
  last_name: string;
  company_name: string;
  email: string;
  phone: string;
  street: string;
  zip_code: string;
  city: string;
  iban: string;
  bic: string;
  bank_name: string;
}

const EMPTY_FORM: PersonFormData = {
  first_name: "",
  last_name: "",
  company_name: "",
  email: "",
  phone: "",
  street: "",
  zip_code: "",
  city: "",
  iban: "",
  bic: "",
  bank_name: "",
};

export function PersonsPage() {
  const [persons, setPersons] = useState<Person[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<PersonFormData>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    listPersons()
      .then(setPersons)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  function openCreate() {
    setEditId(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  }

  function openEdit(p: Person) {
    setEditId(p.id);
    setForm({
      first_name: p.first_name,
      last_name: p.last_name,
      company_name: p.company_name ?? "",
      email: p.email ?? "",
      phone: p.phone ?? "",
      street: p.street,
      zip_code: p.zip_code,
      city: p.city,
      iban: p.iban ?? "",
      bic: p.bic ?? "",
      bank_name: p.bank_name ?? "",
    });
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body: PersonCreate = {
        first_name: form.first_name,
        last_name: form.last_name,
        company_name: form.company_name || undefined,
        email: form.email || undefined,
        phone: form.phone || undefined,
        street: form.street,
        zip_code: form.zip_code,
        city: form.city,
        iban: form.iban || undefined,
        bic: form.bic || undefined,
        bank_name: form.bank_name || undefined,
      };
      if (editId) {
        await updatePerson(editId, body);
      } else {
        await createPerson(body);
      }
      setShowForm(false);
      setEditId(null);
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h1 style={{ margin: 0 }}>Personen</h1>
        <button className="btn btn--primary" onClick={openCreate}>
          + Neue Person
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>{editId ? "Person bearbeiten" : "Neue Person anlegen"}</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              Vorname
              <input
                type="text"
                value={form.first_name}
                onChange={(e) => setForm({ ...form, first_name: e.target.value })}
              />
            </label>
            <label>
              Nachname *
              <input
                type="text"
                required
                value={form.last_name}
                onChange={(e) => setForm({ ...form, last_name: e.target.value })}
              />
            </label>
            <label>
              Firma
              <input
                type="text"
                value={form.company_name}
                onChange={(e) => setForm({ ...form, company_name: e.target.value })}
              />
            </label>
            <label>
              E-Mail
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </label>
            <label>
              Telefon
              <input
                type="tel"
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </label>
            <label>
              Strasse
              <input
                type="text"
                value={form.street}
                onChange={(e) => setForm({ ...form, street: e.target.value })}
              />
            </label>
            <label>
              PLZ
              <input
                type="text"
                value={form.zip_code}
                onChange={(e) => setForm({ ...form, zip_code: e.target.value })}
              />
            </label>
            <label>
              Stadt
              <input
                type="text"
                value={form.city}
                onChange={(e) => setForm({ ...form, city: e.target.value })}
              />
            </label>

            <div className="form-full-width" style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem", marginTop: "0.25rem" }}>
              <strong style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>Bankdaten (optional)</strong>
            </div>
            <label>
              IBAN
              <input
                type="text"
                value={form.iban}
                onChange={(e) => setForm({ ...form, iban: e.target.value })}
              />
            </label>
            <label>
              BIC
              <input
                type="text"
                value={form.bic}
                onChange={(e) => setForm({ ...form, bic: e.target.value })}
              />
            </label>
            <label>
              Bankname
              <input
                type="text"
                value={form.bank_name}
                onChange={(e) => setForm({ ...form, bank_name: e.target.value })}
              />
            </label>

            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? "Speichern..." : "Speichern"}
              </button>
              <button type="button" className="btn" onClick={() => setShowForm(false)}>
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <p>Lade...</p>
      ) : persons.length === 0 ? (
        <p className="empty-hint">Keine Personen vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Firma</th>
                <th>E-Mail</th>
                <th>Telefon</th>
                <th>Ort</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {persons.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link to={`/personen/${p.id}`} className="link">
                      {p.first_name} {p.last_name}
                    </Link>
                  </td>
                  <td>{p.company_name ?? "-"}</td>
                  <td>{p.email ?? "-"}</td>
                  <td>{p.phone ?? "-"}</td>
                  <td>{[p.zip_code, p.city].filter(Boolean).join(" ") || "-"}</td>
                  <td>
                    <button className="btn btn--small" onClick={() => openEdit(p)}>
                      Bearbeiten
                    </button>
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
