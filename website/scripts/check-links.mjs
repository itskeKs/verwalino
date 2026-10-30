import { readFile, readdir, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, relative, sep } from 'node:path';
import { parse } from 'parse5';

const dist = resolve(process.argv[2] || fileURLToPath(new URL('../dist/', import.meta.url)));
async function filesIn(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(entry => entry.isDirectory()
    ? filesIn(resolve(directory, entry.name)) : [resolve(directory, entry.name)]));
  return nested.flat();
}
const allFiles = await filesIn(dist);
const htmlFiles = allFiles.filter(file => file.endsWith('.html'));
if (!htmlFiles.length) throw new Error('No built HTML files found; run npm run build first');
const documents = new Map();
function elements(node, result = []) {
  if (node.tagName) result.push(node);
  for (const child of node.childNodes || []) elements(child, result);
  if (node.content) elements(node.content, result);
  return result;
}
for (const file of htmlFiles) {
  const nodes = elements(parse(await readFile(file, 'utf8')));
  const attrs = nodes.map(node => Object.fromEntries(node.attrs.map(attr => [attr.name, attr.value])));
  documents.set(file, { attrs, ids: new Set(attrs.flatMap(attr => [attr.id, attr.name].filter(Boolean))) });
}
const origins = new Set([...documents.values()].flatMap(({ attrs }) => attrs
  .filter(attr => attr.rel === 'canonical' && attr.href)
  .map(attr => new URL(attr.href).origin)));
const origin = [...origins][0] || 'https://verwalino.example';
const external = new Set();
const failures = [];
let checked = 0;
async function exists(file) { try { return (await stat(file)).isFile(); } catch { return false; } }
for (const [file, { attrs }] of documents) {
  const source = '/' + relative(dist, file).split(sep).join('/');
  const base = new URL(source, origin);
  for (const attr of attrs) {
    for (const value of [attr.href, attr.src].filter(value => value !== undefined)) {
      if (/^(mailto:|tel:|data:)/i.test(value)) { if (!value.startsWith('data:')) external.add(value); continue; }
      let url;
      try { url = new URL(value, base); } catch { failures.push(`${source}: invalid URL ${value}`); continue; }
      if (!['https:', 'http:'].includes(url.protocol)) { failures.push(`${source}: unsupported URL ${value}`); continue; }
      if (!origins.has(url.origin) && url.origin !== origin) { external.add(url.href); continue; }
      let pathname;
      try { pathname = decodeURIComponent(url.pathname); } catch { failures.push(`${source}: invalid encoded path ${value}`); continue; }
      const target = resolve(dist, '.' + pathname);
      if (target !== dist && !target.startsWith(dist + sep)) { failures.push(`${source}: path outside dist ${value}`); continue; }
      const candidates = [target, resolve(target, 'index.html'), target + '.html'];
      let found;
      for (const candidate of candidates) { if (await exists(candidate)) { found = candidate; break; } }
      if (!found) { failures.push(`${source}: missing target ${value}`); continue; }
      checked++;
      if (url.hash && documents.has(found)) {
        let id;
        try { id = decodeURIComponent(url.hash.slice(1)); } catch { failures.push(`${source}: invalid encoded fragment ${value}`); continue; }
        // The browser's top-of-document fragment needs no matching element.
        if (id && id !== 'top' && !documents.get(found).ids.has(id)) failures.push(`${source}: missing anchor ${value}`);
      }
    }
  }
}
console.log(`External URLs (listed only; no requests):\n${[...external].sort().join('\n') || '(none)'}`);
if (failures.length) { console.error(failures.join('\n')); process.exitCode = 1; }
else console.log(`links: ${htmlFiles.length} HTML files, ${checked} internal references checked`);
