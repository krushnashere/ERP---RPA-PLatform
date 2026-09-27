"""Application configuration.

Everything is driven by environment variables so the same code runs on a laptop
(SQLite) and in production (PostgreSQL) without a single code change.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- app ---
    APP_NAME: str = "Enterprise ERP & RPA Automation Platform"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # --- database ---
    # SQLite for local dev / tests. In production point this at PostgreSQL:
    #   DATABASE_URL=postgresql+psycopg2://erp:erp@localhost:5432/erp_rpa
    DATABASE_URL: str = "sqlite:///./erp_rpa.db"
    DB_ECHO: bool = False

    # --- security ---
    JWT_SECRET_KEY: str = "change-me-in-production-please-use-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    BCRYPT_ROUNDS: int = 12

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # --- file uploads (swap for S3/GCS in production) ---
    UPLOAD_DIR: str = "./uploads"

    # --- RPA worker ---
    RPA_MAX_ATTEMPTS: int = 3
    RPA_BACKOFF_BASE_SECONDS: float = 2.0     # exponential backoff base
    RPA_LEASE_SECONDS: int = 120              # how long a worker "owns" a job
    RPA_POLL_INTERVAL_SECONDS: float = 1.0
    RPA_WORKER_CONCURRENCY: int = 2

    # --- bootstrap admin (used by seed script / first-run) ---
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@acme-erp.com"
    BOOTSTRAP_ADMIN_PASSWORD: str = "Admin@123"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
