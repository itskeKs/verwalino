/**
 * Person detail page -- shows contact info, bank data, and role profiles.
 * A person can be designated as owner, tenant, or vendor (Dienstleister).
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getPerson,
  listOwnerProfiles,
  listTenantProfiles,
  listVendors,
  createOwnerProfile,
  createTenantProfile,
  createVendor,
} from "../api/endpoints";
import type { Person, OwnerProfile, TenantProfile, Vendor } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { AttachmentsPanel } from "../components/AttachmentsPanel";
import { DsgvoPanel } from "../components/DsgvoPanel";

export function PersonDetailPage() {
  const { personId } = useParams<{ personId: string }>();
  const { session } = useAuth();
  const requestVersion = useRef(0);
  const [person, setPerson] = useState<Person | null>(null);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Vendor form state
  const [showVendorForm, setShowVendorForm] = useState(false);
  const [vendorTaxId, setVendorTaxId] = useState("");
  const [vendorVatId, setVendorVatId] = useState("");

  const reload = useCallback(() => {
    if (!personId) return;
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    setPerson(null);
    setShowVendorForm(false);
    Promise.all([
      getPerson(personId),
      listOwnerProfiles(personId),
      listTenantProfiles(personId),
      listVendors(personId),
    ])
      .then(([p, op, tp, v]) => {
        if (version !== requestVersion.current) return;
        setPerson(p);
        setOwnerProfiles(op);
        setTenantProfiles(tp);
        setVendors(v);
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
  }, [personId]);

  const invalidateRequests = useCallback(() => { requestVersion.current++; }, []);
  useEffect(() => { reload(); return invalidateRequests; }, [reload, invalidateRequests]);

  async function addOwnerProfile() {
    if (!personId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await createOwnerProfile(personId);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function addTenantProfile() {
    if (!personId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await createTenantProfile(personId);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleVendorSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!personId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await createVendor({
        person_id: personId,
        tax_id: vendorTaxId || undefined,
        vat_id: vendorVatId || undefined,
      });
      setShowVendorForm(false);
      setVendorTaxId("");
      setVendorVatId("");
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  if (loading) return <p>Lade...</p>;
  if (error && !person) return <p className="error-text">{error}</p>;
  if (!person) return <p className="error-text">Person nicht gefunden.</p>;

  const hasOwner = ownerProfiles.length > 0;
  const hasTenant = tenantProfiles.length > 0;
  const hasVendor = vendors.length > 0;
  const isAdmin = session?.role === "OWNER_ADMIN";

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/personen" className="link">&larr; Alle Personen</Link>
      </p>
      <h1>{person.first_name} {person.last_name}</h1>

      {error && <p className="error-text">{error}</p>}
      {actionError && <p className="error-text">{actionError}</p>}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Kontaktdaten</h2>
        <table className="info-table">
          <tbody>
            <tr><td>Vorname</td><td>{person.first_name || "-"}</td></tr>
            <tr><td>Nachname</td><td>{person.last_name}</td></tr>
            {person.company_name && <tr><td>Firma</td><td>{person.company_name}</td></tr>}
            <tr><td>E-Mail</td><td>{person.email ?? "-"}</td></tr>
            <tr><td>Telefon</td><td>{person.phone ?? "-"}</td></tr>
            <tr><td>Strasse</td><td>{person.street || "-"}</td></tr>
            <tr><td>PLZ / Stadt</td><td>{[person.zip_code, person.city].filter(Boolean).join(" ") || "-"}</td></tr>
          </tbody>
        </table>
      </div>

      {(person.iban || person.bic || person.bank_name) && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>Bankdaten</h2>
          <table className="info-table">
            <tbody>
              <tr><td>IBAN</td><td>{person.iban ?? "-"}</td></tr>
              <tr><td>BIC</td><td>{person.bic ?? "-"}</td></tr>
              <tr><td>Bankname</td><td>{person.bank_name ?? "-"}</td></tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Rollen / Profile</h2>
        <div className="profile-badges">
          {hasOwner && <span className="badge badge--weg">Eigentuemer</span>}
          {hasTenant && <span className="badge badge--miet">Mieter</span>}
          {hasVendor && <span className="badge badge--sev">Dienstleister</span>}
          {!hasOwner && !hasTenant && !hasVendor && (
            <span className="empty-hint">Keine Rolle zugewiesen.</span>
          )}
        </div>

        <div style={{ marginTop: "1rem", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          {person.can_prepare && !hasOwner && (
            <button className="btn btn--small" onClick={addOwnerProfile} disabled={actionLoading}>
              Als Eigentuemer kennzeichnen
            </button>
          )}
          {person.can_prepare && !hasTenant && (
            <button className="btn btn--small" onClick={addTenantProfile} disabled={actionLoading}>
              Als Mieter kennzeichnen
            </button>
          )}
          {person.can_prepare && !hasVendor && !showVendorForm && (
            <button className="btn btn--small" onClick={() => setShowVendorForm(true)} disabled={actionLoading}>
              Als Dienstleister kennzeichnen
            </button>
          )}
        </div>

        {person.can_prepare && showVendorForm && (
          <form onSubmit={handleVendorSubmit} style={{ marginTop: "1rem" }} className="form-grid">
            <label>
              Steuer-Nr.
              <input
                type="text"
                value={vendorTaxId}
                onChange={(e) => setVendorTaxId(e.target.value)}
              />
            </label>
            <label>
              USt-IdNr.
              <input
                type="text"
                value={vendorVatId}
                onChange={(e) => setVendorVatId(e.target.value)}
              />
            </label>
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={actionLoading}>
                Dienstleister anlegen
              </button>
              <button type="button" className="btn" onClick={() => setShowVendorForm(false)}>
                Abbrechen
              </button>
            </div>
          </form>
        )}
      </div>

      <AttachmentsPanel personId={person.id} canWrite={(session?.role === "OWNER_ADMIN" || session?.role === "CLERK") && person.can_prepare} />

      {isAdmin && personId && (
        <DsgvoPanel key={personId} personId={personId} canPrepare={person.can_prepare} canExport={person.can_export} onChanged={reload} />
      )}

      {hasVendor && (
        <div className="card">
          <h2>Dienstleister-Details</h2>
          {vendors.map((v) => (
            <table className="info-table" key={v.id}>
              <tbody>
                <tr><td>Steuer-Nr.</td><td>{v.tax_id ?? "-"}</td></tr>
                <tr><td>USt-IdNr.</td><td>{v.vat_id ?? "-"}</td></tr>
                {v.notes && <tr><td>Notizen</td><td>{v.notes}</td></tr>}
              </tbody>
            </table>
          ))}
        </div>
      )}
    </div>
  );
}
