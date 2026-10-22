import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { createSepaMandate, downloadSepaXml, getSepaAccount, previewSepa, revokeSepaMandate, saveSepaCreditor, updateSepaMandate, type SepaAccount, type SepaCreditor, type SepaExportRequest, type SepaMandate, type SepaMandateCreate, type SepaPreview, type SepaSequence } from "../api/sepa";
import type { BankAccount, Property } from "../api/types";
import { AuthCtx } from "../auth/useAuth";
import { AccountingScopeSelector } from "./AccountingScopeSelector";

function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
const normalizedIban = (value: string) => value.replace(/\s/g, "").toUpperCase();
const countryValid = (value: string) => /^[A-Z]{2}$/.test(value);
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "SEPA fehlgeschlagen. Bitte erneut versuchen.";
const months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

export function SepaExport({ bankAccounts, properties }: { bankAccounts: BankAccount[]; properties: Property[] }) {
  const auth = useContext(AuthCtx);
  const canWrite = !!auth?.session && ["OWNER_ADMIN", "CLERK"].includes(auth.session.role);
  const [bankId, setBankId] = useState("");
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [scopeValid, setScopeValid] = useState(true);
  const bank = bankAccounts.find(item => item.id === bankId);
  const onValidity = useCallback((valid: boolean) => setScopeValid(valid), []);
  return <section aria-label="SEPA-Lastschrift" className="mt-8 space-y-4">
    <h2>SEPA-CORE-Lastschrift · Monatsmiete</h2>
    <p>Nur eine pain.008.001.08-Datei zum manuellen Upload: Die App übermittelt niemals an die Bank. Prüfung und Freigabe erfolgen außerhalb der App; die Bankannahme wird nicht zugesichert.</p>
    <p>Der Download erstellt keine Zahlung, bucht nichts und markiert keinen Einzug als ausgeführt. Wiederholte Monatsdownloads ändern weder Sequenz noch letztes Einzugsdatum.</p>
    <p>Vollständiger Export des ganzen Bankobjekts mit allen im Monat überlappenden Mietverhältnissen. Fehlende oder veraltete Mandate blockieren den gesamten Export; niemand wird stillschweigend ausgelassen. Vollständiger Lesezugriff auf Bank und Mieterkontakte sowie Exportberechtigung sind erforderlich und werden serverseitig geprüft.</p>
    <div><label htmlFor="sepa-bank">SEPA-Bankkonto *</label><select id="sepa-bank" value={bankId} onChange={event => { setBankId(event.target.value); setScopeId(null); setScopeValid(true); }}><option value="">Bitte ausdrücklich wählen</option>{bankAccounts.map(item => <option key={item.id} value={item.id}>{item.label} · {properties.find(property => property.id === item.property_id)?.name ?? item.property_name ?? item.property_id}</option>)}</select></div>
    {bank && <>
      <AccountingScopeSelector key={bank.id} propertyId={bank.property_id} capability="export" value={scopeId} onChange={setScopeId} onValidityChange={onValidity} />
      <SepaWorkspace key={`${auth?.session?.organization_id}:${auth?.session?.user_id}:${auth?.session?.role}:${bank.id}:${bank.property_id}:${scopeId ?? ""}:${scopeValid}`} bankId={bank.id} propertyId={bank.property_id} scopeId={scopeId} scopeValid={scopeValid} canWrite={canWrite} />
    </>}
  </section>;
}

