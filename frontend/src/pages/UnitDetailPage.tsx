/**
 * Unit detail page -- shows ownership history and lease agreements.
 * Overlap errors from DB EXCLUDE constraints are displayed as user-friendly messages.
 */

import { Fragment, useEffect, useState, useCallback, useRef } from "react";
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
  patchOwnership,
  createLease,
  patchLease,
  listLeaseRentChanges,
  createLeaseRentChange,
  createStaffelmieteRentChanges,
  previewIndexmieteRentChange,
  createIndexmieteRentChange,
} from "../api/endpoints";
import type { IndexmieteInput, IndexmietePreview } from "../api/endpoints";
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
  LeaseUpdate,
  LeaseRentChange,
} from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { AttachmentsPanel } from "../components/AttachmentsPanel";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { MeterPanel } from "../components/MeterPanel";

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
  const { session } = useAuth();
  const canWriteOwnership = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const { propertyId, unitId } = useParams<{ propertyId: string; unitId: string }>();
  const requestVersion = useRef(0);
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
  const ownerMutationLock = useRef(false);
  const [ownerStatus, setOwnerStatus] = useState<string | null>(null);
  const [ownerSuggestion, setOwnerSuggestion] = useState<{ valid_from: string; mea_share: string } | null>(null);

  // Lease form
  const [showLeaseForm, setShowLeaseForm] = useState(false);
  const [editingLease, setEditingLease] = useState<LeaseRecord | null>(null);
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
    const version = ++requestVersion.current;
    setUnit(null);
    setProperty(null);
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
        if (version !== requestVersion.current) return;
        setUnit(u);
        setProperty(p);
        setOwnerships(oh);
        setLeases(la);
        setOwnerProfiles(op);
        setTenantProfiles(tp);
        setPersons(pe);
      })
      .catch((err) => {
        if (version !== requestVersion.current) return;
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
  }, [unitId, propertyId]);

  const invalidateRequests = useCallback(() => { requestVersion.current++; }, []);
  useEffect(() => { reload(); return invalidateRequests; }, [reload, invalidateRequests]);

  async function handleOwnerSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!unitId || !canWriteOwnership || ownerMutationLock.current || !ownerForm.owner_profile_id) return;
    ownerMutationLock.current = true;
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
      setOwnerSuggestion(null);
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
      ownerMutationLock.current = false;
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
      if (editingLease) {
        const changes: LeaseUpdate = {
          valid_to: leaseForm.valid_to || null,
          notes: leaseForm.notes,
        };
        if (leaseForm.base_rent !== editingLease.base_rent) changes.base_rent = leaseForm.base_rent;
        if (leaseForm.advance_payment !== editingLease.advance_payment) changes.advance_payment = leaseForm.advance_payment;
        if (leaseForm.deposit && leaseForm.deposit !== editingLease.deposit) changes.deposit = leaseForm.deposit;
        await patchLease(editingLease.id, changes);
      } else {
        await createLease(body);
      }
      setShowLeaseForm(false);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setLeaseFormError(
          editingLease
            ? "Änderung nicht möglich: Zeitraum überschneidet sich oder Beträge sind durch Buchungen bzw. Abrechnungen gesperrt. Ende und Notiz können separat geändert werden."
            : "Überschneidung: Bitte beenden Sie den bestehenden Vertrag zuerst."
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
      {(session?.role === "OWNER_ADMIN" || session?.role === "CLERK") && <Link className="btn btn-primary" to={`/aufgaben?${new URLSearchParams({ property_id: property.id, unit_id: unit.id, create: "1" })}`}>Aufgabe anlegen</Link>}

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

      <AttachmentsPanel propertyId={property.id} unitId={unit.id} canWrite={canWriteOwnership} />

      {/* Ownership History */}
      <MeterPanel key={unit.id} propertyId={property.id} unitId={unit.id} canWrite={canWriteOwnership} />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ margin: 0 }}>Eigentuemer-Historie</h2>
        {canWriteOwnership && <button className="btn btn--primary" disabled={savingOwner} onClick={() => {
          setOwnerFormError(null);
          setOwnerForm({ owner_profile_id: "", valid_from: ownerSuggestion?.valid_from ?? "", valid_to: "", mea_share: ownerSuggestion?.mea_share ?? "" });
          setShowOwnerForm(true);
        }}>
          + Eigentum
        </button>}
      </div>

      {ownerStatus && <p role="status">{ownerStatus}</p>}

      {canWriteOwnership && showOwnerForm && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h2>Neues Eigentum anlegen</h2>
          {ownerFormError && <p className="error-text">{ownerFormError}</p>}
          <form onSubmit={handleOwnerSubmit} className="form-grid">
            <label>
              Eigentuemer *
              <select
                aria-label="Eigentuemer *"
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
                {canWriteOwnership && <th>Aktion</th>}
              </tr>
            </thead>
            <tbody>
              {ownerships.map((o) => (
                <tr key={o.id}>
                  <td>{ownerProfileLabel(o.owner_profile_id)}</td>
                  <td>{o.valid_from}</td>
                  <td>{o.valid_to ?? "offen"}</td>
                  <td>{o.mea_share ?? "-"}</td>
                  {canWriteOwnership && <td>{o.valid_to === null && o.unit_id === unitId && <OwnershipEndAction
                    ownership={o}
                    ownerLabel={ownerProfileLabel(o.owner_profile_id)}
                    disabled={savingOwner}
                    onEnded={(ended, nextDay) => {
                      setOwnerships(previous => previous.map(row => row.id === ended.id ? ended : row));
                      setOwnerStatus(`Eigentum bis einschließlich ${ended.valid_to} beendet. Neuen Eigentümer ab ${nextDay} bewusst auswählen und speichern.`);
                      setOwnerFormError(null);
                      setOwnerSuggestion({ valid_from: nextDay, mea_share: o.mea_share ?? "" });
                      setOwnerForm({ owner_profile_id: "", valid_from: nextDay, valid_to: "", mea_share: o.mea_share ?? "" });
                      setShowOwnerForm(true);
                    }}
                  />}</td>}
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
          setEditingLease(null);
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
          <h2>{editingLease ? "Mietvertrag bearbeiten / beenden" : "Neuen Mietvertrag anlegen"}</h2>
          {leaseFormError && <p className="error-text">{leaseFormError}</p>}
          <form onSubmit={handleLeaseSubmit} className="form-grid">
            <label>
              <span id="lease-tenant-label">Mieter *</span>
              <select
                aria-labelledby="lease-tenant-label"
                required
                disabled={editingLease !== null}
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
                disabled={editingLease !== null}
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
                <th>Aktion</th>
              </tr>
            </thead>
            <tbody>
              {leases.map((la) => (
                <Fragment key={la.id}>
                  <tr>
                    <td>{tenantProfileLabel(la.tenant_profile_id)}</td>
                    <td>{la.valid_from}</td>
                    <td>{la.valid_to ?? "offen"}</td>
                    <td>{la.base_rent}</td>
                    <td>{la.advance_payment}</td>
                    <td>{la.deposit ?? "-"}</td>
                    <td><button className="btn" onClick={() => {
                      setEditingLease(la);
                      setLeaseFormError(null);
                      setLeaseForm({
                        tenant_profile_id: la.tenant_profile_id, valid_from: la.valid_from,
                        valid_to: la.valid_to ?? "", base_rent: la.base_rent,
                        advance_payment: la.advance_payment, deposit: la.deposit ?? "", notes: la.notes,
                      });
                      setShowLeaseForm(true);
                    }}>Bearbeiten / beenden</button></td>
                  </tr>
                  <tr><td colSpan={7}>
                    <LeaseRentHistory lease={la} tenantLabel={tenantProfileLabel(la.tenant_profile_id)} />
                  </td></tr>
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Calendar-only arithmetic: API valid_to is inclusive; never send the exclusive DB bound.
function nextOwnershipDay(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  let [year, month, day] = value.split("-").map(Number);
  if (year < 1 || month < 1 || month > 12) return null;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  if (day < 1 || day > days) return null;
  day += 1;
  if (day > days) { day = 1; month += 1; }
  if (month > 12) { month = 1; year += 1; }
  if (year > 9999) return null;
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function ownershipEndError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "Keine Berechtigung, dieses Eigentum zu beenden.";
    if (error.status === 404) return "Eigentum nicht gefunden oder nicht zugänglich.";
    if (error.status === 409) return "Eigentum nicht beendet: Der Eintrag wurde inzwischen geändert oder ist gesperrt. Bitte Historie prüfen.";
    if (error.status === 422) return `Eigentum nicht beendet: Datum prüfen. ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}

function OwnershipEndAction({ ownership, ownerLabel, disabled, onEnded }: {
  ownership: OwnershipRecord;
  ownerLabel: string;
  disabled: boolean;
  onEnded: (ended: OwnershipRecord, nextDay: string) => void;
}) {
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [open, setOpen] = useState(false);
  const [lastDay, setLastDay] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const mutationLock = useRef(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (mutationLock.current || disabled || !canWrite || ownership.valid_to !== null) return;
    setError(null);
    const nextDay = nextOwnershipDay(lastDay);
    if (!nextDay || lastDay < ownership.valid_from) {
      setError("Bitte einen gültigen letzten Eigentumstag ab Eigentumsbeginn eingeben, dessen Folgetag darstellbar ist (spätestens 9999-12-30).");
      return;
    }
    mutationLock.current = true;
    setSaving(true);
    try {
      const ended = await patchOwnership(ownership.id, { valid_to: lastDay });
      setOpen(false);
      onEnded(ended, nextDay);
    } catch (error) {
      setError(ownershipEndError(error));
    } finally {
      mutationLock.current = false;
      setSaving(false);
    }
  }

  return <>
    <button type="button" className="btn" disabled={disabled || saving} onClick={() => {
      setLastDay(""); setError(null); setOpen(true);
    }}>Eigentum beenden</button>
    <Dialog open={open} onOpenChange={next => { if (!mutationLock.current) setOpen(next); }}>
      <DialogContent>
        <DialogTitle>Eigentum beenden</DialogTitle>
        <DialogDescription>
          {ownerLabel} · Eigentum ab {ownership.valid_from} · MEA-Anteil {ownership.mea_share ?? "-"}.
          Der letzte Eigentumstag gilt einschließlich. Eigentümer, Beginn und MEA bleiben unverändert.
          Anschließend neuen Eigentümer ab Folgetag auswählen und separat speichern.
        </DialogDescription>
        <form onSubmit={submit} noValidate>
          <label>Letzter Eigentumstag<Input type="date" required min={ownership.valid_from} max="9999-12-30" value={lastDay} disabled={saving} onChange={event => setLastDay(event.target.value)} /></label>
          {error && <p role="alert" className="error-text">{error}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn--primary" disabled={saving}>Eigentum beenden</button>
            <button type="button" className="btn" disabled={saving} onClick={() => { if (!mutationLock.current) setOpen(false); }}>Abbrechen</button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}

// Validate/normalize decimal input only; rent calculations stay in the backend.
function rentAmount(value: string): string | null {
  const match = /^(\d+)(?:[.,](\d{1,2}))?$/.exec(value.trim());
  if (!match) return null;
  const integer = match[1].replace(/^0+(?=\d)/, "");
  if (integer.length > 12) return null;
  return `${integer}.${(match[2] ?? "").padEnd(2, "0")}`;
}

function rentDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function staffelPercentFraction(value: string): string | null {
  const match = /^(\d+)(?:[.,](\d+))?$/.exec(value.trim());
  if (!match || !/[1-9]/.test(value)) return null;
  // Divide by 100 as text, retaining even sub-cent percentage precision.
  const integer = match[1].replace(/^0+(?=\d)/, "").padStart(3, "0");
  const fraction = `${integer.slice(-2)}${match[2] ?? ""}`.replace(/0+$/, "");
  return `${integer.slice(0, -2)}.${fraction || "0"}`;
}

function rentIndex(value: string): string | null {
  const match = /^(\d+)(?:[.,](\d{1,6}))?$/.exec(value.trim());
  if (!match || !/[1-9]/.test(value)) return null;
  const integer = match[1].replace(/^0+(?=\d)/, "");
  const fraction = (match[2] ?? "").replace(/0+$/, "");
  return fraction ? `${integer}.${fraction}` : integer;
}

function rentChangeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return "Mietänderung nicht gespeichert: Für dieses Datum besteht bereits eine Änderung oder der Vertrag ist inzwischen gesperrt. Bitte den Verlauf prüfen.";
    if (error.status === 422) return `Mietänderung nicht gespeichert: Datum und Beträge prüfen. ${error.message}`;
    if (error.status === 403) return "Keine Berechtigung, diese Mietänderung zu speichern.";
    if (error.status === 404) return "Mietvertrag nicht gefunden oder nicht zugänglich.";
  }
  return error instanceof Error ? error.message : String(error);
}

function LeaseRentHistory({ lease, tenantLabel }: { lease: LeaseRecord; tenantLabel: string }) {
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [rows, setRows] = useState<LeaseRentChange[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [open, setOpen] = useState(false);
  const [validFrom, setValidFrom] = useState("");
  const [baseRent, setBaseRent] = useState(lease.base_rent);
  const [advance, setAdvance] = useState(lease.advance_payment);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const mutationLock = useRef(false);
  const [staffelOpen, setStaffelOpen] = useState(false);
  const [staffelStart, setStaffelStart] = useState("");
  const [staffelMode, setStaffelMode] = useState<"amount" | "percent">("amount");
  const [staffelAmount, setStaffelAmount] = useState("");
  const [staffelPercent, setStaffelPercent] = useState("");
  const [staffelInterval, setStaffelInterval] = useState("12");
  const [staffelSteps, setStaffelSteps] = useState("3");
  const [indexOpen, setIndexOpen] = useState(false);
  const [indexInput, setIndexInput] = useState<IndexmieteInput>({ old_index: "", new_index: "", declaration_received_on: "" });
  const [indexPreview, setIndexPreview] = useState<IndexmietePreview | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);
    listLeaseRentChanges(lease.id)
      .then(result => { if (active) setRows(result); })
      .catch(error => { if (active) setLoadError(rentChangeError(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [lease.id, revision]);

  function rateAt(date: string) {
    return rows.filter(row => row.valid_from <= date).at(-1) ?? lease;
  }

  function selectDate(date: string) {
    setValidFrom(date);
    const rate = rateAt(date);
    setBaseRent(rate.base_rent);
    setAdvance(rate.advance_payment);
    setFormError(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (mutationLock.current || !canWrite || loading || loadError) return;
    setFormError(null);
    // API lease.valid_to is the inclusive display date, not the exclusive DB boundary.
    if (!rentDate(validFrom) || validFrom < lease.valid_from || (lease.valid_to && validFrom > lease.valid_to)) {
      setFormError("Bitte ein gültiges Datum innerhalb des Mietvertrags angeben (Vertragsende einschließlich).");
      return;
    }
    if (rows.some(row => row.valid_from === validFrom)) {
      setFormError("Für dieses Datum besteht bereits eine Mietänderung.");
      return;
    }
    const base_rent = rentAmount(baseRent);
    const advance_payment = rentAmount(advance);
    if (base_rent === null || advance_payment === null) {
      setFormError("Bitte Kaltmiete und Vorauszahlung als nicht negative EUR-Beträge mit höchstens zwei Nachkommastellen eingeben (maximal 999.999.999.999,99).");
      return;
    }
    mutationLock.current = true;
    setSaving(true);
    try {
      const row = await createLeaseRentChange(lease.id, { valid_from: validFrom, base_rent, advance_payment });
      setRows(previous => [...previous, row].sort((a, b) => a.valid_from.localeCompare(b.valid_from)));
      setOpen(false);
      setRevision(previous => previous + 1);
    } catch (error) {
      setFormError(rentChangeError(error));
    } finally {
      mutationLock.current = false;
      setSaving(false);
    }
  }

  async function submitStaffel(event: React.FormEvent) {
    event.preventDefault();
    if (mutationLock.current || !canWrite || loading || loadError) return;
    setFormError(null);
    if (!rentDate(staffelStart) || staffelStart < lease.valid_from || (lease.valid_to && staffelStart > lease.valid_to)) {
      setFormError("Bitte ein gültiges Startdatum innerhalb des Mietvertrags angeben (Vertragsende einschließlich).");
      return;
    }
    if (!/^\d+$/.test(staffelInterval) || Number(staffelInterval) < 12 || Number(staffelInterval) > 120
      || !/^\d+$/.test(staffelSteps) || Number(staffelSteps) < 1 || Number(staffelSteps) > 50) {
      setFormError("Bitte ein Intervall von 12 bis 120 Monaten und 1 bis 50 Erhöhungen als ganze Zahlen eingeben.");
      return;
    }
    const amount = rentAmount(staffelAmount);
    const percent = staffelPercentFraction(staffelPercent);
    if (staffelMode === "amount" ? amount === null || amount === "0.00" : percent === null) {
      setFormError(staffelMode === "amount"
        ? "Bitte eine positive Erhöhung in EUR mit höchstens zwei Nachkommastellen eingeben (maximal 999.999.999.999,99)."
        : "Bitte eine positive Erhöhung in Prozent als Dezimalzahl eingeben (z. B. 5 oder 2,75).");
      return;
    }
    mutationLock.current = true;
    setSaving(true);
    try {
      const changes = await createStaffelmieteRentChanges(lease.id, {
        staffel_start_date: staffelStart,
        staffel_interval_months: Number(staffelInterval),
        staffel_steps: Number(staffelSteps),
        ...(staffelMode === "amount" ? { staffel_increase_amount: amount! } : { staffel_increase_percent: percent! }),
      });
      setRows(previous => [...previous, ...changes].sort((a, b) => a.valid_from.localeCompare(b.valid_from)));
      setStaffelOpen(false);
      setRevision(previous => previous + 1);
    } catch (error) {
      setFormError(rentChangeError(error));
    } finally {
      mutationLock.current = false;
      setSaving(false);
    }
  }

  function changeIndexInput(field: keyof IndexmieteInput, value: string) {
    if (mutationLock.current) return;
    setIndexInput(previous => ({ ...previous, [field]: value }));
    setIndexPreview(null);
    setFormError(null);
  }

  async function submitIndex(event?: React.FormEvent, adopt = false) {
    event?.preventDefault();
    if (mutationLock.current || !canWrite || loading || loadError) return;
    setFormError(null);
    const old_index = rentIndex(indexInput.old_index);
    const new_index = rentIndex(indexInput.new_index);
    if (old_index === null || new_index === null) {
      setFormError("Bitte beide VPI als positive Dezimalzahlen mit höchstens sechs Nachkommastellen eingeben.");
      return;
    }
    if (old_index === new_index) {
      setFormError("VPI alt und VPI neu müssen sich unterscheiden. Auch eine Indexsenkung ist möglich.");
      return;
    }
    if (!rentDate(indexInput.declaration_received_on)) {
      setFormError("Bitte ein gültiges Zugangsdatum der Erklärung eingeben.");
      return;
    }
    if (adopt && !indexPreview) return;
    const body = { ...indexInput, old_index, new_index };
    mutationLock.current = true;
    setSaving(true);
    try {
      if (adopt && indexPreview) {
        const row = await createIndexmieteRentChange(lease.id, {
          ...body,
          expected_base_rent: indexPreview.base_rent,
          expected_advance_payment: indexPreview.advance_payment,
          expected_previous_valid_from: indexPreview.previous_valid_from,
        });
        setRows(previous => [...previous, row].sort((a, b) => a.valid_from.localeCompare(b.valid_from)));
        setIndexPreview(null);
        setIndexOpen(false);
        setRevision(previous => previous + 1);
      } else {
        setIndexPreview(null);
        setIndexPreview(await previewIndexmieteRentChange(lease.id, body));
      }
    } catch (error) {
      setIndexPreview(null);
      setFormError(error instanceof ApiError && error.status === 409
        ? "Indexmiete nicht übernommen: Verlauf oder Vorschau geändert, zukünftige Mietänderung vorhanden oder Vertrag gesperrt. Bitte den Verlauf prüfen und erneut berechnen."
        : rentChangeError(error));
    } finally {
      mutationLock.current = false;
      setSaving(false);
    }
  }

  const source = rateAt(validFrom);
  const contractLabel = `${tenantLabel} · ${lease.valid_from} bis ${lease.valid_to ?? "offen"}`;
  return <section aria-label={`Mietänderungen: ${contractLabel}`}>
    <h3>Mietänderungen · {contractLabel}</h3>
    <p>Historische volle Monatsraten (KALT / NK). Die Beträge im Mietvertrag oben sind die Originalbeträge.</p>
    <table className="data-table" aria-label={`Miethistorie: ${contractLabel}`}>
      <thead><tr><th>Gültig ab</th><th>Kaltmiete (EUR)</th><th>Vorauszahlung (EUR)</th><th>Quelle</th></tr></thead>
      <tbody>
        <tr><td>{lease.valid_from}</td><td>{lease.base_rent}</td><td>{lease.advance_payment}</td><td>Originalvertrag</td></tr>
        {rows.map(row => <tr key={row.id}><td>{row.valid_from}</td><td>{row.base_rent}</td><td>{row.advance_payment}</td><td>Mietänderung</td></tr>)}
      </tbody>
    </table>
    {loading && <p role="status">Lade Mietänderungen…</p>}
    {loadError && <div><p role="alert" className="error-text">{loadError}</p>
      <button type="button" className="btn" onClick={() => setRevision(previous => previous + 1)}>Mietänderungen erneut laden</button>
    </div>}
    {!loading && !loadError && rows.length === 0 && <p>Noch keine Mietänderungen.</p>}
    {canWrite && <button type="button" className="btn" disabled={saving || loading || Boolean(loadError)} onClick={() => {
      selectDate("");
      setOpen(true);
    }}>Miete ändern ab …</button>}
    {canWrite && <button type="button" className="btn" disabled={saving || loading || Boolean(loadError)} onClick={() => {
      setStaffelStart(""); setStaffelMode("amount"); setStaffelAmount(""); setStaffelPercent("");
      setStaffelInterval("12"); setStaffelSteps("3"); setFormError(null); setStaffelOpen(true);
    }}>Staffelmiete anlegen</button>}
    {canWrite && <button type="button" className="btn" disabled={saving || loading || Boolean(loadError)} onClick={() => {
      setIndexInput({ old_index: "", new_index: "", declaration_received_on: "" });
      setIndexPreview(null); setFormError(null); setIndexOpen(true);
    }}>Indexmiete berechnen</button>}
    <Dialog open={indexOpen} onOpenChange={next => { if (!mutationLock.current) setIndexOpen(next); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogTitle>Indexmiete berechnen</DialogTitle>
        <DialogDescription>
          {contractLabel}. VPI alt und VPI neu manuell aus Destatis angeben und die gleiche VPI-Basis verwenden; kein automatischer Abruf.
          Voraussetzung sind eine schriftliche Indexmietvereinbarung und eine Erklärung in Textform; die rechtliche Prüfung liegt beim Betreiber.
          Das eingegebene Zugangsdatum ist kein Nachweis der Zustellung. Es erfolgt kein Versand und keine Erstellung einer Erklärung.
          Die neue Miete gilt ab dem ersten Tag des zweiten Monats nach Zugang (§ 557b BGB), frühestens nach einem Jahr unveränderter Miete.
          Auch Mietsenkungen sind möglich. Die Berechnung ist nur eine Vorschau und speichert noch keine Mietänderung.
          Die Übernahme ergänzt ausschließlich den Verlauf; Originalvertrag, Buchungen und finalisierte Dokumente bleiben unverändert. Keine Neuberechnung wird ausgelöst.
        </DialogDescription>
        <form onSubmit={event => { void submitIndex(event); }} noValidate>
          <label>VPI alt<Input inputMode="decimal" value={indexInput.old_index} disabled={saving} onChange={event => changeIndexInput("old_index", event.target.value)} /></label>
          <label>VPI neu<Input inputMode="decimal" value={indexInput.new_index} disabled={saving} onChange={event => changeIndexInput("new_index", event.target.value)} /></label>
          <label>Zugang der Erklärung<Input type="date" value={indexInput.declaration_received_on} disabled={saving} onChange={event => changeIndexInput("declaration_received_on", event.target.value)} /></label>
          {indexPreview && <div role="status" aria-label="Indexmietvorschau">
            <p>Bisherige Kaltmiete: {indexPreview.previous_base_rent} EUR (seit {indexPreview.previous_valid_from})</p>
            <p>Neue Kaltmiete: {indexPreview.base_rent} EUR</p>
            <p>Vorauszahlung unverändert: {indexPreview.advance_payment} EUR</p>
            <p>Gültig ab: {indexPreview.valid_from}</p>
          </div>}
          {formError && <p role="alert" className="error-text">{formError}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn--primary" disabled={saving}>Berechnen</button>
            {indexPreview && <button type="button" className="btn btn--primary" disabled={saving} onClick={() => { void submitIndex(undefined, true); }}>Als Mietänderung übernehmen</button>}
            <button type="button" className="btn" disabled={saving} onClick={() => { if (!mutationLock.current) setIndexOpen(false); }}>Abbrechen</button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
    <Dialog open={open} onOpenChange={next => { if (!mutationLock.current) setOpen(next); }}>
      <DialogContent>
        <DialogTitle>Miete ändern</DialogTitle>
        <DialogDescription>
          {contractLabel}. Neue volle Monatsrate ab Stichtag. Originalvertrag, Buchungen und finalisierte Dokumente werden nicht geändert; keine Neuberechnung wird ausgelöst.
        </DialogDescription>
        <form onSubmit={submit} noValidate>
          <label>Gültig ab<Input type="date" value={validFrom} min={lease.valid_from} max={lease.valid_to ?? undefined} disabled={saving} onChange={event => selectDate(event.target.value)} /></label>
          <p>Vorbelegung: {source === lease ? "Originalvertrag" : `Mietänderung ab ${source.valid_from}`} · KALT {source.base_rent} EUR / NK {source.advance_payment} EUR. Bei Datumswechsel werden beide Beträge neu vorbelegt.</p>
          <label>Kaltmiete (EUR)<Input inputMode="decimal" value={baseRent} disabled={saving} onChange={event => setBaseRent(event.target.value)} /></label>
          <label>Vorauszahlung (EUR)<Input inputMode="decimal" value={advance} disabled={saving} onChange={event => setAdvance(event.target.value)} /></label>
          {formError && <p role="alert" className="error-text">{formError}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn--primary" disabled={saving}>Mietänderung speichern</button>
            <button type="button" className="btn" disabled={saving} onClick={() => setOpen(false)}>Abbrechen</button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
    <Dialog open={staffelOpen} onOpenChange={next => { if (!mutationLock.current) setStaffelOpen(next); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogTitle>Staffelmiete anlegen</DialogTitle>
        <DialogDescription>
          {contractLabel}. Die erste Erhöhung gilt am Startdatum, weitere folgen im gewählten Monatsintervall, kalendarisch am Startdatum verankert.
          Grundlage ist die am Startdatum geltende Kaltmiete aus dem Verlauf; die Vorauszahlung bleibt unverändert.
          Mindestens zwölf Monate seit der letzten Mietänderung und zwischen den Stufen (§ 557a BGB).
          Prozent ist nur eine Rechenhilfe: Die schriftliche Vereinbarung muss Mieten oder Erhöhungen in Geldbeträgen ausweisen; eine rechtliche Prüfung der Miethöhe erfolgt hier nicht.
          Jede Stufe wird als unveränderliche historische Monatsrate gespeichert. Originalvertrag, Buchungen und finalisierte Dokumente bleiben unverändert; keine Neuberechnung wird ausgelöst.
        </DialogDescription>
        <form onSubmit={submitStaffel} noValidate>
          <label>Erste Erhöhung ab<Input type="date" value={staffelStart} min={lease.valid_from} max={lease.valid_to ?? undefined} disabled={saving} onChange={event => setStaffelStart(event.target.value)} /></label>
          <label>Erhöhungsart<select value={staffelMode} disabled={saving} onChange={event => setStaffelMode(event.target.value as "amount" | "percent")}>
            <option value="amount">Fester EUR-Betrag</option><option value="percent">Prozent</option>
          </select></label>
          {staffelMode === "amount"
            ? <label>Erhöhung (EUR)<Input inputMode="decimal" value={staffelAmount} disabled={saving} onChange={event => setStaffelAmount(event.target.value)} /></label>
            : <label>Erhöhung (%)<Input inputMode="decimal" value={staffelPercent} disabled={saving} onChange={event => setStaffelPercent(event.target.value)} /></label>}
          <label>Intervall (Monate)<Input type="number" min="12" max="120" step="1" value={staffelInterval} disabled={saving} onChange={event => setStaffelInterval(event.target.value)} /></label>
          <label>Anzahl Erhöhungen<Input type="number" min="1" max="50" step="1" value={staffelSteps} disabled={saving} onChange={event => setStaffelSteps(event.target.value)} /></label>
          {formError && <p role="alert" className="error-text">{formError}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn--primary" disabled={saving}>Staffelmiete speichern</button>
            <button type="button" className="btn" disabled={saving} onClick={() => { if (!mutationLock.current) setStaffelOpen(false); }}>Abbrechen</button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </section>;
}
