/**
 * Mandate -- Verwaltungsmandate (Verwalteraufträge) der Organisation.
 *
 * Liste + Anlegen (nur OWNER_ADMIN). Ein Mandat ist der Auftrag/Vertrag, unter
 * dem die Verwaltung für einen Auftraggeber tätig wird (WEG / Miet / SEV).
 */

import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { Plus } from "lucide-react";
import { listMandates, createMandate } from "../api/endpoints";
import type { Mandate, MandateCreate } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Card, CardContent } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge, type BadgeTone } from "../components/ui/badge";

const TYPE_LABELS: Record<Mandate["mandate_type"], string> = {
  WEG: "WEG-Verwaltung",
  MIET: "Mietverwaltung",
  SEV: "Sondereigentum (SEV)",
};

const TYPE_TONES: Record<Mandate["mandate_type"], BadgeTone> = {
  WEG: "primary",
  MIET: "success",
  SEV: "warning",
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function emptyForm(): MandateCreate {
  return { mandate_type: "WEG", label: "", start_date: todayIso(), end_date: null };
}

export function MandatePage() {
  const { session } = useAuth();
  const isAdmin = session?.role === "OWNER_ADMIN";

  const [mandates, setMandates] = useState<Mandate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<MandateCreate>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    listMandates()
      .then(setMandates)
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  function openCreate() {
    setForm(emptyForm());
    setFormError(null);
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setFormError(null);
    try {
      await createMandate({ ...form, end_date: form.end_date || null });
      setShowForm(false);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="m-0">Mandate</h1>
        {isAdmin && (
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4" />
            Neues Mandat
          </Button>
        )}
      </div>

      <p className="max-w-3xl text-sm text-muted-foreground">
        Ein Mandat ist der Verwaltungsauftrag, unter dem Sie für eine:n
        Auftraggeber:in tätig werden — als <strong>WEG-Verwaltung</strong>,{" "}
        <strong>Mietverwaltung</strong> oder <strong>Sondereigentumsverwaltung</strong>.
        Kostenpositionen und Abrechnungen können einem Mandat zugeordnet werden.
      </p>

      {error && <p className="error-text">{error}</p>}

      {showForm && (
        <Card>
          <CardContent className="p-5">
            <h2>Neues Mandat anlegen</h2>
            {formError && <p className="error-text">{formError}</p>}
            <form onSubmit={handleSubmit} className="form-grid">
              <label>
                Art des Mandats *
                <select
                  required
                  value={form.mandate_type}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      mandate_type: e.target.value as Mandate["mandate_type"],
                    })
                  }
                >
                  <option value="WEG">WEG-Verwaltung</option>
                  <option value="MIET">Mietverwaltung</option>
                  <option value="SEV">Sondereigentumsverwaltung (SEV)</option>
                </select>
              </label>
              <label>
                Bezeichnung
                <input
                  type="text"
                  placeholder="z. B. WEG Hauptstr. 5"
                  value={form.label}
                  onChange={(e) => setForm({ ...form, label: e.target.value })}
                />
              </label>
              <label>
                Beginn *
                <input
                  type="date"
                  required
                  value={form.start_date}
                  onChange={(e) =>
                    setForm({ ...form, start_date: e.target.value || todayIso() })
                  }
                />
              </label>
              <label>
                Ende (optional)
                <input
                  type="date"
                  value={form.end_date ?? ""}
                  onChange={(e) =>
                    setForm({ ...form, end_date: e.target.value || null })
                  }
                />
              </label>
              <div className="form-actions">
                <Button type="submit" disabled={saving}>
                  {saving ? "Speichern…" : "Mandat anlegen"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setShowForm(false)}
                >
                  Abbrechen
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">Lade…</p>
      ) : mandates.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center">
            <p className="text-sm text-muted-foreground">
              Noch keine Mandate angelegt.
              {isAdmin
                ? " Legen Sie oben mit der Schaltfläche Neues Mandat Ihren ersten Verwaltungsauftrag an."
                : ""}
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th className="pl-5">Art</th>
                    <th>Bezeichnung</th>
                    <th>Beginn</th>
                    <th>Ende</th>
                    <th className="pr-5"></th>
                  </tr>
                </thead>
                <tbody>
                  {mandates.map((m) => (
                    <tr key={m.id}>
                      <td className="pl-5">
                        <Badge tone={TYPE_TONES[m.mandate_type]}>
                          {TYPE_LABELS[m.mandate_type]}
                        </Badge>
                      </td>
                      <td className="font-medium">{m.label || "—"}</td>
                      <td className="tabular-nums">{formatDate(m.start_date)}</td>
                      <td className="tabular-nums">
                        {m.end_date ? formatDate(m.end_date) : "laufend"}
                      </td>
                      <td className="pr-5 text-right">
                        <Link to={`/mandate/${m.id}`} className="btn btn--small">
                          Öffnen
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
