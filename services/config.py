"""Runtime configuration. Environment variables only, with defaults that make
the system runnable on a laptop with nothing installed."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# Drivers SQLAlchemy needs told about explicitly. Hosted Postgres providers
# hand out `postgres://` or `postgresql://` URLs; SQLAlchemy 2 will not pick a
# driver from those on its own and fails at connect time with an error that
# reads like a missing package rather than a missing driver name.
_POSTGRES_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg2"}
_TARGET_SCHEME = "postgresql+psycopg"

# Managed Postgres is remote by definition, so TLS is not optional. Neon,
# Supabase, Render and Railway all require it; some include it in the URL they
# give you and some do not, and the ones that do not fail with a connection
# error that says nothing about TLS.
_SSL_REQUIRED_HOST_HINTS = ("neon.tech", "supabase.co", "supabase.com",
                            "render.com", "railway.app", "rds.amazonaws.com",
                            "azure.com", "cockroachlabs.cloud")


def normalise_database_url(url: str) -> str:
    """Make a provider's connection string usable, without silently changing it.

    Two corrections, both for failures whose error messages point somewhere
    unhelpful:

    * the driver. `postgres://` is what most dashboards copy out, and it is not
      a scheme SQLAlchemy 2 resolves.
    * `sslmode=require` for hosts that are obviously managed Postgres, where its
      absence surfaces as a bare connection refusal.

    An explicit `sslmode` is never overridden — someone who set `verify-full`
    means it, and quietly downgrading a TLS setting is not a thing this should
    do to anyone, let alone on a service carrying victim disclosures.
    """
    if not url or url.startswith("sqlite"):
        return url

    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme in _POSTGRES_SCHEMES:
        scheme = _TARGET_SCHEME

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    host = (parts.hostname or "").lower()
    if (scheme.startswith("postgresql")
            and "sslmode" not in query
            and any(hint in host for hint in _SSL_REQUIRED_HOST_HINTS)):
        query["sslmode"] = "require"

    # psycopg3 does not accept libpq's channel_binding as a connection keyword
    # through SQLAlchemy's URL; Neon includes it by default and it is not needed
    # once sslmode is set.
    query.pop("channel_binding", None)

    return urlunsplit((scheme, parts.netloc, parts.path,
                       urlencode(query), parts.fragment))


def redact_database_url(url: str) -> str:
    """For logs and health output. A connection string in a log line is a
    credential in a log line."""
    if not url or "://" not in url:
        return url
    parts = urlsplit(url)
    if not parts.password:
        return url
    netloc = parts.netloc.replace(f":{parts.password}", ":***")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # SQLite by default so `make dev` needs no services. Point this at
    # postgresql+psycopg://... in production; the application code is identical.
    database_url: str = normalise_database_url(
        os.environ.get("SAMVEDNA_DATABASE_URL", "sqlite:///samvedna.db"))

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

    # Operator identities permitted to use this instance. Empty means any
    # non-empty identity is accepted, which is correct behind the ministry's
    # gateway — it authenticates, this service trusts the header it sets. On a
    # demo instance with no gateway in front of it, setting this turns the
    # header into a shared secret and is the difference between a link you can
    # hand to judges and an open triage endpoint.
    allowed_operators: tuple = tuple(
        o.strip() for o in os.environ.get("SAMVEDNA_ALLOWED_OPERATORS", "").split(",")
        if o.strip())

    # Renders a permanent banner in the console. Set on every deployment that is
    # not a cleared production instance, which today is all of them.
    demo_banner: bool = os.environ.get("SAMVEDNA_DEMO_BANNER", "").lower() in {"1", "true"}


SETTINGS = Settings()
