"""Runtime configuration. Environment variables only, with defaults that make
the system runnable on a laptop with nothing installed."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # SQLite by default so `make dev` needs no services. Point this at
    # postgresql+psycopg://... in production; the application code is identical.
    database_url: str = os.environ.get("SAMVEDNA_DATABASE_URL", "sqlite:///samvedna.db")

    # Retention. Raw audio is purged aggressively; derived features and the SVI
    # persist for the life of the case because they are part of the record.
    audio_retention_days: int = _int("SAMVEDNA_AUDIO_RETENTION_DAYS", 30)
    transcript_retention_days: int = _int("SAMVEDNA_TRANSCRIPT_RETENTION_DAYS", 180)

    echo_sql: bool = os.environ.get("SAMVEDNA_ECHO_SQL", "").lower() in {"1", "true"}

    # Browser origins permitted to call this service. A wildcard on a service
    # that handles victim disclosures is not a default anyone should inherit,
    # so the default is the local development console and nothing else — a
    # deployed backend refuses browser traffic until an origin is named.
    allowed_origins: tuple = tuple(
        o.strip() for o in os.environ.get(
            "SAMVEDNA_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip())

    # Renders a permanent banner in the console. Set on every deployment that is
    # not a cleared production instance, which today is all of them.
    demo_banner: bool = os.environ.get("SAMVEDNA_DEMO_BANNER", "").lower() in {"1", "true"}


SETTINGS = Settings()
