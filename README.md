# Verwalino

Verwalino is a property-management web app for rental management, homeowners'
associations (WEG), and individual unit management (SEV). It is intended for small
and mid-size property managers and private landlords in Germany. The UI is German.
One Docker image supports self-hosting; a free hosted offering with an isolated
organization per company is [planned](docs/roadmap.md).

<!-- badges: add license/CI/release badges once a license and a first release exist -->

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
  reviewed declaration-of-division import and property knowledge with sources.
- **People and tenancies:** contacts, tenant and owner profiles, vendors, leases,
  tenant and owner changes, and effective-date rent history with graduated
  (Staffelmiete) and index-linked rent (Indexmiete).
- **Tenant ledger, bank and payments:** day-accurate expected charges, tenant and
  owner ledgers, payment booking and reversal, CSV/CAMT bank-file import with
  previews, match suggestions and split booking, and SEPA monthly rent files.
- **Vouchers and costs:** cost entry and confirmation, BetrKV categories, voucher
  upload and cost adoption, OCR and e-invoice XML extraction (automatic OCR is
  off in the portable stack), clarification cases, and GoBD immutability,
  reversal, and correction.
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
From a checkout on a new host, run:

```bash
bash scripts/self-host.sh init
bash scripts/self-host.sh start
bash scripts/self-host.sh admin --org "Meine Hausverwaltung" --email admin@example.org
```

The app listens at **http://127.0.0.1:18080**. The admin password is prompted for;
there is no default password. For a new server with a domain, use
`bash scripts/self-host.sh init --https <domain>` before the first start;
DNS and public TCP ports 80/443 must be ready for Caddy.

Database and documents live in named volumes. Back up the private configuration,
database, and documents together. This is not yet a general production release.
See [portable installation](DEPLOY.md#portable-docker-first-installation) for
HTTPS, image selection, operation, backups, and the remaining acceptance limits.

### Local development

Install both dependency sets:

```bash
cd backend && uv sync --extra dev --extra ops --extra ocr
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

## Status and roadmap

The MVP for in-house use is complete at migration head `0139`.
An alpha instance runs for one property-management company.
Next: self-hosting ready → scenario evals → hosted offering → domain gaps.
See the [roadmap](docs/roadmap.md), [feature status](docs/feature-status.md), and
[open domain questions](https://github.com/itskeKs/verwalino/issues?q=is%3Aissue%20is%3Aopen%20label%3Aquestion).

## Documentation

| Goal | Start here |
| --- | --- |
| Install and operate | [Deployment](DEPLOY.md), [operations](docs/operations/) |
| Understand the architecture | [Architecture](docs/architecture.md), [ADRs](docs/adr/) |
| Understand the domain | [Voucher flow](docs/m2-gobd-belegfluss.md), [operating costs](docs/m3-abrechnungs-engine.md), [WEG](docs/m4-weg-abrechnung.md), [payments](docs/m6-zahlungen-mietkonto.md), [domain reference](docs/domain-reference.md) |
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

The whole map is [docs/README.md](docs/README.md).

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md), then use
the [issue templates](.github/ISSUE_TEMPLATE/). Report vulnerabilities according to
[SECURITY.md](SECURITY.md).

## License

GNU Affero General Public License v3.0, see [LICENSE](LICENSE). Anyone who runs a
modified Verwalino as a service must offer its source to the users of that service.
