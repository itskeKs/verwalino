# Verwalino public website

A static, bilingual Astro 5 website, deployed independently of the property-management app.
German lives at `/`, English at `/en/`. No client framework, analytics, cookies, external fonts
or runtime third-party requests are used. The only browser scripts control the theme and mobile
menu. The theme preference is stored locally when storage is available; language is determined
by the URL. FAQ entries use native `details` elements.

[DESIGN.md](../DESIGN.md) is the app's frontend contract and does not cover `website/`.

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

The public **origin** is `https://verwalino.de`. `SITE_URL` can set it explicitly when building:

```sh
SITE_URL=https://verwalino.de npm run build
```

The default is `https://verwalino.de`. Paths, credentials, queries and fragments are rejected.
Canonical URLs, language alternates, Open Graph URLs, sitemap URLs and `robots.txt` all use this
origin. The sign-up page shows a disabled “demnächst” / “coming soon” button without a link;
registration is not open.

## Structure and languages

- `src/content/routes.ts`: shared route names and translated paths, including error pages.
- `src/i18n/de.json` and `src/i18n/en.json`: flat translation dictionaries. Add every key to both;
  `t(locale)` provides typed access and fills `{operator.field}` placeholders from `operator.ts`
  without client-side translation code. In running text, wrap
  flags, file names, paths and URLs in backticks; `Rich.astro` renders them as non-wrapping `<code>`.
- `src/components/Page.astro`: selects one shared page component for both languages.
- `src/pages/[...path].astro`: generates the ordinary routes. The two small error-page entries
  reuse the same component and layout; German builds to `404.html`, English to `en/404/index.html`.
- `src/layouts/Site.astro`: shared header, footer, metadata, local fonts and vanilla controls.
- `public/theme.js`, `public/controls.js`: external local scripts for theme and menu;
  the hosting CSP needs no inline script allowance.
- `src/styles/`: the approved Kontor Signal tokens, square geometry and responsive rules.
- `src/content/operator.ts`: supplied provider details for the final legal notice.
- `src/content/repository.ts`: shared public GitHub repository URL.
- `src/content/drafts.ts`: shared legal-document outlines and operator checklist keys.
- `public/`: favicon, touch icon and committed share images.

Every page links to its corresponding page in the other language, with `html lang`, `de` / `en`
/ `x-default` alternates, a canonical URL and localized Open Graph metadata. The sitemap integration
uses the translated route table so different slugs remain paired. Error pages are excluded from
indexing and the sitemap, as are privacy, terms and DPA drafts in both languages until legal
review (#26); the imprint remains indexable. Fonts are Latin-subset WOFF2 files from the three
Fontsource packages, using swap; the bold display face is preloaded.

The [desktop screenshot](../docs/assets/website/landing-de-1280.png) shows the German landing page at 1280 px.
The [mobile screenshot](../docs/assets/website/landing-de-360.png) shows the German landing page at 360 px.

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
(`https://app.verwalino.de` and the shared repository URL) or on `mailto:`/`tel:` links outside the imprint pages; it never
fetches external URLs. It accepts an optional
directory argument for fixtures. It reads the built canonical origin, so it also checks absolute
internal URLs after a build with a custom `SITE_URL`. CI runs these checks using Node 22 for
changes to this directory or its workflow. PRs run checks; a main push also builds, publishes and
signs the site image, then deploys it through the existing hosted forced-command key.

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

`HEADLESS_CHROMIUM` overrides the macOS default `~/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-x64/chrome-headless-shell`.
The renderer uses `playwright-core`; it does not download a browser. Rebuild after regenerating
images. Review and commit the resulting `public/og/` PNGs.

## Legal publication boundary

The legal notice contains the final fogu UG (haftungsbeschränkt) provider data supplied in #26:
address, managing director, register court/number, VAT ID, email and supervisory authority.
The earlier telephone number was not supplied in that decision and is omitted.
Its English translation states that
the German version is authoritative. Privacy, terms of use and the Article 28 GDPR data processing
agreement are **unreviewed drafts**. Each begins with a visible warning and uses only bracketed
placeholders under its section headings. The boxed checklist at the end of **each draft page**
is driven by `src/content/drafts.ts` and translated in the dictionaries.

Before publication of the hosted service, the operator must supply the hosting provider and
location, all sub-processors (including email and backup), retention and erasure periods,
technical and organisational measures, a decision about a data-protection officer, and legal
review, including the configured contact for data-protection requests. The draft pages use
`datenschutz@verwalino.de`; the public contact in the legal notice is `hallo@verwalino.de`.
No provider, retention period or availability promise is invented here. The current browser setup
exists in the checkout; its public release acceptance is still pending, as shown in the self-hosting
release list.

The repository `itskeKs/verwalino` is public on GitHub under AGPL-3.0. The landing page,
self-hosting page and footer link to it using the shared URL in `src/content/repository.ts`.
`DEPLOY.md` in the repository contains the full self-hosting guide. The public container image
and clean-host installation are not yet released.

## Container deployment

The [Website workflow](../.github/workflows/website.yml) publishes
`ghcr.io/itskeks/verwalino-site:<full-git-sha>` and `:main` on matching main pushes. It builds
amd64 and arm64, attaches BuildKit provenance/SBOMs and signs the index with keyless cosign.
The build stage runs every check above. The final image uses the pinned Caddy 2.10.2 static
binary on pinned Alpine, serves only `dist/` as uid 10001 on port 8080, and has no Node/npm or sources.

The hosted operator enables [the optional compose override](../infra/docker-compose.site.yml)
and imports [the public Caddy snippet](../infra/Caddyfile.site) from an operator-owned config.
Caddy proxies the apex to the closed site container and redirects `www` with 308. The site has
its own internal network, a read-only root, dropped capabilities, no privilege escalation,
no published ports, host mounts or credentials, and a 1 MiB `/data` tmpfs for Caddy housekeeping. Scripts and fonts
are local; inline scripts are forbidden. Astro's inline styles remain permitted.

See [hosted deployment](../DEPLOY.md#website-container) and the
[website hosting runbook](../docs/operations/website-hosting.md) for setup, exact network layout,
digest verification, troubleshooting and `site <previous-sha>` rollback. Site deployments recreate
only the site container and probe HTTP 200 over its network. The generic installer is unchanged.
The server's reviewed deployment files and operator opt-in must exist before CI can deploy;
this change does not perform that rollout.

Missing paths return HTTP 404 using `404.html`, or `en/404/index.html` under `/en/`, without an
SPA fallback. The public origin is `https://verwalino.de`. Legal draft pages remain visibly
marked until #26 review; a successful build does not approve opening hosted registration.

## Deferred operator work

DNS, enabling the hosted extension, hosted registration and quotas, public app image/installer
release, portable clean-host acceptance and final legal review remain operator work.
The self-hosting page lists planned release tools separately from commands available today.
