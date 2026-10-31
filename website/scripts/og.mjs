import { readFile, mkdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { chromium } from 'playwright-core';

const root = new URL('../', import.meta.url);
const executablePath = process.env.HEADLESS_CHROMIUM || `${homedir()}/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-x64/chrome-headless-shell`;
const template = await readFile(new URL('og.html', import.meta.url), 'utf8');
const font = async (name, weight) => (await readFile(new URL(`node_modules/@fontsource/${name}/files/${name}-latin-${weight}-normal.woff2`, root))).toString('base64');
const fonts = { displayFont: await font('barlow-condensed', 700), bodyFont: await font('ibm-plex-sans', 400), monoFont: await font('ibm-plex-mono', 400) };
const browser = await chromium.launch({ executablePath });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  await page.route('**/*', route => route.abort());
  await mkdir(new URL('public/og/', root), { recursive: true });
  for (const lang of ['de', 'en']) {
    const copy = JSON.parse(await readFile(new URL(`src/i18n/${lang}.json`, root), 'utf8'));
    const data = { ...fonts, lang, scope: copy['hero.eyebrow'], line1: copy['hero.1'], line2: copy['hero.2'], line3: copy['hero.3'], intro: copy['page.landing.description'], statement: copy['visual.statement'], water: copy['visual.water'], clean: copy['visual.clean'], heat: copy['visual.heat'], stamp: copy['visual.stamp'], trust: copy['s.trust.1'], soon: copy['host.domain'] };
    const html = template.replace(/\{\{(\w+)\}\}/g, (_, key) => data[key]);
    await page.setContent(html);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: new URL(`public/og/verwalino-${lang}.png`, root).pathname });
    console.log(`Rendered OG ${lang}: 1200 × 630`);
  }
} finally {
  await browser.close();
}
