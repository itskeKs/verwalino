/**
 * Resolution register page -- Beschluss-Sammlung (read-only per GoBD/§24 VII).
 *
 * M8: Numbered register entries per WEG property. Only the challenge/revocation
 * annotation (Anfechtungs-/Aufhebungsvermerk) can be edited inline.
 * PDF export for the entire register per property.
 *
 * Register entries are immutable once created -- no deletion, no content edit.
 */

import { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import {
  listRegisterEntries,
  annotateChallenge,
  registerPdfUrl,
  listProperties,
} from "../api/endpoints";
import type { RegisterEntry, Property } from "../api/types";
import { ApiError } from "../api/client";

export function ResolutionRegisterPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [entries, setEntries] = useState<RegisterEntry[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Inline editing state for challenge annotation
  const [editingEntryId, setEditingEntryId] = useState<string | null>(null);
  const [annotationDraft, setAnnotationDraft] = useState("");
  const [annotationSaving, setAnnotationSaving] = useState(false);
  const [annotationError, setAnnotationError] = useState<string | null>(null);

  const propertyFilter = searchParams.get("property_id") ?? "";

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);

    const propertiesPromise = listProperties();

    if (propertyFilter) {
      Promise.all([listRegisterEntries(propertyFilter), propertiesPromise])
        .then(([e, p]) => {
          setEntries(e);
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
    } else {
      propertiesPromise
        .then((p) => {
          setProperties(p);
          setEntries([]);
        })
        .catch((err) => {
          if (err instanceof ApiError && err.status === 401) {
            setError("Nicht authentifiziert. Bitte erneut anmelden.");
          } else {
            setError(String(err));
          }
        })
        .finally(() => setLoading(false));
    }
  }, [propertyFilter]);

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

  /** Only WEG/MISCH properties have a Beschluss-Sammlung */
  const wegProperties = properties.filter(
    (p) => p.management_type === "WEG" || p.management_type === "MISCH",
  );

  function getPropertyName(propertyId: string): string {
    const prop = properties.find((p) => p.id === propertyId);
    return prop ? prop.name : "-";
  }

  // --- Challenge annotation inline edit ---

  function startAnnotationEdit(entry: RegisterEntry) {
    setEditingEntryId(entry.id);
    setAnnotationDraft(entry.challenge_annotation ?? "");
    setAnnotationError(null);
  }

  async function saveAnnotation() {
    if (!editingEntryId) return;
    setAnnotationSaving(true);
    setAnnotationError(null);
    try {
      const updated = await annotateChallenge(editingEntryId, {
        annotation: annotationDraft || null,
      });
      // Update the entry in the local list
      setEntries((prev) =>
        prev.map((e) => (e.id === updated.id ? updated : e)),
      );
      setEditingEntryId(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setAnnotationError(
          "Fehler: Dieser Eintrag kann nicht mehr geaendert werden.",
        );
      } else if (err instanceof ApiError) {
        setAnnotationError(err.message);
      } else {
        setAnnotationError(String(err));
      }
    } finally {
      setAnnotationSaving(false);
    }
  }

  function handleDownloadPdf() {
    if (!propertyFilter) return;
    window.open(registerPdfUrl(propertyFilter), "_blank");
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
        <h1 style={{ margin: 0 }}>Beschluss-Sammlung</h1>
        {propertyFilter && entries.length > 0 && (
          <button className="btn btn--primary" onClick={handleDownloadPdf}>
            Beschluss-Sammlung PDF
          </button>
        )}
      </div>

      {/* Property filter */}
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
          <option value="">-- WEG-Objekt waehlen --</option>
          {wegProperties.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      {/* GoBD/§24 VII notice */}
      <div
        className="card"
        style={{
          marginBottom: "1rem",
          background: "#fff8e1",
          borderColor: "#ffcc02",
          fontSize: "0.85rem",
        }}
      >
        <strong>Hinweis (§ 24 VII WEG):</strong> Die Beschluss-Sammlung ist
        unveraenderbar. Eintraege koennen nicht geloescht oder inhaltlich
        geaendert werden. Nur ein Anfechtungs- oder Aufhebungsvermerk kann
        nachgetragen werden.
      </div>

      {error && <p className="error-text">{error}</p>}
      {annotationError && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            background: "#ffebee",
            borderColor: "#ef9a9a",
          }}
        >
          <strong>Fehler:</strong> {annotationError}
        </div>
      )}

      {!propertyFilter ? (
        <p className="empty-hint">
          Bitte waehlen Sie ein WEG-Objekt, um dessen Beschluss-Sammlung
          anzuzeigen.
        </p>
      ) : loading ? (
        <p>Lade...</p>
      ) : entries.length === 0 ? (
        <p className="empty-hint">
          Keine Eintraege in der Beschluss-Sammlung fuer{" "}
          {getPropertyName(propertyFilter)}.
        </p>
      ) : (
        <div className="card">
          <h2>
            Beschluss-Sammlung: {getPropertyName(propertyFilter)}
          </h2>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: "50px" }}>Nr.</th>
                  <th style={{ width: "100px" }}>Datum</th>
                  <th style={{ width: "120px" }}>Ort</th>
                  <th>Beschlusswortlaut</th>
                  <th style={{ width: "220px" }}>
                    Anfechtungs-/Aufhebungsvermerk
                  </th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.id}>
                    <td
                      style={{
                        fontFamily: "var(--mono)",
                        fontWeight: 600,
                      }}
                    >
                      {entry.sequence_number}
                    </td>
                    <td>{entry.entry_date}</td>
                    <td>{entry.location || "-"}</td>
                    <td style={{ whiteSpace: "pre-wrap" }}>
                      {entry.wording}
                    </td>
                    <td>
                      {editingEntryId === entry.id ? (
                        <div>
                          <textarea
                            rows={3}
                            style={{
                              width: "100%",
                              padding: "0.3rem",
                              fontFamily: "var(--sans)",
                              fontSize: "0.85rem",
                            }}
                            value={annotationDraft}
                            onChange={(e) =>
                              setAnnotationDraft(e.target.value)
                            }
                            placeholder="Vermerk eingeben oder leer lassen zum Entfernen"
                          />
                          <div
                            style={{
                              display: "flex",
                              gap: "0.25rem",
                              marginTop: "0.25rem",
                            }}
                          >
                            <button
                              className="btn btn--small btn--primary"
                              onClick={saveAnnotation}
                              disabled={annotationSaving}
                            >
                              {annotationSaving
                                ? "..."
                                : "Speichern"}
                            </button>
                            <button
                              className="btn btn--small"
                              onClick={() => setEditingEntryId(null)}
                            >
                              Abbrechen
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: "0.25rem",
                          }}
                        >
                          {entry.challenge_annotation ? (
                            <span
                              style={{
                                color: "#b71c1c",
                                fontWeight: 500,
                                fontSize: "0.85rem",
                              }}
                            >
                              {entry.challenge_annotation}
                            </span>
                          ) : (
                            <span
                              style={{
                                color: "var(--text-muted)",
                                fontSize: "0.8rem",
                                fontStyle: "italic",
                              }}
                            >
                              Kein Vermerk
                            </span>
                          )}
                          <button
                            className="btn btn--small"
                            style={{ alignSelf: "flex-start" }}
                            onClick={() => startAnnotationEdit(entry)}
                          >
                            {entry.challenge_annotation
                              ? "Vermerk bearbeiten"
                              : "Vermerk hinzufuegen"}
                          </button>
                        </div>
                      )}
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
