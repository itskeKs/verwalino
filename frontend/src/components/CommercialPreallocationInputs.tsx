import type { CommercialPreallocationIn } from '../api/types';
import { commercialRuleValidation } from '../lib/commercialPreallocation';

export function CommercialPreallocationInputs({ options, rules, onChange, disabled, attempted }: {
  options: { id: string; label: string }[]; rules: CommercialPreallocationIn[];
  onChange: (rules: CommercialPreallocationIn[]) => void; disabled: boolean; attempted: boolean;
}) {
  function update(id: string, values: Partial<CommercialPreallocationIn>) {
    const old = rules.find(rule => rule.cost_entry_id === id);
    onChange([...rules.filter(rule => rule.cost_entry_id !== id), { cost_entry_id: id,
      commercial_share_percent: null, commercial_amount: null, commercial_reason: null, ...old, ...values }]);
  }
  return <section className="card" aria-label="Vorwegabzug Gewerbe" style={{ marginBottom: '1rem' }}>
    <h2>Vorwegabzug Gewerbe</h2>
    <p>Je Kostenposition einen begründeten Gewerbeanteil oder Festbetrag festlegen. Der Rest geht nach demselben Schlüssel an die übrigen teilnehmenden Einheiten; Leerstand bleibt beim Eigentümer.</p>
    <p>„Berechnen“ speichert die Regel für alle Mietabrechnungen dieser Kostenposition. „Vorschau“ speichert sie nicht. Nach der ersten Finalisierung ist sie gesperrt.</p>
    {options.map(option => {
      const rule = rules.find(rule => rule.cost_entry_id === option.id);
      const mode = rule?.commercial_share_percent != null ? 'percent' : rule?.commercial_amount != null ? 'fixed' : 'none';
      const error = attempted && rule ? commercialRuleValidation(rule) : null;
      const errorId = `commercial-${option.id}-error`;
      return <fieldset key={option.id} disabled={disabled} style={{ minWidth: 0, marginBottom: '1rem' }}>
        <legend>{option.label}</legend>
        <div className="form-grid">
          <label>Vorwegabzug<select aria-label="Vorwegabzug" value={mode} onChange={event => update(option.id, {
            commercial_share_percent: event.target.value === 'percent' ? '' : null,
            commercial_amount: event.target.value === 'fixed' ? '' : null,
            commercial_reason: event.target.value === 'none' ? null : rule?.commercial_reason ?? '',
          })}><option value="none">Kein Vorwegabzug</option><option value="percent">Prozent</option><option value="fixed">Festbetrag</option></select></label>
          {mode !== 'none' && rule && <label>{mode === 'percent' ? 'Gewerbeanteil (%)' : 'Festbetrag (EUR)'}<input type="text" inputMode="decimal"
            value={(mode === 'percent' ? rule.commercial_share_percent : rule.commercial_amount) ?? ''}
            aria-invalid={error?.field === 'value'} aria-describedby={error?.field === 'value' ? errorId : undefined}
            onChange={event => update(option.id, mode === 'percent' ? { commercial_share_percent: event.target.value } : { commercial_amount: event.target.value })} /></label>}
        </div>
        {mode !== 'none' && rule && <div className="form-grid" style={{ marginTop: '0.75rem' }}><label style={{ gridColumn: '1 / -1' }}>Begründung<textarea value={rule.commercial_reason ?? ''} maxLength={2000} rows={3}
          aria-invalid={error?.field === 'reason'} aria-describedby={error?.field === 'reason' ? errorId : undefined}
          onChange={event => update(option.id, { commercial_reason: event.target.value })} /></label></div>}
        {error && <p id={errorId} className="error-text">{error.message}</p>}
      </fieldset>;
    })}
  </section>;
}
