"""Runtime settings, read from environment variables (and a local .env file).

Nothing secret is ever logged from here. Every value has a safe fallback so the
system runs with no credentials at all (SQLite, outbox emails, template text).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from a .env file without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class Settings:
    """Typed view over environment variables. Re-read on every access so tests
    can change the environment freely."""

    @property
    def on_vercel(self) -> bool:
        return bool(os.environ.get("VERCEL"))

    @property
    def var_dir(self) -> Path:
        if _env("FRESHROOTS_VAR_DIR"):
            return Path(_env("FRESHROOTS_VAR_DIR"))
        # Vercel's filesystem is read-only except /tmp.
        return Path("/tmp/freshroots") if self.on_vercel else ROOT / "var"

    @property
    def database_url(self) -> str:
        url = _env("DATABASE_URL")
        if not url:
            self.var_dir.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{(self.var_dir / 'freshroots.db').as_posix()}"
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url

    anthropic_api_key = property(lambda self: _env("ANTHROPIC_API_KEY"))
    anthropic_model = property(lambda self: _env("ANTHROPIC_MODEL", "claude-haiku-5-5"))

    email_provider = property(lambda self: _env("EMAIL_PROVIDER").lower())
    resend_api_key = property(lambda self: _env("RESEND_API_KEY"))
    smtp_host = property(lambda self: _env("SMTP_HOST"))
    smtp_port = property(lambda self: int(_env("SMTP_PORT", "587")))
    smtp_user = property(lambda self: _env("SMTP_USER"))
    smtp_password = property(lambda self: _env("SMTP_PASSWORD"))
    email_from = property(lambda self: _env("EMAIL_FROM", "Freshroots Planning <onboarding@resend.dev>"))

    @property
    def recipients(self) -> dict[str, list[str]]:
        def split(v: str) -> list[str]:
            return [x.strip() for x in v.split(",") if x.strip()]
        return {
            "raghav": split(_env("EMAIL_TO_RAGHAV")),
            "procurement": split(_env("EMAIL_TO_PROCUREMENT")),
            "deepa": split(_env("EMAIL_TO_DEEPA")),
        }

    app_url = property(lambda self: _env("APP_URL", "http://localhost:8000"))
    secret_key = property(lambda self: _env("SECRET_KEY", "dev-only-not-secret"))
    cron_secret = property(lambda self: _env("CRON_SECRET"))
    dashboard_users = property(lambda self: _env("DASHBOARD_USERS"))

    data_source_url = property(lambda self: _env("DATA_SOURCE_URL"))
    data_source_credentials = property(lambda self: _env("DATA_SOURCE_CREDENTIALS"))
    zoho_enabled = property(lambda self: _env("ZOHO_ENABLED", "false").lower() == "true")

    @property
    def outbox_dir(self) -> Path:
        path = self.var_dir / "outbox"
        path.mkdir(parents=True, exist_ok=True)
        return path


load_env_file()
settings = Settings()
