"""Test isolation: every test gets its own SQLite database and outbox folder, and
no test can reach the real database, email provider or Claude API."""
from datetime import date

import pytest

from src.connectors.csv_folder import CsvFolderConnector
from src.settings import ROOT

RUN_DATE = date(2026, 10, 22)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("FRESHROOTS_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("JUDGMENT_LOG_PATH", str(tmp_path / "judgment_log.md"))
    for name in ("RESEND_API_KEY", "EMAIL_PROVIDER", "ANTHROPIC_API_KEY", "SMTP_HOST", "DATA_SOURCE_URL",
                 "DASHBOARD_USERS", "VERCEL", "NEON_API_KEY", "VERCEL_TOKEN", "AUTH_REQUIRED", "ZOHO_ENABLED",
                 "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_ORG_ID"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("EMAIL_TO_RAGHAV", "raghav@example.com")
    monkeypatch.setenv("EMAIL_TO_PROCUREMENT", "procurement@example.com")
    monkeypatch.setenv("EMAIL_TO_DEEPA", "deepa@example.com")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("CRON_SECRET", "test-cron")
    monkeypatch.setenv("APP_URL", "http://testserver")
    from src.app import main
    main.get_store.cache_clear()
    yield tmp_path


@pytest.fixture
def raw():
    return CsvFolderConnector(ROOT / "data").fetch()


class StaticConnector:
    """A second connector implementation: serves tables from memory."""
    name = "static"

    def __init__(self, tables):
        self.tables = tables

    def fetch(self):
        return {k: v.copy() for k, v in self.tables.items()}


def no_sleep(_seconds):
    pass
