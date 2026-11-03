/**
 * Payment detail page -- view, book (draft->booked), reverse (Storno),
 * delete draft. GoBD-compliant: booked payments are immutable.
 *
 * Storno chain is shown: if reversed, link to the Storno row;
 * if this IS a Storno, link to the original.
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  getPayment,
  bookPayment,
  reversePayment,
  deletePayment,
  updatePayment,
  listBankAccounts,
  listProperties,
  listLeases,
  listTenantProfiles,
  listOwnerProfiles,
  listPersons,
  getCostEntry,
  listRegisterEntries,
} from "../api/endpoints";
import type {
  Payment,
  BankAccount,
  Property,
  LeaseRecord,
  TenantProfile,
  OwnerProfile,
  Person,
  CostEntry,
  RegisterEntry,
} from "../api/types";
import { ReserveEvidenceFields } from "../components/ReserveEvidenceFields";
import { ApiError } from "../api/client";

const STATUS_LABELS: Record<string, string> = {
  draft: "Entwurf",
  booked: "Gebucht",
  cancelled: "Verworfen",
};

const STATUS_CSS: Record<string, string> = {
  draft: "badge--draft",
  booked: "badge--confirmed",
  cancelled: "badge--superseded",
};

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "NK-Vorauszahlung",
  KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss",
  RUECKLAGENANTEIL: "Ruecklagenanteil",
  RUECKLAGENENTNAHME: "Rücklagenentnahme",
  SONDERZAHLUNG: "Sonder-/Nachzahlung",
};

const DIRECTION_LABELS: Record<string, string> = {
  IN: "Eingang",
  OUT: "Ausgang",
};

export function PaymentDetailPage() {
  const { paymentId } = useParams<{ paymentId: string }>();
  const navigate = useNavigate();

  const [payment, setPayment] = useState<Payment | null>(null);
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [leases, setLeases] = useState<LeaseRecord[]>([]);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editPeriodYear, setEditPeriodYear] = useState<number | null>(null);
  const [editPeriodMonth, setEditPeriodMonth] = useState<number | null>(null);
  const [evidence, setEvidence] = useState<{ reserve_cost_entry_id: string | null; reserve_register_entry_id: string | null } | null>(null);
  const [editNote, setEditNote] = useState<string | null>(null);
  const [reserveReceipt, setReserveReceipt] = useState<CostEntry | null>(null);
  const [reserveResolution, setReserveResolution] = useState<RegisterEntry | null>(null);
  const [reserveEvidenceError, setReserveEvidenceError] = useState('');

  const costId = payment?.payment_type === 'RUECKLAGENENTNAHME' ? payment.reserve_cost_entry_id : null;
  const registerId = payment?.payment_type === 'RUECKLAGENENTNAHME' ? payment.reserve_register_entry_id : null;
  const propertyId = payment?.property_id;
  useEffect(() => {
    let active = true;
    setReserveReceipt(null); setReserveResolution(null); setReserveEvidenceError('');
    Promise.all([
      costId ? getCostEntry(costId) : Promise.resolve(null),
      registerId && propertyId ? listRegisterEntries(propertyId).then(rows => rows.find(row => row.id === registerId) ?? null) : Promise.resolve(null),
    ]).then(([cost, resolution]) => {
      if (active) {
        setReserveReceipt(cost); setReserveResolution(resolution);
        if (registerId && !resolution) setReserveEvidenceError('Der verknüpfte Beschluss konnte nicht geladen werden.');
      }
    }).catch(() => { if (active) setReserveEvidenceError('Die verknüpften Rücklagenbelege konnten nicht geladen werden. Bitte laden Sie die Seite erneut.'); });
    return () => { active = false; };
  }, [costId, registerId, propertyId]);

  const reload = useCallback(() => {
    if (!paymentId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      getPayment(paymentId),
      listBankAccounts(),
      listProperties(),
      listLeases(),
      listTenantProfiles(),
      listOwnerProfiles(),
      listPersons(),
    ])
      .then(([p, ba, prop, lease, tp, op, per]) => {
        setPayment(p);
        setBankAccounts(ba);
        setProperties(prop);
        setLeases(lease);
        setTenantProfiles(tp);
        setOwnerProfiles(op);
        setPersons(per);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [paymentId]);

  useEffect(() => {
    reload();
  }, [reload]);

  function getAccountLabel(id: string): string {
    const a = bankAccounts.find((ba) => ba.id === id);
    if (!a) return id.slice(0, 8);
    const prop = properties.find((p) => p.id === a.property_id);
    return `${a.label}${prop ? ` (${prop.name})` : ""}`;
  }

  function getPropertyName(id: string): string {
    const prop = properties.find((p) => p.id === id);
    return prop ? prop.name : id.slice(0, 8);
  }

  function getPersonName(personId: string): string {
    const person = persons.find((p) => p.id === personId);
    if (!person) return "-";
    const parts = [person.first_name, person.last_name].filter(Boolean);
    return parts.length > 0 ? parts.join(" ") : "-";
  }

  function getCounterpartyLabel(): string {
    if (!payment) return "-";
    if (payment.tenant_profile_id) {
      const tp = tenantProfiles.find((t) => t.id === payment.tenant_profile_id);
      return tp ? `Mieter: ${getPersonName(tp.person_id)}` : "-";
    }
    if (payment.owner_profile_id) {
      const op = ownerProfiles.find((o) => o.id === payment.owner_profile_id);
      return op ? `Eigentuemer: ${getPersonName(op.person_id)}` : "-";
    }
    return "-";
  }

  function getLeaseLabel(): string {
    if (!payment?.lease_agreement_id) return "-";
    const lease = leases.find((l) => l.id === payment.lease_agreement_id);
    if (!lease) return payment.lease_agreement_id.slice(0, 8);
    const tp = tenantProfiles.find((t) => t.id === lease.tenant_profile_id);
    const person = tp ? persons.find((p) => p.id === tp.person_id) : null;
    const name = person
      ? [person.first_name, person.last_name].filter(Boolean).join(" ")
      : "";
    return `${name} ab ${lease.valid_from}${lease.valid_to ? ` bis ${lease.valid_to}` : " (laufend)"}`;
  }

  const isDraft = payment?.status === "draft";
  const isBooked = payment?.status === "booked";
  const isReversed = !!payment?.reversed_by_id;
  const isStorno = !!payment?.reverses_id;
  const canReleaseImport = isDraft && payment?.bank_transaction_id && payment.payment_type === 'RUECKLAGENENTNAHME'
    && !payment.booked_at && !payment.booked_by && !(payment.reserve_cost_entry_id && payment.reserve_register_entry_id);

  // --- Actions ---

  async function handleBook() {
    if (!paymentId) return;
    const confirmed = window.confirm(
      "Zahlung buchen? Nach der Buchung ist die Zahlung unveraenderbar (GoBD). " +
        "Korrekturen sind nur als Stornobuchung moeglich.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await bookPayment(paymentId);
      setPayment(updated);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleReverse() {
    if (!paymentId) return;
    const confirmed = window.confirm(
      "Stornobuchung erstellen? Es wird eine Gegenbuchung erzeugt, " +
        "die den Betrag neutralisiert. Die Originalbuchung bleibt erhalten (GoBD).",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const storno = await reversePayment(paymentId);
      // Navigate to the new Storno payment
      navigate(`/zahlungen/${storno.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleDelete() {
    if (!paymentId) return;
    const confirmed = window.confirm(
      canReleaseImport
        ? 'Möchten Sie die Zuordnung lösen? Der Zahlungsentwurf wird verworfen und der Bankumsatz erhält den Status „Neu“. Sie können ihn anschließend neu zuordnen oder ignorieren.'
        : "Entwurf verwerfen? Die Zahlung kann danach nicht mehr gebucht werden.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await deletePayment(paymentId);
      navigate(canReleaseImport ? `/bankkonten/${payment!.bank_account_id}/umsaetze` : "/zahlungen");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleSaveEdit() {
    if (!paymentId) return;
    if (editPeriodYear === null && editPeriodMonth === null && editNote === null && evidence === null) {
      setIsEditing(false);
      return;
    }
    const selected = evidence || payment;
    if (payment?.payment_type === 'RUECKLAGENENTNAHME' && (!selected?.reserve_cost_entry_id || !selected.reserve_register_entry_id)) {
      setActionError('Bitte verknüpfen Sie einen bestätigten Kostenbeleg und einen Beschluss aus der Beschluss-Sammlung.'); return;
    }
    setActionLoading(true);
    setActionError(null);
    try {
      const updates: Pick<Parameters<typeof updatePayment>[1], "period_year" | "period_month" | "note" | "reserve_cost_entry_id" | "reserve_register_entry_id"> = {};
      if (editPeriodYear !== null) updates.period_year = editPeriodYear;
      if (editPeriodMonth !== null) updates.period_month = editPeriodMonth;
      if (editNote !== null) updates.note = editNote;
      if (evidence) Object.assign(updates, evidence);
      const updated = await updatePayment(paymentId, updates);
      setPayment(updated);
      setIsEditing(false);
      setEvidence(null);
      setEditPeriodYear(null);
      setEditPeriodMonth(null);
      setEditNote(null);
      // Reload to refresh lookups
      await reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  function handleCancelEdit() {
    setIsEditing(false);
    setEvidence(null);
    setEditPeriodYear(null);
    setEditPeriodMonth(null);
    setEditNote(null);
  }

  function handleStartEdit() {
    setEvidence(null);
    setEditPeriodYear(payment?.period_year ?? null);
    setEditPeriodMonth(payment?.period_month ?? null);
    setEditNote(payment?.note ?? null);
    setIsEditing(true);
  }

  // --- Render ---

  if (loading) return <p>Lade...</p>;
  if (error && !payment) return <p className="error-text">{error}</p>;
  if (!payment) return <p className="error-text">Zahlung nicht gefunden.</p>;

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/zahlungen" className="link">
          &larr; Alle Zahlungen
        </Link>
      </p>

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
        <h1 style={{ margin: 0 }}>
          Zahlung{" "}
          <span
            className={`badge ${STATUS_CSS[payment.status] ?? ""}`}
            style={{ fontSize: "0.8rem", verticalAlign: "middle" }}
          >
            {STATUS_LABELS[payment.status] ?? payment.status}
          </span>
          {isStorno && (
            <span
              className="badge badge--rejected"
              style={{ fontSize: "0.8rem", verticalAlign: "middle", marginLeft: "0.25rem" }}
            >
              Storno
            </span>
          )}
          {isReversed && (
            <span
              className="badge badge--superseded"
              style={{ fontSize: "0.8rem", verticalAlign: "middle", marginLeft: "0.25rem" }}
            >
              Storniert
            </span>
          )}
        </h1>
      </div>

      {actionError && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#ffebee",
            borderColor: "#ef9a9a",
          }}
        >
          <strong>Fehler:</strong> {actionError}
        </div>
      )}

      {/* GoBD notice for booked payments */}
      {isBooked && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#fff8e1",
            borderColor: "#ffcc02",
          }}
        >
          <strong>GoBD:</strong> Diese Zahlung ist gebucht und unveraenderbar.
          Korrekturen sind nur als Stornobuchung (Gegenbuchung) moeglich.
          {payment.retention_until && (
            <div style={{ marginTop: "0.25rem", fontSize: "0.82rem" }}>
              Aufbewahrung bis: {payment.retention_until}
            </div>
          )}
        </div>
      )}

      {/* Storno chain */}
      {isReversed && payment.reversed_by_id && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#ffebee",
            borderColor: "#ef9a9a",
          }}
        >
          Diese Zahlung wurde storniert durch{" "}
          <Link
            to={`/zahlungen/${payment.reversed_by_id}`}
            className="link"
          >
            Stornobuchung anzeigen
          </Link>
          .
        </div>
      )}

      {isStorno && payment.reverses_id && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#e3f2fd",
            borderColor: "#90caf9",
          }}
        >
          Dies ist eine Stornobuchung (Gegenbuchung) zu{" "}
          <Link to={`/zahlungen/${payment.reverses_id}`} className="link">
            Originalbuchung anzeigen
          </Link>
          .
        </div>
      )}

      {/* Detail card */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Details</h2>
        <table className="info-table">
          <tbody>
            <tr>
              <td>Zahlungstyp</td>
              <td>
                {PAYMENT_TYPE_LABELS[payment.payment_type] ?? payment.payment_type}
              </td>
            </tr>
            <tr>
              <td>Richtung</td>
              <td>
                <span
                  style={{
                    color: payment.direction === "IN" ? "#2e7d32" : "#b71c1c",
                    fontWeight: 600,
                  }}
                >
                  {DIRECTION_LABELS[payment.direction] ?? payment.direction}
                </span>
              </td>
            </tr>
            <tr>
              <td>Betrag</td>
              <td style={{ fontFamily: "var(--mono)", fontWeight: 700, fontSize: "1.05rem" }}>
                {payment.amount} EUR
              </td>
            </tr>
            <tr>
              <td>Wertstellung</td>
              <td>{payment.value_date}</td>
            </tr>
            <tr>
              <td>Bankkonto</td>
              <td>{getAccountLabel(payment.bank_account_id)}</td>
            </tr>
            <tr>
              <td>Objekt</td>
              <td>
                <Link
                  to={`/objekte/${payment.property_id}`}
                  className="link"
                >
                  {getPropertyName(payment.property_id)}
                </Link>
              </td>
            </tr>
            <tr>
              <td>Gegenpartei</td>
              <td>{getCounterpartyLabel()}</td>
            </tr>
            <tr>
              <td>Mietvertrag</td>
              <td>{getLeaseLabel()}</td>
            </tr>
            <tr>
              <td>Periode</td>
              <td>
                {payment.period_year
                  ? `${payment.period_year}${payment.period_month ? "/" + String(payment.period_month).padStart(2, "0") : ""}`
                  : "-"}
              </td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <span className={`badge ${STATUS_CSS[payment.status] ?? ""}`}>
                  {STATUS_LABELS[payment.status] ?? payment.status}
                </span>
              </td>
            </tr>
            {payment.booked_at && (
              <tr>
                <td>Gebucht am</td>
                <td>{payment.booked_at}</td>
              </tr>
            )}
            {payment.note && (
              <tr>
                <td>Notiz</td>
                <td>{payment.note}</td>
              </tr>
            )}
            {costId && <tr>
              <td>Kostenbeleg</td>
              <td><Link className="link" to={`/kosten/${costId}`}>{reserveReceipt?.document_number || 'Kostenbeleg öffnen'}</Link></td>
            </tr>}
            {registerId && <tr>
              <td>Beschluss</td>
              <td><Link className="link" to={`/beschluss-sammlung?property_id=${payment.property_id}#beschluss-${registerId}`}>
                {reserveResolution ? `Beschluss Nr. ${reserveResolution.sequence_number} · ${reserveResolution.wording}` : 'Beschluss öffnen'}
              </Link></td>
            </tr>}
          </tbody>
        </table>
        {reserveEvidenceError && <p role="alert" className="error-text">{reserveEvidenceError}</p>}
      </div>

      {/* Action buttons */}
      <div
        style={{
          display: "flex",
          gap: "0.5rem",
          marginBottom: "1.5rem",
          flexWrap: "wrap",
        }}
      >
        {isDraft && (
          <>
            {payment.payment_type === 'RUECKLAGENENTNAHME' && !isEditing && (!payment.reserve_cost_entry_id || !payment.reserve_register_entry_id) &&
              <p role="alert">Diese Rücklagenentnahme bleibt ein Entwurf. Bitte ergänzen Sie über „Bearbeiten“ den bestätigten Kostenbeleg und den Beschluss vor dem Buchen.</p>}
            {isEditing ? (
              <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                <label style={{ fontSize: "0.875rem" }}>
                  Monat:
                  <input
                    type="number"
                    min={1}
                    max={12}
                    value={editPeriodMonth ?? ""}
                    onChange={(e) => setEditPeriodMonth(e.target.value ? parseInt(e.target.value, 10) : null)}
                    className="input"
                    style={{ marginLeft: "4px", width: "60px" }}
                  />
                </label>
                <label style={{ fontSize: "0.875rem" }}>
                  Jahr:
                  <input
                    type="number"
                    value={editPeriodYear ?? ""}
                    onChange={(e) => setEditPeriodYear(e.target.value ? parseInt(e.target.value, 10) : null)}
                    className="input"
                    style={{ marginLeft: "4px", width: "80px" }}
                  />
                </label>
                <label style={{ fontSize: "0.875rem" }}>
                  Notiz:
                  <input
                    type="text"
                    value={editNote ?? ""}
                    onChange={(e) => setEditNote(e.target.value || null)}
                    className="input"
                    style={{ marginLeft: "4px", width: "200px" }}
                  />
                </label>
                {payment.payment_type === 'RUECKLAGENENTNAHME' && <div className="form-grid" style={{ width: '100%' }}>
                  <ReserveEvidenceFields propertyId={payment.property_id} cost={(evidence || payment).reserve_cost_entry_id || ''}
                    register={(evidence || payment).reserve_register_entry_id || ''}
                    onChange={(cost, register) => setEvidence({ reserve_cost_entry_id: cost || null, reserve_register_entry_id: register || null })} />
                </div>}
                <button
                  className="btn btn--primary"
                  onClick={handleSaveEdit}
                  disabled={actionLoading}
                >
                  {actionLoading ? "Speichern..." : "Speichern"}
                </button>
                <button
                  className="btn"
                  onClick={handleCancelEdit}
                  disabled={actionLoading}
                >
                  Abbrechen
                </button>
              </div>
            ) : (
              <>
                <button
                  className="btn btn--primary"
                  onClick={handleBook}
                  disabled={actionLoading}
                >
                  {actionLoading ? "Buchen..." : "Buchen"}
                </button>
                <button
                  className="btn"
                  onClick={handleStartEdit}
                  disabled={actionLoading}
                >
                  Bearbeiten
                </button>
                {!payment.bank_transaction_id && <button
                  className="btn"
                  onClick={handleDelete}
                  disabled={actionLoading}
                  style={{ color: "#b71c1c" }}
                >
                  Entwurf verwerfen
                </button>}
                {canReleaseImport && <button className="btn" onClick={handleDelete} disabled={actionLoading}>
                  Zuordnung lösen
                </button>}
              </>
            )}
          </>
        )}
        {isBooked && !isReversed && !isStorno && (
          <button
            className="btn"
            onClick={handleReverse}
            disabled={actionLoading}
            style={{ color: "#b71c1c" }}
          >
            {actionLoading ? "Stornieren..." : "Stornieren (Gegenbuchung)"}
          </button>
        )}
      </div>
    </div>
  );
}
