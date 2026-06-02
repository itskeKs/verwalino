/**
 * Meeting detail page -- Versammlungs-Detailansicht with:
 * - Overview (date, type, location, status)
 * - Invitation/protocol text editing
 * - Agenda items (TOPs): create, list, edit, delete
 * - Resolutions (Beschluesse): create per TOP, vote tallies, result
 * - "Als gehalten markieren" action (after which only protocol is editable)
 * - Niederschrift-PDF download
 * - "In Beschluss-Sammlung uebernehmen" per resolution
 *
 * M8 Versammlung: full meeting lifecycle with 409 handling for held meetings.
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getMeeting,
  updateMeeting,
  markMeetingHeld,
  deleteMeeting,
  listAgendaItems,
  createAgendaItem,
  updateAgendaItem,
  deleteAgendaItem,
  listResolutions,
  createResolution,
  deleteResolution,
  enterIntoRegister,
  meetingProtocolPdfUrl,
  listProperties,
} from "../api/endpoints";
import type {
  Meeting,
  AgendaItem,
  Resolution,
  ResolutionCreate,
  Property,
} from "../api/types";
import { ApiError } from "../api/client";

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

const RESULT_LABELS: Record<string, string> = {
  angenommen: "Angenommen",
  abgelehnt: "Abgelehnt",
};

const RESULT_CSS: Record<string, string> = {
  angenommen: "badge--confirmed",
  abgelehnt: "badge--rejected",
};

function emptyResolutionForm(meetingId: string): ResolutionCreate {
  return {
    meeting_id: meetingId,
    wording: "",
    votes_yes: 0,
    votes_no: 0,
    votes_abstain: 0,
    result: "angenommen",
    agenda_item_id: null,
    votes_yes_mea: null,
    votes_no_mea: null,
    votes_abstain_mea: null,
    majority_type: null,
  };
}

export function MeetingDetailPage() {
  const { meetingId } = useParams<{ meetingId: string }>();

  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [agendaItems, setAgendaItems] = useState<AgendaItem[]>([]);
  const [resolutions, setResolutions] = useState<Resolution[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Editing states
  const [editingProtocol, setEditingProtocol] = useState(false);
  const [protocolDraft, setProtocolDraft] = useState("");
  const [editingInvitation, setEditingInvitation] = useState(false);
  const [invitationDraft, setInvitationDraft] = useState("");

  // TOP creation
  const [showTopForm, setShowTopForm] = useState(false);
  const [topTitle, setTopTitle] = useState("");
  const [topDescription, setTopDescription] = useState("");
  const [topSaving, setTopSaving] = useState(false);

  // TOP editing
  const [editingTopId, setEditingTopId] = useState<string | null>(null);
  const [editTopTitle, setEditTopTitle] = useState("");
  const [editTopDescription, setEditTopDescription] = useState("");

  // Resolution creation
  const [showResolutionForm, setShowResolutionForm] = useState(false);
  const [resForm, setResForm] = useState<ResolutionCreate>(
    emptyResolutionForm(""),
  );
  const [resSaving, setResSaving] = useState(false);
  const [showMeaFields, setShowMeaFields] = useState(false);

  // Register transfer tracking (to disable button after success)
  const [registeredResolutions, setRegisteredResolutions] = useState<
    Set<string>
  >(new Set());

  const reload = useCallback(() => {
    if (!meetingId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      getMeeting(meetingId),
      listAgendaItems(meetingId),
      listResolutions(meetingId),
      listProperties(),
    ])
      .then(([m, a, r, p]) => {
        setMeeting(m);
        setAgendaItems(a);
        setResolutions(r);
        setProperties(p);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [meetingId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const isPlanned = meeting?.status === "planned";
  const isHeld = meeting?.status === "held";

  function getPropertyName(propertyId: string): string {
    const prop = properties.find((p) => p.id === propertyId);
    return prop ? prop.name : "-";
  }

  // --- Meeting update helpers ---

  async function saveProtocol() {
    if (!meetingId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await updateMeeting(meetingId, {
        protocol_text: protocolDraft,
      });
      setMeeting(updated);
      setEditingProtocol(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: Aenderung nicht moeglich. Nur das Protokoll kann nach Durchfuehrung bearbeitet werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function saveInvitation() {
    if (!meetingId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await updateMeeting(meetingId, {
        invitation_text: invitationDraft,
      });
      setMeeting(updated);
      setEditingInvitation(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: Nach Durchfuehrung der Versammlung kann der Einladungstext nicht mehr geaendert werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function handleMarkHeld() {
    if (!meetingId) return;
    const confirmed = window.confirm(
      "Versammlung als durchgefuehrt markieren? Danach koennen nur noch das Protokoll bearbeitet werden. " +
        "TOPs und Beschluesse koennen nicht mehr geaendert werden.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const updated = await markMeetingHeld(meetingId);
      setMeeting(updated);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError("Fehler: Die Versammlung wurde bereits als durchgefuehrt markiert.");
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function handleDelete() {
    if (!meetingId) return;
    const confirmed = window.confirm(
      "Versammlung loeschen? Nur geplante Versammlungen koennen geloescht werden.",
    );
    if (!confirmed) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await deleteMeeting(meetingId);
      window.location.href = "/versammlungen";
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: Durchgefuehrte Versammlungen koennen nicht geloescht werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  function handleDownloadPdf() {
    if (!meetingId) return;
    window.open(meetingProtocolPdfUrl(meetingId), "_blank");
  }

  // --- Agenda item (TOP) handlers ---

  async function handleCreateTop(e: React.FormEvent) {
    e.preventDefault();
    if (!meetingId) return;
    setTopSaving(true);
    setActionError(null);
    try {
      await createAgendaItem(meetingId, {
        meeting_id: meetingId,
        title: topTitle,
        description: topDescription || null,
      });
      setTopTitle("");
      setTopDescription("");
      setShowTopForm(false);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: TOPs koennen nach Durchfuehrung der Versammlung nicht mehr hinzugefuegt werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setTopSaving(false);
    }
  }

  function startEditTop(item: AgendaItem) {
    setEditingTopId(item.id);
    setEditTopTitle(item.title);
    setEditTopDescription(item.description ?? "");
  }

  async function saveEditTop() {
    if (!editingTopId) return;
    setActionLoading(true);
    setActionError(null);
    try {
      const body: Record<string, string | null> = {};
      body.title = editTopTitle;
      body.description = editTopDescription || null;
      await updateAgendaItem(editingTopId, body);
      setEditingTopId(null);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: TOPs koennen nach Durchfuehrung der Versammlung nicht mehr bearbeitet werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function handleDeleteTop(itemId: string) {
    const confirmed = window.confirm("TOP loeschen?");
    if (!confirmed) return;
    setActionError(null);
    try {
      await deleteAgendaItem(itemId);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: TOPs koennen nach Durchfuehrung der Versammlung nicht mehr geloescht werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    }
  }

  // --- Resolution handlers ---

  function openResolutionForm(agendaItemId?: string) {
    if (!meetingId) return;
    setResForm({
      ...emptyResolutionForm(meetingId),
      agenda_item_id: agendaItemId ?? null,
    });
    setShowMeaFields(false);
    setShowResolutionForm(true);
    setActionError(null);
  }

  async function handleCreateResolution(e: React.FormEvent) {
    e.preventDefault();
    setResSaving(true);
    setActionError(null);
    try {
      await createResolution(resForm);
      setShowResolutionForm(false);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: Beschluesse koennen nach Durchfuehrung nicht mehr hinzugefuegt werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setResSaving(false);
    }
  }

  async function handleDeleteResolution(resId: string) {
    const confirmed = window.confirm("Beschluss loeschen?");
    if (!confirmed) return;
    setActionError(null);
    try {
      await deleteResolution(resId);
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: Beschluesse koennen nach Durchfuehrung nicht mehr geloescht werden.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    }
  }

  async function handleRegisterTransfer(resolutionId: string) {
    setActionError(null);
    setActionLoading(true);
    try {
      await enterIntoRegister(resolutionId);
      setRegisteredResolutions((prev) => new Set(prev).add(resolutionId));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(
          "Fehler: Dieser Beschluss wurde bereits in die Beschluss-Sammlung uebernommen.",
        );
      } else if (err instanceof ApiError) {
        setActionError(err.message);
      } else {
        setActionError(String(err));
      }
    } finally {
      setActionLoading(false);
    }
  }

  // --- Helpers ---

  function getTopTitle(agendaItemId: string | null): string {
    if (!agendaItemId) return "(kein TOP zugeordnet)";
    const item = agendaItems.find((a) => a.id === agendaItemId);
    return item ? `TOP ${item.item_number}: ${item.title}` : "-";
  }

  function resolutionsForTop(topId: string): Resolution[] {
    return resolutions.filter((r) => r.agenda_item_id === topId);
  }

  function resolutionsWithoutTop(): Resolution[] {
    return resolutions.filter((r) => !r.agenda_item_id);
  }

  // --- Render ---

  if (loading) return <p>Lade...</p>;
  if (error && !meeting) return <p className="error-text">{error}</p>;
  if (!meeting) return <p className="error-text">Versammlung nicht gefunden.</p>;

  return (
    <div>
      <p style={{ marginBottom: "0.5rem" }}>
        <Link to="/versammlungen" className="link">
          &larr; Alle Versammlungen
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
          Versammlung {meeting.meeting_date}{" "}
          <span
            className={`badge ${STATUS_CSS[meeting.status] ?? ""}`}
            style={{ fontSize: "0.8rem", verticalAlign: "middle" }}
          >
            {STATUS_LABELS[meeting.status] ?? meeting.status}
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

      {/* Held notice */}
      {isHeld && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#e8f5e9",
            borderColor: "#a5d6a7",
          }}
        >
          Diese Versammlung ist als durchgefuehrt markiert.
          TOPs und Beschluesse sind nicht mehr aenderbar.
          Nur das Protokoll kann noch bearbeitet werden.
        </div>
      )}

      {/* Overview card */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2>Uebersicht</h2>
        <table className="info-table">
          <tbody>
            <tr>
              <td>Objekt</td>
              <td>{getPropertyName(meeting.property_id)}</td>
            </tr>
            <tr>
              <td>Datum</td>
              <td>{meeting.meeting_date}</td>
            </tr>
            <tr>
              <td>Typ</td>
              <td>
                {MEETING_TYPE_LABELS[meeting.meeting_type] ??
                  meeting.meeting_type}
              </td>
            </tr>
            <tr>
              <td>Ort</td>
              <td>{meeting.location || "-"}</td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <span className={`badge ${STATUS_CSS[meeting.status] ?? ""}`}>
                  {STATUS_LABELS[meeting.status] ?? meeting.status}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Invitation text */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "0.5rem",
          }}
        >
          <h2 style={{ margin: 0 }}>Einladungstext</h2>
          {isPlanned && !editingInvitation && (
            <button
              className="btn btn--small"
              onClick={() => {
                setInvitationDraft(meeting.invitation_text ?? "");
                setEditingInvitation(true);
              }}
            >
              Bearbeiten
            </button>
          )}
        </div>
        {editingInvitation ? (
          <div>
            <textarea
              rows={5}
              style={{ width: "100%", padding: "0.5rem", fontFamily: "var(--sans)", fontSize: "0.9rem" }}
              value={invitationDraft}
              onChange={(e) => setInvitationDraft(e.target.value)}
            />
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
              <button
                className="btn btn--primary"
                onClick={saveInvitation}
                disabled={actionLoading}
              >
                Speichern
              </button>
              <button
                className="btn"
                onClick={() => setEditingInvitation(false)}
              >
                Abbrechen
              </button>
            </div>
          </div>
        ) : (
          <p style={{ whiteSpace: "pre-wrap", color: meeting.invitation_text ? undefined : "var(--text-muted)" }}>
            {meeting.invitation_text || "Kein Einladungstext hinterlegt."}
          </p>
        )}
      </div>

      {/* Protocol text */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "0.5rem",
          }}
        >
          <h2 style={{ margin: 0 }}>Protokoll (Niederschrift)</h2>
          {!editingProtocol && (
            <button
              className="btn btn--small"
              onClick={() => {
                setProtocolDraft(meeting.protocol_text ?? "");
                setEditingProtocol(true);
              }}
            >
              Bearbeiten
            </button>
          )}
        </div>
        {editingProtocol ? (
          <div>
            <textarea
              rows={8}
              style={{ width: "100%", padding: "0.5rem", fontFamily: "var(--sans)", fontSize: "0.9rem" }}
              value={protocolDraft}
              onChange={(e) => setProtocolDraft(e.target.value)}
            />
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
              <button
                className="btn btn--primary"
                onClick={saveProtocol}
                disabled={actionLoading}
              >
                Speichern
              </button>
              <button
                className="btn"
                onClick={() => setEditingProtocol(false)}
              >
                Abbrechen
              </button>
            </div>
          </div>
        ) : (
          <p style={{ whiteSpace: "pre-wrap", color: meeting.protocol_text ? undefined : "var(--text-muted)" }}>
            {meeting.protocol_text || "Noch kein Protokoll erfasst."}
          </p>
        )}
      </div>

      {/* Agenda Items (TOPs) */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "0.75rem",
          }}
        >
          <h2 style={{ margin: 0 }}>Tagesordnungspunkte (TOPs)</h2>
          {isPlanned && (
            <button
              className="btn btn--small"
              onClick={() => setShowTopForm(true)}
            >
              + TOP hinzufuegen
            </button>
          )}
        </div>

        {showTopForm && (
          <form
            onSubmit={handleCreateTop}
            style={{
              marginBottom: "1rem",
              padding: "0.75rem",
              background: "var(--bg-content)",
              borderRadius: "4px",
            }}
            className="form-grid"
          >
            <label>
              Titel *
              <input
                type="text"
                required
                value={topTitle}
                onChange={(e) => setTopTitle(e.target.value)}
                placeholder="z.B. Genehmigung der Jahresabrechnung"
              />
            </label>
            <label>
              Beschreibung
              <input
                type="text"
                value={topDescription}
                onChange={(e) => setTopDescription(e.target.value)}
              />
            </label>
            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={topSaving}
              >
                {topSaving ? "Speichern..." : "TOP anlegen"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setShowTopForm(false)}
              >
                Abbrechen
              </button>
            </div>
          </form>
        )}

        {agendaItems.length === 0 ? (
          <p className="empty-hint">Noch keine Tagesordnungspunkte angelegt.</p>
        ) : (
          <div>
            {agendaItems.map((item) => (
              <div
                key={item.id}
                style={{
                  padding: "0.75rem",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                {editingTopId === item.id ? (
                  <div className="form-grid">
                    <label>
                      Titel *
                      <input
                        type="text"
                        value={editTopTitle}
                        onChange={(e) => setEditTopTitle(e.target.value)}
                      />
                    </label>
                    <label>
                      Beschreibung
                      <input
                        type="text"
                        value={editTopDescription}
                        onChange={(e) => setEditTopDescription(e.target.value)}
                      />
                    </label>
                    <div className="form-actions">
                      <button
                        className="btn btn--primary btn--small"
                        onClick={saveEditTop}
                        disabled={actionLoading}
                      >
                        Speichern
                      </button>
                      <button
                        className="btn btn--small"
                        onClick={() => setEditingTopId(null)}
                      >
                        Abbrechen
                      </button>
                    </div>
                  </div>
                ) : (
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      gap: "0.5rem",
                    }}
                  >
                    <div>
                      <strong>TOP {item.item_number}:</strong> {item.title}
                      {item.description && (
                        <div
                          style={{
                            fontSize: "0.85rem",
                            color: "var(--text-muted)",
                            marginTop: "0.15rem",
                          }}
                        >
                          {item.description}
                        </div>
                      )}
                      {/* Resolutions under this TOP */}
                      {resolutionsForTop(item.id).length > 0 && (
                        <div style={{ marginTop: "0.5rem" }}>
                          {resolutionsForTop(item.id).map((res) => (
                            <div
                              key={res.id}
                              style={{
                                padding: "0.5rem",
                                marginTop: "0.25rem",
                                background: "#fafafa",
                                borderRadius: "4px",
                                border: "1px solid var(--border)",
                                fontSize: "0.85rem",
                              }}
                            >
                              <div style={{ marginBottom: "0.25rem" }}>
                                <span className={`badge ${RESULT_CSS[res.result] ?? ""}`}>
                                  {RESULT_LABELS[res.result] ?? res.result}
                                </span>
                              </div>
                              <div style={{ whiteSpace: "pre-wrap" }}>{res.wording}</div>
                              <div
                                style={{
                                  marginTop: "0.25rem",
                                  fontSize: "0.8rem",
                                  color: "var(--text-muted)",
                                }}
                              >
                                Ja: {res.votes_yes} / Nein: {res.votes_no} / Enthaltung: {res.votes_abstain}
                                {res.votes_yes_mea && (
                                  <span> | MEA Ja: {res.votes_yes_mea}</span>
                                )}
                                {res.votes_no_mea && (
                                  <span> | MEA Nein: {res.votes_no_mea}</span>
                                )}
                                {res.votes_abstain_mea && (
                                  <span> | MEA Enthaltung: {res.votes_abstain_mea}</span>
                                )}
                                {res.majority_type && (
                                  <span> | Mehrheitsart: {res.majority_type}</span>
                                )}
                              </div>
                              <div
                                style={{
                                  display: "flex",
                                  gap: "0.5rem",
                                  marginTop: "0.4rem",
                                }}
                              >
                                {isHeld && (
                                  <button
                                    className="btn btn--small"
                                    disabled={
                                      actionLoading ||
                                      registeredResolutions.has(res.id)
                                    }
                                    onClick={() =>
                                      handleRegisterTransfer(res.id)
                                    }
                                  >
                                    {registeredResolutions.has(res.id)
                                      ? "Uebernommen"
                                      : "In Beschluss-Sammlung"}
                                  </button>
                                )}
                                {isPlanned && (
                                  <button
                                    className="btn btn--small"
                                    style={{ color: "#b71c1c" }}
                                    onClick={() =>
                                      handleDeleteResolution(res.id)
                                    }
                                  >
                                    Loeschen
                                  </button>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <div
                      style={{
                        display: "flex",
                        gap: "0.25rem",
                        flexShrink: 0,
                      }}
                    >
                      {isPlanned && (
                        <>
                          <button
                            className="btn btn--small"
                            onClick={() => openResolutionForm(item.id)}
                          >
                            + Beschluss
                          </button>
                          <button
                            className="btn btn--small"
                            onClick={() => startEditTop(item)}
                          >
                            Bearbeiten
                          </button>
                          <button
                            className="btn btn--small"
                            style={{ color: "#b71c1c" }}
                            onClick={() => handleDeleteTop(item.id)}
                          >
                            Loeschen
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Resolutions without TOP */}
      {resolutionsWithoutTop().length > 0 && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>Beschluesse ohne TOP-Zuordnung</h2>
          {resolutionsWithoutTop().map((res) => (
            <div
              key={res.id}
              style={{
                padding: "0.5rem",
                marginBottom: "0.5rem",
                background: "#fafafa",
                borderRadius: "4px",
                border: "1px solid var(--border)",
                fontSize: "0.85rem",
              }}
            >
              <div style={{ marginBottom: "0.25rem" }}>
                <span className={`badge ${RESULT_CSS[res.result] ?? ""}`}>
                  {RESULT_LABELS[res.result] ?? res.result}
                </span>
              </div>
              <div style={{ whiteSpace: "pre-wrap" }}>{res.wording}</div>
              <div
                style={{
                  marginTop: "0.25rem",
                  fontSize: "0.8rem",
                  color: "var(--text-muted)",
                }}
              >
                Ja: {res.votes_yes} / Nein: {res.votes_no} / Enthaltung: {res.votes_abstain}
                {res.votes_yes_mea && <span> | MEA Ja: {res.votes_yes_mea}</span>}
                {res.votes_no_mea && <span> | MEA Nein: {res.votes_no_mea}</span>}
                {res.votes_abstain_mea && <span> | MEA Enthaltung: {res.votes_abstain_mea}</span>}
                {res.majority_type && <span> | Mehrheitsart: {res.majority_type}</span>}
              </div>
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.4rem" }}>
                {isHeld && (
                  <button
                    className="btn btn--small"
                    disabled={actionLoading || registeredResolutions.has(res.id)}
                    onClick={() => handleRegisterTransfer(res.id)}
                  >
                    {registeredResolutions.has(res.id)
                      ? "Uebernommen"
                      : "In Beschluss-Sammlung"}
                  </button>
                )}
                {isPlanned && (
                  <button
                    className="btn btn--small"
                    style={{ color: "#b71c1c" }}
                    onClick={() => handleDeleteResolution(res.id)}
                  >
                    Loeschen
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Resolution creation form */}
      {showResolutionForm && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2>Neuen Beschluss erfassen</h2>
          <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
            Zu: {getTopTitle(resForm.agenda_item_id ?? null)}
          </p>
          <form onSubmit={handleCreateResolution} className="form-grid">
            <label className="form-full-width">
              Beschlusswortlaut *
              <textarea
                rows={3}
                required
                value={resForm.wording}
                onChange={(e) =>
                  setResForm({ ...resForm, wording: e.target.value })
                }
                placeholder="Der genaue Wortlaut des Beschlusses"
              />
            </label>
            <label>
              Stimmen Ja *
              <input
                type="number"
                min="0"
                required
                value={resForm.votes_yes}
                onChange={(e) =>
                  setResForm({
                    ...resForm,
                    votes_yes: parseInt(e.target.value, 10) || 0,
                  })
                }
              />
            </label>
            <label>
              Stimmen Nein *
              <input
                type="number"
                min="0"
                required
                value={resForm.votes_no}
                onChange={(e) =>
                  setResForm({
                    ...resForm,
                    votes_no: parseInt(e.target.value, 10) || 0,
                  })
                }
              />
            </label>
            <label>
              Enthaltungen *
              <input
                type="number"
                min="0"
                required
                value={resForm.votes_abstain}
                onChange={(e) =>
                  setResForm({
                    ...resForm,
                    votes_abstain: parseInt(e.target.value, 10) || 0,
                  })
                }
              />
            </label>
            <label>
              Ergebnis *
              <select
                required
                value={resForm.result}
                onChange={(e) =>
                  setResForm({ ...resForm, result: e.target.value })
                }
              >
                <option value="angenommen">Angenommen</option>
                <option value="abgelehnt">Abgelehnt</option>
              </select>
            </label>
            <label>
              Mehrheitsart
              <input
                type="text"
                value={resForm.majority_type ?? ""}
                onChange={(e) =>
                  setResForm({
                    ...resForm,
                    majority_type: e.target.value || null,
                  })
                }
                placeholder="z.B. einfache Mehrheit"
              />
            </label>
            <label>
              TOP-Zuordnung
              <select
                value={resForm.agenda_item_id ?? ""}
                onChange={(e) =>
                  setResForm({
                    ...resForm,
                    agenda_item_id: e.target.value || null,
                  })
                }
              >
                <option value="">(keinem TOP zugeordnet)</option>
                {agendaItems.map((a) => (
                  <option key={a.id} value={a.id}>
                    TOP {a.item_number}: {a.title}
                  </option>
                ))}
              </select>
            </label>

            {/* MEA toggle */}
            <div className="form-full-width">
              <button
                type="button"
                className="btn btn--small"
                style={{ marginBottom: "0.5rem" }}
                onClick={() => setShowMeaFields(!showMeaFields)}
              >
                {showMeaFields
                  ? "MEA-Stimmen ausblenden"
                  : "MEA-Stimmen anzeigen (optional)"}
              </button>
            </div>

            {showMeaFields && (
              <>
                <label>
                  MEA Ja
                  <input
                    type="text"
                    inputMode="decimal"
                    value={resForm.votes_yes_mea ?? ""}
                    onChange={(e) =>
                      setResForm({
                        ...resForm,
                        votes_yes_mea: e.target.value || null,
                      })
                    }
                    placeholder="z.B. 520.00"
                  />
                </label>
                <label>
                  MEA Nein
                  <input
                    type="text"
                    inputMode="decimal"
                    value={resForm.votes_no_mea ?? ""}
                    onChange={(e) =>
                      setResForm({
                        ...resForm,
                        votes_no_mea: e.target.value || null,
                      })
                    }
                    placeholder="z.B. 180.00"
                  />
                </label>
                <label>
                  MEA Enthaltung
                  <input
                    type="text"
                    inputMode="decimal"
                    value={resForm.votes_abstain_mea ?? ""}
                    onChange={(e) =>
                      setResForm({
                        ...resForm,
                        votes_abstain_mea: e.target.value || null,
                      })
                    }
                    placeholder="z.B. 300.00"
                  />
                </label>
              </>
            )}

            <div className="form-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={resSaving}
              >
                {resSaving ? "Speichern..." : "Beschluss erfassen"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setShowResolutionForm(false)}
              >
                Abbrechen
              </button>
            </div>
          </form>
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
        {isPlanned && (
          <>
            <button
              className="btn btn--primary"
              onClick={handleMarkHeld}
              disabled={actionLoading}
            >
              {actionLoading
                ? "Markieren..."
                : "Als durchgefuehrt markieren"}
            </button>
            <button
              className="btn btn--small"
              onClick={() => openResolutionForm()}
            >
              + Beschluss (ohne TOP)
            </button>
            <button
              className="btn"
              onClick={handleDelete}
              disabled={actionLoading}
              style={{ color: "#b71c1c" }}
            >
              Versammlung loeschen
            </button>
          </>
        )}
        <button className="btn" onClick={handleDownloadPdf}>
          Niederschrift-PDF
        </button>
      </div>
    </div>
  );
}
