import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = new URL('../', import.meta.url);
const brand = new URL('../docs/assets/brand/', root);
const output = name => new URL(`public/${name}`, root);
const executablePath = process.env.HEADLESS_CHROMIUM || `${homedir()}/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-x64/chrome-headless-shell`;
const mark = await readFile(new URL('verwalino-mark.svg', brand), 'utf8');
const appIcon = await readFile(new URL('verwalino-app-icon.svg', brand), 'utf8');
const favicon = mark.replace('<path', '<style>@media (prefers-color-scheme:dark){path{fill:#EFF1E9}}</style><path');
await writeFile(output('favicon.svg'), favicon);

const browser = await chromium.launch({ executablePath });
try {
  const page = await browser.newPage({ deviceScaleFactor: 1, colorScheme: 'light' });
  await page.route('**/*', route => route.abort());
  const render = async (svg, size, name, omitBackground) => {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:100vw;height:100vh}</style>${svg}`);
    await page.screenshot({ path: fileURLToPath(output(name)), omitBackground });
    console.log(`Rendered ${name}: ${size} × ${size}`);
  };
  for (const size of [16, 32]) await render(mark, size, `favicon-${size}.png`, true);
  await render(appIcon, 180, 'apple-touch-icon.png', false);
} finally {
  await browser.close();
}

const sizes = [16, 32];
const pngs = await Promise.all(sizes.map(size => readFile(output(`favicon-${size}.png`))));
const directory = Buffer.alloc(6 + 16 * sizes.length);
directory.writeUInt16LE(1, 2);
directory.writeUInt16LE(sizes.length, 4);
let offset = directory.length;
for (const [index, size] of sizes.entries()) {
  const entry = 6 + 16 * index;
  directory.writeUInt8(size, entry);
  directory.writeUInt8(size, entry + 1);
  directory.writeUInt16LE(1, entry + 4);
  directory.writeUInt16LE(32, entry + 6);
  directory.writeUInt32LE(pngs[index].length, entry + 8);
  directory.writeUInt32LE(offset, entry + 12);
  offset += pngs[index].length;
}
await writeFile(output('favicon.ico'), Buffer.concat([directory, ...pngs]));

const ico = await readFile(output('favicon.ico'));
assert.equal(ico.readUInt16LE(0), 0);
assert.equal(ico.readUInt16LE(2), 1);
assert.equal(ico.readUInt16LE(4), 2);
for (const [index, size] of sizes.entries()) {
  const entry = 6 + 16 * index;
  assert.equal(ico.readUInt8(entry), size);
  assert.equal(ico.readUInt8(entry + 1), size);
  const start = ico.readUInt32LE(entry + 12);
  const length = ico.readUInt32LE(entry + 8);
  assert.deepEqual(ico.subarray(start, start + length), pngs[index]);
}
console.log('Verified favicon.ico: reserved 0, type 1, two PNG entries (16 and 32 px)');
