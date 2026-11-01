import { useEffect, useState } from 'react';
import { listCostEntries, listRegisterEntries } from '../api/endpoints';
import type { CostEntry, RegisterEntry } from '../api/types';

export function ReserveEvidenceFields({ propertyId, cost, register, onChange, required = true }: {
  propertyId?: string; cost: string; register: string;
  onChange: (cost: string, register: string) => void; required?: boolean;
}) {
  const [sources, setSources] = useState<{ costs: CostEntry[]; register: RegisterEntry[] } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setSources(null); setError('');
    if (propertyId) Promise.all([listCostEntries({ property_id: propertyId, status: 'confirmed' }), listRegisterEntries(propertyId)])
      .then(([costs, register]) => { if (active) setSources({ costs, register }); })
      .catch(() => { if (active) setError('Belege konnten nicht geladen werden. Bitte öffnen Sie das Formular erneut.'); });
    return () => { active = false; };
  }, [propertyId]);
  return <div className="form-grid form-full-width reserve-evidence-fields" data-reserve-evidence>
    <p className="form-full-width">Bitte verknüpfen Sie vor dem Buchen einen bestätigten Kostenbeleg und einen angenommenen Beschluss aus der Beschluss-Sammlung.</p>
    {error && <p role="alert" className="error-text form-full-width">{error}</p>}
    <label>Bestätigter Kostenbeleg{required ? ' *' : ''}<select aria-label={`Bestätigter Kostenbeleg${required ? ' *' : ''}`} required={required} disabled={!sources} value={cost} onChange={e => onChange(e.target.value, register)}>
      <option value="">Bitte wählen</option>{sources?.costs.map(row => <option key={row.id} value={row.id}>{row.document_number || row.invoice_date.split('-').reverse().join('.')} · {new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(Number(row.gross_amount))}</option>)}
    </select></label>
    <label>Beschluss-Sammlung{required ? ' *' : ''}<select aria-label={`Beschluss-Sammlung${required ? ' *' : ''}`} required={required} disabled={!sources} value={register} onChange={e => onChange(cost, e.target.value)}>
      <option value="">Bitte wählen</option>{sources?.register.map(row => <option key={row.id} value={row.id}>Nr. {row.sequence_number} · {row.wording.slice(0, 120)}</option>)}
    </select></label>
  </div>;
}
