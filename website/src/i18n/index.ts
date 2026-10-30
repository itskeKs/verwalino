import de from './de.json';
import en from './en.json';

export type Locale = 'de' | 'en';
export type TranslationKey = keyof typeof de;
const dictionaries = { de, en };
export function t(locale: Locale) {
  return (key: TranslationKey): string => dictionaries[locale][key];
}