function SepaWorkspace({ bankId, propertyId, scopeId, scopeValid, canWrite }: { bankId: string; propertyId: string; scopeId: string | null; scopeValid: boolean; canWrite: boolean }) {
  const [account, setAccount] = useState<SepaAccount | null>(null);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ request: SepaExportRequest; preview: SepaPreview } | null>(null);
  const lifecycle = useRef({ mounted: false, generation: 0, busy: false });
  useEffect(() => { const state = lifecycle.current; state.mounted = true; return () => { state.mounted = false; state.generation++; }; }, []);
  const invalidate = useCallback(() => { lifecycle.current.generation++; setResult(null); setError(null); }, []);
  const current = (generation: number) => lifecycle.current.mounted && lifecycle.current.generation === generation;
  async function run(kind: string, action: (generation: number) => Promise<void>) {
    if (lifecycle.current.busy || !scopeValid) return;
    lifecycle.current.busy = true; setBusy(kind); setError(null);
    const generation = lifecycle.current.generation;
    try { await action(generation); }
    catch (reason) {
      if (current(generation)) {
        if (reason instanceof ApiError && reason.status === 409) setResult(null);
        setError(`${errorMessage(reason)}${reason instanceof ApiError && reason.status === 409 ? " Bitte Konfiguration und Quellen erneut prüfen und eine neue Vorschau erstellen." : " Bitte erneut versuchen."}`);
      }
    } finally { if (lifecycle.current.mounted) { lifecycle.current.busy = false; setBusy(null); } }
  }
  async function readAccount(generation: number) {
    const data = await getSepaAccount(bankId);
    if (data.bank.id !== bankId || data.bank.property_id !== propertyId) throw new Error("Geladene SEPA-Konfiguration passt nicht zum Bankobjekt.");
    if (current(generation)) { setAccount(data); setRevision(value => value + 1); }
  }
  function load() {
    if (lifecycle.current.busy) return;
    invalidate(); setAccount(null);
    void run("load", readAccount);
  }
  function mutate(action: () => Promise<void>) {
    if (!canWrite || lifecycle.current.busy) return;
    invalidate();
    void run("config", async generation => {
      await action();
      // Never allow stale local consent/configuration after a successful write.
      if (current(generation)) setAccount(null);
      await readAccount(generation);
    });
  }
  async function showPreview(request: SepaExportRequest) {
    if (!account || lifecycle.current.busy) return;
    invalidate();
    await run("preview", async generation => { const preview = await previewSepa(request); if (current(generation)) setResult({ request, preview }); });
  }
  async function download() {
    if (!result || lifecycle.current.busy) return;
    await run("download", async generation => {
      const blob = await downloadSepaXml({ ...result.request, expected_source_digest: result.preview.source_digest });
      if (!current(generation)) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      try {
        anchor.href = url; anchor.download = `SEPA_${result.request.year}_${String(result.request.month).padStart(2, "0")}.xml`;
        document.body.append(anchor); anchor.click();
      } finally { anchor.remove(); URL.revokeObjectURL(url); }
    });
  }
  return <div className="space-y-4">
    <button type="button" disabled={!!busy || !scopeValid} onClick={load}>{busy === "load" ? "SEPA-Konfiguration wird geladen…" : "SEPA-Konfiguration laden"}</button>
    {!scopeValid && <p role="alert">Bitte einen verfügbaren Export-Abrechnungskreis wählen.</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {account && <>
      <p>Bankkonto: {account.bank.label} · IBAN {account.bank.iban || "fehlt – bestehendes Bankkonto separat bearbeiten"} · BIC {account.bank.bic || "nicht hinterlegt"}. Die Bank-IBAN wird hier nicht bearbeitet.</p>
      <fieldset disabled={!!busy} className="space-y-4" key={revision} onChange={invalidate}>
        {canWrite ? <CreditorForm creditor={account.creditor} onSave={value => mutate(() => saveSepaCreditor(bankId, value))} /> : <p>Gläubiger: {account.creditor ? `${account.creditor.name} · ${account.creditor.creditor_id} · ${account.creditor.country} · ${account.creditor.town} · Bankvereinbarung ${account.creditor.bank_authorized ? "bestätigt" : "nicht bestätigt"}` : "Noch nicht konfiguriert"}. Nur Schreibberechtigte dürfen Gläubiger und Mandate konfigurieren.</p>}
        <h3>Registrierte Mandate</h3>
        <p>Eine Checkbox erzeugt kein unterschriebenes Mandat und ersetzt keine rechtliche Prüfung. Das vollständige unterschriebene Original muss beim Gläubiger aufbewahrt werden; die externe Aufbewahrungsreferenz dient nur dem Wiederauffinden. Identitätsdaten können nicht überschrieben werden: bei Änderungen widerrufen und mit neuer Referenz registrieren.</p>
        {account.mandates.length === 0 && <p>Keine Mandate registriert. Fehlende Mandate blockieren die Vorschau.</p>}
        {account.mandates.map(mandate => <MandateHistory key={mandate.id} mandate={mandate} tenantName={account.tenants.find(tenant => tenant.tenant_profile_id === mandate.tenant_profile_id)?.name ?? mandate.tenant_profile_id} canWrite={canWrite} onSave={value => mutate(() => updateSepaMandate(mandate.id, value))} onRevoke={() => mutate(() => revokeSepaMandate(mandate.id))} />)}
        {canWrite && <MandateForm tenants={account.tenants} onSave={value => mutate(() => createSepaMandate(bankId, value))} />}
      </fieldset>
      <fieldset disabled={busy === "config" || busy === "load"}>
        <ExportForm key={`export:${revision}`} bankId={bankId} scopeId={scopeId} configured={!!account.creditor?.bank_authorized && !!account.bank.iban} busy={!!busy} onChange={invalidate} onPreview={request => void showPreview(request)} />
      </fieldset>
    </>}
    {result && <section aria-label="Geprüfte SEPA-Datei">
      <h3>SEPA-Vorschau</h3><p>Gesamt: {result.preview.total} EUR</p>
      <div className="overflow-x-auto"><table aria-label="SEPA-Vorschau"><thead><tr><th>Mieter</th><th>Mandatsreferenz</th><th>Sequenz</th><th>Betrag (EUR)</th><th>Mietverträge</th></tr></thead><tbody>{result.preview.rows.map(row => <tr key={`${row.tenant_profile_id}:${row.mandate_id}`}><td>{row.name}</td><td>{row.reference}</td><td>{row.sequence_type}</td><td>{row.amount}</td><td>{row.lease_ids.join(", ")}</td></tr>)}</tbody></table></div>
      <button type="button" disabled={!!busy || !account} onClick={() => void download()}>{busy === "download" ? "SEPA-XML wird geladen…" : "SEPA-XML herunterladen"}</button>
      <p>Nachrichten- und daraus abgeleitete End-to-End-IDs müssen eindeutig sein. Erneuter Download oder Upload mit wiederverwendeten IDs kann doppelte Einzüge verursachen. Datei und Bankfreigabe außerhalb der App prüfen; keine automatische Übertragung.</p>
    </section>}
  </div>;
}

