"""FastAPI application bootstrap."""

from fastapi import FastAPI

from app.api import auth, organizations, users, mandates
from app.api import properties, units, persons, ownership, leases
from app.api import owner_profiles, tenant_profiles, vendors
from app.api import cost_categories, cost_entries, invoice_documents
from app.api import settlements
from app.api import economic_plans, weg_settlements
from app.api import payments
from app.api import bank_accounts
from app.api import dunning
from app.api import meetings
from app.api import fristen, dsgvo
from app.core.extraction import set_extractor
from app.core.pdf_extraction import PdfTextExtractor
from app.core.einvoice import RoutingExtractor

app = FastAPI(
    title="Hausverwaltung MVP",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# --- Register the routing extractor as default (M5): E-Rechnung (XRechnung/
# ZUGFeRD) is parsed structurally; everything else falls back to PDF text/OCR. ---
set_extractor(RoutingExtractor(PdfTextExtractor()))

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

# M3 Verteilungs-/Abrechnungs-Engine (Betriebskostenabrechnung Mieter)
app.include_router(settlements.router)

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


@app.get("/api/health")
async def health():
    return {"status": "ok"}
