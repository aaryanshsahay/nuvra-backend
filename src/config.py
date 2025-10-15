from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    currency: str
    refresh_interval_seconds: float


def load_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL", "")
    currency = os.getenv("PAYMENTS_CURRENCY", "USD")
    refresh_interval = float(os.getenv("DASHBOARD_REFRESH_SECONDS", "0.1"))

    return Settings(
        database_url=database_url,
        currency=currency,
        refresh_interval_seconds=refresh_interval,
    )
