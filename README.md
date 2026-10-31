<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-232724" alt="License: AGPL-3.0" /></a>
  <a href="https://github.com/itskeKs/verwalino/actions/workflows/tests-on-pr.yaml"><img src="https://github.com/itskeKs/verwalino/actions/workflows/tests-on-pr.yaml/badge.svg?branch=main" alt="Tests on PR" /></a>
  <a href="https://www.python.org/downloads/release/python-3120/"><img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12" /></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" /></a>
  <a href="https://react.dev/"><img src="https://img.shields.io/badge/React-19-20232A?logo=react&logoColor=61DAFB" alt="React 19" /></a>
  <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL 16" /></a>
  <a href="DEPLOY.md#portable-docker-first-installation"><img src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white" alt="Docker" /></a>
  <!-- release badge: add https://img.shields.io/github/v/release/itskeKs/verwalino once a first tag exists -->
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/verwalino-wordmark-dark.svg" />
    <img src="docs/assets/brand/verwalino-wordmark.svg" alt="Verwalino" height="120" />
  </picture>
</p>

# Verwalino

Verwalino is a property-management web app for rental management, homeowners'
associations (WEG), and individual unit management (SEV). It is intended for small
and mid-size property managers and private landlords in Germany. The UI is German.
One Docker image supports self-hosting; a free hosted offering with an isolated
organization per company is [planned](docs/roadmap.md).

| Dashboard | Property list |
| --- | --- |
| ![Dashboard with rent figures and due tasks](docs/assets/design/dashboard-desktop.png) | ![Property list](docs/assets/design/list-objekte-desktop.png) |

Screenshots show demo data.

## Features

- **Access and organization:** login, user administration, admin/clerk/read-only
  roles, password changes and session revocation, management mandates, and a
  dashboard with deadlines, due tasks, and monthly rent figures.
- **Properties and units:** rental, WEG, SEV, and mixed properties; unit areas and
  co-ownership shares (MEA); meters and readings; CSV master-data import;
  AI-assisted declaration-of-division import (feature flag and OpenAI key required)
  and property knowledge with sources.
- **People and tenancies:** contacts, tenant and owner profiles, vendors, leases,
  tenant and owner changes, and effective-date rent history with graduated
  (Staffelmiete) and index-linked rent (Indexmiete).
- **Tenant ledger, bank and payments:** day-accurate expected charges, tenant and
  owner ledgers, payment booking and reversal, CSV/CAMT bank-file import with
  previews, match suggestions and split booking, and SEPA monthly rent files.
- **Vouchers and costs:** cost entry and confirmation, BetrKV categories, voucher
  upload and cost adoption, OCR and e-invoice XML extraction, clarification cases,
  and GoBD immutability, reversal, and correction.
- **Operating and heating cost statements:** calculation, finalization, PDF and
  corrections; allocation keys, pro rata and vacancy; property batch drafts,
  advance-payment proposals, and internal heating allocation or external billing.
- **WEG:** economic plans and prior-year proposals, annual settlements and PDFs,
  paid owner contributions (Hausgeld), asset reports, special levies, day-accurate
  owner changes, meetings, minutes, resolution register, and invitation PDFs.
- **Dunning, deadlines, tasks:** three dunning levels, fees and default interest,
  selected draft batches, a derived deadline monitor, and tasks with follow-ups
  from property and unit pages and on the dashboard.
- **Documents, communication, reports:** property/unit/person documents, letter
  templates and recipient PDFs, email dispatch when SMTP is configured, CSV and
  DATEV exports, and GDPR access on screen.

