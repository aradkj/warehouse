"""Runtime configuration.

Every knob has a safe default so that the application can start with **no**
environment variables set, and every override is explicit and typed. Secrets are
never baked into the image: the database URL comes from the environment (or a
mounted secret file) exactly as the specification requires.

Values are read with the ``ARAD_`` prefix, e.g. ``ARAD_DATABASE_URL``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ordering.domain.policies import UnavailableStockPolicy


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_prefix="ARAD_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    # -- database ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://arad:arad@localhost:5432/arad",
        description="Async SQLAlchemy URL of the PostgreSQL system of record.",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 10.0
    db_pool_recycle_seconds: int = 1800
    db_statement_timeout_ms: int = 15_000
    db_echo: bool = False
    app_db_user: str = Field(
        default="arad_app",
        description="Least-privilege runtime application database username.",
    )
    app_db_password: str | None = Field(
        default=None,
        description="Least-privilege runtime application database password.",
    )
    postgres_password: str | None = Field(
        default=None,
        description="PostgreSQL administrator bootstrap password.",
    )

    # -- runtime -----------------------------------------------------------
    environment: str = "local"
    log_level: str = "INFO"
    log_json: bool = True
    api_prefix: str = "/v1"
    api_title: str = "Arad Order & Inventory Orchestration Platform"
    #: Bodies larger than this are rejected with 413 before they are parsed.
    max_request_bytes: int = 1_048_576

    # -- business policy ---------------------------------------------------
    #: What happens when stock cannot (fully) be reserved during submission:
    #: ``BACKORDER`` accepts the order and records the shortage, ``REJECT``
    #: refuses it outright. Both branches are required by the specification.
    unavailable_stock_policy: UnavailableStockPolicy = UnavailableStockPolicy.BACKORDER
    reservation_ttl_seconds: int = 1800
    max_reservation_ttl_seconds: int = 86_400
    idempotency_retention_seconds: int = 604_800

    # -- messaging & outbox (Phase 2) --------------------------------------
    rabbitmq_url: str = Field(
        default="amqp://arad:arad@localhost:5672//",
        description="RabbitMQ connection URL.",
    )
    rabbitmq_exchange: str = "arad.events"
    rabbitmq_prefetch: int = 10

    outbox_batch_size: int = 50
    outbox_poll_interval_seconds: float = 1.0
    outbox_claim_timeout_seconds: float = 30.0
    outbox_max_attempts: int = 5

    # -- consumer retries (Phase 2) ----------------------------------------
    consumer_max_retries: int = 3
    consumer_initial_retry_delay_seconds: float = 1.0
    consumer_max_retry_delay_seconds: float = 30.0
    consumer_retry_multiplier: float = 2.0
    consumer_retry_jitter_seconds: float = 0.5

    # -- celery & workers (Phase 2) ----------------------------------------
    celery_broker_url: str = Field(
        default="amqp://arad:arad@localhost:5672//",
        description="Celery broker connection URL.",
    )
    celery_result_backend: str = "rpc://"
    celery_task_max_retries: int = 3
    celery_retry_backoff: bool = True
    celery_retry_max_delay_seconds: int = 300

    reservation_expiry_interval_seconds: int = 30
    reservation_expiry_batch_size: int = 50

    # -- caching & redis (Phase 3) -----------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("ARAD_REDIS_URL", "REDIS_URL"),
        description="Redis connection URL for cache and distributed locks.",
    )
    redis_max_connections: int = 50
    redis_socket_timeout_seconds: float = 2.0
    redis_connect_timeout_seconds: float = 2.0
    redis_key_prefix: str = "arad"
    redis_default_ttl_seconds: int = 300
    redis_negative_ttl_seconds: int = 30
    redis_lock_timeout_seconds: float = 5.0

    # -- search & elasticsearch (Phase 3) ----------------------------------
    elasticsearch_url: str = Field(
        default="http://localhost:9200",
        validation_alias=AliasChoices("ARAD_ELASTICSEARCH_URL", "ELASTICSEARCH_URL"),
        description="Elasticsearch connection URL.",
    )
    elasticsearch_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "ARAD_ELASTICSEARCH_TIMEOUT_SECONDS", "ELASTICSEARCH_TIMEOUT"
        ),
        description="Socket timeout for Elasticsearch operations.",
    )
    elasticsearch_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts on transient Elasticsearch network errors.",
    )
    search_cache_ttl_seconds: int = Field(
        default=60,
        description="Cache TTL in seconds for search query results.",
    )
    search_max_page_size: int = Field(
        default=100,
        description="Maximum permitted page size for search queries.",
    )
    reindex_batch_size: int = Field(
        default=200,
        description="Batch size for reindexing products from PostgreSQL.",
    )
    reconciliation_batch_size: int = Field(
        default=200,
        description="Batch size for reconciling PostgreSQL and Elasticsearch read models.",
    )

    # -- identity & authentication (Phase 4.1) -----------------------------
    auth_jwt_secret: str = Field(
        default="insecure-dev-secret-change-in-production-0123456789abcdef",
        description="Secret key used to sign and verify JWT access tokens.",
    )
    auth_jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm.",
    )
    auth_jwt_issuer: str = Field(
        default="arad-platform",
        description="JWT issuer (iss claim).",
    )
    auth_jwt_audience: str = Field(
        default="arad-api",
        description="JWT audience (aud claim).",
    )
    auth_access_token_ttl_seconds: int = Field(
        default=900,
        description="Access token TTL in seconds (default 15 minutes).",
    )
    auth_refresh_token_ttl_seconds: int = Field(
        default=604_800,
        description="Refresh token TTL in seconds (default 7 days).",
    )
    auth_required: bool = Field(
        default=False,
        description="Whether authentication is strictly required on all tenant endpoints.",
    )

    # -- tests -------------------------------------------------------------
    test_database_url: str = "postgresql+asyncpg://arad:arad@localhost:5432/arad_test"

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("the database URL must use the asyncpg driver")
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log level must be one of {sorted(allowed)}")
        return upper

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> Settings:
        if self.is_production:
            if not self.auth_required:
                raise ValueError("In production, ARAD_AUTH_REQUIRED must be set to true")
            insecure_placeholders = (
                "insecure-dev-secret",
                "replace_with",
                "change_in_production",
                "secret-key-must-be",
                "your_secure",
                "placeholder",
            )
            secret_lower = self.auth_jwt_secret.lower()
            if len(self.auth_jwt_secret) < 32 or any(
                p in secret_lower for p in insecure_placeholders
            ):
                raise ValueError(
                    "In production, ARAD_AUTH_JWT_SECRET must be configured "
                    "with a secure, non-placeholder key of at least 32 characters"
                )

            # Independent credential validation: application password cannot equal admin password
            if (
                self.app_db_password is not None
                and self.postgres_password is not None
                and self.app_db_password == self.postgres_password
            ):
                raise ValueError(
                    "In production, application database password and administrator "
                    "password must be independently configured"
                )

            # Prevent using the bootstrap superuser account in production runtime database URL
            from urllib.parse import urlparse

            cleaned_url = self.database_url.replace("postgresql+asyncpg://", "postgresql://")
            parsed_db = urlparse(cleaned_url)
            if parsed_db.username and parsed_db.username.lower() in ("postgres", "root"):
                raise ValueError(
                    "In production, application services must connect via a dedicated "
                    "least-privilege role, not a database superuser"
                )
            if (
                self.postgres_password is not None
                and parsed_db.password is not None
                and parsed_db.password == self.postgres_password
                and parsed_db.username not in ("arad_app", self.app_db_user)
            ):
                raise ValueError(
                    "In production, application database connection must not "
                    "use the administrator credentials"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings instance (cached: settings are immutable)."""
    return Settings()
