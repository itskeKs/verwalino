"""Application configuration via environment / .env file."""

from decimal import Decimal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Database (app_rw role -- NO BYPASSRLS) ---
    database_url: str = (
        "postgresql+asyncpg://app_rw:app_secret@localhost:5433/hausverwaltung"
    )

    # Required only for the declaration worker; never fall back to web credentials.
    database_url_worker: SecretStr | None = None
    database_url_review: SecretStr | None = None

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

    # Reports are attached manually; this URL only opens the operator's tracker.
    support_url: HttpUrl = HttpUrl("https://github.com/itskeKs/verwalino/issues")

    @field_validator("support_url")
    @classmethod
    def support_url_has_no_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password:
            raise ValueError("Support URL must not contain credentials")
        return value

    # --- Optional errors-only tracker (empty DSN leaves reporting disabled) ---
    error_tracking_dsn: SecretStr = SecretStr("")
    error_tracking_environment: str = "selfhost"
    error_tracking_sample_rate: float = Field(1.0, ge=0, le=1, allow_inf_nan=False)

    # Operator-supplied percentage, not an assumed official market rate.
    # None retains legacy zero-interest behavior.
    dunning_base_interest_rate: Decimal | None = Field(None, allow_inf_nan=False)

    @field_validator("dunning_base_interest_rate", mode="before")
    @classmethod
    def validate_dunning_rate(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Dunning base interest rate must be an exact decimal")
        return value

    # --- Outbound SMTP (disabled until explicitly configured) ---
    smtp_host: str | None = None
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_from: str | None = None
    smtp_security: str = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: int = Field(15, ge=1, le=60)

    # --- Document Storage ---
    document_storage_root: str = "/tmp/hv-documents"

    # --- Ordinary invoice worker (independent of declaration providers) ---
    financial_computation_enabled: bool = True
    invoice_extraction_enabled: bool = True
    invoice_queue_limit: int = Field(100, ge=1, le=1000)

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
    declaration_primary_reasoning_effort: str = "medium"
    declaration_escalation_reasoning_effort: str = "none"
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
    declaration_primary_cached_input_usd_per_million: Decimal = Decimal("0.02")
    declaration_primary_cache_write_multiplier: Decimal = Decimal("1.25")
    declaration_escalation_input_usd_per_million: Decimal = Decimal("0.75")
    declaration_escalation_output_usd_per_million: Decimal = Decimal("4.50")
    declaration_escalation_cached_input_usd_per_million: Decimal = Decimal("0.075")
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

    @field_validator(
        "declaration_primary_input_usd_per_million",
        "declaration_primary_output_usd_per_million",
        "declaration_primary_cached_input_usd_per_million",
        "declaration_escalation_input_usd_per_million",
        "declaration_escalation_output_usd_per_million",
        "declaration_escalation_cached_input_usd_per_million",
    )
    @classmethod
    def _validate_declaration_rates(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("declaration model rates must be positive and finite")
        return value

    @field_validator("declaration_primary_cache_write_multiplier")
    @classmethod
    def _validate_cache_write_multiplier(cls, value: Decimal) -> Decimal:
        if value != Decimal("1.25"):
            raise ValueError("declaration cache write multiplier must be exactly 1.25")
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

    @model_validator(mode="after")
    def _validate_declaration_efforts(self) -> "Settings":
        supported_efforts = {
            "gpt-5.6-luna": frozenset({"none", "low", "medium", "high", "xhigh", "max"}),
            "gpt-5.4-mini": frozenset({"none", "low", "medium", "high", "xhigh"}),
        }
        if self.declaration_primary_reasoning_effort not in supported_efforts.get(
            self.declaration_primary_model, frozenset()
        ):
            raise ValueError("primary declaration reasoning effort is not supported by its model")
        if self.declaration_escalation_reasoning_effort not in supported_efforts.get(
            self.declaration_escalation_model, frozenset()
        ):
            raise ValueError("escalation declaration reasoning effort is not supported by its model")
        return self

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
