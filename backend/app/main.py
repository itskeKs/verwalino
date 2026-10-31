"""FastAPI application bootstrap."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, organizations, users, mandates, intake_batches, intake_items, exceptions, invoice_reviews, context_decisions, allocation_proposals, allocation_treatments, allocation_adoptions, duplicate_comparisons
from app.api import context_mappings, setup, signup, password_reset, diagnostics
from app.api import properties, units, persons, ownership, leases
from app.api import owner_profiles, tenant_profiles, vendors
from app.api import cost_categories, cost_entries, invoice_documents, document_refs, logical_documents, extraction_revisions, source_partitions, visual_extracts, visual_disclosures
from app.api import financial_input_coverage, accounting_scopes, access_preparation, mandate_quarantine, dunning_quarantine
from app.api import settlements
from app.api import economic_plans, weg_settlements
from app.api import special_levies
from app.api import payments
from app.api import bank_accounts, bank_transactions
from app.api import dunning, mail, letters
from app.api import meetings
from app.api import fristen, dsgvo
from app.api import declaration_imports
from app.api import property_documents, attachments, reports, tasks
from app.api import property_knowledge, property_data_domains, person_data_domains
from app.api import property_source_admission_reviews,property_source_classifications
from app.api import meters
from app.diagnostics import APP_VERSION
from app.core import health
from app.core.static_files import resolve_spa_file
from app.core.uploads import RequestBodyLimitMiddleware
from app.core.config import settings
from app.services import mail_service
from app.core.deployment_policy import get_deployment_policy
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware, http_error, unhandled_error
from app.core.error_tracking import configure_error_tracking

configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    signup.validate_signup_configuration()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Hausverwaltung MVP",
    version=APP_VERSION,
    docs_url=settings.docs_url,
    openapi_url=settings.openapi_url,
)
configure_error_tracking(settings, release=os.environ.get("HV_RELEASE_ID") or app.version)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(signup.ConfirmationLinkPrivacyMiddleware)
# Starlette prepends middleware: registering last makes request IDs run first.
app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(StarletteHTTPException, http_error)
app.add_exception_handler(Exception, unhandled_error)


# A write authenticated just before cooling-off may reach the native guard.
# Keep that race a German conflict response; preserve normal error handling
# for every unrelated database failure.
from sqlalchemy.exc import DBAPIError


async def organization_read_only_error(request, exc):
    if getattr(exc.orig, 'sqlstate', None) == 'P0023':
        return await http_error(request, HTTPException(409,
            'Ihre Organisation ist während der Löschung schreibgeschützt.'))
    raise exc


app.add_exception_handler(DBAPIError, organization_read_only_error)

# Invoice extraction runs in the durable worker after upload commits.

# --- Routers ---
app.include_router(auth.router)
app.include_router(setup.router)
app.include_router(signup.router)
app.include_router(password_reset.router)
app.include_router(organizations.router)
app.include_router(users.router)
app.include_router(diagnostics.router)
app.include_router(accounting_scopes.router)
app.include_router(access_preparation.router)
app.include_router(mandate_quarantine.router)
app.include_router(dunning_quarantine.router)
app.include_router(property_data_domains.router)
app.include_router(person_data_domains.router)
app.include_router(property_source_admission_reviews.router)
app.include_router(property_source_classifications.router)
app.include_router(mandates.router)

# M1 Stammdaten
app.include_router(properties.router)
app.include_router(units.router)
app.include_router(meters.router)
app.include_router(persons.router)
app.include_router(ownership.router)
app.include_router(leases.router)
app.include_router(owner_profiles.router)
app.include_router(tenant_profiles.router)
app.include_router(vendors.router)

# M2 Kosten & GoBD-Belegfluss
app.include_router(cost_categories.router)
app.include_router(cost_entries.router)
app.include_router(invoice_documents.router)
app.include_router(document_refs.router)
app.include_router(logical_documents.router)
app.include_router(source_partitions.router)
app.include_router(visual_extracts.router)
app.include_router(visual_disclosures.router)
app.include_router(extraction_revisions.router)
app.include_router(intake_batches.router)
app.include_router(intake_items.router)
app.include_router(exceptions.router)
app.include_router(invoice_reviews.router)
app.include_router(context_decisions.router)
app.include_router(allocation_proposals.router)
app.include_router(allocation_treatments.router)
app.include_router(allocation_adoptions.router)
app.include_router(context_mappings.router)
app.include_router(duplicate_comparisons.router)

# M3 Verteilungs-/Abrechnungs-Engine (Betriebskostenabrechnung Mieter)
app.include_router(settlements.router)
app.include_router(financial_input_coverage.router)

# M4 WEG-Abrechnung (Wirtschaftsplan, Jahresabrechnung, Vermoegensbericht)
app.include_router(economic_plans.router)
app.include_router(weg_settlements.router)

# M6 Zahlungen, Mietkonto & offene Posten
app.include_router(bank_accounts.router)
app.include_router(bank_transactions.router)
app.include_router(payments.router)
app.include_router(special_levies.router)

# M7 Mahnwesen
app.include_router(dunning.router)
app.include_router(mail.router)
app.include_router(letters.router)

# M8 Eigentümerversammlung & Beschluss-Sammlung
app.include_router(meetings.router)

# M9 Fristenüberwachung & DSGVO-Härtung
app.include_router(fristen.router)
app.include_router(dsgvo.router)

# Declaration imports must be registered before the production SPA fallback.
app.include_router(declaration_imports.router)
app.include_router(property_documents.router)
app.include_router(attachments.router)
app.include_router(tasks.router)
app.include_router(reports.router)
app.include_router(property_knowledge.router)


@app.get("/api/meta")
async def meta(response: Response) -> dict[str, str | bool]:
    """Public deployment facts only; no settings or organization data."""
    response.headers["Cache-Control"] = "no-store"
    return {"deployment_mode": get_deployment_policy().deployment_mode, "version": app.version,
            "mail_available": mail_service.password_reset_available(settings)}


@app.get("/api/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/api/health")
@app.get("/api/health/ready")
async def health_ready():
    if await health.is_ready():
        return {"status": "ok"}
    return JSONResponse(status_code=503, content={"status": "unavailable"})


# ---------------------------------------------------------------------------
# Serve the built frontend (production single-container deploy).
#
# In development the frontend runs on the Vite dev server (proxying /api here),
# so HV_FRONTEND_DIST is unset and none of this is mounted. In the Docker image
# the built SPA is copied in and HV_FRONTEND_DIST points at it: FastAPI then
# serves the static assets and falls back to index.html for client-side routes
# (so a hard refresh on /fristen, /mandate/... works). API routes are matched
# first (registered above); the catch-all explicitly 404s unknown /api paths.
# ---------------------------------------------------------------------------
_frontend_dist = os.environ.get("HV_FRONTEND_DIST")
if _frontend_dist and os.path.isdir(_frontend_dist):
    _assets_dir = os.path.join(_frontend_dist, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(resolve_spa_file(_frontend_dist, full_path), headers={
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
        } if full_path in {"signup/confirm", "passwort-zuruecksetzen"} else None)
