"""FastAPI application bootstrap."""

from fastapi import FastAPI

from app.api import auth, organizations, users, mandates
from app.api import properties, units, persons, ownership, leases
from app.api import owner_profiles, tenant_profiles, vendors
from app.api import cost_categories, cost_entries, invoice_documents
from app.core.extraction import set_extractor
from app.core.pdf_extraction import PdfTextExtractor

app = FastAPI(
    title="Hausverwaltung MVP",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# --- Register real PDF extractor as default (replaces StubExtractor) ---
set_extractor(PdfTextExtractor())

# --- Routers ---
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(users.router)
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


@app.get("/api/health")
async def health():
    return {"status": "ok"}
