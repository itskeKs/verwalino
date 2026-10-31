# Verwalino design contract

DESIGN.md is the authoritative frontend design contract for Verwalino: layout, tokens, UI kit
components and their behaviour, states, copy and formats. Define visual changes here before CSS
tweaks land. New screens follow this contract; changes to the look or copy update it in the same PR.
Named implementation deviations remain documented below as known debt tracked in #48, #49, #50
and #52. New work follows the contract.

Source files: `frontend/tailwind.config.cjs` (token mapping), `frontend/src/index.css` (tokens, global
classes), `frontend/src/components/ui/` (UI kit), `frontend/src/components/AppShell.tsx`,
`Sidebar.tsx`, `Topbar.tsx` (shell). Copy and format rules also appear in the
[frontend rule](.claude/rules/frontend-ui.md). The current theme is light; dark mode is outside this
contract.

## How to build a screen

Two styling layers coexist:

- **UI kit** (`frontend/src/components/ui/`, Tailwind utilities on the tokens below). The shell, the
  dashboard, mandates and the dialogs are written this way; their file headers call it "UI-Kit".
- **Global classes** in `index.css` (`.card`, `.btn`, `.badge`, `.data-table`, `.info-table`, `.form-grid`,
  `.error-text`, `.empty-hint`, ...). About 430 raw `<button class="btn">` and most list/detail pages use
  them. `index.css` says they were restyled to match the kit, so both layers look the same.

