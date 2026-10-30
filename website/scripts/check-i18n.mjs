import { readFile } from 'node:fs/promises';

const dictionaries = await Promise.all(['de', 'en'].map(async (locale) =>
  JSON.parse(await readFile(new URL(`../src/i18n/${locale}.json`, import.meta.url), 'utf8'))));
const keys = dictionaries.map((dictionary) => Object.keys(dictionary).sort());
const missing = keys.flatMap((list, index) => list.filter((key) =>
  !Object.hasOwn(dictionaries[1 - index], key)).map((key) => `${key} missing in ${index ? 'de' : 'en'}`));
const empty = dictionaries.flatMap((dictionary, index) => Object.entries(dictionary)
  .filter(([, value]) => typeof value !== 'string' || !value.trim())
  .map(([key]) => `${key} is empty in ${index ? 'en' : 'de'}`));
if (missing.length || empty.length) {
  console.error([...missing, ...empty].join('\n'));
  process.exitCode = 1;
} else {
  console.log(`i18n: ${keys[0].length} matching, non-empty keys in de and en`);
}
