import { readFile } from 'node:fs/promises';

const dictionaries = await Promise.all(['de', 'en'].map(async (locale) =>
  JSON.parse(await readFile(new URL(`../src/i18n/${locale}.json`, import.meta.url), 'utf8'))));
const keys = dictionaries.map((dictionary) => Object.keys(dictionary).sort());
const missing = keys.flatMap((list, index) => list.filter((key) =>
  !Object.hasOwn(dictionaries[1 - index], key)).map((key) => `${key} missing in ${index ? 'de' : 'en'}`));
const empty = dictionaries.flatMap((dictionary, index) => Object.entries(dictionary)
  .filter(([, value]) => typeof value !== 'string' || !value.trim())
  .map(([key]) => `${key} is empty in ${index ? 'en' : 'de'}`));
// Backticks mark code fragments (flags, files, paths, URLs) rendered by Rich.astro; they must pair up and match in both languages.
const fragments = (value) => (value.match(/`/g) || []).length;
const markup = Object.keys(dictionaries[0]).filter((key) => Object.hasOwn(dictionaries[1], key)
  && typeof dictionaries[0][key] === 'string' && typeof dictionaries[1][key] === 'string'
  && (fragments(dictionaries[0][key]) % 2 || fragments(dictionaries[0][key]) !== fragments(dictionaries[1][key])))
  .map((key) => `${key} has unbalanced or differing code fragments in de and en`);
if (missing.length || empty.length || markup.length) {
  console.error([...missing, ...empty, ...markup].join('\n'));
  process.exitCode = 1;
} else {
  console.log(`i18n: ${keys[0].length} matching, non-empty keys in de and en`);
}
