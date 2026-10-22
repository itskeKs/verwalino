import type { HeatingBaseKey, HeatingConfig, HeatingConsumptionCandidate } from "../api/types";
import { fractionValid, seventy, sliceKey, useHeatingCandidates } from "../lib/heatingInputs";

export interface HeatingCostOption { id: string; label: string; kind: HeatingConfig["kind"] }

export function HeatingInputs({ options, configs, onChange, candidates, disabled }: {
  options: HeatingCostOption[]; configs: HeatingConfig[]; onChange: (configs: HeatingConfig[]) => void;
  candidates: ReturnType<typeof useHeatingCandidates>; disabled: boolean;
}) {
  function update(config: HeatingConfig, patch: Partial<HeatingConfig>) {
    onChange(configs.map(current => current.cost_entry_id === config.cost_entry_id ? { ...config, ...patch } : current));
  }
  function editSlice(config: HeatingConfig, row: HeatingConsumptionCandidate, field: "consumption" | "base_quantity", value: string) {
    const identity = { unit_id: row.unit_id, period_from: row.period_from, period_to: row.period_to };
    const previous = config.slices.find(slice => sliceKey(slice) === sliceKey(row));
    const next = { ...identity, ...previous };
    if (value.trim()) next[field] = value; else delete next[field];
    update(config, { slices: [...config.slices.filter(slice => sliceKey(slice) !== sliceKey(row)), next]
      .filter(slice => slice.consumption !== undefined || slice.base_quantity !== undefined) });
  }
  return <section className="card" aria-label="Interne Heizkostenverordnung" style={{ marginBottom: "1rem" }}>
    <h2>Interne HeizkostenV</h2>
    <p>Je Kostenposition entweder interne Verteilung oder externer Messdienst. Interne Heizwerte sind unabhängig von Verbrauch / Personenzahl.</p>
    {options.map(option => {
      const config = configs.find(current => current.cost_entry_id === option.id);
      const source = config ? candidates.sourceFor(config) : undefined;
      return <fieldset key={option.id} disabled={disabled} style={{ marginBottom: "1rem" }}>
        <legend>{option.label}</legend>
        <label><input type="checkbox" checked={Boolean(config)} onChange={event => onChange(event.target.checked
          ? [...configs, { cost_entry_id: option.id, kind: option.kind, consumption_fraction: "", base_key: "AREA", consumption_recorded: true,
            mandatory_seventy_percent: false, meter_type: "", slices: [] }]
          : configs.filter(current => current.cost_entry_id !== option.id))} />Intern nach HeizkostenV abrechnen: {option.label}</label>
        {config && <>
          <p>{config.kind === "HEATING" ? "Heizung" : "Warmwasser"} · Externe Messdienstbeträge für diese Position werden nicht gesendet.</p>
          <div className="form-grid">
            <label>Verbrauchsanteil (0,50–0,70)<input type="text" inputMode="decimal" value={config.consumption_fraction}
              onChange={event => update(config, { consumption_fraction: event.target.value })} /></label>
            <label>Grundkostenschlüssel<select value={config.base_key} onChange={event => update(config, { base_key: event.target.value as HeatingBaseKey, slices: [] })}>
              <option value="AREA">Wohnfläche (gespeicherte Einheitsbasis)</option><option value="HEATED_AREA">Beheizte Fläche (gespeicherte Einheitsbasis)</option>
              <option value="VOLUME">Volumen (manuell je Zeitraum)</option>
            </select></label>
            <label>Zählertyp<input type="text" list={`heating-meter-${option.id}`} value={config.meter_type}
              onChange={event => update(config, { meter_type: event.target.value, slices: [] })} /></label>
            <datalist id={`heating-meter-${option.id}`}><option value="HEIZUNG" /><option value="WARMWASSER" /></datalist>
            <label><input type="checkbox" checked={config.consumption_recorded} onChange={event => update(config, {
              consumption_recorded: event.target.checked, slices: config.slices.map(slice => { const next = { ...slice }; delete next.consumption; return next; }),
            })} />Verbrauch erfasst</label>
            {config.kind === "HEATING" && <label><input type="checkbox" checked={config.mandatory_seventy_percent}
              onChange={event => update(config, { mandatory_seventy_percent: event.target.checked })} />§ 7 Sonderfall: zwingend 70 % Verbrauchsanteil</label>}
          </div>
          <p>Verbrauchsanteil und §-7-Sonderfall bewusst wählen; es werden keine Gebäudeeigenschaften angenommen. Zählertyp ausdrücklich auswählen oder eingeben.</p>
          {!fractionValid(config.consumption_fraction) && <p>Verbrauchsanteil zwischen 0,50 und 0,70 eingeben.</p>}
          {config.mandatory_seventy_percent && !seventy(config.consumption_fraction) && <p>Der gewählte §-7-Sonderfall erfordert genau 0,70.</p>}
          {!config.consumption_recorded && <p role="note">Ohne Verbrauchserfassung: 15 % Kürzung des Mieteranteils. Keine manuellen Verbrauchswerte in diesem Modus.</p>}
          {!config.meter_type.trim() && <p>Bitte Zählertyp wählen, um die Zeiträume zu laden.</p>}
          {config.meter_type.trim() && (!source || source.loading) && <p>Heizbasis wird geladen …</p>}
          {source?.error && <p role="alert">{source.error} <button type="button" className="btn" onClick={candidates.retry}>Heizbasis erneut laden</button></p>}
          {source && !source.loading && !source.error && !source.rows.length && <p role="alert">Keine Heizzeiträume verfügbar. <button type="button" className="btn" onClick={candidates.retry}>Heizbasis erneut laden</button></p>}
          {source && !source.loading && !source.error && source.rows.length > 0 && <>
            <p>Zeiträume umfassen Mieterwechsel und Leerstand. Automatische Zählerwerte werden beim Berechnen frisch geprüft; nur Handänderungen werden gesendet.
              Leeres Feld verwirft eine Handänderung. {config.base_key === "VOLUME" ? "Volumen muss für jeden Zeitraum manuell eingetragen werden." : "Grundmengen stammen aus der gespeicherten Einheitsbasis; Handänderungen überschreiben diese ausdrücklich."}</p>
            <div style={{ overflowX: "auto" }}><table className="data-table"><thead><tr><th>Einheit / Zeitraum</th><th>Grundmenge</th>{config.consumption_recorded && <th>Verbrauch</th>}<th>Quelle / Hinweis</th></tr></thead>
              <tbody>{source.rows.map(row => {
                const override = config.slices.find(slice => sliceKey(slice) === sliceKey(row));
                const label = `${row.unit_label} ${row.period_from}–${row.period_to}`;
                return <tr key={sliceKey(row)}><td>{label}</td><td>
                  <input aria-label={`Grundmenge ${label}`} type="text" inputMode="decimal" value={override?.base_quantity ?? (config.base_key === "VOLUME" ? "" : row.base_quantity ?? "")}
                    onChange={event => editSlice(config, row, "base_quantity", event.target.value)} />
                  <small>{override?.base_quantity !== undefined ? "Manuelle Grundmenge" : config.base_key === "VOLUME" ? "Handeingabe erforderlich" : "Gespeicherte Einheitsbasis"}</small>
                </td>{config.consumption_recorded && <td><input aria-label={`Heizverbrauch ${label}`} type="text" inputMode="decimal" value={override?.consumption ?? row.consumption ?? ""}
                  onChange={event => editSlice(config, row, "consumption", event.target.value)} /><small>{override?.consumption !== undefined ? "Manueller Verbrauch" : "Automatische Zählerbasis"}</small></td>}
                <td>{row.meter_id && <span>Zähler: {row.meter_id}</span>}{row.reason && <p>{row.reason}</p>}</td></tr>;
              })}</tbody></table></div>
            {config.slices.some(slice => !source.rows.some(row => sliceKey(row) === sliceKey(slice))) && <p role="alert">Gespeicherte Handwerte passen nicht mehr zu den Zeiträumen. <button type="button" onClick={() => update(config, { slices: [] })}>Heizhandwerte zurücksetzen</button></p>}
          </>}
        </>}
      </fieldset>;
    })}
  </section>;
}
