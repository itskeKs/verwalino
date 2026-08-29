"""Application configuration via environment / .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Database (app_rw role -- NO BYPASSRLS) ---
    database_url: str = (
        "postgresql+asyncpg://app_rw:app_secret@localhost:5433/hausverwaltung"
    )

    # --- Database (migrator role -- owns tables, runs DDL) ---
    database_url_migrator: str = (
        "postgresql+asyncpg://app_migrator:migrator_secret@localhost:5433/hausverwaltung"
    )

    # Sync URL for Alembic (psycopg2)
    database_url_sync: str = (
        "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung"
    )

    # --- Session ---
    session_secret: str = "CHANGE-ME-IN-PRODUCTION"
    session_cookie_name: str = "hv_session"
    session_max_age_seconds: int = 7200
    # Set true in production (HTTPS): the browser then only sends the cookie
    # over TLS. Must be false for plain-HTTP setups, else the login cookie is
    # dropped by the browser and authentication silently fails.
    session_cookie_secure: bool = False

    # --- HTTP perimeter ---
    # Keep local development and ASGI tests working by default. Production
    # Compose replaces this with the single public alpha hostname.
    allowed_hosts: str = "localhost,127.0.0.1,test"
    enable_api_docs: bool = True

    # --- Document Storage ---
    document_storage_root: str = "/tmp/hv-documents"

    # --- Region / Compliance ---
    data_region: str = "EU"

    @property
    def allowed_host_list(self) -> list[str]:
        """Return exact, non-empty Host header values for TrustedHostMiddleware."""
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    @property
    def docs_url(self) -> str | None:
        return "/api/docs" if self.enable_api_docs else None

    @property
    def openapi_url(self) -> str | None:
        return "/api/openapi.json" if self.enable_api_docs else None

    model_config = {"env_prefix": "HV_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