Follow [Components](#components) for the permitted styling layers and their source files, and
[Colour](#colour) for token use.

## Foundations

### Contract ownership and verification

- One fact has one visual place: show a status, result or action on the surface that owns it.
  An equivalent hidden accessible label may repeat it.
- Enforce the contract's behaviour and copy with interaction tests in the area's
  `frontend/src/**/*.test.tsx`; browser features in `features/` verify geometry that jsdom cannot check.
- When the look changes, update this contract and the screenshots in `docs/assets/design/`
  in the same PR.

### Colour

Tokens are HSL channels on `:root` in `index.css`, exposed to Tailwind in `tailwind.config.cjs`
(`bg-primary`, `text-muted-foreground`, `border-border`, ...). Hex values are rounded renderings.

| Token (Tailwind) | HSL | Hex | Role |
|---|---|---|---|
| `background` | 210 20% 98% | `#f9fafb` | page background, inputs, dialog surface |
| `foreground` | 222 24% 17% | `#212736` | body text |
| `card` / `card-foreground` | 0 0% 100% / as foreground | `#ffffff` | cards, topbar, outline buttons |
| `primary` | 221 72% 46% | `#2156ca` | main action, links, focus ring, active hover border |
| `primary-foreground` | 0 0% 100% | `#ffffff` | text on primary |
| `secondary` | 214 24% 95% | `#eff2f5` | default badge, role chip, quiet panels (`bg-secondary/40`) |
| `muted` / `muted-foreground` | 214 24% 95% / 215 16% 47% | `#65758b` text | secondary text, hints, descriptions, table headers |
| `accent` | 214 30% 93% | `#e8ecf3` | hover on outline and ghost buttons, table row hover (50%) |
| `destructive` | 0 72% 50% | `#db2424` | error text, destructive button |
| `border` (`--ui-border`), `input` | 214 20% 89% | `#dde2e9` | all borders and input outlines |
| `ring` | 221 72% 46% | `#2156ca` | keyboard focus ring |
| `sidebar` / `sidebar-foreground` | 222 32% 13% / 214 22% 88% | `#171d2c` / `#dae0e7` | navigation rail |
| `sidebar-accent` | 221 64% 54% | `#3f6ed5` | active nav item, brand tile |

Semantic colours that are **not** tokens: `ui/badge.tsx` and `ui/stat-card.tsx` use the Tailwind palette
(blue = primary/type, emerald = success, amber = warning/attention, red = destructive, slate = info/neutral).
`success` and `warning` exist as tokens but no component uses them. Legacy `.badge--*` classes use fixed
hex pairs. `--border` (hex) and `--ui-border` (HSL) are different variables: the kit uses `--ui-border`.
Legacy variables `--color-primary` (`#2456c8`), `--bg-content`, `--text`, `--text-muted` remain for old
inline styles. Use token colours rather than hard-coded hex values: pick a role from the table;
for a new status colour reuse a `Badge` tone.

### Typography

- Family: Inter Variable, self-hosted through `@fontsource-variable/inter` (imported in `main.tsx`),
  fallback `system-ui`. In `index.css`, monospace (`--mono`) is used for `code`, declaration `pre`
  blocks (`.declaration-value-grid`, `.declaration-rule-details`) and `.candidate-decision textarea`.
  Inline styles also use it for amounts in `OpenItemsPage.tsx` and identifiers in `ResolutionRegisterPage.tsx`.
- The root font size is **15 px** (`:root`), so 1 rem = 15 px and all rem-based Tailwind sizes scale with it.
- Scale as used: page title `h1` 1.5rem / 600 / -0.01em (`index.css`; dashboard uses `text-2xl`);
  section title `h2` 1.05rem / 600; card title `text-base font-semibold`; body 15 px; form labels
  `text-sm` (kit) or 0.82rem / 500 (`.form-grid`); table header 0.72rem uppercase, tracking 0.05em;
  badges `text-xs`; nav group labels 0.68rem uppercase; stat values `text-2xl font-semibold`.
- Numbers in tables and tiles use `tabular-nums`. `-webkit-font-smoothing: antialiased` is global.

### Spacing, radius, elevation

- Spacing is the default Tailwind scale (on the 15 px rem). Page gutter `px-4 py-6`, from `md`
  `px-8 py-8`; content is centred at `max-w-6xl` (72 rem). Vertical rhythm between blocks: `space-y-5`
  or `space-y-6` (kit pages), `1.5rem` bottom margin per legacy card. Card padding `p-5` (kit) or
  `1.25rem 1.4rem` (`.card`). Grids: `gap-4` for stat tiles, `gap-6` for main panels, `.form-grid` gap
  `1rem 1.1rem`.
- Radius: `--radius` = 0.65 rem. `rounded-lg` = radius (cards, tiles), `rounded-md` = radius - 2 px
  (buttons, inputs, nav items), `rounded-sm` = radius - 4 px. Badges and the role chip are `rounded-full`.
- Elevation is flat: cards and primary buttons `shadow-sm` with a 1 px border; a linked `StatCard` gains
  `shadow-md` and `border-primary/40` on hover; dialogs `shadow-lg` over a `bg-black/50` overlay.
  Stacking: mobile nav overlay `z-30`, sidebar `z-40`, dialog and overlay `z-50`.

### Icons

`lucide-react` only; no custom SVG, no emoji. Icons accompany a text label, except the mobile menu
button (`aria-label="Navigation umschalten"`) and the topbar key button below `sm` (`title`). Sizes (rem,
so x 15 px): `h-4 w-4` inside buttons (`Button` forces `[&_svg]:h-4 [&_svg]:w-4`) and in the quick-action
tile, 1.05 rem in the nav, `h-5 w-5` in the `h-10 w-10` `StatCard` tile. Colour follows the text colour.

### Interaction

Every control has a label, and dialogs are keyboard-operable. Mark errors with `role="alert"` and
outcomes with `role="status"`.

Focus: kit controls show `ring-2 ring-ring ring-offset-2`; `.btn` shows a 3 px `ring/30` shadow; form-grid
fields a 3 px `ring/18` shadow with a primary border. Disabled controls use `opacity-50` and
`not-allowed`/`pointer-events-none`. Transitions are colour/shadow only and short (about 0.15 s). Note: `ui/dialog.tsx`
carries `animate-in`/`fade-in` classes, but the `tailwindcss-animate` plugin is not installed
(`plugins: []`), so dialogs do not animate.

## Layout

### Organization quota card

The dashboard shows a kit `Card` named “Kontingent” for the hosted free plan,
with a semantic `h2` and a responsive one/three-column definition list. It shows
“Speicher”, “Objekte” and “Einheiten” as usage / limit, with German number
formatting and binary storage units. At 90% or above, a warning `Badge` says
“Fast ausgeschöpft” and the card says “Ihr Kontingent ist zu mindestens 90 %
belegt.” Errors show “Kontingent nicht verfügbar.” with retry; loading shows
“Kontingent wird geladen …”. The card waits for deployment metadata and requests
usage only in hosted mode. Selfhost renders no quota, loading or error card and
never requests the aggregate. Aggregate
usage is available to every authenticated member, independently of source grants.
The declaration-import failure `ORGANIZATION_AI_BUDGET_EXCEEDED` uses “Das
monatliche KI-Budget ist ausgeschöpft. Bitte wenden Sie sich an den Betreiber.”
Quota refusals carry a stable `detail.code` and German `detail.message`. The shared
API client reads the code for JSON and multipart refusals, exposes it on `ApiError`,
and preserves the German message in existing error surfaces.
Attachment upload errors with status 413 display the backend's German explanation,
including “Speicherkontingent erschöpft …”, while preserving the file and title.
Declaration, intake and attachment file checks read the public metadata file cap,
with 25 MiB as the fallback while metadata is loading or unavailable.

### App shell

`AppShell.tsx`: `flex h-screen overflow-hidden` = **Sidebar** (16 rem) + a column of **Topbar**
(`h-16`, `border-b bg-card`, `px-4 md:px-8`) and `main` (the scroll container; the window itself never
scrolls) with the centred `max-w-6xl` content. Topbar right side: role chip (from `sm`), "Passwort ändern"
(opens the password dialog; label hidden below `sm`), "Abmelden". Signed out, `LoginPage` renders alone
(own `LoginPage.css`, 380 px card); while the session is probed `App.tsx` shows a centred "Lade...".

Below `md` (768 px) the sidebar is off-canvas (`fixed`, slides in via the hamburger, `bg-black/40`
overlay, closes on link click or overlay click); from `md` it is static. `AppShell.css`, `Sidebar.css`
and `Topbar.css` exist but are imported nowhere; the shell is styled with utilities only.

First-run setup opens when `GET /api/setup` reports `required: true`; the backend
owns the deployment decision. Required setup takes precedence over login and
signup without requesting deployment metadata. In `hosted` mode it reports
`required: false` and `/setup` redirects to login. While setup status loads, the
existing "Lade..." state applies; if that check fails, login and signed-in pages stay available and setup
requires a later successful app load. This changes route availability without
adding visual elements.

### Hosted registration

`/signup` and `/signup/confirm` are standalone pages, gated by confirmed hosted
metadata. Selfhost and unavailable metadata redirect to login without submitting.
A mistyped confirmation password shows “Passwort nicht korrekt. Bitte versuchen Sie es erneut.”
and keeps the live token for another attempt (422). Invalid/expired/used links and
throttling return 403 and clear it; success clears it too.
Hosted login adds the text link “Konto erstellen”; selfhost login has no signup link.
Login and signup show "Lade..." while metadata loads; metadata errors leave login available.

Registration uses the setup pattern: centred `max-w-lg` kit `Card`, `p-4 sm:p-6`,
`min-w-0`, a wrapping `h1` “Konto erstellen”, labelled `Input` controls at `h-11`,
and one full-width large primary button. Fields are “Organisation”, “Name”,
“E-Mail”, “Passwort” and “Passwort wiederholen”; passwords explain the 12–128
character requirement. The honeypot is hidden from layout, assistive technology
and keyboard focus. Busy forms retain their inputs and disable their fieldset.
Errors use `role="alert"` and preserve inputs; password mismatch is explained
before submitting. “Zur Anmeldung” remains available.

On acceptance the form is replaced by the server's generic `role="status"` receipt:
“Wenn die Adresse frei ist, haben wir eine E-Mail geschickt”. Passwords are cleared.
A muted note explains checking spam and the ten-minute resend interval. The ghost
button “Erneut senden” requests another link; it shows the same conditional receipt
without claiming delivery. Failure keeps the receipt and offers retry.

Confirmation uses the same card and heading “E-Mail bestätigen”. It asks for the
chosen “Passwort” with `current-password` autocomplete and a large full-width
“E-Mail bestätigen” button. The query token is removed immediately; no request is
sent until explicit submit. The password proves that confirmation activates the
user’s own registration, including after resend. Busy state says “Bestätigung läuft …”;
duplicate submits and StrictMode never create a second POST. The token is saved in
tab-local `sessionStorage` before removing it from the URL, preserving reloads. It
is cleared after success or a 403, and retained for retryable errors. The password
never enters browser storage. Success reloads the dashboard from the session cookie. Failure shows the German server refusal with `role="alert"`
and “Zur Registrierung”. The HTML uses a no-referrer policy. Inputs and receipt
text must wrap without horizontal scrolling at 360 px.

### Password recovery

Login shows “Passwort vergessen?” only when public metadata confirms mail is
available, in either mode. `/passwort-vergessen` uses the same centred `max-w-lg`
kit card and `h-11` labelled inputs as signup, with “E-Mail” and a honeypot hidden
from layout, keyboard and assistive technology. “Link anfordern” submits once;
busy state disables the fieldset and says “Anfrage läuft …”. The form becomes a
conditional `role="status"` receipt that does not claim an account exists or mail
was delivered. It explains the one-hour expiry and checking spam. Failed requests
preserve the address and show `role="alert"`. “Zur Anmeldung” stays available.

`/passwort-zuruecksetzen` uses the same card, with “Neues Passwort”, “Passwort
wiederholen”, a 12–128 character hint and “Passwort speichern”. Password mismatch
is explained before submitting. The query token moves to tab-local sessionStorage
and is removed from the URL; reloads keep it and no GET or effect submits it.
Success clears passwords and stored token, shows “Ihr Passwort wurde geändert.
Alle bisherigen Sitzungen wurden beendet.” and a “Zur Anmeldung” link that reloads
authentication state. There is no automatic login. A 403 clears the token; retryable
errors and policy 422 responses retain it. Password length is validated by the
backend in Unicode code points; “Das Passwort muss zwischen 12 und 128 Zeichen
lang sein.” appears in the alert without browser UTF-16 length constraints. A
malformed request address shows “Bitte geben Sie eine gültige E-Mail-Adresse ein.”
and preserves the entered address. Errors offer “Neuen Link anfordern”. Fields retain useful inputs,
use `new-password` autocomplete and remain labelled and usable at 360 px.

### Sidebar groups

Source: `GROUPS` in `Sidebar.tsx`. Active item `bg-sidebar-accent text-sidebar-accent-foreground`,
idle `text-sidebar-foreground/75`, hover `bg-white/5`; group label 0.68 rem uppercase at 40 % opacity.
Detail pages (`/objekte/:id`, `/mandate/:id`, ...) have no nav entry; the parent stays highlighted.

| Group | Entries | Visible to |
|---|---|---|
| Übersicht | Dashboard, Aufgaben, Briefe, Fristenmonitor, Berichte, Mandate | all |
| Stammdaten | Objekte, Personen | all |
| Kosten & Belege | Kostenpositionen, Belege, Posteingang, Klärungsfälle | all |
| Abrechnungen | Betriebskosten | all |
| WEG | Sonderumlagen, Wirtschaftspläne, WEG-Abrechnungen, Vermögensberichte | all |
| Versammlungen | Versammlungen, Beschluss-Sammlung | all |
| Zahlungen | Bankkonten, Zahlungen, Mietkonto, Kautionen, Offene Posten, Mahnungen | all |
| Verwaltung | Benutzer | `OWNER_ADMIN` |
| Erweitert | Altquellen, Berechtigungen, Seitenauszüge | `OWNER_ADMIN` |

Navigation entries are `NavItem`s in `GROUPS` (label = German noun, one lucide icon); their routes are in
`App.tsx`. Add a new top-level screen's nav entry to `GROUPS`; detail pages keep their parent's entry.
`FileText` is reused for document entries; the permissions area uses `KeyRound`.

### Page patterns

![Dashboard](docs/assets/design/dashboard-desktop.png)

New screens follow these page structures and use a German noun as their `h1`.

- **Page header.** Either a date eyebrow plus greeting `h1` (dashboard: `text-sm muted` line, then
  `h1 text-2xl`), or `h1` left and actions right (`flex justify-between`, wraps): secondary actions
  first, the single primary action last, labelled "+ Neues Objekt" (legacy) or an icon `Button` "Neues Mandat".
- **List page** (`PropertiesPage.tsx`, `MandatePage.tsx`): header, optional import panel, `ListSearch`
  (explicit "Suchen" submit), error line, inline create/edit card, table card, "Weitere laden" for the
  next cursor page. Empty and loading text replace the table (see [States](#states)).
- **Detail page** (`PropertyDetailPage.tsx`, `MandateDetailPage.tsx`): back link ("← Alle Objekte";
  kit pages use an `ArrowLeft` icon link), `h1` (kit pages put a `Badge` beside it), action buttons, then
  stacked cards, each with an `h2` or `CardTitle`: key-value `info-table` first, then related lists and
  panels, each with its own "+ Neue ..." action. Loading, error and not-found replace the whole page
  (`if (loading) return <p>Lade...</p>`, `Objekt nicht gefunden.`).
- **Form.** `.form-grid` (two columns, one below 768 px). A `<label>` wraps its control with the caption
  above it; required fields end in ` *` plus native `required`; `form-full-width` spans both columns.
  `.form-actions` at the end: submit first (`btn btn--primary`), then "Abbrechen". While saving, the form
  is wrapped in `<fieldset disabled>` and the button label changes ("Speichern..."). Create and edit forms
  open as a card above the list; small edits of master data open `MasterDataEditor` as a dialog.
  Selects, checkboxes and file inputs are the native elements.

### Property settlement periods

Property forms and details call the existing start-month setting "Beginn des
Abrechnungszeitraums". The adjacent recurring bounds use `01.04. bis 31.03.`;
concrete dates remain in labelled date controls. Statement and batch forms offer
backend period labels (`2025`, `2025/2026`) and prefill both dates while retaining
free date entry. Statement and batch defaults use the backend's most recent
completed property period; offers never include a period that has not ended.
Automatic prefill requires both date fields to be empty, so a
delayed offer never replaces user-entered dates. Explicitly choosing an offer sets
both dates. Single-statement offers intersect the property's dates with the
selected lease's inclusive dates, omit empty intersections and show the actual
span beside the label (e.g. `2025 · 01.01.2025 – 30.09.2025`). Changing the lease
resets both date fields and loads its clipped offers. Batch offers retain the
property dates; creation clips each lease separately. Changing the property
resets the period selection. Plan forms
use a start year and the same offers; their prefilled date controls remain editable
for mid-year accounting scopes and gap-closing transition plans.
Batch results show per-lease straddling errors with unit and tenant labels alongside
the created and already-existing counts.
The WEG statement form adopts the selected plan's persisted dates. The previous-
actuals dialog accepts the property's prior period and does not predict calendar-
year dates. Forms constrain long selects to their grid cells at 360 px.

Before/after property, statement and plan form captures, plus batch and WEG captures,
are in [docs/assets/settlement-period/](docs/assets/settlement-period/), taken on
the disposable database with synthetic records in "Test Hausverwaltung" and
"Verwaltung Alpha" (lease-clipped statements).
The `*-before-review-*` files preserve the prior statement and plan captures;
their refreshed `*-after-*` files show completed November periods and editable
plan dates. Batch captures show per-lease errors beside the actual result counts.
The `statement-before-clipping-*` captures preserve the checked-in November-period
baseline; refreshed `statement-after-*` captures show the selected lease's clipped
span at 1280 px and 360 px.

### Permissions area

`/berechtigungen` has one admin sidebar entry, a kit card overview and wrapping section
navigation: **Übersicht**, **Teamrechte**, **Bestand prüfen**, **Offene Prüfungen**.
Contact and building-data permissions are text links from the area. Altquellen and
Seitenauszüge retain their existing navigation.

The overview reads the confirmed source-security status separately from the inventory.
One shared label map supplies **Vorbereitung**, **Zugriffsregeln aktiv**, **Aktionen pausiert**;
raw state codes are never visible. Each state explains staff access and the next action.
Unavailable status hides the advice; unavailable inventory hides its counts. Reload clears
previous results. Counts use German grouping, may overlap and never certify readiness.

Teamrechte contains the existing accounting scopes and grants. Bestand prüfen retains the
inventory, runtime budget, versioned coverage, retention decision and guarded activation/pause
control. Offene Prüfungen groups the existing source-review and two quarantine lists; each
labels its page count as **N geladen** and, while a continuation exists,
**weitere verfügbar**. Quarantine lists explicitly include already-reviewed observations;
loaded counts are not open-review totals. Existing grant forms and immutable receipts
keep their own confirmation/retry rules.
The legacy-source page and declaration publication dialog read the same live admin
status and shared state descriptions as the overview, with loading and unavailable states.
Clerks see no admin review link; the combined reviews list omits its self-link.
Section navigation wraps at 360 px; tables scroll inside their cards, identifiers wrap.
Old source-review, preparation, quarantine and scope-detail routes redirect with their detail
references preserved. See [the operations guide](docs/operations/accounting-scope-setup.md).

### Manual problem reports

`UsersPage.tsx` places an outline kit Button **Problem melden** above the administrator's
user form. It opens the existing kit Dialog, titled **Problem melden**, with German
Sie-form disclosure of metadata/counts and excluded personal data. It states **Es wird
nichts automatisch gesendet** and asks the user to review and attach the package manually.
The disclosure limits error records to the organization's live API workers in one
container and explains that a worker restart can make its records unavailable.
The support link **Problem im Issue-Tracker melden** comes from `HV_SUPPORT_URL`; the
last remembered server-error request ID appears separately, wraps long IDs, and is never
added to the URL. A failed link lookup is an inline alert; reopening retries it.

**Diagnosepaket herunterladen** is the primary action. During collection it reads
**Diagnosepaket wird erstellt…** and disables duplicate downloads. Failure remains an
inline `role="alert"` and permits retry. Success says only that the download started
(`role="status"`). The dialog remains keyboard-operable and dismissible; completing a
request after dismissal or session replacement cannot initiate a download. Content
scrolls inside 90vh and fits the existing narrow-screen dialog. No new tokens or CSS
components are introduced. See [reporting instructions](DEPLOY.md#diagnostics-and-reporting-a-problem).

### Responsive behaviour (down to 360 px)

Verify screens at 360 px with long German names, four-digit amounts and many rows. The page must not
scroll sideways; wide tables stay within their own horizontal scroll container.

| Width | Behaviour |
|---|---|
| < 640 (`sm`) | stat tiles 1 column; role chip and "Passwort ändern" label hidden; `DialogFooter` stacks (Cancel under primary) |
| < 768 (`md`) | sidebar off-canvas; page padding 1 rem; `.form-grid` 1 column; `.data-table` 0.82 rem |
| 640-1023 | stat tiles 2 columns |
| >= 1024 (`lg`) | stat tiles 4 columns; "Handlungsbedarf" and "Schnellzugriff" side by side (2:1) |

Wide tables use `overflow-x-auto` (`MandatePage.tsx`) or `.paged-list .paged-table` (`index.css`). Long
content uses `min-w-0` plus `truncate` (`DashboardPage.tsx`) or `overflow-wrap: anywhere`
(`.settlement-detail`). Deviation: in the 360 px detail screenshot the units table
and the document upload button extend beyond their card, so `main` scrolls sideways there.

## Components

New screens use the kit components and tokens. Reuse global classes only for what has no kit
counterpart yet (tables, key-value tables, form grid, error and empty text). Do not add new global
classes or inline `style={{}}`; the existing inline styles (about 470) are known debt.

### UI kit (`frontend/src/components/ui/`)

| File | Exports | Variants | Use when |
|---|---|---|---|
| `button.tsx` | `Button` (forwardRef) | variant `primary` (default), `secondary`, `outline`, `ghost`, `destructive`; size `sm` (h-8), `md` (h-9, default), `lg` (h-11), `icon` (h-9 w-9) | `primary`: the main action of a view or form (with the deviation below). `outline`: cancel and secondary actions. `ghost`: topbar and toolbars. `destructive`: confirm of an irreversible action. `secondary`, `lg`, `icon` and `destructive` have no call site yet. Legacy twin: `.btn`, `.btn--primary`, `.btn--danger`, `.btn--small`, `.btn--wrap` (wrapping label). |
| `badge.tsx` | `Badge`, `BadgeTone` | tone `default`, `primary`, `success`, `warning`, `destructive`, `info`, `outline` | A short status or type label. `primary` = category/type (WEG), `success` = done/confirmed, `warning` = needs attention/due, `destructive` = overdue/rejected, `info` = neutral note, `default` = neutral, `outline` = unmapped raw value. Legacy twin: `.badge` + modifier. |
| `card.tsx` | `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter` | none; compose | Every panel. A table card uses `CardContent className="p-0"` with edge cells `pl-5`/`pr-5`. Legacy twin: `.card` with an `h2`. |
| `dialog.tsx` | `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogDescription`, `DialogFooter` (+ `DialogPortal`, `DialogOverlay`) | none | Short focused tasks and confirmations on Radix: focus trap, Esc, built-in close "Schließen". Max width 32 rem, full width on phones. |
| `input.tsx` | `Input` (forwardRef) | none; `h-10`, `text-sm`, ring on focus | Inside kit dialogs and cards, always with a `Label`. In `.form-grid` forms the native input is styled by the grid instead. |
| `label.tsx` | `Label` (forwardRef) | none; `text-sm font-medium` | `Label htmlFor` + `Input id` in a `grid gap-2`. |
| `stat-card.tsx` | `StatCard` | props `label`, `value`, `icon`, `tone` (`default`, `primary`, `success`, `warning`, `destructive`), `hint`, `to` | One headline number with an icon tile; with `to` the whole tile is a link. Value `…` while loading, `—` when unavailable (with hint "Nicht verfügbar"). |

Kit adoption today, counted as non-test files with runtime imports (excluding type-only
imports): `Badge` 8, `Dialog` 9, `Card` 7, `Button` 6, `Input` 3, `Label` 2, `StatCard` 1.
The remaining UI uses the global classes below.

Deviation: the archive form's "Abbrechen" in `MandateDetailPage.tsx` uses the default `primary` variant.
The create form's "Abbrechen" in `MandatePage.tsx` explicitly uses `outline`.

### Global classes (`index.css`)

| Class | Purpose |
|---|---|
| `.card` | panel surface, `h2` heading |
| `.btn`, `.btn--primary`, `.btn--danger`, `.btn--small`, `.btn--wrap` | button, main action, destructive, compact (table rows), wrapping label |
| `.badge` + `--weg`/`--primary` (blue), `--miet`/`--confirmed`/`--finalized`/`--booked`/`--success` (green), `--sev`/`--review` (amber), `--misch`/`--superseded` (violet), `--draft` (grey), `--rejected`/`--storno`/`--danger` (red) | status pill |
| `.data-table`, `.info-table` | list table; key-value table (label column 180 px, muted) |
| `.form-grid`, `.form-full-width`, `.form-actions` | form layout |
| `.error-text`, `.empty-hint` | error line (destructive), empty-state line (muted) |
| `.link`, `.link-button` | text link; button that looks like a link |
| `.paged-list`, `.paged-list .paged-table` | list page wrapper; horizontally scrollable table wrapper only inside `.paged-list` |

Referenced in components but defined nowhere, so they do nothing: `muted` (5 files: `MasterImport.tsx`,
`DepositsPage.tsx`, `MietkontoPage.tsx`, `PropertiesPage.tsx`, `SpecialLeviesPage.tsx`), `btn-primary` (3,
typo of `btn--primary`), `badge--warning`, `form-hint`, `table-responsive`.

### Recurring composites

- **Lease documents.** Each lease in unit detail offers “Mietvertrag erstellen” and a retained
  document history. The kit dialog displays “Vorlage; rechtliche Prüfung erforderlich”, an issue
  date, handover date and a clearly labelled free-text agreement block before saving. The PDF link
  appears only after issuance succeeds. Inputs remain available after failure; readers see history
  without creation actions. Labels and long names wrap inside the available width.
  The “Übergabeprotokoll” kit dialog captures move-in/out, date, present parties, latest unit-meter
  readings, repeatable key type/count rows, defects/notes and signature names. It explains that names
  are not an electronic signature. Decimal inputs share the meter panel parser, including German
  grouping (`1.234,5`). Prefills follow the selected handover date; explicit edits survive date changes.
  Unchanged prefilled values reuse existing readings without adding duplicates.
  Meter-load errors block saving with an explicit retry; a failed
  save keeps inputs, and success reveals the retained PDF and refreshes the unit meter history.
  Network and access failures use German feedback and preserve the draft.
  “Briefe” lists the additional landlord/unit/lease placeholders and explains the single selected
  original lease used for a recipient with multiple leases.

- **Tables.** Legacy: `<div class="card paged-table"><table class="data-table">`. Kit:
  `Card > CardContent p-0 > div.overflow-x-auto > table.data-table` (`MandatePage.tsx`). First column is
  the `.link` to the detail page, last column holds row actions as `btn btn--small` ("Bearbeiten",
  "Öffnen"), numeric columns `tabular-nums`. Cursor paging through `hooks/usePagedList.ts` and
  `MoreResults` in `ListSearch.tsx`. Export previews (`DatevExport`, `SepaExport`, `ReportsPage`) use
  unclassed tables inside `overflow-x-auto`.
- **Status badges.** One label-and-tone map per domain next to the page, never the raw enum:
  `STATUS_META` in `DashboardPage.tsx` (OVERDUE "Überfällig" destructive, DUE "Fällig" warning, UPCOMING
  "Anstehend" primary, INFO "Info" info, OK "Erledigt" success); mandate types in `MandatePage.tsx`
  (WEG primary, MIET success, SEV warning); `components/SourceBadge.tsx` (E-Rechnung primary, PDF-Import
  info, Manuell default, unknown outline); legacy maps `STATUS_CSS` in `CostEntriesPage.tsx` and
  `SettlementsPage.tsx`. `InvoiceProcessingStatus.tsx` and the rule pill in `knowledge/RuleCard.tsx` are
  plain `role="status"` text/pills, not `Badge`. Management types are still shown as raw codes (MIET, WEG).
- **Stat cards.** `StatCard` grid `grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4` (`DashboardPage.tsx`);
  "Fristen überfällig" uses `destructive` above 0, while "Überfällige Posten" uses `warning` above 0.
  Both use `success` at 0 and `default` when their data is unavailable. The three rent figures in
  `DashboardKpis.tsx` are bordered boxes, not `StatCard`.
- **Panels.** `<section class="card" aria-labelledby>` with an `h2`, a sentence saying what the action does
  and what it does not do, the form, then outcome lines: `role="status"` for the receipt, `role="alert"`
  for errors, `<details>` for history. Examples: `AttachmentsPanel.tsx`, `MeterPanel.tsx`, `DsgvoPanel.tsx`,
  and the `components/intake/*Panel.tsx` family.
  Additional panel files: `MailDispatch.tsx`, `FinancialCoverageReview.tsx`, `SourceSecurityPanel.tsx`,
  `FinancialCorrectionControls.tsx`; source links use `knowledge/KnowledgeSourceButton.tsx`.
- **Dialogs.** Default is `ui/dialog.tsx`: `MasterDataEditor.tsx` (edit fields), `MasterDataDelete.tsx`
  (confirm: title, description naming the consequence, "Löschen bestätigen", "Abbrechen"; Esc and outside
  click are blocked while the request runs), `AttachmentsPanel.tsx` (archive with reason), `Topbar.tsx`
  (password; the only dialog fully in kit components). Other mechanisms exist: native `<dialog>`
  (`declarations/ApprovalSummary.tsx`, knowledge viewer), inline `role="dialog"` cards (`PreviousPlan.tsx`,
  `DunningBatch.tsx`), and `window.confirm` (22 calls in 11 files) for one-line irreversible confirmations.
  New code uses `ui/dialog.tsx`.
- **Selectors.** `PropertySelector.tsx`, `SearchChoice.tsx` (generic, any paged endpoint),
  `AccountingScopeSelector.tsx` (wraps `SearchChoice`): `fieldset` + `legend`, a search input above a native
  `select` with an empty option ("Alle Objekte", "Bitte wählen"), the current selection survives new
  searches, errors as `role="alert"`, "Weitere laden" for more pages.
- **Upload.** Native `<input type="file">` (the browser's own button text is visible) in a `.form-grid`
  form: `AttachmentsPanel.tsx` (documents, 25 MiB limit stated in the copy), `MasterImport.tsx` (CSV with
  preview table and row errors), the mandate upload in `MandateDetailPage.tsx`. These forms have no drag
  and drop or progress bar; controls are disabled while requests run. CSV import and attachments report
  outcomes in `role="status"` lines. `DeclarationImportPage.tsx` renders a `<progress>` for processing,
  styled by `.declaration-import-section progress` in `index.css`.
- **Money and date fields.** Date: `<input type="date">` (63 uses), value ISO `YYYY-MM-DD`, the browser
  shows its locale format. Money: `<input type="text" inputMode="decimal">` (62 uses) with placeholder
  `0.00`; the user may type a comma, helpers turn it into a dot (`moneyInput` in `SpecialLeviesPage.tsx`,
  `decimal()` in `lib/heatingInputs.ts`, `normalizePlanAmount` in `api/previousPlan.ts`, `exactCents` in
  `api/allocationProposals.ts`). The API carries money as decimal strings: no floats.

## States

The property year overview at `/berichte` appends an "Anlage V" section using
`Card`, `CardHeader`, `CardTitle`, `CardDescription` and `CardContent`. Its two
section tables show German mapping labels, judgement notes and money as
`1.234,56 €` from decimal strings. Tables scroll within their panel on narrow
screens; notes wrap. "Nicht zugeordnet" lists source labels, codes, amounts and
reasons, including explicit exclusions. The reconciliation table shows assigned,
unassigned, original overview totals and differences without client arithmetic.
The panel always states "Zuordnungshilfe, keine Steuerberatung"; zero mapped rows
do not assert tax completeness. A real CSV link uses the same displayed selection
and reports export errors inline. Loading and retry belong to the panel, leaving
the overview visible; changing property, year or scope removes the old panel and
suppresses late responses/downloads. The existing year-overview CSV stays separate.

Every async region provides loading, empty, error and success copy using the patterns below.
Failed requests preserve the user's input.

Loading, error and success are inline text, there is no toast, spinner or skeleton component.

| State | Markup | Copy patterns (German text verbatim; `{...}` is a value) |
|---|---|---|
| Loading | `<p>`, often `role="status"`; page-level `if (loading) return` | "Lade...", "Lade…" (28 and 6 uses), "Aufgaben werden geladen…", "Mietkennzahlen werden geladen…". Buttons: "Laden..." / "Lade…". |
| Busy (saving) | button label swaps, form `fieldset disabled` | "Speichern..." (about 15), "Speichern…", "Löschung läuft…" |
| Empty | `<p class="empty-hint">` or muted `<p>`; kit pages a `Card` with centred text | "Keine Objekte vorhanden.", "Keine Zahlungen vorhanden." (about 27 "Keine X vorhanden."); "Noch keine Mandate angelegt." (16 "Noch keine ..."); "Keine fälligen Aufgaben in diesem Zeitraum." Mostly a bare sentence; a next step only for admins ("Legen Sie oben mit der Schaltfläche Neues Mandat Ihren ersten Verwaltungsauftrag an."). An empty result is reported as empty, never as success. |
| Error | `<p role="alert" class="error-text">` (116 uses), sometimes a red bordered box; a retry button in the same line | "Die Eingänge konnten nicht geladen werden. Bitte erneut versuchen.", "Klärungsfälle derzeit nicht verfügbar. Bitte erneut laden.", "Nicht authentifiziert. Bitte erneut anmelden." (401), "Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Auftrag; Ihre Angaben bleiben erhalten.", fallback "Eingaben unvollständig oder ungültig. Bitte die angegebenen Felder prüfen." (`api/client.ts`). Server `detail` strings are shown as they arrive. Failed requests keep the user's input ("bleiben erhalten"). |
| Success | `<p role="status">` below the action (62 files), receipt style | "<Objekt> gespeichert." plus what it does not do: "Prüfvermerk gespeichert · <Zeitpunkt> · Offene Punkte bleiben bestehen."; "Dokument archiviert."; "{n} Mahnentwürfe angelegt."; "Passwort erfolgreich geändert. Sie werden abgemeldet." |
| Permission denied | hidden, not disabled: `{canWrite && ...}` (25 uses) | Role model `OWNER_ADMIN`, `CLERK` (write), `READ_ONLY` (`api/types.ts`); `canWrite` = admin or clerk. Page block: "Zugriff nur für Inhaber/Admin." (`error-text`); notices: "Hinweis: Sie sind im Nur-Lesen-Modus angemeldet.", "Nur Lesezugriff. Vorlagen und bereits erstellte Briefe können eingesehen werden."; 403 mapped to "Keine Berechtigung für diese Dokumentaktion."; 401/403/404 on detail pages: "Dieser Fall ist nicht mehr zugänglich."; the declaration-import routes redirect silently to `/objekte` for `READ_ONLY` (`App.tsx`). |
| Confirm | `ui/dialog.tsx` or `window.confirm` | Question plus consequence: "Dokument wirklich löschen?", "WEG-Jahresabrechnung finalisieren? Danach unveränderbar (GoBD).", "„{Name}“ unwiderruflich löschen? Bestehende Referenzen oder Quellschutz können das Löschen verhindern." |

Principle from [AGENTS.md](AGENTS.md#design-rules): report real outcomes only, no placeholder text and no
"everything OK" when there is nothing to report. Unavailable data shows `—` or "Nicht verfügbar", not zero.

Hide write actions for `READ_ONLY`. Admin-only pages say "Zugriff nur für Inhaber/Admin." or sit in an
admin-only nav group.

## Copy and formats

### Tone and wording

- Use German UI text with real umlauts and formal **Sie**: 47 files address the user as Sie/Ihr, none
  uses du ("Geben Sie Ihr aktuelles Passwort und das neue Passwort ein."). Instructions are otherwise
  impersonal ("Bitte erneut versuchen.", "Bitte ein Objekt auswählen.").
- Headings and nav entries are nouns ("Objekte", "Offene Posten"); buttons are verbs or verb phrases:
  "Speichern", "Abbrechen", "Bearbeiten", "Anlegen" (create: "Mandat anlegen", "Benutzer anlegen"),
  "Hochladen", "Löschen", "Schließen", "Erneut laden". Cancel is always "Abbrechen". "Archivieren" means
  keep-and-hide, "Löschen" means remove; copy says which.
- Separators and punctuation: middle dot ` · ` between facts in one line; German quotes „..."; "z. B." and
  "z.B." both occur; ellipsis `…` and `...` both occur. Technical labels may stay English-derived codes
  (MEA, GoBD, SEPA, DATEV).
- Page titles: each page renders its own `h1`; `document.title` is the static "Hausverwaltung".
- Deviation: about 60 strings are written without umlauts ("Strasse", "waehlen", "Ruecklage",
  "Mietvertraege", "FLAECHE").

### Number, money, date and size formats

Use the money and date contracts below; carry amounts as decimal strings.

`frontend/src/lib/` holds no formatting helper (`utils.ts` is only `cn`; `costVocabulary.ts`,
`heatingInputs.ts`, `settlementInputNeeds.ts` are domain helpers). Formatting uses file-local functions
or inline expressions, with the variants below. The inconsistencies are tracked in #48.

| What | Contract | Implemented by |
|---|---|---|
| Money | `1.234,56 €`: `.` thousands, `,` decimals, always two, sign first (`-1.234,56 €`), no float arithmetic | `euros()` in `components/DashboardKpis.tsx`, `money()` in `pages/BankTransactionsPage.tsx` (both `€`) |
| Money, deviating | same grouping but suffix ` EUR`; `OpenItemsPage.tsx` uses ungrouped `toFixed(2)` plus ` EUR` (`1234.56 EUR`) | `money()` in `pages/ReportsPage.tsx`, `formatCents()` in `pages/PaymentsPage.tsx` and `pages/CostEntriesPage.tsx`, `formatProposalMoney()` in `pages/SettlementDetailPage.tsx`; ` EUR` appears 93 times in 30 files, `€` in 2. `displayCents()` in `api/allocationProposals.ts` prints `1234,56` without grouping or suffix. |
| Date | `TT.MM.JJJJ`, e.g. `05.03.2026`, from the ISO API value | `formatDate()` in `pages/DashboardPage.tsx`, `FristenmonitorPage.tsx`, `MandatePage.tsx`; `date()` in `BankTransactionsPage.tsx`; `dateLabel()` in `DashboardKpis.tsx` |
| Date, deviating | `toLocaleDateString("de-DE")` or `Intl.DateTimeFormat("de-DE")` prints `5.3.2026` (no zero padding) | `MandateDetailPage.tsx`, `AttachmentsPanel.tsx`; `components/knowledge/PropertyDocuments.tsx` uses `Intl.DateTimeFormat` |
| Date and time | `new Date(x).toLocaleString("de-DE")` gives `5.3.2026, 15:03:09` (no zero padding, with seconds) | 45 `toLocaleString` calls, all `de-DE`; used for receipts and history |
| Long date | weekday and month in words ("Freitag, 30. Oktober 2026") | `dateLabel` in `DashboardPage.tsx` |
| Plain numbers | `Intl.NumberFormat("de-DE")` / `toLocaleString("de-DE")` (`1.234.567`) | `PropertyDetailPage.tsx`, `AttachmentsPanel.tsx`, `IntakeBatchPage.tsx` |
| File size | `1.5 KB` / `1.0 MB` with a dot (`toFixed(1)`); mandates use whole KB (`12 KB`, `toFixed(0)`). Attachments show `1.234 Bytes` (`Intl.NumberFormat("de-DE")`); intake shows `1,5 KiB` (`toLocaleString("de-DE")`) | `formatBytes()` in `InvoiceDocumentsPage.tsx`, `formatSize()` in `MandateDetailPage.tsx`, `AttachmentsPanel.tsx`, `IntakeBatchPage.tsx` |
| IBAN | shown as stored; SEPA confirmation input removes whitespace and upper-cases it | `normalizedIban` in `components/SepaExport.tsx`; `BankAccountsPage.tsx` prints `iban` raw. There is no IBAN display grouping in the frontend. |
| Empty value | `—` (kit, newer pages), `-` (older tables), `–` (`BankTransactionsPage.tsx`) | `MandatePage.tsx`, `InvoiceDocumentsPage.tsx`, `BankTransactionsPage.tsx`, respectively |
| Money input | accept `1234,56` and `1234.56`, send `1234.56` | `moneyInput`, `exactCents`, `normalizePlanAmount` (see above) |

## E-Mail

Transactional mail uses `backend/app/mail/` and the website brand. The shared
HTML5 layout is a centred presentation table, at most 600 px wide, with inline
styles, square corners and an Outlook conditional 600 px wrapper. The dark
header carries the lowercase text wordmark **verwalino** and a subtle 3 px accent
rule. A hidden preheader repeats the opening sentence for inbox previews.

| Role | Light | Dark |
| --- | --- | --- |
| Outer background | `#F3F4F2` | `#1C2420` |
| Content card | `#FFFFFF` | `#26312A` |
| Card border / fact separators | `#D2D6D0` | `#435047` |
| Main text | `#232724` | `#EFF1E9` |
| Secondary text | `#555F57` | `#B4BFB4` |
| Header band | `#17211B` | `#111A15` |
| Wordmark | `#EFF1E9` | `#EFF1E9` |
| Accent / button | `#B62930` | `#F18280` |
| Button text | `#FFFFFF` | `#1C2420` |
| Note box | `#E7EBE4` | `#2C382F` |
| Note left border | `#7E8980` | `#7E8980` |

Body type is `'IBM Plex Sans', Arial, Helvetica, sans-serif`, 16 px / 1.6;
headings are 24 px / 1.25, weight 700, in normal case. The uppercase eyebrow
uses `'IBM Plex Mono', Consolas, 'Courier New', monospace`, 12 px, letter spacing
`.065em`, in the accent colour. Fact rows use a two-column table, body type and tabular
numbers. Header padding is 24 × 32 px, card padding 32 px, footer padding
24 × 32 px. Below 480 px the outer and inner padding shrink and the button
fills the available width. Dark mode uses `prefers-color-scheme` overrides;
base inline styles also work without stylesheet support.

Each action is one table button with 14 × 24 px padding on its anchor, 16 px
type, weight 600, and square corners. Directly underneath, the full wrapping
URL follows “Falls die Schaltfläche nicht funktioniert, öffnen Sie diesen Link:”.
Validity and security hints use a 14 px note box with a 3 px left border.
Text/background pairs meet 4.5:1 contrast in both schemes.

The 13 px / 1.5 footer sits outside the card. Every kind explains why the
recipient receives it (“Sie erhalten diese E-Mail, weil …”). Hosted mail names
“fogu UG (haftungsbeschränkt), Im Großenbruch 34, 66583 Spiesen-Elversberg”,
`hallo@verwalino.de`, and the imprint/privacy links. Self-hosted mail says
“Diese E-Mail wurde von einer selbst betriebenen Verwalino-Installation versendet.”
and directs questions to the administration that provided access. It includes no
fogu details or product-site links; deletion questions also go to that administration.
The internal operator alert identifies automatic alerts of hosted operations.

Subjects start with `Verwalino: ` and state the event or requested action.
Use formal “Sie”, “Guten Tag,” or “Guten Tag <Name>,”, short concrete sentences,
no marketing, exclamation marks or emoji, and no sign-off. Unverified-address
mail contains no names, organization names or other free text. The new address
in a change notice is masked, for example `m•••@example.de`.

Every mail has a separately written plain-text alternative: `VERWALINO`, heading,
body wrapped around 72 columns, links on their own line, `Label: Wert` facts and
a `-- ` footer separator. It is never extracted from HTML. Dates use `31.10.2026`,
times use Europe/Berlin (`31.10.2026, 14:05 Uhr`), binary sizes use `1,6 GiB`,
and percentages use a normal space (`80 %`).

There are no images, webfonts, remote stylesheets or other fetched resources.
This avoids sending recipient IP addresses to a third party when a mail is
opened, works offline and keeps self-hosted installations independent of the
product website. Fonts fall back to the locally available stack.

Run `make mail-previews` for the synthetic HTML/plain-text gallery and full-page
600/375 px light/dark PNGs under `backend/build/mail-previews/`, without a database
or SMTP. See the [preview workflow](docs/operations/mail-previews.md).
Representative captures are listed below, including the
[confirmation](docs/assets/design/mail/signup_confirmation-600-light.png),
[reset in dark mode](docs/assets/design/mail/password_reset-600-dark.png), and
[self-hosted footer](docs/assets/design/mail/signup_confirmation-selfhost-600-light.png).

## Screenshots

Settlement structured inputs state that manual consumption and person counts refer
to the entire property settlement year, including for clipped lease statements.
Consumption suggestions display a day-fallback reason beside the actual reading
basis when an interim measurement is missing.

Taken from the running dev stack of `scripts/dev-local.sh` (backend `:18000`, Vite `127.0.0.1:13000`) against the
disposable test database with synthetic data (organisation "Muster Hausverwaltung", fictitious addresses, persons and
amounts). Desktop is 1280 px wide at 1x; the 360 px images are 2x. File names are fixed so the table
stays valid when they are retaken. The review-list screenshots were refreshed with
synthetic browser-intercepted API responses, without starting a backend or accessing a database.

| Screen | Page | Desktop | 360 px |
|---|---|---|---|
| Permissions | `/berechtigungen` | [`after-overview-desktop.png`](docs/assets/permissions/after-overview-desktop.png) | [`after-overview-360.png`](docs/assets/permissions/after-overview-360.png) |
| Team rights | `/berechtigungen/teamrechte` | [Desktop](docs/assets/permissions/after-team-desktop.png) | [Phone](docs/assets/permissions/after-team-360.png) |
| Preparation | `/berechtigungen/bestand` | [Desktop](docs/assets/permissions/after-inventory-desktop.png) | [Phone](docs/assets/permissions/after-inventory-360.png) |
| Review lists | `/berechtigungen/pruefungen` | [Desktop](docs/assets/permissions/after-reviews-desktop.png) | [Phone](docs/assets/permissions/after-reviews-360.png) |
| Legacy source status | `/altquellen` | [Desktop](docs/assets/permissions/after-legacy-desktop.png) | [Phone](docs/assets/permissions/after-legacy-360.png) |
| Declaration publication | `/teilungserklaerung/import/:id/review`, "Übernahme vorbereiten" | [Desktop](docs/assets/permissions/after-publication-desktop.png) | [Phone](docs/assets/permissions/after-publication-360.png) |
| Emergency pause | `/berechtigungen` (paused) | [Desktop](docs/assets/permissions/paused-overview-desktop.png) | [Phone](docs/assets/permissions/paused-overview-360.png) |
| Dashboard | `/` | [`dashboard-desktop.png`](docs/assets/design/dashboard-desktop.png) | [`dashboard-360.png`](docs/assets/design/dashboard-360.png) |
| Hosted dashboard quota | `/`, free plan at 90% | [`dashboard-quota-1280.png`](docs/assets/design/dashboard-quota-1280.png) | [`dashboard-quota-360.png`](docs/assets/design/dashboard-quota-360.png) |
| List | `/objekte` | [`list-objekte-desktop.png`](docs/assets/design/list-objekte-desktop.png) | [`list-objekte-360.png`](docs/assets/design/list-objekte-360.png) |
| Detail | `/objekte/:id` | [`detail-objekt-desktop.png`](docs/assets/design/detail-objekt-desktop.png) | [`detail-objekt-360.png`](docs/assets/design/detail-objekt-360.png) |
| Hosted signup | `/signup` | [`signup-desktop.png`](docs/assets/design/signup-desktop.png) | [`signup-360.png`](docs/assets/design/signup-360.png) |
| Signup confirmation, retryable password error | `/signup/confirm` | [`signup-confirm-desktop.png`](docs/assets/design/signup-confirm-desktop.png) | [`signup-confirm-360.png`](docs/assets/design/signup-confirm-360.png) |
| Signup receipt | `/signup`, accepted request | — | [`signup-receipt-360.png`](docs/assets/design/signup-receipt-360.png) |
| Password reset request | `/passwort-vergessen` | [`password-reset-request-1280.png`](docs/assets/design/password-reset-request-1280.png) | [`password-reset-request-360.png`](docs/assets/design/password-reset-request-360.png) |
| Password reset | `/passwort-zuruecksetzen` | [`password-reset-1280.png`](docs/assets/design/password-reset-1280.png) | [`password-reset-360.png`](docs/assets/design/password-reset-360.png) |
| Reset receipt | `/passwort-vergessen`, accepted request | — | [`password-reset-receipt-360.png`](docs/assets/design/password-reset-receipt-360.png) |
| Reset success | `/passwort-zuruecksetzen`, accepted reset | — | [`password-reset-success-360.png`](docs/assets/design/password-reset-success-360.png) |
| Form | `/objekte`, "+ Neues Objekt" | [`form-objekt-desktop.png`](docs/assets/design/form-objekt-desktop.png) | [`form-objekt-360.png`](docs/assets/design/form-objekt-360.png) |
| Dialog | "Passwort ändern" in the topbar | [`dialog-password-desktop.png`](docs/assets/design/dialog-password-desktop.png) | [`dialog-password-360.png`](docs/assets/design/dialog-password-360.png) |

<a href="docs/assets/design/list-objekte-desktop.png"><img src="docs/assets/design/list-objekte-desktop.png" alt="List page: Objekte" width="720"></a>
<a href="docs/assets/design/dialog-password-desktop.png"><img src="docs/assets/design/dialog-password-desktop.png" alt="Dialog: Passwort ändern" width="720"></a>

The hosted quota images use the running frontend with synthetic API responses.
The signup and password recovery images use synthetic deployment metadata and
captured API responses. These are visual/360 px geometry checks, not end-to-end proof.

The screenshots show the app as it is, including the deviations named above (legacy table, native file
button, unstyled buttons on the dashboard task list, ASCII "Strasse").

### Property year mapping aid

The report comparison uses the same synthetic property, 1,234.56 EUR rent, 150 EUR
advances, 100.03 EUR water costs and 9,876.54 EUR unassigned metering costs on the
disposable test database. These report captures use 1280 and 360 CSS px at 1x.
The after images focus the new panel; the supplemental images show unassigned
amounts, reconciliation and download. The reconciliation table scrolls horizontally
within the card on narrow screens.

| View | Before | After |
| --- | --- | --- |
| Desktop | [Year overview](docs/assets/design/reports-before-desktop.png) | [Anlage V](docs/assets/design/reports-after-desktop.png) |
| 360 px | [Year overview](docs/assets/design/reports-before-360.png) | [Anlage V](docs/assets/design/reports-after-360.png) |
| Desktop reconciliation | — | [Unassigned amounts and reconciliation](docs/assets/design/reports-after-desktop-reconciliation.png) |
| 360 px reconciliation | — | [Unassigned amounts and reconciliation](docs/assets/design/reports-after-360-reconciliation.png) |

### Transactional mail

Synthetic, database-free captures from `make mail-previews`, at 1× pixel density.

| Kind | 600 px light | 600 px dark | 375 px light | 375 px dark |
| --- | --- | --- | --- | --- |
| `ai_budget_exhausted` | [600-light](docs/assets/design/mail/ai_budget_exhausted-600-light.png) | — | — | — |
| `email_change_confirm` | [600-light](docs/assets/design/mail/email_change_confirm-600-light.png) | — | — | — |
| `email_change_notice` | [600-light](docs/assets/design/mail/email_change_notice-600-light.png) | — | — | — |
| `export_ready` | [600-light](docs/assets/design/mail/export_ready-600-light.png) | — | — | — |
| `operator_alert` | [600-light](docs/assets/design/mail/operator_alert-600-light.png) | — | — | — |
| `operator_notice` | [600-light](docs/assets/design/mail/operator_notice-600-light.png) | — | — | — |
| `organization_deleted` | [600-light](docs/assets/design/mail/organization_deleted-600-light.png) | — | — | — |
| `organization_deletion_scheduled` | [600-light](docs/assets/design/mail/organization_deletion_scheduled-600-light.png) | — | — | — |
| `password_changed` | [600-light](docs/assets/design/mail/password_changed-600-light.png) | — | — | — |
| `password_reset` | [600-light](docs/assets/design/mail/password_reset-600-light.png) | [600-dark](docs/assets/design/mail/password_reset-600-dark.png) | [375-light](docs/assets/design/mail/password_reset-375-light.png) | [375-dark](docs/assets/design/mail/password_reset-375-dark.png) |
| `quota_storage_full` | [600-light](docs/assets/design/mail/quota_storage_full-600-light.png) | — | — | — |
| `quota_storage_warning` | [600-light](docs/assets/design/mail/quota_storage_warning-600-light.png) | — | — | — |
| `signup_confirmation` | [600-light](docs/assets/design/mail/signup_confirmation-600-light.png) | [600-dark](docs/assets/design/mail/signup_confirmation-600-dark.png) | [375-light](docs/assets/design/mail/signup_confirmation-375-light.png) | [375-dark](docs/assets/design/mail/signup_confirmation-375-dark.png) |
| `signup_welcome` | [600-light](docs/assets/design/mail/signup_welcome-600-light.png) | — | — | — |
| `user_invitation` | [600-light](docs/assets/design/mail/user_invitation-600-light.png) | — | — | — |
| Self-hosted confirmation | [600-light](docs/assets/design/mail/signup_confirmation-selfhost-600-light.png) | — | — | — |
