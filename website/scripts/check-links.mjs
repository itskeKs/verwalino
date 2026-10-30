import { readFile, readdir, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, relative, sep } from 'node:path';
import { parse } from 'parse5';

// External targets the site may link to. Anything else fails the check.
const allowedExternal = ['https://app.verwalino.de/', 'https://app.verwalino.de'];
const allowedMail = 'mailto:hello@fogu.app';
const contactFiles = ['impressum', 'legal-notice'];
const dist = resolve(process.argv[2] || fileURLToPath(new URL('../dist/', import.meta.url)));
async function filesIn(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(entry => entry.isDirectory()
    ? filesIn(resolve(directory, entry.name)) : [resolve(directory, entry.name)]));
  return nested.flat();
}
const allFiles = await filesIn(dist);
// Only HTML is scanned: XML namespace URLs in sitemap*.xml are not links and must not be treated as such.
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
  // Anchors are element ids and the name of <a> elements only; <meta name="description"> is not an anchor.
  const ids = new Set(nodes.flatMap((node, index) => [attrs[index].id, node.tagName === 'a' ? attrs[index].name : undefined]
    .filter(Boolean)));
  documents.set(file, { attrs, ids });
}
const origins = new Set([...documents.values()].flatMap(({ attrs }) => attrs
  .filter(attr => attr.rel === 'canonical' && attr.href)
  .map(attr => new URL(attr.href).origin)));
const origin = [...origins][0] || 'https://verwalino.example';
const external = new Set();
// URL of every candidate in a srcset value: split on commas, keep the URL before the width/density descriptor.
const srcsetUrls = value => value.split(',').map(candidate => candidate.trim().split(/\s+/)[0]).filter(Boolean);
const failures = [];
let checked = 0;
async function exists(file) { try { return (await stat(file)).isFile(); } catch { return false; } }
for (const [file, { attrs }] of documents) {
  const source = '/' + relative(dist, file).split(sep).join('/');
  const base = new URL(source, origin);
  for (const attr of attrs) {
    const references = [attr.href, attr.src,
      ...[attr.srcset, attr.imagesrcset].filter(value => value !== undefined).flatMap(srcsetUrls)]
      .filter(value => value !== undefined);
    for (const value of references) {
      if (/^data:/i.test(value)) continue;
      if (/^(mailto:|tel:)/i.test(value)) {
        external.add(value);
        const contactPage = contactFiles.some(name => source.includes(name));
        if (!contactPage) failures.push(`${source}: ${value.split(':')[0]}: link only allowed on the imprint page`);
        else if (/^mailto:/i.test(value) && value !== allowedMail) failures.push(`${source}: mailto address not allowed ${value}`);
        continue;
      }
      let url;
      try { url = new URL(value, base); } catch { failures.push(`${source}: invalid URL ${value}`); continue; }
      if (!['https:', 'http:'].includes(url.protocol)) { failures.push(`${source}: unsupported URL ${value}`); continue; }
      if (!origins.has(url.origin) && url.origin !== origin) {
        external.add(value);
        if (!allowedExternal.includes(value)) failures.push(`${source}: external URL not in allowlist ${value}`);
        continue;
      }
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
console.log(`External URLs (allowlist checked; no requests):\n${[...external].sort().join('\n') || '(none)'}`);
if (failures.length) { console.error(failures.join('\n')); process.exitCode = 1; }
else console.log(`links: ${htmlFiles.length} HTML files, ${checked} internal references checked`);
