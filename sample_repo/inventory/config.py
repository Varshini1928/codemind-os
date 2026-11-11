"""Configuration read from environment variables."""
import os


def database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///inventory.db")


def api_port() -> int:
    return int(os.environ.get("API_PORT", "8080"))


def smtp_host() -> str:
    return os.getenv("SMTP_HOST", "localhost")
