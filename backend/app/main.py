"""FastAPI application bootstrap."""

from fastapi import FastAPI

from app.api import auth, organizations, users, mandates

app = FastAPI(
    title="Hausverwaltung MVP",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# --- Routers ---
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(users.router)
app.include_router(mandates.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
