import type { CommercialPreallocationIn } from '../api/types';
import { parseGermanAmount } from './germanAmount';

export function commercialRuleValidation(rule: CommercialPreallocationIn) {
  if (rule.commercial_share_percent === null && rule.commercial_amount === null) return null;
  if (!rule.commercial_reason?.trim()) return { field: 'reason', message: 'Vorwegabzug Gewerbe: Bitte geben Sie eine Begründung an.' };
  const percent = rule.commercial_share_percent !== null;
  const amount = parseGermanAmount((percent ? rule.commercial_share_percent : rule.commercial_amount) ?? '');
  if (amount === null || amount.startsWith('-') || (percent && BigInt(amount.replace('.', '')) > 10000n)) {
    return { field: 'value', message: percent
      ? 'Vorwegabzug Gewerbe: Bitte geben Sie einen Prozentsatz zwischen 0 und 100 mit höchstens zwei Nachkommastellen an.'
      : 'Vorwegabzug Gewerbe: Bitte geben Sie einen gültigen, nichtnegativen EUR-Betrag ein.' };
  }
  return null;
}

export function commercialRulesPayload(rules: CommercialPreallocationIn[]): CommercialPreallocationIn[] {
  return rules.map(rule => {
    const error = commercialRuleValidation(rule);
    if (error) throw new Error(error.message);
    return { ...rule,
      commercial_share_percent: rule.commercial_share_percent !== null ? parseGermanAmount(rule.commercial_share_percent) : null,
      commercial_amount: rule.commercial_amount !== null ? parseGermanAmount(rule.commercial_amount) : null };
  });
}
