/**
 * Unit detail page -- shows ownership history and lease agreements.
 * Overlap errors from DB EXCLUDE constraints are displayed as user-friendly messages.
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getUnit,
  getProperty,
  listOwnership,
  listLeases,
  listOwnerProfiles,
  listTenantProfiles,
  listPersons,
  createOwnership,
  createLease,
} from "../api/endpoints";
import type {
  Unit,
  Property,
  OwnershipRecord,
  LeaseRecord,
  OwnerProfile,
  TenantProfile,
  Person,
  OwnershipCreate,
  LeaseCreate,
} from "../api/types";
import { ApiError } from "../api/client";

function formatLegalMea(mea: string | null, property: Property): string {
  if (!mea) return "-";
  if (property.mea_scale_status !== "CONFIRMED" || !Number.isSafeInteger(property.mea_denominator) || !property.mea_denominator) {
    return `${mea} (Zähler/Skala unbestätigt)`;
  }
  return `${mea} / ${new Intl.NumberFormat("de-DE").format(property.mea_denominator)}`;
}

interface OwnershipFormData {
  owner_profile_id: string;
  valid_from: string;
  valid_to: string;
  mea_share: string;
}

interface LeaseFormData {
  tenant_profile_id: string;
  valid_from: string;
  valid_to: string;
  base_rent: string;
  advance_payment: string;
  deposit: string;
  notes: string;
}

export function UnitDetailPage() {
  const { propertyId, unitId } = useParams<{ propertyId: string; unitId: string }>();
  const [unit, setUnit] = useState<Unit | null>(null);
  const [property, setProperty] = useState<Property | null>(null);
  const [ownerships, setOwnerships] = useState<OwnershipRecord[]>([]);
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Ownership form
  const [showOwnerForm, setShowOwnerForm] = useState(false);
  const [ownerForm, setOwnerForm] = useState<OwnershipFormData>({
    owner_profile_id: "", valid_from: "", valid_to: "", mea_share: "",
  });
  const [ownerFormError, setOwnerFormError] = useState<string | null>(null);
  const [savingOwner, setSavingOwner] = useState(false);

  // Lease form
  const [showLeaseForm, setShowLeaseForm] = useState(false);
  const [leaseForm, setLeaseForm] = useState<LeaseFormData>({
    tenant_profile_id: "", valid_from: "", valid_to: "",
    base_rent: "", advance_payment: "", deposit: "", notes: "",
  });
  const [leaseFormError, setLeaseFormError] = useState<string | null>(null);
  const [savingLease, setSavingLease] = useState(false);

  const personName = useCallback((profilePersonId: string) => {
    const p = persons.find((pe) => pe.id === profilePersonId);
    return p ? `${p.first_name} ${p.last_name}`.trim() : profilePersonId.slice(0, 8);
  }, [persons]);

  const ownerProfileLabel = useCallback((profileId: string) => {
    const profile = ownerProfiles.find((op) => op.id === profileId);
    if (!profile) return profileId.slice(0, 8);
    return personName(profile.person_id);
  }, [ownerProfiles, personName]);

  const tenantProfileLabel = useCallback((profileId: string) => {
    const profile = tenantProfiles.find((tp) => tp.id === profileId);
    if (!profile) return profileId.slice(0, 8);
    return personName(profile.person_id);
  }, [tenantProfiles, personName]);

  const reload = useCallback(() => {
    if (!unitId || !propertyId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      getUnit(unitId),
      getProperty(propertyId),
      listOwnership(unitId),
      listLeases(unitId),
      listOwnerProfiles(),
      listTenantProfiles(),
      listPersons(),
    ])
      .then(([u, p, oh, la, op, tp, pe]) => {
        setUnit(u);
        setProperty(p);
        setOwnerships(oh);
        setLeases(la);
        setOwnerProfiles(op);
        setTenantProfiles(tp);
        setPersons(pe);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [unitId, propertyId]);

  useEffect(() => { reload(); }, [reload]);

  async function handleOwnerSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!unitId) return;
    setSavingOwner(true);
    setOwnerFormError(null);
    try {
      const body: OwnershipCreate = {
        unit_id: unitId,
        owner_profile_id: ownerForm.owner_profile_id,
        valid_from: ownerForm.valid_from,
      };
      if (ownerForm.valid_to) body.valid_to = ownerForm.valid_to;
      if (ownerForm.mea_share) body.mea_share = ownerForm.mea_share;
      await createOwnership(body);
      setShowOwnerForm(false);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setOwnerFormError(
          "Ueberschneidung: Fuer diese Einheit existiert bereits ein Eigentumseintrag im angegebenen Zeitraum. " +
          "Bitte waehlen Sie einen anderen Zeitraum oder beenden Sie den bestehenden Eintrag zuerst."
        );
      } else if (err instanceof ApiError) {
        setOwnerFormError(err.message);
      } else {
        setOwnerFormError(String(err));
      }
    } finally {
      setSavingOwner(false);
    }
  }

  async function handleLeaseSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!unitId) return;
    setSavingLease(true);
    setLeaseFormError(null);
    try {
      const body: LeaseCreate = {
        unit_id: unitId,
        tenant_profile_id: leaseForm.tenant_profile_id,
        valid_from: leaseForm.valid_from,
        base_rent: leaseForm.base_rent,
        advance_payment: leaseForm.advance_payment,
      };
      if (leaseForm.valid_to) body.valid_to = leaseForm.valid_to;
      if (leaseForm.deposit) body.deposit = leaseForm.deposit;
      if (leaseForm.notes) body.notes = leaseForm.notes;
      await createLease(body);
      setShowLeaseForm(false);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setLeaseFormError(
          "Ueberschneidung: Fuer diese Einheit existiert bereits ein Mietvertrag im angegebenen Zeitraum. " +
          "Bitte waehlen Sie einen anderen Zeitraum oder beenden Sie den bestehenden Vertrag zuerst."
        );
      } else if (err instanceof ApiError) {
        setLeaseFormError(err.message);
      } else {
        setLeaseFormError(String(err));
      }
    } finally {
      setSavingLease(false);
    }
  }

  if (loading) return <p>Lade...</p>;
  if (error && !unit) return <p className="error-text">{error}</p>;
  if (!unit || !property) return <p className="error-text">Einheit nicht gefunden.</p>;

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to={`/objekte/${propertyId}`} className="link">&larr; {property.name}</Link>
      </p>
      <h1>Einheit {unit.number}</h1>

      {error && <p className="error-text">{error}</p>}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Einheitsdaten</h2>
        <table className="info-table">
          <tbody>
            <tr><td>Nummer</td><td>{unit.number}</td></tr>
            <tr><td>Lage</td><td>{unit.location || "-"}</td></tr>
            <tr><td>Nutzungsart</td><td>{unit.usage_type}</td></tr>
            <tr><td>Gebäudeteil</td><td>{unit.building_part || "-"}</td></tr>
            <tr><td>Flaeche</td><td>{unit.area ? `${unit.area} m2` : "-"}</td></tr>
            <tr><td>Beh. Flaeche</td><td>{unit.heated_area ? `${unit.heated_area} m2` : "-"}</td></tr>
            <tr><td>MEA</td><td>{formatLegalMea(unit.mea, property)}</td></tr>
          </tbody>
        </table>
      </div>

      {/* Ownership History */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ margin: 0 }}>Eigentuemer-Historie</h2>
        <button className="btn btn--primary" onClick={() => {
          setOwnerFormError(null);
          setOwnerForm({ owner_profile_id: ownerProfiles[0]?.id ?? "", valid_from: "", valid_to: "", mea_share: "" });
          setShowOwnerForm(true);
        }}>
          + Eigentum
        </button>
      </div>

      {showOwnerForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neues Eigentum anlegen</h2>
          {ownerFormError && <p className="error-text">{ownerFormError}</p>}
          <form onSubmit={handleOwnerSubmit} className="form-grid">
            <label>
              Eigentuemer *
              <select
                required
                value={ownerForm.owner_profile_id}
                onChange={(e) => setOwnerForm({ ...ownerForm, owner_profile_id: e.target.value })}
              >
                <option value="">-- waehlen --</option>
                {ownerProfiles.map((op) => (
                  <option key={op.id} value={op.id}>{personName(op.person_id)}</option>
                ))}
              </select>
            </label>
            <label>
              Gueltig ab *
              <input
                type="date"
                required
                value={ownerForm.valid_from}
                onChange={(e) => setOwnerForm({ ...ownerForm, valid_from: e.target.value })}
              />
            </label>
            <label>
              Gueltig bis
              <input
                type="date"
                value={ownerForm.valid_to}
                onChange={(e) => setOwnerForm({ ...ownerForm, valid_to: e.target.value })}
              />
            </label>
            <label>
              MEA-Anteil
              <input
                type="text"
                inputMode="decimal"
                value={ownerForm.mea_share}
                onChange={(e) => setOwnerForm({ ...ownerForm, mea_share: e.target.value })}
              />
            </label>
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={savingOwner}>
                {savingOwner ? "Speichern..." : "Speichern"}
              </button>
              <button type="button" className="btn" onClick={() => setShowOwnerForm(false)}>
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {ownerships.length === 0 ? (
        <p className="empty-hint" style={{ marginBottom: "2rem" }}>Keine Eigentumshistorie vorhanden.</p>
      ) : (
        <div className="card" style={{ marginBottom: "2rem" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Eigentuemer</th>
                <th>Von</th>
                <th>Bis</th>
                <th>MEA-Anteil</th>
              </tr>
            </thead>
            <tbody>
              {ownerships.map((o) => (
                <tr key={o.id}>
                  <td>{ownerProfileLabel(o.owner_profile_id)}</td>
                  <td>{o.valid_from}</td>
                  <td>{o.valid_to ?? "offen"}</td>
                  <td>{o.mea_share ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Lease Agreements */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ margin: 0 }}>Mietvertraege</h2>
        <button className="btn btn--primary" onClick={() => {
          setLeaseFormError(null);
          setLeaseForm({
            tenant_profile_id: tenantProfiles[0]?.id ?? "",
            valid_from: "", valid_to: "",
            base_rent: "", advance_payment: "", deposit: "", notes: "",
          });
          setShowLeaseForm(true);
        }}>
          + Mietvertrag
        </button>
      </div>

      {showLeaseForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neuen Mietvertrag anlegen</h2>
          {leaseFormError && <p className="error-text">{leaseFormError}</p>}
          <form onSubmit={handleLeaseSubmit} className="form-grid">
            <label>
              Mieter *
              <select
                required
                value={leaseForm.tenant_profile_id}
                onChange={(e) => setLeaseForm({ ...leaseForm, tenant_profile_id: e.target.value })}
              >
                <option value="">-- waehlen --</option>
                {tenantProfiles.map((tp) => (
                  <option key={tp.id} value={tp.id}>{personName(tp.person_id)}</option>
                ))}
              </select>
            </label>
            <label>
              Vertragsbeginn *
              <input
                type="date"
                required
                value={leaseForm.valid_from}
                onChange={(e) => setLeaseForm({ ...leaseForm, valid_from: e.target.value })}
              />
            </label>
            <label>
              Vertragsende
              <input
                type="date"
                value={leaseForm.valid_to}
                onChange={(e) => setLeaseForm({ ...leaseForm, valid_to: e.target.value })}
              />
            </label>
            <label>
              Kaltmiete (EUR) *
              <input
                type="text"
                inputMode="decimal"
                required
                value={leaseForm.base_rent}
                onChange={(e) => setLeaseForm({ ...leaseForm, base_rent: e.target.value })}
              />
            </label>
            <label>
              NK-Vorauszahlung (EUR) *
              <input
                type="text"
                inputMode="decimal"
                required
                value={leaseForm.advance_payment}
                onChange={(e) => setLeaseForm({ ...leaseForm, advance_payment: e.target.value })}
              />
            </label>
            <label>
              Kaution (EUR)
              <input
                type="text"
                inputMode="decimal"
                value={leaseForm.deposit}
                onChange={(e) => setLeaseForm({ ...leaseForm, deposit: e.target.value })}
              />
            </label>
            <label className="form-full-width">
              Notizen
              <textarea
                value={leaseForm.notes}
                onChange={(e) => setLeaseForm({ ...leaseForm, notes: e.target.value })}
                rows={2}
              />
            </label>
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={savingLease}>
                {savingLease ? "Speichern..." : "Speichern"}
              </button>
              <button type="button" className="btn" onClick={() => setShowLeaseForm(false)}>
                Abbrechen
              </button>
            </div>
          </form>
        </div>
      )}

      {leases.length === 0 ? (
        <p className="empty-hint">Keine Mietvertraege vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Mieter</th>
                <th>Von</th>
                <th>Bis</th>
                <th>Kaltmiete</th>
                <th>NK-Voraus.</th>
                <th>Kaution</th>
              </tr>
            </thead>
            <tbody>
              {leases.map((la) => (
                <tr key={la.id}>
                  <td>{tenantProfileLabel(la.tenant_profile_id)}</td>
                  <td>{la.valid_from}</td>
                  <td>{la.valid_to ?? "offen"}</td>
                  <td>{la.base_rent}</td>
                  <td>{la.advance_payment}</td>
                  <td>{la.deposit ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
