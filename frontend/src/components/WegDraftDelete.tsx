import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { deleteWegDraft } from '../api/endpoints';
import type { WegRun } from '../api/types';

/** Shared deletion action for the two WEG report families. */
export function WegDraftDelete({ run, disabled = false }: { run: WegRun; disabled?: boolean }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  if (run.status !== 'draft') return null;

  async function remove() {
    if (!window.confirm('Diesen leeren Entwurf endgültig löschen?')) return;
    setBusy(true); setError(null);
    try {
      await deleteWegDraft(run.id, run.run_type === 'VERMOEGENSBERICHT');
      if (active.current) navigate(run.run_type === 'VERMOEGENSBERICHT' ? '/vermoegensberichte' : '/weg-abrechnungen');
    } catch (err) {
      if (active.current) {
        setError(err instanceof ApiError ? err.message : 'Der Entwurf konnte nicht gelöscht werden.');
        if (err instanceof ApiError && err.status === 409) setRefused(true);
      }
    } finally {
      if (active.current) setBusy(false);
    }
  }

  return <div className="card" style={{ marginBottom: '1rem' }}>
    {run.delete_restriction ? <p>{run.delete_restriction.message}</p>
      : !refused && <button className="btn btn--danger" disabled={disabled || busy} onClick={remove}>
        {busy ? 'Wird gelöscht…' : 'Entwurf löschen'}
      </button>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
