import type { CorrectionImpact } from '../../api/allocationAdoptions';

export function correctionDestination(item: CorrectionImpact) {
  const base = item.kind === 'economic_plan' ? 'wirtschaftsplaene' : item.run_type === 'WEG_JAHRESABRECHNUNG' ? 'weg-abrechnungen' : item.run_type === 'VERMOEGENSBERICHT' ? 'vermoegensberichte' : 'abrechnungen';
  return `/${base}/${item.id}`;
}
