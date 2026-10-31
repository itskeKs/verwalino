# Security policy

Verwalino stores tenants' and owners' personal data and bookkeeping. Reports about anything that
lets one organization see or change another's data, bypasses authentication or authorization,
or exposes documents, secrets or backups are welcome and taken seriously.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private report form:
<https://github.com/itskeKs/verwalino/security/advisories/new>. If the form is not available to
you, email <security@verwalino.de> instead.

Include what you can: the affected route, page or script, steps to reproduce, the version
(git sha or image digest) and whether it affects the self-hosted stack, the hosted offering or
both. You will get an answer within a week; fixes land on `main` and in the next image.

## Scope

- In scope: the application in this repository (`backend/`, `frontend/`), the self-host
  installer (`scripts/self-host.sh`, `infra/`), the Docker image and the Caddy configuration.
- Out of scope: a self-hosted instance's own host, network or operating system, and the demo or
  test configurations under `infra/docker-compose.declaration-test.yml`.

## Supported versions

There are no releases yet; `main` is the only supported line. Once images are published, the
latest tag is the supported one.