function CreditorForm({ creditor, onSave }: { creditor: SepaCreditor | null; onSave: (value: SepaCreditor & { bank_authorized: true }) => void }) {
  const [value, setValue] = useState<SepaCreditor>(creditor ?? { creditor_id: "", name: "", country: "", town: "", bank_authorized: false });
  const valid = !!value.creditor_id.trim() && !!value.name.trim() && countryValid(value.country) && !!value.town.trim() && value.bank_authorized;
  return <form noValidate className="space-y-4" onSubmit={event => { event.preventDefault(); if (valid) onSave({ ...value, bank_authorized: true }); }}>
    <h3>Gläubiger und Bankvereinbarung</h3>
    {([ ["creditor_id", "Gläubiger-ID *"], ["name", "Gläubigername *"], ["country", "Gläubiger-Land (ISO) *"], ["town", "Gläubigerort *"] ] as const).map(([key, label]) => <div key={key}><label htmlFor={`sepa-creditor-${key}`}>{label}</label><input id={`sepa-creditor-${key}`} value={value[key]} maxLength={key === "country" ? 2 : undefined} onChange={event => setValue({ ...value, [key]: event.target.value, bank_authorized: false })} /></div>)}
    <label><input type="checkbox" checked={value.bank_authorized} onChange={event => setValue({ ...value, bank_authorized: event.target.checked })} /> Gläubigerangaben und bestehende Lastschriftvereinbarung mit der Bank sind geprüft.</label>
    <button type="submit" disabled={!valid}>Gläubiger speichern</button>
  </form>;
}

