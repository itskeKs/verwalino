"""FastAPI application bootstrap."""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, organizations, users, mandates
from app.api import properties, units, persons, ownership, leases
from app.api import owner_profiles, tenant_profiles, vendors
from app.api import cost_categories, cost_entries, invoice_documents
from app.api import financial_input_coverage, accounting_scopes
from app.api import settlements
from app.api import economic_plans, weg_settlements
from app.api import payments
from app.api import bank_accounts
from app.api import dunning
from app.api import meetings
from app.api import fristen, dsgvo
from app.api import declaration_imports
from app.api import property_documents
from app.api import property_knowledge, property_data_domains
from app.api import property_source_admission_reviews,property_source_classifications
from app.core import health
from app.core.static_files import resolve_spa_file
from app.core.uploads import RequestBodyLimitMiddleware
from app.core.config import settings

app = FastAPI(
    title="Hausverwaltung MVP",
    version="0.1.0",
    docs_url=settings.docs_url,
    openapi_url=settings.openapi_url,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
app.add_middleware(RequestBodyLimitMiddleware)

# Invoice extraction runs in the durable worker after upload commits.

# --- Routers ---
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(users.router)
app.include_router(accounting_scopes.router)
app.include_router(property_data_domains.router)
app.include_router(property_source_admission_reviews.router)
app.include_router(property_source_classifications.router)
app.include_router(mandates.router)

# M1 Stammdaten
app.include_router(properties.router)
app.include_router(units.router)
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

# M3 Verteilungs-/Abrechnungs-Engine (Betriebskostenabrechnung Mieter)
app.include_router(settlements.router)
app.include_router(financial_input_coverage.router)

# M4 WEG-Abrechnung (Wirtschaftsplan, Jahresabrechnung, Vermoegensbericht)
app.include_router(economic_plans.router)
app.include_router(weg_settlements.router)

# M6 Zahlungen, Mietkonto & offene Posten
app.include_router(bank_accounts.router)
app.include_router(payments.router)

# M7 Mahnwesen
app.include_router(dunning.router)

# M8 Eigentümerversammlung & Beschluss-Sammlung
app.include_router(meetings.router)

# M9 Fristenüberwachung & DSGVO-Härtung
app.include_router(fristen.router)
app.include_router(dsgvo.router)

# Declaration imports must be registered before the production SPA fallback.
app.include_router(declaration_imports.router)
app.include_router(property_documents.router)
app.include_router(property_knowledge.router)


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
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(resolve_spa_file(_frontend_dist, full_path))
