/**
 * Property detail page -- shows property info and its units.
 * Units can be created/edited inline.
 */

import { settlementPeriodPattern } from "../lib/settlementPeriod";
import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { getProperty, getPropertyKnowledge, listUnits, createUnit, updateUnit, confirmMeaScale, deleteProperty } from "../api/endpoints";
import type { Property, PropertyKnowledgeOverview, Unit, UnitCreate, UnitUpdate, UsageType } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { AttachmentsPanel } from "../components/AttachmentsPanel";
import { MeterPanel } from "../components/MeterPanel";

import { MasterDataDelete } from "../components/MasterDataDelete";

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
  return <PropertyDetailPageContent key={propertyId} propertyId={propertyId} />;
}

function PropertyDetailPageContent({ propertyId }: { propertyId?: string }) {
  const navigate = useNavigate();

  const { session } = useAuth();
  const isWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const requestVersion = useRef(0);
  const [property, setProperty] = useState<Property | null>(null);
  const [units, setUnits] = useState<Unit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showUnitForm, setShowUnitForm] = useState(false);
  const [editUnitId, setEditUnitId] = useState<string | null>(null);
  const [unitForm, setUnitForm] = useState<UnitFormData>(EMPTY_UNIT_FORM);
  const [unitNumberError, setUnitNumberError] = useState<string | null>(null);
  const [unitAreaError, setUnitAreaError] = useState<{ field: "area" | "heated_area"; message: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [knowledge, setKnowledge] = useState<PropertyKnowledgeOverview | null>(null);
  const [meaDenominator, setMeaDenominator] = useState<string>("");
  const [confirmingMea, setConfirmingMea] = useState(false);

  const reload = useCallback(() => {
    if (!propertyId) return;
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    Promise.all([
      getProperty(propertyId),
      listUnits(propertyId),
      getPropertyKnowledge(propertyId).catch(() => null),
    ])
      .then(([p, u, overview]) => {
        if (version !== requestVersion.current) return;
        setProperty(p);
        setUnits(u);
        setKnowledge(overview);
      })
      .catch((err) => {
        if (version !== requestVersion.current) return;
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) setProperty(null);
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
  }, [propertyId]);

  const invalidateRequests = useCallback(() => { requestVersion.current++; }, []);
  useEffect(() => { reload(); return invalidateRequests; }, [reload, invalidateRequests]);

  async function handleConfirmMeaScale(e: React.FormEvent) {
    e.preventDefault();
    if (!propertyId || !property) return;
    setSuccess(null);
    setConfirmingMea(true);
    setError(null);
    try {
      const denominator = Number(meaDenominator);
      if (!Number.isSafeInteger(denominator) || denominator < 1) {
        setError("Nenner muss eine positive ganze Zahl sein.");
        return;
      }
      await confirmMeaScale(propertyId, denominator);
      setSuccess("MEA-Skala gespeichert.");
      setMeaDenominator("");
      reload();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setConfirmingMea(false);
    }
  }

  function openCreateUnit() {
    setUnitNumberError(null);
    setUnitAreaError(null);
    setEditUnitId(null);
    setUnitForm(EMPTY_UNIT_FORM);
    setShowUnitForm(true);
  }

  function openEditUnit(u: Unit) {
    setUnitNumberError(null);
    setUnitAreaError(null);
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
    setSuccess(null);
    setSaving(true);
    setError(null);
    setUnitNumberError(null);
    setUnitAreaError(null);
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
      setSuccess("Einheit gespeichert.");
      setShowUnitForm(false);
      setEditUnitId(null);
      reload();
    } catch (err) {
      if (err instanceof ApiError && (
        (err.status === 422 && err.code === "unit_number_too_long") ||
        (err.status === 409 && err.code === "duplicate_unit_number")
      )) {
        setUnitNumberError(err.message);
      } else if (err instanceof ApiError && err.status === 422 && (
        err.code === "invalid_unit_area" || err.code === "invalid_unit_heated_area"
      )) {
        setUnitAreaError({ field: err.code === "invalid_unit_area" ? "area" : "heated_area", message: err.message });
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  if (loading && !property) return <p>Lade...</p>;
  if (error && !property) return <p className="error-text">{error}</p>;
  if (!property) return <p className="error-text">Objekt nicht gefunden.</p>;

  const showMea = property.management_type !== "MIET" || units.some(unit => unit.mea !== null);

  return (
    <div style={{ overflowAnchor: "none" }}>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/objekte" className="link">&larr; Alle Objekte</Link>
      </p>
      <h1>{property.name}</h1>
      {isWrite && <MasterDataDelete entityId={property.id} title="Objekt löschen" entityName={property.name} remove={deleteProperty} onDeleted={() => navigate("/objekte", { replace: true })} />}
      {isWrite && <Link className="btn btn-primary" to={`/aufgaben?${new URLSearchParams({ property_id: property.id, create: "1" })}`}>Aufgabe anlegen</Link>}

      {error && <p className="error-text">{error}</p>}
      {success && <p role="status" className="sticky top-0 z-10 bg-background py-2">{success}</p>}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Objektdaten</h2>
        <table className="info-table">
          <tbody>
            <tr><td>Verwaltungsart</td><td><span className={`badge badge--${property.management_type.toLowerCase()}`}>{property.management_type}</span></td></tr>
            <tr><td>Strasse</td><td>{property.street || "-"}</td></tr>
            <tr><td>PLZ / Stadt</td><td>{[property.zip_code, property.city].filter(Boolean).join(" ") || "-"}</td></tr>
            <tr><td>Baujahr</td><td>{property.construction_year ?? "—"}</td></tr>
            <tr><td>Gesamtfläche (m²)</td><td>{property.total_area ?? "—"}</td></tr>
            <tr><td>Beginn des Abrechnungszeitraums</td><td>{settlementPeriodPattern(property.fiscal_year_start_month)}</td></tr>
          </tbody>
        </table>
      </div>

      {isWrite && showMea && property.mea_scale_status === "LEGACY_UNCONFIRMED" && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>MEA-Skala bestätigen</h2>
          <p>
            Bisheriger Status: <span className="badge badge--warning">Skala noch nicht bestätigt</span>.
            Bitte geben Sie den Nenner der MEA-Skala ein (z.B. 1000 für 1000 Gesamteinheiten).
          </p>
          <form onSubmit={handleConfirmMeaScale} className="form-grid" style={{ gap: "0.5rem" }}>
            <label>
              Nenner *
              <input
                type="number"
                required
                min="1"
                value={meaDenominator}
                onChange={(e) => setMeaDenominator(e.target.value)}
                placeholder="z.B. 1000"
              />
            </label>
            <button 
              type="submit" 
              className="btn btn--primary"
              disabled={confirmingMea}
            >
              {confirmingMea ? "Bestätige..." : "MEA-Skala bestätigen"}
            </button>
          </form>
        </div>
      )}

      <section className="card property-knowledge-link" aria-labelledby="property-knowledge-heading" style={{ marginBottom: "1.5rem" }}>
        <h2 id="property-knowledge-heading">Dokumente &amp; Wissen</h2>
        <p>{knowledge?.documents.length ?? 0} Dokumente · {Object.values(knowledge?.rules_by_topic ?? {}).flat().length} Regeln</p>
        <Link className="btn" to={`/objekte/${propertyId}/wissen`}>Dokumente &amp; Wissen öffnen</Link>
      </section>

      <div className="[&_input]:min-w-0 [&_label]:min-w-0 max-[479px]:[&_button[type=submit]]:w-full">
        <AttachmentsPanel propertyId={property.id} canWrite={isWrite} />
      </div>

      <MeterPanel key={property.id} propertyId={property.id} canWrite={isWrite} />

      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <h2 style={{ margin: 0 }}>Einheiten</h2>
        {isWrite && <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
          <Link className="btn btn--wrap max-w-full" to={`/objekte/${propertyId}/teilungserklaerung/import`}>
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
            <div>
              <label>
                Nummer *
                <input
                  type="text"
                  required
                  aria-invalid={unitNumberError !== null}
                  aria-describedby={unitNumberError ? "unit-number-error" : undefined}
                  value={unitForm.number}
                  onChange={(e) => {
                    setUnitNumberError(null);
                    setUnitForm({ ...unitForm, number: e.target.value });
                  }}
                />
              </label>
              {unitNumberError && <p id="unit-number-error" role="alert" className="error-text mt-1">{unitNumberError}</p>}
            </div>
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
            {([["area", "Flaeche (m2)"], ["heated_area", "Beheizte Flaeche (m2)"]] as const).map(([field, label]) => {
              const message = unitAreaError?.field === field ? unitAreaError.message : null;
              const errorId = `unit-${field}-error`;
              return (
                <div key={field}>
                  <label>
                    {label}
                    <input
                      type="text"
                      inputMode="decimal"
                      aria-invalid={message !== null}
                      aria-describedby={message ? errorId : undefined}
                      value={unitForm[field]}
                      onChange={(e) => {
                        if (message) setUnitAreaError(null);
                        setUnitForm({ ...unitForm, [field]: e.target.value });
                      }}
                    />
                  </label>
                  {message && <p id={errorId} role="alert" className="error-text mt-1">{message}</p>}
                </div>
              );
            })}
            {showMea && <label>
              MEA (Miteigentumsanteil)
              <input
                type="text"
                inputMode="decimal"
                value={unitForm.mea}
                onChange={(e) => setUnitForm({ ...unitForm, mea: e.target.value })}
              />
            </label>}
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
        <div className="card overflow-x-auto">
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