function MandateForm({ tenants, onSave }: { tenants: SepaAccount["tenants"]; onSave: (value: SepaMandateCreate) => void }) {
  const [tenantId, setTenantId] = useState("");
  const [reference, setReference] = useState(""); const [signedOn, setSignedOn] = useState("");
  const [sequence, setSequence] = useState<"" | SepaSequence>(""); const [lastUsed, setLastUsed] = useState("");
  const [country, setCountry] = useState(""); const [iban, setIban] = useState(""); const [bic, setBic] = useState("");
  const [original, setOriginal] = useState(""); const [confirmed, setConfirmed] = useState(false);
  const tenant = tenants.find(item => item.tenant_profile_id === tenantId);
  const valid = !!tenant?.iban && normalizedIban(iban) === normalizedIban(tenant.iban) && !!reference.trim() && reference.length <= 35 && validDate(signedOn) && !!sequence && countryValid(country) && !!original.trim() && confirmed
    && (sequence === "FRST" ? !lastUsed : validDate(lastUsed) && lastUsed >= signedOn);
  return <form noValidate className="space-y-4" onSubmit={event => {
    event.preventDefault(); if (!valid || !sequence) return;
    onSave({ tenant_profile_id: tenantId, reference, signed_on: signedOn, sequence_type: sequence, last_used_on: lastUsed || null, debtor_country: country, ...(bic.trim() ? { debtor_bic: bic.trim() } : {}), confirmed_iban: normalizedIban(iban), original_reference: original, mandate_confirmed: true });
  }}>
    <h3>Vorhandenes unterschriebenes Mandat registrieren</h3>
    <p>Eine bestehende Personen-IBAN ist erforderlich. Fehlende oder geänderte IBAN zuerst im Kontakt separat bearbeiten. Name und aktuelle IBAN werden serverseitig per Hash an die Zustimmung gebunden, nicht als zweite Rohdatenkopie gespeichert.</p>
    <div><label htmlFor="sepa-tenant">Mieter für neues Mandat *</label><select id="sepa-tenant" value={tenantId} onChange={event => { setTenantId(event.target.value); setIban(""); setConfirmed(false); }}><option value="">Bitte ausdrücklich wählen</option>{tenants.map(item => <option key={item.tenant_profile_id} value={item.tenant_profile_id}>{item.name}</option>)}</select></div>
    {tenant && <p>Aktueller Kontakt: {tenant.name} · {tenant.iban || "Keine Personen-IBAN hinterlegt"} · {tenant.city || "Ort fehlt"}</p>}
    <div><label htmlFor="sepa-reference">Mandatsreferenz *</label><input id="sepa-reference" maxLength={35} value={reference} onChange={event => { setReference(event.target.value); setConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-signed">Unterschrieben am *</label><input id="sepa-signed" type="date" value={signedOn} onChange={event => { setSignedOn(event.target.value); setConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-new-sequence">Sequenz neues Mandat *</label><select id="sepa-new-sequence" value={sequence} onChange={event => { setSequence(event.target.value as "" | SepaSequence); setLastUsed(""); setConfirmed(false); }}><option value="">Bitte ausdrücklich wählen</option><option value="FRST">FRST – erster Einzug</option><option value="RCUR">RCUR – wiederkehrender Einzug</option></select></div>
    <div><label htmlFor="sepa-new-last">Tatsächlicher vorheriger Einzug (neues Mandat)</label><input id="sepa-new-last" type="date" value={lastUsed} disabled={sequence !== "RCUR"} onChange={event => { setLastUsed(event.target.value); setConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-debtor-country">Schuldner-Land (ISO) *</label><input id="sepa-debtor-country" maxLength={2} value={country} onChange={event => { setCountry(event.target.value); setConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-iban-confirm">IBAN ausdrücklich bestätigen *</label><input id="sepa-iban-confirm" autoComplete="off" value={iban} onChange={event => { setIban(event.target.value); setConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-debtor-bic">Schuldner-BIC (optional; bei Nicht-EWR erforderlich)</label><input id="sepa-debtor-bic" autoComplete="off" value={bic} onChange={event => { setBic(event.target.value); setConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-original">Original-Aufbewahrungsreferenz *</label><input id="sepa-original" value={original} onChange={event => { setOriginal(event.target.value); setConfirmed(false); }} /></div>
    <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> Das unterschriebene Original liegt bereits beim Gläubiger vor, wird vollständig extern aufbewahrt und stimmt mit Name, IBAN und Mandatsdaten überein. Diese Bestätigung erzeugt keine Unterschrift.</label>
    <button type="submit" disabled={!valid}>Mandat registrieren</button>
  </form>;
}

function MandateHistory({ mandate, tenantName, canWrite, onSave, onRevoke }: { mandate: SepaMandate; tenantName: string; canWrite: boolean; onSave: (value: { sequence_type: SepaSequence; last_used_on: string | null }) => void; onRevoke: () => void }) {
  const [sequence, setSequence] = useState(mandate.sequence_type);
  const [lastUsed, setLastUsed] = useState(mandate.last_used_on ?? "");
  const [confirmed, setConfirmed] = useState(false); const [revokeConfirmed, setRevokeConfirmed] = useState(false);
  const valid = confirmed && (sequence === "FRST" ? !lastUsed : validDate(lastUsed) && lastUsed >= mandate.signed_on);
  return <section aria-label={`Mandat ${mandate.reference}`} className="space-y-4">
    <p>{tenantName} · {mandate.reference} · unterschrieben {mandate.signed_on} · {mandate.debtor_country} · Original: {mandate.original_reference} · {mandate.sequence_type} · letzter Einzug: {mandate.last_used_on ?? "nicht erfasst"}</p>
    <p>{mandate.revoked_at ? `Widerrufen am ${mandate.revoked_at}` : mandate.source_current ? "Kontaktdaten entsprechen der registrierten Zustimmung; keine rechtliche Gültigkeitszusage." : "Kontaktdaten geändert: Mandat nicht mehr aktuell. Widerrufen und neues unterschriebenes Mandat registrieren."}</p>
    {canWrite && !mandate.revoked_at && <>
      <form noValidate onSubmit={event => { event.preventDefault(); if (valid) onSave({ sequence_type: sequence, last_used_on: lastUsed || null }); }}>
        <div><label htmlFor={`sepa-sequence-${mandate.id}`}>Sequenz {mandate.reference}</label><select id={`sepa-sequence-${mandate.id}`} value={sequence} onChange={event => { setSequence(event.target.value as SepaSequence); setLastUsed(""); setConfirmed(false); }}><option value="FRST">FRST</option><option value="RCUR">RCUR</option></select></div>
        <div><label htmlFor={`sepa-last-${mandate.id}`}>Tatsächlicher vorheriger Einzug {mandate.reference}</label><input id={`sepa-last-${mandate.id}`} type="date" value={lastUsed} disabled={sequence !== "RCUR"} onChange={event => { setLastUsed(event.target.value); setConfirmed(false); }} /></div>
        <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> Historie manuell anhand tatsächlich bei der Bank vorgelegter Einzüge geprüft (auch zurückgewiesen, zurückgegeben oder erstattet), nicht anhand eines Downloads.</label>
        <button type="submit" disabled={!valid}>Historie {mandate.reference} speichern</button>
      </form>
      <label><input type="checkbox" checked={revokeConfirmed} onChange={event => setRevokeConfirmed(event.target.checked)} /> Widerruf von {mandate.reference} ausdrücklich bestätigen.</label>
      <button type="button" disabled={!revokeConfirmed} onClick={onRevoke}>Mandat {mandate.reference} widerrufen</button>
    </>}
  </section>;
}

function ExportForm({ bankId, scopeId, configured, busy, onChange, onPreview }: { bankId: string; scopeId: string | null; configured: boolean; busy: boolean; onChange: () => void; onPreview: (request: SepaExportRequest) => void }) {
  const [year, setYear] = useState(""); const [month, setMonth] = useState(""); const [collection, setCollection] = useState("");
  const [messageId, setMessageId] = useState(""); const [noticeOn, setNoticeOn] = useState(""); const [days, setDays] = useState("");
  const [shorter, setShorter] = useState(false); const [noticeConfirmed, setNoticeConfirmed] = useState(false); const [bankConfirmed, setBankConfirmed] = useState(false);
  const validPeriod = /^(?:[1-9]|1[0-4])$/.test(days) && (days === "14" || shorter);
  const validTiming = validDate(noticeOn) && validDate(collection) && new Date(`${noticeOn}T00:00:00Z`).getTime() + Number(days) * 86400000 <= new Date(`${collection}T00:00:00Z`).getTime();
  const valid = configured && /^[1-9]\d{3}$/.test(year) && /^(?:[1-9]|1[0-2])$/.test(month) && !!messageId.trim() && messageId.length <= 35 && validPeriod && validTiming && noticeConfirmed && bankConfirmed;
  return <form noValidate className="space-y-4" onChange={onChange} onSubmit={event => {
    event.preventDefault(); if (!valid || busy) return;
    onPreview({ bank_account_id: bankId, accounting_scope_id: scopeId, year: Number(year), month: Number(month), collection_date: collection, message_id: messageId, pre_notification_on: noticeOn, notice_period_days: Number(days), shorter_period_agreed: shorter, pre_notification_confirmed: true, bank_profile_confirmed: true });
  }}>
    <h3>Monatsdatei vorbereiten</h3>
    {!configured && <p>Gläubiger mit bestätigter Bankvereinbarung und bestehende Bank-IBAN sind zuerst erforderlich.</p>}
    <div><label htmlFor="sepa-year">Jahr *</label><input id="sepa-year" type="number" min="1000" max="9999" step="1" value={year} onChange={event => { setYear(event.target.value); setNoticeConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-month">Monat *</label><select id="sepa-month" value={month} onChange={event => { setMonth(event.target.value); setNoticeConfirmed(false); }}><option value="">Bitte ausdrücklich wählen</option>{months.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select></div>
    <div><label htmlFor="sepa-collection">Einzugsdatum *</label><input id="sepa-collection" type="date" value={collection} onChange={event => { setCollection(event.target.value); setNoticeConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-message">Nachrichten-ID *</label><input id="sepa-message" maxLength={35} value={messageId} onChange={event => setMessageId(event.target.value)} /></div>
    <div><label htmlFor="sepa-notice">Vorabankündigung erfolgt am *</label><input id="sepa-notice" type="date" value={noticeOn} onChange={event => { setNoticeOn(event.target.value); setNoticeConfirmed(false); }} /></div>
    <div><label htmlFor="sepa-days">Ankündigungsfrist in Tagen *</label><input id="sepa-days" type="number" min="1" max="14" step="1" value={days} onChange={event => setDays(event.target.value)} /></div>
    <p>14 Tage, sofern keine kürzere Frist vereinbart wurde. Vorabankündigungsdatum plus vereinbarte Frist muss spätestens das Einzugsdatum ergeben. T50 versendet keine E-Mail und keine Vorabankündigung.</p>
    <label><input type="checkbox" checked={shorter} onChange={event => setShorter(event.target.checked)} /> Kürzere Frist ist mit den Zahlungspflichtigen ausdrücklich vereinbart.</label>
    <label><input type="checkbox" checked={noticeConfirmed} onChange={event => setNoticeConfirmed(event.target.checked)} /> Die tatsächliche Vorabankündigung über Betrag und Einzugstermin wurde bereits allen betroffenen Mietern fristgerecht gegeben.</label>
    <label><input type="checkbox" checked={bankConfirmed} onChange={event => setBankConfirmed(event.target.checked)} /> Bankprofil pain.008.001.08, CORE und Zeichensatz wurden für den manuellen Upload mit der Bank vereinbart und geprüft; dies ist keine Annahmezusage.</label>
    <button type="submit" disabled={!valid || busy}>{busy ? "SEPA-Vorgang läuft…" : "SEPA-Vorschau erstellen"}</button>
  </form>;
}
