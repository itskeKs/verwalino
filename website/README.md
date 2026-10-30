# Verwalino public website

A static, bilingual Astro 5 website, deployed independently of the property-management app.
German lives at `/`, English at `/en/`. No client framework, analytics, cookies, external fonts
or runtime third-party requests are used. The only browser scripts control the theme and mobile
menu. The theme preference is stored locally when storage is available; language is determined
by the URL. FAQ entries use native `details` elements.

## Develop and build

Use Node 22.12 or later and npm:

```sh
cd website
npm ci
npm run dev
npm run build
npm run preview
```

Development and preview bind to `127.0.0.1:4321`. `npm run build` writes static files to `dist/`.
There is no database, Docker requirement or connection to the application stack.

Set `SITE_URL` to the chosen public **origin**, including the protocol, before building:

```sh
SITE_URL=https://verwalino.example npm run build
```

The default is `https://verwalino.example`. Paths, credentials, queries and fragments are rejected.
Canonical URLs, language alternates, Open Graph URLs, sitemap URLs and `robots.txt` all use this
origin. The app CTA uses the planned placeholder `https://app.verwalino.de`, explicitly labelled
“demnächst” / “coming soon”; registration is not open.

## Structure and languages

- `src/content/routes.ts`: shared route names and translated paths, including error pages.
- `src/i18n/de.json` and `src/i18n/en.json`: flat translation dictionaries. Add every key to both;
  `t(locale)` provides typed access without client-side translation code. In running text, wrap
  flags, file names, paths and URLs in backticks; `Rich.astro` renders them as non-wrapping `<code>`.
- `src/components/Page.astro`: selects one shared page component for both languages.
- `src/pages/[...path].astro`: generates the ordinary routes. The two small error-page entries
  reuse the same component and layout; German builds to `404.html`, English to `en/404/index.html`.
- `src/layouts/Site.astro`: shared header, footer, metadata, local fonts and vanilla controls.
- `src/styles/`: the approved Kontor Signal tokens, square geometry and responsive rules.
- `src/content/operator.ts`: supplied provider details for the final legal notice.
- `src/content/drafts.ts`: shared legal-document outlines and operator checklist keys.
- `public/`: favicon, touch icon and committed share images.

Every page links to its corresponding page in the other language, with `html lang`, `de` / `en`
/ `x-default` alternates, a canonical URL and localized Open Graph metadata. The sitemap integration
uses the translated route table so different slugs remain paired. Error pages are excluded from
indexing. Fonts are Latin-subset WOFF2 files from the three Fontsource packages, using swap;
the bold display face is preloaded.

## Checks

```sh
npm run lint
npm run check:i18n
npm run build
npm run check:links
```

`lint` runs `astro check`. `check:i18n` requires matching non-empty dictionary keys and matching backtick pairs.
`check:links` parses every built HTML document with parse5, verifies internal `href`, `src` and
`srcset` targets and HTML fragments, and fails on any external URL outside a short allowlist
(`https://app.verwalino.de`) or on `mailto:`/`tel:` links outside the imprint pages; it never
fetches external URLs. It accepts an optional
directory argument for fixtures. It reads the built canonical origin, so it also checks absolute
internal URLs after a build with a custom `SITE_URL`. CI runs these checks using Node 22 for
changes to this directory or its workflow. CI builds and checks files; no deployment is configured.

From the repository root, `make lint` and `make docs-check` remain the existing gates.
The website does not change backend or app behaviour; `make test` is unaffected.

## Icons

The outlined [brand assets](../docs/assets/brand/README.md) are the source for the icons.
Regenerate them with `npm run icons`. Commit `public/favicon.svg` (adapts to the system
colour scheme), `public/favicon-16.png` and `public/favicon-32.png` (ink on transparent),
`public/favicon.ico` (both PNG sizes), and `public/apple-touch-icon.png` (180 × 180 opaque
ink tile with the paper mark, matching the app-icon SVG). The renderer uses the same local
Chromium binary and `HEADLESS_CHROMIUM` override as the share-image renderer below.
It blocks network requests, verifies the ICO directory and PNG entries, and adds no dependency.

## Regenerate share images

The committed DE and EN PNGs are 1200 × 630 pixels.
Social cards use the light palette and do not adapt to dark mode.
The template in `scripts/og.html` and `scripts/og.mjs` render them using local fonts with all
network requests blocked:

```sh
npm run og
# Use a different existing Chromium binary if necessary:
HEADLESS_CHROMIUM=/absolute/path/to/chrome-headless-shell npm run og
```

The default binary is
`~/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-x64/chrome-headless-shell`.
The renderer uses `playwright-core`; it does not download a browser. Rebuild after regenerating
images. Review and commit the resulting `public/og/` PNGs.

## Legal publication boundary

The legal notice contains the supplied final provider data. Its English translation states that
the German version is authoritative. Privacy, terms of use and the Article 28 GDPR data processing
agreement are **unreviewed drafts**. Each begins with a visible warning and uses only bracketed
placeholders under its section headings. The boxed checklist at the end of **each draft page**
is driven by `src/content/drafts.ts` and translated in the dictionaries.

Before publication of the hosted service, the operator must supply the hosting provider and
location, all sub-processors (including email and backup), retention and erasure periods,
technical and organisational measures, the contact for data-protection requests, a decision about
a data-protection officer, and legal review. No provider, retention period or availability promise
is invented here. The current browser setup exists in the checkout; its public release acceptance
is still pending, as shown in the self-hosting release list.

The repository is private. The self-hosting page links to an in-page repository notice naming
`DEPLOY.md` and “Quellcode: Veröffentlichung geplant” / “Source code: publication planned”, rather
than offering an unavailable source URL. Public repository links await publication.

## Deploy static files

Upload the contents of `dist/` to any static host. Configure missing paths to return an HTTP 404
with `404.html`, and missing paths below `/en/` to return `en/404/index.html`. Do not use an SPA
fallback to the home page. The domain decision, hosting and DNS are **not configured**. Set the
final `SITE_URL`, rebuild, and arrange the host separately. The draft pages remain visible drafts
until reviewed; a successful build is not approval to launch the hosted service.

Example Caddy configuration for a separate static host (replace the root and placeholder domain):

```caddyfile
verwalino.example {
    root * /srv/verwalino-website/dist
    encode zstd gzip
    header {
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
        X-Frame-Options "DENY"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        Strict-Transport-Security "max-age=31536000"
        Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    }
    try_files {path} {path}/index.html =404
    file_server
    handle_errors {
        @english path /en /en/*
        handle @english {
            rewrite * /en/404/index.html
            file_server
        }
        handle {
            rewrite * /404.html
            file_server
        }
    }
}
```

The inline allowances cover Astro's inlined styles and the tiny theme/menu scripts. All resources
still originate from this site. This is a deployment example, not an applied server configuration.

## Deferred operator work

Domain/DNS and static-host setup, hosted registration and quotas, public source/image/installer
release, portable clean-host acceptance and final legal review remain outside this website task.
The self-hosting page lists the planned release tools separately from the commands available today.
