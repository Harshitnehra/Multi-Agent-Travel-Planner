import os
import unittest
from unittest.mock import patch

from config.settings import Settings, get_settings


class SettingsTests(unittest.TestCase):
    def test_defaults_are_available(self):
        with patch.dict(
            os.environ,
            {
                "APP_NAME": "",
                "APP_VERSION": "",
                "APP_ENV": "",
                "GROQ_MODEL": "",
                "DEFAULT_ORIGIN_IATA": "",
                "REQUEST_TIMEOUT_SECONDS": "",
            },
        ):
            settings = get_settings()

        self.assertEqual(settings.app_name, "TripMate")
        self.assertEqual(settings.app_version, "3.0.0")
        self.assertEqual(settings.default_origin_iata, "DEL")
        self.assertEqual(settings.request_timeout_seconds, 20)

    def test_database_url_requires_tls_when_unspecified(self):
        settings = Settings(database_url="postgresql://localhost/tripmate")
        self.assertEqual(
            settings.postgres_checkpoint_url(),
            "postgresql://localhost/tripmate?sslmode=require",
        )

    def test_database_url_preserves_existing_query_and_ssl_mode(self):
        with_query = Settings(database_url="postgresql://host/db?connect_timeout=5")
        with_ssl = Settings(database_url="postgresql://host/db?sslmode=verify-full")

        self.assertEqual(
            with_query.postgres_checkpoint_url(),
            "postgresql://host/db?connect_timeout=5&sslmode=require",
        )
        self.assertEqual(
            with_ssl.postgres_checkpoint_url(),
            "postgresql://host/db?sslmode=verify-full",
        )

    def test_invalid_timeout_uses_safe_default(self):
        with patch.dict(os.environ, {"REQUEST_TIMEOUT_SECONDS": "invalid"}):
            self.assertEqual(get_settings().request_timeout_seconds, 20)

    def test_application_database_defaults_to_sqlite(self):
        settings = Settings(database_url=None, application_database_url=None)
        self.assertTrue(settings.sqlalchemy_database_url().startswith("sqlite:///"))

    def test_postgres_url_uses_psycopg_driver(self):
        settings = Settings(application_database_url="postgresql://host/tripmate")
        self.assertEqual(
            settings.sqlalchemy_database_url(),
            "postgresql+psycopg://host/tripmate",
        )

    def test_secret_fields_are_hidden_from_repr(self):
        settings = Settings(groq_api_key="secret-value", razorpay_key_secret="payment-secret")
        self.assertNotIn("secret-value", repr(settings))
        self.assertNotIn("payment-secret", repr(settings))


if __name__ == "__main__":
    unittest.main()