Partial and missing features are listed in [Gaps](docs/feature-status.md#gaps).
Usage notes and operational limits are linked in the documentation table below.

## Quick start

### Self-hosting

Prerequisites: Linux or macOS, Bash, OpenSSL, Docker, and Compose v2 2.24 or later.

**Install in one command** downloads the latest release, verifies its checksums and starts it
(available once the first release with installer assets is published):

```bash
curl -fsSL https://github.com/itskeKs/verwalino/releases/latest/download/install.sh | bash -s --
```

See [Install in one command](DEPLOY.md#install-in-one-command) for HTTPS, a pinned version,
checksum and signature verification, and offline bundles.

**Or from a checkout** on a new host, run:

```bash
bash scripts/self-host.sh init
bash scripts/self-host.sh start
```

`init` writes five random secrets to `.env.selfhost`; `start` builds the image from the
checkout and prints a one-time setup token. Open **http://127.0.0.1:18080/setup**,
enter the token, your organization, the administrator and a password, and you are
signed in. There is no default password; the token expires after 24 hours, and
`start` issues a new one. The CLI alternative is
`bash scripts/self-host.sh admin --org "Meine Hausverwaltung" --email admin@example.org`.
For a new server with a domain, use `bash scripts/self-host.sh init --https <domain>`
before the first start; DNS and public TCP ports 80/443 must be ready for Caddy.
Back up with `bash scripts/self-host.sh backup [--out DIR]` and recover with `stop`, then
`restore FILE [--yes]`; keep `.env.selfhost` separately
([backup and restore](DEPLOY.md#portable-backup-and-restore)).

The portable stack ships without AI import and AI Q&A, SMTP and automatic invoice
OCR; manual voucher processing works. Database and documents live in named volumes:
back up the private configuration, database, and documents together. A signed
multi-arch image on `ghcr.io/itskeks/verwalino` is prepared but not published yet;
this is not yet a general production release. See
[portable installation](DEPLOY.md#portable-docker-first-installation) for HTTPS,
image verification and selection, operation, backups, and the remaining
acceptance limits.

### Local development

`scripts/dev-local.sh` needs Poppler and Tesseract with the German language pack; see [CONTRIBUTING.md](CONTRIBUTING.md#development-environment).

Install both dependency sets:

```bash
cd backend && uv sync --extra dev --extra ocr --extra ops
cd ../frontend && npm ci
```

Back in the repository root, start the test database and the local app:

```bash
docker compose -f infra/docker-compose.declaration-test.yml up -d
scripts/dev-local.sh
```

The launcher starts the API, UI, and invoice worker. Stop it before running tests,
which reset the shared test database:

```bash
make test
```

See [CONTRIBUTING.md](CONTRIBUTING.md#development-environment) for prerequisites,
ports, OCR tools, test environment, and contribution checks.

Request IDs correlate error displays, response headers and logs; optional scrubbed
error tracking is [off by default](DEPLOY.md#request-ids-and-optional-error-tracking).

## Status and roadmap

The MVP for in-house use is complete; `main` targets migration `0141`.
An alpha instance at migration `0139` runs for one property-management company.
Next: self-hosting ready → scenario evals → hosted offering → domain gaps.
See the [roadmap](docs/roadmap.md), [feature status](docs/feature-status.md), and
[open domain questions](https://github.com/itskeKs/verwalino/issues?q=is%3Aissue%20is%3Aopen%20label%3Aquestion).

## Documentation

| Goal | Start here |
| --- | --- |
| Install and operate | [Deployment](DEPLOY.md), [operations](docs/operations/) |
| Understand the architecture | [Architecture](docs/architecture.md), [ADRs](docs/adr/) |
| Domain design (2026-06 drafts) | [Voucher flow](docs/m2-gobd-belegfluss.md), [operating costs](docs/m3-abrechnungs-engine.md), [WEG](docs/m4-weg-abrechnung.md), [payments](docs/m6-zahlungen-mietkonto.md), [domain reference](docs/domain-reference.md) |
| Manage passwords and users | [Users and access](docs/operations/users-and-access.md) |
| Maintain master data | [Master data and selectors](docs/operations/selector-pages.md) |
| Manage tenancies and rent | [Tenancies, rent changes, deposits, ownership](docs/operations/tenancies-and-rent.md) |
| Import bank files and handle payments | [Bank, CSV lists, dunning, rent figures](docs/operations/bank-and-payments.md) |
| Prepare statements | [Heating, batch runs, advance proposals, annual figures, plans](docs/operations/statements.md) |
| Manage WEG levies and invitations | [WEG operations](docs/operations/weg.md) |
| Record meters and consumption | [Meters and readings](docs/operations/meters.md) |
| Handle tasks, documents and exchanges | [Tasks, documents, letters, mail, DATEV, SEPA, CSV import](docs/operations/tasks-and-documents.md) |
| Process invoices and declarations | [Invoice extraction](docs/operations/invoice-extraction.md), [declaration imports](docs/operations/declaration-imports.md) |
| Change Verwalino | [Contributing](CONTRIBUTING.md), [repository rules](AGENTS.md), [design contract](DESIGN.md), [executable scenarios](features/README.md) |
| Track work | [Roadmap](docs/roadmap.md), [feature status](docs/feature-status.md) |
| Work on the public website | [Website guide](website/README.md) |

The whole map is [docs/README.md](docs/README.md).

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md), then use
the [issue templates](.github/ISSUE_TEMPLATE/). Report vulnerabilities according to
[SECURITY.md](SECURITY.md).

## License

GNU Affero General Public License v3.0, see [LICENSE](LICENSE). Anyone who runs a
modified Verwalino as a service must offer its source to the users of that service.
