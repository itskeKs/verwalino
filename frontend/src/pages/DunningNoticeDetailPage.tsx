/**
 * Dunning notice detail page -- view, issue (draft->issued), delete draft,
 * download PDF (issued only). GoBD-compliant: issued notices are immutable.
 *
 * M7 Mahnwesen: dunning notice lifecycle with GoBD immutability.
 * Shows included items (Rückstandsaufstellung), amounts breakdown,
 * and status-dependent actions.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  getDunningNotice,
  issueDunningNotice,
  deleteDunningNotice,
  dunningPdfUrl,
  listTenantProfiles,
  listOwnerProfiles,
  listPersons,
} from "../api/endpoints";
import type {
  DunningNotice,
  TenantProfile,
  OwnerProfile,
  Person,
} from "../api/types";
import { centsFromDecimal, formatDate, formatDateTime, formatMoney } from "../lib/format";
import { ApiError } from "../api/client";
import { MailDispatch } from "../components/MailDispatch";
import { PAYMENT_TYPE_LABELS } from "../lib/paymentTypes";

const STATUS_LABELS: Record<string, string> = {
  draft: "Entwurf",
  issued: "Ausgestellt",
  cancelled: "Verworfen",
};

const STATUS_CSS: Record<string, string> = {
  draft: "badge--draft",
  issued: "badge--confirmed",
};

const LEVEL_LABELS: Record<number, string> = {
  1: "Zahlungserinnerung",
  2: "Mahnung",
  3: "Letzte Mahnung",
};

export function DunningNoticeDetailPage() {
  const { dunningId } = useParams<{ dunningId: string }>();
  const navigate = useNavigate();

  const requestId = useRef(0);
  const [notice, setNotice] = useState<DunningNotice | null>(null);
  const [tenantProfiles, setTenantProfiles] = useState<TenantProfile[]>([]);
  const [ownerProfiles, setOwnerProfiles] = useState<OwnerProfile[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [bankChoices, setBankChoices] = useState<
    { id: string; label: string; iban: string }[] | null
  >(null);
  const [selectedBankId, setSelectedBankId] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  const reload = useCallback(() => {
    if (!dunningId) return;
    const current = ++requestId.current;
    setNotice(null);
    setLoading(true);
    setError(null);
    Promise.all([
      getDunningNotice(dunningId),
      listTenantProfiles(),
      listOwnerProfiles(),
      listPersons(),
    ])
      .then(([dn, tp, op, per]) => {
        if (current !== requestId.current) return;
        setNotice(dn);
        setTenantProfiles(tp);
        setOwnerProfiles(op);
        setPersons(per);
      })
      .catch((err) => {
        if (current !== requestId.current) return;
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => { if (current === requestId.current) setLoading(false); });
    return () => { requestId.current++; };
  }, [dunningId]);

  useEffect(() => {
    return reload();
  }, [reload]);

  function getPersonName(personId: string): string {
    const person = persons.find((p) => p.id === personId);
    if (!person) return "-";
    const parts = [person.first_name, person.last_name].filter(Boolean);
    return parts.length > 0 ? parts.join(" ") : "-";
  }

  function getRecipientLabel(): string {
    if (!notice) return "-";
    if (notice.tenant_profile_id) {
      const tp = tenantProfiles.find((t) => t.id === notice.tenant_profile_id);
      return tp ? `Mieter: ${getPersonName(tp.person_id)}` : "-";
    }
    if (notice.owner_profile_id) {
      const op = ownerProfiles.find((o) => o.id === notice.owner_profile_id);
      return op ? `Eigentümer: ${getPersonName(op.person_id)}` : "-";
    }
    return "-";
  }

  const isDraft = notice?.status === "draft";
  const isIssued = notice?.status === "issued";

  // --- Actions ---

  async function handleIssue() {
    if (!dunningId) return;
    const confirmed = window.confirm(
      "Mahnung ausgeben? Nach der Ausgabe ist die Mahnung unveränderbar (GoBD). " +
        "Der Entwurf wird endgültig als Mahnung gespeichert. Die Ausstellung weist keine Zustellung nach.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await issueDunningNotice(
        dunningId,
        bankChoices ? selectedBankId || undefined : undefined,
      );
      setNotice(updated);
      setBankChoices(null);
    } catch (err) {
      if (err instanceof ApiError) {
        if ([401, 403, 404].includes(err.status)) {
          setNotice(null);
          setError(err.message);
        } else if (err.status === 409 && err.code === "dunning_bank_selection_required") {
          // Several eligible accounts: the manager picks the destination per
          // notice; retry issues with the selected account.
          const accounts =
            (err.payload as { bank_accounts?: { id: string; label: string; iban: string }[] })
              ?.bank_accounts ?? [];
          setBankChoices(accounts);
          setSelectedBankId(accounts[0]?.id ?? "");
          setActionError(err.message);
        } else if (
          err.status === 409 &&
          (err.code === "dunning_bank_account_missing" ||
            err.code === "dunning_bank_account_ineligible")
        ) {
          setActionError(err.message);
        } else if (err.status === 409) {
          setActionError(
            "Fehler: Die Mahnung kann in diesem Status nicht ausgegeben werden. " +
              "Möglicherweise wurde sie bereits ausgegeben.",
          );
        } else {
          setActionError(err.message);
        }
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function handleDelete() {
    if (!dunningId) return;
    const confirmed = window.confirm(
      notice?.accounting_scope_id
        ? "Entwurf verwerfen? Er bleibt mit seiner Zuordnung nachvollziehbar und kann nicht mehr ausgestellt werden."
        : "Entwurf löschen? Nur nicht ausgestellte Mahnungen können gelöscht werden.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await deleteDunningNotice(dunningId);
      navigate("/mahnungen");
    } catch (err) {
      if (err instanceof ApiError) {
        if ([401, 403, 404].includes(err.status)) { setNotice(null); setError(err.message); }
        if (err.status === 409) {
          setActionError(
            "Fehler: Ausgestellte Mahnungen können nicht gelöscht werden (GoBD).",
          );
        } else {
          setActionError(err.message);
        }
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  function handleDownloadPdf() {
    if (!dunningId) return;
    window.open(dunningPdfUrl(dunningId), "_blank");
  }

  // --- Render ---

  if (loading) return <p>Lade...</p>;
  if (error) return <p role="alert" className="error-text">{error} <button className="btn" onClick={reload}>Erneut laden</button></p>;
  if (!notice) return <p className="error-text">Mahnung nicht gefunden.</p>;

  const includedItems = notice.included_items ?? [];
  const issuedAt = notice.issued_at ? formatDateTime(notice.issued_at) : null;

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/mahnungen" className="link">
          &larr; Alle Mahnungen
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
          {LEVEL_LABELS[notice.level] ?? `Stufe ${notice.level}`}{" "}
          <span
            className={`badge ${STATUS_CSS[notice.status] ?? ""}`}
            style={{ fontSize: "0.8rem", verticalAlign: "middle" }}
          >
            {STATUS_LABELS[notice.status] ?? notice.status}
          </span>
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

      {/* GoBD notice for issued dunning notices */}
      {isIssued && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#fff8e1",
            borderColor: "#ffcc02",
          }}
        >
          <strong>GoBD:</strong> Diese Mahnung ist ausgestellt und unveränderbar.
          Die gespeicherten Inhalte dürfen nicht nachträglich geändert werden. Die Ausstellung allein ist kein Zustellnachweis.
          {notice.issued_at && (
            <div style={{ marginTop: "0.25rem", fontSize: "0.82rem" }}>
              Ausgestellt am: {issuedAt}
            </div>
          )}
        </div>
      )}

      {/* Detail card */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Übersicht</h2>
        <table className="info-table">
          <tbody>
            <tr>
              <td>Empfänger</td>
              <td>{getRecipientLabel()}</td>
            </tr>
            <tr>
              <td>Stufe</td>
              <td>
                Stufe {notice.level} ({LEVEL_LABELS[notice.level] ?? "-"})
              </td>
            </tr>
            <tr>
              <td>Ausstellungsdatum</td>
              <td>{formatDate(notice.issue_date)}</td>
            </tr>
            <tr>
              <td>Zahlungsfrist</td>
              <td>{formatDate(notice.due_date)}</td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <span className={`badge ${STATUS_CSS[notice.status] ?? ""}`}>
                  {STATUS_LABELS[notice.status] ?? notice.status}
                </span>
              </td>
            </tr>
            {notice.issued_at && (
              <tr>
                <td>Ausgestellt am</td>
                <td>{issuedAt}</td>
              </tr>
            )}
            {notice.note && (
              <tr>
                <td>Notiz</td>
                <td>{notice.note}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Amounts summary */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Beträge</h2>
        <table className="info-table">
          <tbody>
            <tr>
              <td>Hauptforderung (Rückstand)</td>
              <td
                style={{
                  fontFamily: "var(--mono)",
                  fontWeight: 600,
                  fontSize: "1.05rem",
                }}
              >
                {formatMoney(centsFromDecimal(notice.principal_amount))}
              </td>
            </tr>
            <tr>
              <td>Mahngebühr</td>
              <td style={{ fontFamily: "var(--mono)" }}>
                {formatMoney(centsFromDecimal(notice.fee_amount))}
              </td>
            </tr>
            {(notice.interest_snapshot || notice.interest_amount !== undefined) && <tr>
              <td>Verzugszinsen</td>
              <td style={{ fontFamily: "var(--mono)" }}>{formatMoney(centsFromDecimal(notice.interest_amount))}</td>
            </tr>}
            <tr>
              <td>
                <strong>Gesamtsumme</strong>
              </td>
              <td
                style={{
                  fontFamily: "var(--mono)",
                  fontWeight: 700,
                  fontSize: "1.1rem",
                  color: "#b71c1c",
                }}
              >
                {formatMoney(centsFromDecimal(notice.total_amount))}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {notice.interest_snapshot && <section className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Eingefrorene Zinsberechnung</h2>
        {notice.interest_snapshot.premium
          ? <p>Basiszinssatz + {notice.interest_snapshot.premium} Prozentpunkte (§ 288 Abs. {notice.interest_snapshot.premium === 9 ? 2 : 1} BGB)</p>
          : <>
            <p>Basiszinssatz: {notice.interest_snapshot.base_interest_rate} %</p>
            <p>Jahreszinssatz: {notice.interest_snapshot.annual_rate} % (Basiszinssatz + 5 Prozentpunkte)</p>
          </>}
        <p>Stichtag: <span>{formatDate(notice.interest_snapshot.reference_date)}</span></p>
        <p className="muted">Gespeicherte Werte dieser Mahnung; keine Neuberechnung mit heutigen Zinssätzen.</p>
        <div style={{ overflowX: "auto" }}><table className="data-table">
          <thead><tr><th>Hauptforderung</th><th>Zinsen</th><th>Typ</th><th>Periode</th><th>Fälligkeit</th><th>Tage</th></tr></thead>
          <tbody>{notice.interest_snapshot.items.map((item, index) => <tr key={index}>
            <td className="whitespace-nowrap">
              <span>{formatMoney(centsFromDecimal(item.principal))}</span>
              {item.interest_start && ["KALTMIETE", "NK_VORAUSZAHLUNG"].includes(item.payment_type) &&
                <div className="text-xs font-normal">Zinsbeginn:<br />{formatDate(item.interest_start)}</div>}
            </td><td className="whitespace-nowrap">{formatMoney(centsFromDecimal(item.interest))}</td>
            <td>{PAYMENT_TYPE_LABELS[item.payment_type] ?? item.payment_type}</td>
            <td>{item.period_year === null ? "-" : `${item.period_year}${item.period_month === null ? "" : `/${String(item.period_month).padStart(2, "0")}`}`}</td>
            <td>{formatDate(item.due_date)}</td><td>{item.days}</td>
          </tr>)}</tbody>
        </table></div>
        {notice.interest_snapshot.premium && <div className="mt-4">
          <h3>Zinsabschnitte</h3>
          <div className="overflow-x-auto"><table className="data-table">
            <thead><tr><th>Zinszeitraum</th><th>Zinsen</th><th>Hauptforderung</th><th>Jahreszinssatz</th><th>Basiszinssatz</th><th>Tage</th><th>Typ</th></tr></thead>
            <tbody>{notice.interest_snapshot.items.flatMap((item, index) => item.segments?.map(segment =>
              <tr key={`${index}-${segment.start}`}>
                <td>{formatDate(segment.start)} bis {formatDate(segment.end)}</td>
                <td className="whitespace-nowrap">{formatMoney(centsFromDecimal(segment.interest))}</td>
                <td className="whitespace-nowrap">{formatMoney(centsFromDecimal(segment.principal))}</td>
                <td>{segment.annual_rate.replace(".", ",")} %</td>
                <td>{segment.base_interest_rate.replace(".", ",")} %</td>
                <td>{segment.days}</td>
                <td>{PAYMENT_TYPE_LABELS[item.payment_type] ?? item.payment_type}</td>
              </tr>) ?? [])}</tbody>
          </table></div>
        </div>}
      </section>}

      {/* Included items (Rückstandsaufstellung) */}
      {includedItems.length > 0 && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>Rückstandsaufstellung</h2>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ textAlign: "right" }}>Offen</th>
                  <th style={{ textAlign: "right" }}>Soll</th>
                  <th style={{ textAlign: "right" }}>Ist</th>
                  <th>Periode</th>
                  <th>Typ</th>
                </tr>
              </thead>
              <tbody>
                {includedItems.map((item, idx) => {
                  const hasOpenAmount = /[1-9]/.test(item.open_amount);
                  return (
                    <tr key={idx}>
                      <td
                        className="whitespace-nowrap"
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--mono)",
                          fontWeight: 600,
                          color: !hasOpenAmount ? undefined : item.open_amount.startsWith("-") ? "#2e7d32" : "#b71c1c",
                        }}
                      >
                        {formatMoney(centsFromDecimal(item.open_amount))}
                      </td>
                      <td
                        className="whitespace-nowrap"
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--mono)",
                        }}
                      >
                        {formatMoney(centsFromDecimal(item.soll))}
                      </td>
                      <td
                        className="whitespace-nowrap"
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--mono)",
                        }}
                      >
                        {formatMoney(centsFromDecimal(item.ist))}
                      </td>
                      <td>
                        {item.period_year
                          ? `${item.period_year}${item.period_month ? "/" + String(item.period_month).padStart(2, "0") : ""}`
                          : "-"}
                      </td>
                      <td>
                        {PAYMENT_TYPE_LABELS[item.payment_type] ?? item.payment_type}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {isIssued && notice.id === dunningId && <MailDispatch kind="dunning" documentId={notice.id} />}

      {isDraft && bankChoices && (
        <div style={{ marginBottom: "1rem", maxWidth: "32rem" }}>
          <label htmlFor="dunning-bank-account" style={{ display: "block", marginBottom: "0.25rem" }}>
            Zielkonto für die Zahlung
          </label>
          <select
            id="dunning-bank-account"
            value={selectedBankId}
            onChange={(event) => setSelectedBankId(event.target.value)}
            style={{ width: "100%", marginBottom: "0.25rem" }}
          >
            {bankChoices.map((account) => (
              <option key={account.id} value={account.id}>
                {account.label} (IBAN: {account.iban})
              </option>
            ))}
          </select>
        </div>
      )}

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
            <button
              className="btn btn--primary"
              onClick={handleIssue}
              disabled={actionLoading}
            >
              {actionLoading ? "Ausgeben..." : "Mahnung ausgeben"}
            </button>
            <button
              className="btn"
              onClick={handleDelete}
              disabled={actionLoading}
              style={{ color: "#b71c1c" }}
            >
              {notice.accounting_scope_id ? "Entwurf verwerfen" : "Entwurf löschen"}
            </button>
          </>
        )}
        {isIssued && (
          <button
            className="btn btn--primary"
            onClick={handleDownloadPdf}
          >
            PDF herunterladen
          </button>
        )}
      </div>
    </div>
  );
}
