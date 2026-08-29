"""Application configuration via environment / .env file."""

from decimal import Decimal

from pydantic import SecretStr, field_validator, model_validator
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
    # Set true in production (HTTPS): the browser then only sends the cookie
    # over TLS. Must be false for plain-HTTP setups, else the login cookie is
    # dropped by the browser and authentication silently fails.
    session_cookie_secure: bool = False

    # --- Document Storage ---
    document_storage_root: str = "/tmp/hv-documents"

    # --- Declaration import / PDF preflight ---
    declaration_import_enabled: bool = False
    declaration_max_pdf_bytes: int = 25 * 1024 * 1024
    declaration_max_documents: int = 10
    declaration_max_pages: int = 500
    declaration_ocr_dpi: int = 180
    declaration_ocr_timeout_seconds: int = 30
    declaration_text_min_chars: int = 40
    declaration_ocr_max_pixels_per_page: int = 20_000_000
    declaration_ocr_max_temp_bytes: int = 256 * 1024 * 1024
    declaration_ocr_max_pages_per_import: int = 200
    declaration_ocr_max_pixels_per_import: int = 400_000_000
    declaration_preflight_deadline_seconds: int = 600

    # --- Declaration import / OpenAI provider ---
    # Server-only secret. Do not place a real value in the project .env.
    openai_api_key: SecretStr | None = None
    declaration_primary_model: str = "gpt-5.6-luna"
    declaration_escalation_model: str = "gpt-5.4-mini"
    declaration_prompt_version: str = "declaration-v1"
    declaration_schema_version: str = "1"
    declaration_harness_version: str = "1"
    declaration_provider_timeout_seconds: int = 60
    declaration_max_cost_usd: Decimal = Decimal("1.00")
    declaration_max_recheck_pages: int = 12
    declaration_max_recheck_calls: int = 2
    declaration_provider_batch_pages: int = 20
    declaration_provider_batch_chars: int = 80_000
    declaration_max_input_tokens: int = 500_000
    declaration_max_output_tokens: int = 60_000
    declaration_provider_max_output_tokens_per_call: int = 12_000
    declaration_max_candidates: int = 5_000
    # --- Declaration import / background worker ---
    declaration_worker_id: str = "declaration-worker"
    declaration_worker_poll_seconds: float = 2.0
    declaration_lease_seconds: int = 120
    declaration_max_attempts: int = 3
    # Hybrid lexical retrieval fallback.  Kept deploy-time only; requests may
    # narrow results, never relax this similarity threshold.
    declaration_search_trigram_threshold: Decimal = Decimal("0.25")
    # Reverify these standard short-context rates whenever model, service tier,
    # processing region, or pricing changes.
    declaration_primary_input_usd_per_million: Decimal = Decimal("0.20")
    declaration_primary_output_usd_per_million: Decimal = Decimal("1.20")
    declaration_escalation_input_usd_per_million: Decimal = Decimal("0.75")
    declaration_escalation_output_usd_per_million: Decimal = Decimal("4.50")
    # --- Property knowledge Q&A (optional; no key is required at startup) ---
    declaration_qa_enabled: bool = False
    declaration_qa_model: str = "gpt-5.6-luna"
    declaration_qa_timeout_seconds: int = 30
    declaration_qa_max_hits: int = 20
    declaration_qa_max_input_tokens: int = 20_000
    declaration_qa_max_output_tokens: int = 1_000
    declaration_qa_max_cost_usd: Decimal = Decimal("0.05")
    declaration_qa_budget_window_minutes: int = 60
    declaration_qa_reservation_seconds: int = 120
    declaration_qa_max_concurrency: int = 4
    declaration_qa_input_usd_per_million: Decimal = Decimal("0.20")
    declaration_qa_output_usd_per_million: Decimal = Decimal("1.20")

    @field_validator("declaration_search_trigram_threshold")
    @classmethod
    def _validate_search_trigram_threshold(cls, value: Decimal) -> Decimal:
        if not Decimal("0") <= value <= Decimal("1"):
            raise ValueError("declaration_search_trigram_threshold must be between 0 and 1")
        return value

    @field_validator(
        "declaration_qa_timeout_seconds",
        "declaration_qa_max_hits",
        "declaration_qa_max_input_tokens",
        "declaration_qa_max_output_tokens",
        "declaration_qa_budget_window_minutes",
        "declaration_qa_reservation_seconds",
        "declaration_qa_max_concurrency",
    )
    @classmethod
    def _validate_positive_qa_integer(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("Q&A limits must be positive")
        return value

    @field_validator("declaration_qa_max_hits")
    @classmethod
    def _validate_qa_max_hits(cls, value: int) -> int:
        if value > 50:
            raise ValueError("declaration_qa_max_hits must not exceed 50")
        return value

    @field_validator(
        "declaration_qa_max_cost_usd",
        "declaration_qa_input_usd_per_million",
        "declaration_qa_output_usd_per_million",
    )
    @classmethod
    def _validate_positive_qa_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("Q&A monetary values must be positive and finite")
        return value

    @model_validator(mode="after")
    def _validate_qa_reservation_lifetime(self) -> "Settings":
        """Leave ten seconds after a bounded call for final durable accounting."""
        margin_seconds = 10
        if self.declaration_qa_reservation_seconds < (
            self.declaration_qa_timeout_seconds + margin_seconds
        ):
            raise ValueError(
                "declaration_qa_reservation_seconds must include a finalization margin"
            )
        return self

    # --- Region / Compliance ---
    data_region: str = "EU"

    model_config = {"env_prefix": "HV_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
