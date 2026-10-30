import de from './de.json';
import en from './en.json';
import { operator } from '../content/operator';

export type Locale = 'de' | 'en';
export type TranslationKey = keyof typeof de;
const dictionaries = { de, en };
export function t(locale: Locale) {
  return (key: TranslationKey): string => dictionaries[locale][key].replace(
    /\{operator\.(\w+)\}/g,
    (placeholder, field: keyof typeof operator) => operator[field] ?? placeholder,
  );
}

/** Splits a string at backtick pairs; odd parts are code fragments (flags, files, paths, URLs). */
export function richParts(value: string): { code: boolean; text: string }[] {
  return value.split('`').map((text, index) => ({ code: index % 2 === 1, text })).filter(part => part.text);
}
