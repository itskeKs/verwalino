/**
 * Meetings list page -- Eigentuemerversammlungen with property/status filter
 * and creation form.
 *
 * M8 Versammlung: meeting CRUD, status planned/held.
 * Supports ordentlich/ausserordentlich meeting types.
 */

import { useEffect, useState, useCallback } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import {
  listMeetings,
  createMeeting,
  listProperties,
} from "../api/endpoints";
import type {
  Meeting,
  MeetingCreate,
  Property,
} from "../api/types";
import { ApiError } from "../api/client";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Alle Status" },
  { value: "planned", label: "Geplant" },
  { value: "held", label: "Durchgefuehrt" },
];

const STATUS_LABELS: Record<string, string> = {
  planned: "Geplant",
  held: "Durchgefuehrt",
};

const STATUS_CSS: Record<string, string> = {
  planned: "badge--draft",
  held: "badge--confirmed",
};

const MEETING_TYPE_LABELS: Record<string, string> = {
  ordentlich: "Ordentlich",
  ausserordentlich: "Ausserordentlich",
};

function makeEmptyForm(): MeetingCreate {
  return {
    property_id: "",
    meeting_date: "",
    meeting_type: "ordentlich",
    location: "",
    invitation_text: null,
  };
}

export function MeetingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<MeetingCreate>(makeEmptyForm());
  const [saving, setSaving] = useState(false);

  const propertyFilter = searchParams.get("property_id") ?? "";
  const statusFilter = searchParams.get("status") ?? "";

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    const pId = propertyFilter || undefined;

    Promise.all([listMeetings(pId), listProperties()])
      .then(([m, p]) => {
        let filtered = m;
        if (statusFilter) {
          filtered = m.filter((mt) => mt.status === statusFilter);
        }
        setMeetings(filtered);
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
  }, [propertyFilter, statusFilter]);

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

  /** Resolve property name from ID */
  function getPropertyName(propertyId: string): string {
    const prop = properties.find((p) => p.id === propertyId);
    return prop ? prop.name : "-";
  }

  /** Only show WEG/MISCH properties for meeting creation */
  const wegProperties = properties.filter(
    (p) => p.management_type === "WEG" || p.management_type === "MISCH",
  );

  function openCreate() {
    setForm(makeEmptyForm());
    setShowForm(true);
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await createMeeting(form);
      setShowForm(false);
      navigate(`/versammlungen/${created.id}`);
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
        <h1 style={{ margin: 0 }}>Eigentuemerversammlungen</h1>
        <button className="btn btn--primary" onClick={openCreate}>
          + Neue Versammlung
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
        <select
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
      </div>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neue Versammlung anlegen</h2>
          <form onSubmit={handleSubmit} className="form-grid">
            <label>
              WEG-Objekt *
              <select
                required
                value={form.property_id}
                onChange={(e) =>
                  setForm({ ...form, property_id: e.target.value })
                }
              >
                <option value="">-- Objekt waehlen --</option>
                {wegProperties.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Datum *
              <input
                type="date"
                required
                value={form.meeting_date}
                onChange={(e) =>
                  setForm({ ...form, meeting_date: e.target.value })
                }
              />
            </label>
            <label>
              Typ *
              <select
                required
                value={form.meeting_type}
                onChange={(e) =>
                  setForm({ ...form, meeting_type: e.target.value })
                }
              >
                <option value="ordentlich">Ordentlich</option>
                <option value="ausserordentlich">Ausserordentlich</option>
              </select>
            </label>
            <label>
              Ort
              <input
                type="text"
                value={form.location ?? ""}
                onChange={(e) =>
                  setForm({ ...form, location: e.target.value })
                }
                placeholder="z.B. Gemeinschaftsraum"
              />
            </label>
            <label className="form-full-width">
              Einladungstext
              <textarea
                rows={3}
                value={form.invitation_text ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    invitation_text: e.target.value || null,
                  })
                }
                placeholder="Optionaler Einladungstext"
              />
            </label>
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={saving}
              >
                {saving ? "Erstellen..." : "Versammlung anlegen"}
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
      ) : meetings.length === 0 ? (
        <p className="empty-hint">Keine Versammlungen vorhanden.</p>
      ) : (
        <div className="card">
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Datum</th>
                  <th>Objekt</th>
                  <th>Typ</th>
                  <th>Ort</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {meetings.map((m) => (
                  <tr key={m.id}>
                    <td>{m.meeting_date}</td>
                    <td>{getPropertyName(m.property_id)}</td>
                    <td>
                      {MEETING_TYPE_LABELS[m.meeting_type] ?? m.meeting_type}
                    </td>
                    <td>{m.location || "-"}</td>
                    <td>
                      <span className={`badge ${STATUS_CSS[m.status] ?? ""}`}>
                        {STATUS_LABELS[m.status] ?? m.status}
                      </span>
                    </td>
                    <td>
                      <Link
                        to={`/versammlungen/${m.id}`}
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
        </div>
      )}
    </div>
  );
}
