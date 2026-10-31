# Transactional mail previews

Run `make mail-previews` from the repository root. It uses the existing backend
dev extra and Playwright Chromium, needs no database or SMTP configuration, and
sends nothing. If Chromium is missing, install the existing browser dependency
with `cd backend && uv run --locked --extra dev playwright install chromium`.
Network requests are blocked while taking captures.

Open `backend/build/mail-previews/index.html` locally. It contains every kind
from `app/mail/contexts.py`, using the single synthetic sample set in
`app/mail/samples.py`: `Hausverwaltung Muster GmbH`, `Erika Mustermann`,
`example.de` addresses and one long organization name. Both hosted and self-hosted
variants have an HTML file, plain-text file and full-page PNGs at 600 and 375 px
in light and emulated dark mode. Hosted filenames are `<kind>.html`, `<kind>.txt`
and `<kind>-600-light.png` (likewise `600-dark`, `375-light`, `375-dark`);
self-hosted filenames add `-selfhost` after the kind. The gallery places HTML
and plain text side by side and links to every capture. Output is ignored by Git.

Review wording, visible fallback URLs, fact rows, long-name wrapping, note boxes
and footers in both modes. The command fails on horizontal overflow. Chromium
captures demonstrate layout and dark-mode media rules; they do not prove rendering
in every email client. The conditional Outlook wrapper and table layout provide
the base compatibility without external resources.

Commit the representative PNG set to `docs/assets/design/mail/` after a design
change: every hosted kind at `600-light`, confirmation and password reset also
at `600-dark`, `375-light`, `375-dark`, and confirmation's self-hosted footer at
`600-light`. [DESIGN.md](../../DESIGN.md#e-mail) lists these captures.

Snapshot tests compare the subject, text and HTML of every hosted sample with
`backend/tests/mail_snapshots/`. To deliberately regenerate after reviewing a
copy or layout change, run:

```sh
UPDATE_MAIL_SNAPSHOTS=1 make test-backend PYTEST_ARGS="-q tests/test_mail_templates.py"
```

The Makefile owns the shared test DB lock; do not wrap this command in another
lock. Inspect the snapshot diff, then rerun without the update variable. Ordinary
test runs never update snapshots. Template files under `app/mail/templates/de/`
are developer-owned code, with shared copy in `.content.jinja` files. They must
never become user-editable or use unescaped markup from a context.

For #111/#115, construct the appropriate context, call `render_mail(context)`,
then `rendered.apply_to(message)` before the existing SMTP submission. Keep
recipient checks, transport configuration and token handling in the sender;
the renderer neither sends nor logs links. Senders on these branches are not
rewired by #125. Tenant document mail uses its own organization identity and is
outside this template system.
