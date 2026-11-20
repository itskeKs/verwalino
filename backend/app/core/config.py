"""Application configuration via environment / .env file."""

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.core.document_limits import MAX_UPLOAD_BYTES


class DeploymentMode(StrEnum):
    SELFHOST = "selfhost"
    HOSTED = "hosted"


QA_FINALIZATION_MARGIN_SECONDS = 10


class Settings(BaseSettings):
    # One image, with mode-dependent decisions in core/deployment_policy.py.
    deployment_mode: DeploymentMode = DeploymentMode.SELFHOST

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
    # Compose replaces this with the single public hostname.
    allowed_hosts: str = "localhost,127.0.0.1,test"
    enable_api_docs: bool = True

    # Reports are attached manually; this URL only opens the operator's tracker.
    support_url: HttpUrl = HttpUrl("https://github.com/itskeKs/verwalino/issues")
    diagnostics_dir: str = "/tmp/verwalino-diagnostics"

    @field_validator("support_url")
    @classmethod
    def support_url_has_no_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password:
            raise ValueError("Support URL must not contain credentials")
        return value

    # --- Optional errors-only tracker (empty DSN leaves reporting disabled) ---
    error_tracking_dsn: SecretStr = SecretStr("")
    error_tracking_environment: str = "selfhost"

    # Public hostname shared with deployment routing; signup always links via HTTPS.
    public_host: str | None = None

    # --- Outbound SMTP (disabled until explicitly configured) ---
    smtp_host: str | None = None
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_from: str | None = None
    smtp_security: str = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: int = Field(15, ge=1, le=60)
    # Hosted staging: exactly one permitted recipient. None leaves mail unrestricted.
    mail_allowlist: str | None = None

    # --- Document Storage ---
    document_storage_root: str = "/tmp/hv-documents"

    # --- Ordinary invoice worker (independent of declaration providers) ---
    financial_computation_enabled: bool = True
    invoice_extraction_enabled: bool = True
    invoice_queue_limit: int = Field(100, ge=1, le=1000)

    # --- Declaration import / PDF preflight ---
    declaration_max_pdf_bytes: int = MAX_UPLOAD_BYTES
    declaration_max_documents: int = 10
    declaration_max_pages: int = 500
    declaration_ocr_dpi: int = 180
    declaration_ocr_timeout_seconds: int = 30
    declaration_text_min_chars: int = 40
    declaration_ocr_max_pixels_per_page: int = 20_000_000
    declaration_ocr_max_temp_bytes: int = 256 * 1024 * 1024
    declaration_ocr_max_pages_per_import: int = 200
    # The count is taken from the page size at 180 dpi, before the image is scaled to the render ceiling of
    # 4,000,000 pixels (READING_MAX_IMAGE_PIXELS in the preflight). An A4 page (595 x 842 points) counts 3,132,240
    # pixels, so 200 A4 pages and the rechecks fit; larger formats reach the bound earlier (A3 at page 128).
    # A scan reaches that many pages only within the upload limit of 25 MiB per PDF (declaration_max_pdf_bytes).
    declaration_ocr_max_pixels_per_import: int = 800_000_000
    declaration_preflight_deadline_seconds: int = 600

    # --- Declaration import / OpenAI provider ---
    # Server-only secret. Do not place a real value in the project .env.
    declaration_prompt_version: str = "declaration-v4"
    declaration_schema_version: str = "2"
    declaration_harness_version: str = "4"
    declaration_provider_timeout_seconds: int = 60
    declaration_max_cost_usd: Decimal = Decimal("3.00")
    declaration_max_recheck_pages: int = 12
    declaration_max_recheck_calls: int = 2
    declaration_provider_batch_pages: int = 20
    declaration_provider_batch_chars: int = 80_000
    # Per-import token limits (input, output), sized for the 200 scanned pages the page limit permits
    # (declaration_ocr_max_pages_per_import) now that reading costs tokens per page (issue #499, ADR 0017). Derivation
    # for 200 dense scanned A4 pages (1,489 x 2,105 pixels at 180 dpi, about 4,000 characters each), as reported:
    # * reading, one call per page: about 5,000 input (image 3,000 on OpenAI's patch models, 4,100 by Anthropic's
    #   formula, plus about 700 for prompt, schema and wrapper) and about 2,000 output (the transcript as JSON,
    #   1,000 to 1,900 for prose to table pages) -> 1,000,000 in, 400,000 out.
    # * extraction, three passes (inventory, core, rules) over every transcript page: 12 batches of at most 19
    #   pages and 80,000 characters, one boundary page repeated per batch, so 211 page sends per pass at about
    #   1,500 input each, plus about 5,600 for prompt and schema per batch round -> 1,017,000 in, 54,000 out;
    #   reconciliation up to 40,000 in, 6,000 out. Extraction thus keeps the former 60,000 output limit as its share.
    # * total 2,060,000 in, 460,000 out. A reservation settles to the reported usage after its call, so the
    #   peak is that plus ONE outstanding worst-case reservation, up to 200,000 input (an 80,000 character batch
    #   counted twice, bytes as tokens, on a compatible endpoint) -> 2,200,000. Round numbers with at most 25 %
    #   headroom. The cost limit above stays the money guard; dearer models reach it first. The upload limit of
    #   25 MiB per PDF bounds a large scan before the page limit: 200 pages in high resolution can exceed it.
    declaration_max_input_tokens: int = 2_500_000
    declaration_max_output_tokens: int = 500_000
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
    # --- Property knowledge Q&A (optional; no key is required at startup) ---
    declaration_qa_timeout_seconds: int = 30
    declaration_qa_max_hits: int = 20
    declaration_qa_max_input_tokens: int = 20_000
    declaration_qa_max_output_tokens: int = 1_000
    declaration_qa_max_cost_usd: Decimal = Decimal("0.05")
    declaration_qa_budget_window_minutes: int = 60
    declaration_qa_reservation_seconds: int = 120
    declaration_qa_max_concurrency: int = 4

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
    )
    @classmethod
    def _validate_positive_qa_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("Q&A monetary values must be positive and finite")
        return value

    @model_validator(mode="after")
    def _validate_qa_reservation_lifetime(self) -> "Settings":
        """Leave ten seconds after a bounded call for final durable accounting."""
        if self.declaration_qa_reservation_seconds < (
            self.declaration_qa_timeout_seconds + QA_FINALIZATION_MARGIN_SECONDS
        ):
            raise ValueError(
                "declaration_qa_reservation_seconds must include a finalization margin"
            )
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
