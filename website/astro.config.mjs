import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import { routes } from './src/content/routes.ts';

const site = process.env.SITE_URL || 'https://verwalino.example';
const base = new URL(site);
if (!['https:', 'http:'].includes(base.protocol) || base.pathname !== '/' || base.search || base.hash || base.username || base.password) {
  throw new Error('SITE_URL must be an HTTP(S) origin without a path, query, fragment or credentials');
}

export default defineConfig({
  site: base.origin,
  output: 'static',
  trailingSlash: 'always',
  build: { inlineStylesheets: 'always' },
  integrations: [sitemap({
    i18n: { defaultLocale: 'de', locales: { de: 'de-DE', en: 'en-GB' } },
    filter: page => !new URL(page).pathname.includes('/404'),
    serialize(item) {
      const paths = Object.values(routes).find(paths => Object.values(paths).includes(new URL(item.url).pathname));
      if (paths) item.links = [
        { lang: 'de', url: new URL(paths.de, base).href },
        { lang: 'en', url: new URL(paths.en, base).href },
        { lang: 'x-default', url: new URL(paths.de, base).href },
      ];
      return item;
    },
  })],
});
