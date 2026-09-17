"""Centralized, environment-backed settings for TripMate.

Settings are intentionally read on demand instead of cached. This keeps command-line
tools and tests predictable when environment variables are changed at runtime.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import certifi
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


@dataclass(frozen=True)
class Settings:
    """Typed snapshot of the process environment."""

    app_name: str = "TripMate"
    app_version: str = "3.0.0"
    app_environment: str = "development"
    database_url: str | None = None
    application_database_url: str | None = None
    groq_api_key: str | None = field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    aviationstack_api_key: str | None = field(default=None, repr=False)
    tavily_api_key: str | None = field(default=None, repr=False)
    openweather_api_key: str | None = field(default=None, repr=False)
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = field(default=None, repr=False)
    default_origin_iata: str = "DEL"
    request_timeout_seconds: int = 20

    @classmethod
    def from_environment(cls) -> "Settings":
        timeout_text = _env("REQUEST_TIMEOUT_SECONDS", "20")
        try:
            timeout = max(1, int(timeout_text))
        except ValueError:
            timeout = 20

        return cls(
            app_name=_env("APP_NAME", "TripMate"),
            app_version=_env("APP_VERSION", "3.0.0"),
            app_environment=_env("APP_ENV", "development"),
            database_url=_optional_env("DATABASE_URL"),
            application_database_url=_optional_env("APP_DATABASE_URL"),
            groq_api_key=_optional_env("GROQ_API_KEY"),
            groq_model=_env("GROQ_MODEL", "openai/gpt-oss-120b"),
            aviationstack_api_key=_optional_env("AVIATIONSTACK_API_KEY"),
            tavily_api_key=_optional_env("TAVILY_API_KEY"),
            openweather_api_key=_optional_env("OPENWEATHER_API_KEY"),
            razorpay_key_id=_optional_env("RAZORPAY_KEY_ID"),
            razorpay_key_secret=_optional_env("RAZORPAY_KEY_SECRET"),
            default_origin_iata=_env("DEFAULT_ORIGIN_IATA", "DEL").upper(),
            request_timeout_seconds=timeout,
        )

    def postgres_checkpoint_url(self) -> str | None:
        """Return the configured database URL with TLS required."""
        if not self.database_url:
            return None
        if "sslmode=" in self.database_url:
            return self.database_url
        separator = "&" if "?" in self.database_url else "?"
        return f"{self.database_url}{separator}sslmode=require"

    def sqlalchemy_database_url(self) -> str:
        """Return an application-data URL compatible with SQLAlchemy."""
        database_url = self.application_database_url or self.database_url
        if not database_url:
            sqlite_path = (PROJECT_ROOT / "tripmate.db").as_posix()
            return f"sqlite:///{sqlite_path}"
        if database_url.startswith("postgres://"):
            return database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if database_url.startswith("postgresql://"):
            return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return database_url

    def razorpay_test_credentials(self) -> tuple[str, str] | None:
        """Return test credentials, refusing incomplete or live-mode keys."""
        if not self.razorpay_key_id and not self.razorpay_key_secret:
            return None
        if not self.razorpay_key_id or not self.razorpay_key_secret:
            raise ValueError("Both RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required.")
        if not self.razorpay_key_id.startswith("rzp_test_"):
            raise ValueError("TripMate accepts Razorpay test-mode keys only.")
        return self.razorpay_key_id, self.razorpay_key_secret


def get_settings() -> Settings:
    """Read a fresh settings snapshot from the environment."""
    return Settings.from_environment()


def configure_transport_security() -> None:
    """Use Certifi's CA bundle for HTTPS clients started by this process."""
    certificate_path = certifi.where()
    os.environ["SSL_CERT_FILE"] = certificate_path
    os.environ["REQUESTS_CA_BUNDLE"] = certificate_path
