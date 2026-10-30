import { defineConfig } from 'astro/config';

export default defineConfig({
  site: process.env.SITE_URL || 'https://verwalino.example',
  output: 'static',
  trailingSlash: 'always',
  build: { inlineStylesheets: 'always' },
});
