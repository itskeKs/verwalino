/**
 * Property detail page -- shows property info and its units.
 * Units can be created/edited inline.
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { getProperty, getPropertyKnowledge, listUnits, createUnit, updateUnit } from "../api/endpoints";
import type { Property, PropertyKnowledgeOverview, Unit, UnitCreate, UnitUpdate, UsageType } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";

const USAGE_TYPES: { value: UsageType; label: string }[] = [
  { value: "WOHNUNG", label: "Wohnung" },
  { value: "GEWERBE", label: "Gewerbe" },
  { value: "STELLPLATZ", label: "Stellplatz" },
  { value: "SONSTIGE", label: "Sonstige" },
];

interface UnitFormData {
  number: string;
  location: string;
  area: string;
  heated_area: string;
  usage_type: UsageType;
  mea: string;
  building_part: string;
}

const EMPTY_UNIT_FORM: UnitFormData = {
  number: "",
  location: "",
  area: "",
  heated_area: "",
  usage_type: "WOHNUNG",
  mea: "",
  building_part: "",
};

function formatLegalMea(mea: string | null, property: Property): string {
  if (!mea) return "-";
  if (property.mea_scale_status !== "CONFIRMED" || !Number.isSafeInteger(property.mea_denominator) || !property.mea_denominator) {
    return `${mea} (Zähler/Skala unbestätigt)`;
  }
  return `${mea} / ${new Intl.NumberFormat("de-DE").format(property.mea_denominator)}`;
}

export function PropertyDetailPage() {
  const { propertyId } = useParams<{ propertyId: string }>();
  const { session } = useAuth();
  const isWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [property, setProperty] = useState<Property | null>(null);
  const [units, setUnits] = useState<Unit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showUnitForm, setShowUnitForm] = useState(false);
  const [editUnitId, setEditUnitId] = useState<string | null>(null);
  const [unitForm, setUnitForm] = useState<UnitFormData>(EMPTY_UNIT_FORM);
  const [saving, setSaving] = useState(false);
  const [knowledge, setKnowledge] = useState<PropertyKnowledgeOverview | null>(null);

  const reload = useCallback(() => {
    if (!propertyId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      getProperty(propertyId),
      listUnits(propertyId),
      getPropertyKnowledge(propertyId).catch(() => null),
    ])
      .then(([p, u, overview]) => {
        setProperty(p);
        setUnits(u);
        setKnowledge(overview);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [propertyId]);

  useEffect(() => { void Promise.resolve().then(reload); }, [reload]);

  function openCreateUnit() {
    setEditUnitId(null);
    setUnitForm(EMPTY_UNIT_FORM);
    setShowUnitForm(true);
  }

  function openEditUnit(u: Unit) {
    setEditUnitId(u.id);
    setUnitForm({
      number: u.number,
      location: u.location,
      area: u.area ?? "",
      heated_area: u.heated_area ?? "",
      usage_type: u.usage_type,
      mea: u.mea ?? "",
      building_part: u.building_part ?? "",
    });
    setShowUnitForm(true);
  }

  async function handleUnitSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!propertyId) return;
    setSaving(true);
    setError(null);
    try {
      if (editUnitId) {
        const body: UnitUpdate = {
          number: unitForm.number,
          location: unitForm.location,
          usage_type: unitForm.usage_type,
        };
        if (unitForm.area) body.area = unitForm.area;
        if (unitForm.heated_area) body.heated_area = unitForm.heated_area;
        if (unitForm.mea) body.mea = unitForm.mea;
        body.building_part = unitForm.building_part;
        await updateUnit(editUnitId, body);
      } else {
        const body: UnitCreate = {
          property_id: propertyId,
          number: unitForm.number,
          location: unitForm.location,
          usage_type: unitForm.usage_type,
        };
        if (unitForm.area) body.area = unitForm.area;
        if (unitForm.heated_area) body.heated_area = unitForm.heated_area;
        if (unitForm.mea) body.mea = unitForm.mea;
        if (unitForm.building_part) body.building_part = unitForm.building_part;
        await createUnit(body);
      }
      setShowUnitForm(false);
      setEditUnitId(null);
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

  if (loading) return <p>Lade...</p>;
  if (error && !property) return <p className="error-text">{error}</p>;
  if (!property) return <p className="error-text">Objekt nicht gefunden.</p>;

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/objekte" className="link">&larr; Alle Objekte</Link>
      </p>
      <h1>{property.name}</h1>

      {error && <p className="error-text">{error}</p>}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Objektdaten</h2>
        <table className="info-table">
          <tbody>
            <tr><td>Verwaltungsart</td><td><span className={`badge badge--${property.management_type.toLowerCase()}`}>{property.management_type}</span></td></tr>
            <tr><td>Strasse</td><td>{property.street || "-"}</td></tr>
            <tr><td>PLZ / Stadt</td><td>{[property.zip_code, property.city].filter(Boolean).join(" ") || "-"}</td></tr>
            <tr><td>GJ-Startmonat</td><td>{property.fiscal_year_start_month}</td></tr>
          </tbody>
        </table>
      </div>

      <section className="card property-knowledge-link" aria-labelledby="property-knowledge-heading" style={{ marginBottom: "1.5rem" }}>
        <h2 id="property-knowledge-heading">Dokumente &amp; Wissen</h2>
        <p>{knowledge?.documents.length ?? 0} Dokumente · {Object.values(knowledge?.rules_by_topic ?? {}).flat().length} Regeln</p>
        <Link className="btn" to={`/objekte/${propertyId}/wissen`}>Dokumente &amp; Wissen öffnen</Link>
      </section>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ margin: 0 }}>Einheiten</h2>
        {isWrite && <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
          <Link className="btn" to={`/objekte/${propertyId}/teilungserklaerung/import`}>
            Nachtrag oder Teilungserklärung einlesen
          </Link>
          <button className="btn btn--primary" onClick={openCreateUnit}>
            + Neue Einheit
          </button>
        </div>}
      </div>

      {isWrite && showUnitForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>{editUnitId ? "Einheit bearbeiten" : "Neue Einheit anlegen"}</h2>
          <form onSubmit={handleUnitSubmit} className="form-grid">
            <label>
              Nummer *
              <input
                type="text"
                required
                value={unitForm.number}
                onChange={(e) => setUnitForm({ ...unitForm, number: e.target.value })}
              />
            </label>
            <label>
              Lage
              <input
                type="text"
                value={unitForm.location}
                onChange={(e) => setUnitForm({ ...unitForm, location: e.target.value })}
              />
            </label>
            <label>
              Nutzungsart *
              <select
                value={unitForm.usage_type}
                onChange={(e) => setUnitForm({ ...unitForm, usage_type: e.target.value as UsageType })}
              >
                {USAGE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </label>
            <label>
              Flaeche (m2)
              <input
                type="text"
                inputMode="decimal"
                value={unitForm.area}
                onChange={(e) => setUnitForm({ ...unitForm, area: e.target.value })}
              />
            </label>
            <label>
              Beheizte Flaeche (m2)
              <input
                type="text"
                inputMode="decimal"
                value={unitForm.heated_area}
                onChange={(e) => setUnitForm({ ...unitForm, heated_area: e.target.value })}
              />
            </label>
            <label>
              MEA (Miteigentumsanteil)
              <input
                type="text"
                inputMode="decimal"
                value={unitForm.mea}
                onChange={(e) => setUnitForm({ ...unitForm, mea: e.target.value })}
              />
            </label>
            <label>
              Gebäudeteil
              <input
                type="text"
                value={unitForm.building_part}
                onChange={(e) => setUnitForm({ ...unitForm, building_part: e.target.value })}
              />
            </label>
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={saving}>
                {saving ? "Speichern..." : "Speichern"}
              </button>
              <button type="button" className="btn" onClick={() => setShowUnitForm(false)}>
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {units.length === 0 ? (
        <p className="empty-hint">Keine Einheiten vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Nr.</th>
                <th>Lage</th>
                <th>Typ</th>
                <th>Flaeche</th>
                <th>Beh. Fl.</th>
                <th>Gebäudeteil</th>
                <th>MEA</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {units.map((u) => (
                <tr key={u.id}>
                  <td>
                    <Link to={`/objekte/${propertyId}/einheiten/${u.id}`} className="link">
                      {u.number}
                    </Link>
                  </td>
                  <td>{u.location || "-"}</td>
                  <td>{u.usage_type}</td>
                  <td>{u.area ?? "-"}</td>
                  <td>{u.heated_area ?? "-"}</td>
                  <td>{u.building_part || "-"}</td>
                  <td>{formatLegalMea(u.mea, property)}</td>
                  <td>{isWrite && <button className="btn btn--small" onClick={() => openEditUnit(u)}>Bearbeiten</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
