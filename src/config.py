from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    api_key: str
    database_url: str
    currency: str
    refresh_interval_seconds: int


def load_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL", "")
    api_key = os.getenv("PAYMENTS_API_KEY", "nvra_test_key_123456")
    currency = os.getenv("PAYMENTS_CURRENCY", "USD")
    refresh_interval = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "5"))

    return Settings(
        api_key=api_key,
        database_url=database_url,
        currency=currency,
        refresh_interval_seconds=refresh_interval,
    )
