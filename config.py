"""Centralized configuration: all environment access and validation.

Every value the pipeline reads from the environment is parsed and validated
here, once, when this module is imported. Malformed values (a non-integer
port, an unknown browser) fail immediately with a clear ``ConfigError``
instead of surfacing as a raw ``ValueError`` deep inside a step or, worse, only
when a function first runs.

Values that are required but have no default (IMAP credentials, the target
spreadsheet) are still read eagerly, but the "is it present?" check is deferred
to the step that needs it via the ``require_*`` helpers — so running one step
never fails because an unrelated step's variables are unset.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when environment configuration is missing or malformed."""


# Browser User-Agent sent with the GDPR submit request. Bump the Chrome
# version here periodically so it doesn't look stale; override via the
# USER_AGENT env var without touching the code.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)

BROWSER_CHOICES = frozenset({"firefox", "chrome"})


# ---------------------------------------------------------
# Validators — each raises ConfigError so failures are one
# greppable, catchable type instead of a mix of RuntimeError
# and raw ValueError.
# ---------------------------------------------------------
def _parse_int_env(name: str, default: int, minimum: int) -> int:
    """Parse an integer env var, raising ConfigError on malformed input and
    clamping to a sensible minimum (e.g. so attempts is never < 1 and the
    sleep interval is never negative)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = int(raw.strip())
        except ValueError:
            raise ConfigError(
                f"Invalid value for {name}: {raw!r} (expected an integer)"
            ) from None
    return max(minimum, value)


def _parse_choice_env(name: str, default: str, choices: frozenset[str]) -> str:
    """Parse a lowercased enum-style env var, raising ConfigError if the value
    isn't one of the allowed choices."""
    raw = os.getenv(name)
    value = default if raw is None or raw.strip() == "" else raw.strip().lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ConfigError(
            f"Invalid value for {name}: {value!r} (expected one of: {allowed})"
        )
    return value


def _require(**values: str | None) -> list[str]:
    """Return the given values in declared order, or raise ConfigError naming
    every one that is missing (unset or empty)."""
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )
    # All present and truthy after the check above; the ``is not None`` guard
    # narrows the type for mypy without changing the result.
    return [value for value in values.values() if value is not None]


# ---------------------------------------------------------
# The single settings object, built once at import time.
# ---------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    # --- IMAP / retrieve ---
    imap_server: str | None
    email_user: str | None
    email_pass: str | None
    sender_filter: str | None
    state_file: str
    retrieve_retry_interval: int
    retrieve_max_attempts: int
    # --- request ---
    user_agent: str
    browser_type: str
    # --- SMTP / notify ---
    smtp_server: str
    smtp_port: int
    alert_email: str | None
    # --- Google Sheets / update ---
    spreadsheet_id: str | None
    sheet_name: str | None

    @classmethod
    def load(cls) -> "Settings":
        """Read and validate every environment variable the pipeline uses.

        Malformed values raise ConfigError here; missing required values are
        stored as ``None`` and only rejected by the ``require_*`` helpers, so
        importing this module never fails just because one step's variables
        happen to be unset.
        """
        email_user = os.getenv("EMAIL_USER")
        return cls(
            imap_server=os.getenv("IMAP_SERVER"),
            email_user=email_user,
            email_pass=os.getenv("EMAIL_PASS"),
            sender_filter=os.getenv("SENDER_FILTER"),
            state_file=os.getenv("STATE_FILE", "state.json"),
            retrieve_retry_interval=_parse_int_env(
                "RETRIEVE_RETRY_INTERVAL", 5, minimum=0
            ),
            retrieve_max_attempts=_parse_int_env(
                "RETRIEVE_MAX_ATTEMPTS", 12, minimum=1
            ),
            user_agent=os.getenv("USER_AGENT", DEFAULT_USER_AGENT),
            browser_type=_parse_choice_env("BROWSER_TYPE", "firefox", BROWSER_CHOICES),
            smtp_server=os.getenv("SMTP_SERVER", "smtp.gmail.com"),
            smtp_port=_parse_int_env("SMTP_PORT", 465, minimum=1),
            # Alerts default to the sending account when ALERT_EMAIL is unset.
            alert_email=os.getenv("ALERT_EMAIL") or email_user,
            spreadsheet_id=os.getenv("SPREADSHEET_ID"),
            sheet_name=os.getenv("SHEET_NAME"),
        )

    def require_imap(self) -> tuple[str, str, str]:
        """IMAP server + credentials, or ConfigError naming what's missing."""
        server, user, password = _require(
            IMAP_SERVER=self.imap_server,
            EMAIL_USER=self.email_user,
            EMAIL_PASS=self.email_pass,
        )
        return server, user, password

    def require_sender_filter(self) -> str:
        """The sender to filter on, or ConfigError if unset."""
        (value,) = _require(SENDER_FILTER=self.sender_filter)
        return value

    def require_sheets(self) -> tuple[str, str]:
        """Target spreadsheet id + sheet name, or ConfigError naming what's
        missing."""
        spreadsheet_id, sheet_name = _require(
            SPREADSHEET_ID=self.spreadsheet_id,
            SHEET_NAME=self.sheet_name,
        )
        return spreadsheet_id, sheet_name


settings = Settings.load()
