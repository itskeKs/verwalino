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
    session_max_age_seconds: int = 8 * 3600  # 8 hours

    # --- Document Storage ---
    document_storage_root: str = "/tmp/hv-documents"

    # --- Region / Compliance ---
    data_region: str = "EU"

    model_config = {"env_prefix": "HV_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
