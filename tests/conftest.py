"""Test-session bootstrap.

Importing any project module (``main`` → ``retrieve``/``update`` → ``config``)
triggers ``config.load_dotenv()`` plus eager validation at import time. A
developer with a malformed local ``.env`` (a non-integer ``SMTP_PORT``, an
unknown ``BROWSER_TYPE``) would otherwise get a collection-time ``ConfigError``
in *every* test file, before a single test runs.

pytest imports this ``conftest`` before collecting the test modules in this
directory, so setting known-good values here — directly in ``os.environ``, which
``load_dotenv(override=False)`` will not clobber — normalizes the eagerly
validated variables before ``config`` is ever imported. The unit tests mock all
I/O and never depend on real credentials, so deterministic values are correct.
"""

import os

# Only the eagerly *validated* variables need normalizing: a bad value in any of
# these raises ConfigError at import. Required-but-unvalidated secrets (IMAP/SMTP
# creds, spreadsheet ids) are read lazily via config's require_* helpers, so a
# missing value never breaks import — leave them alone.
_SAFE_ENV = {
    "BROWSER_TYPE": "firefox",
    "SMTP_PORT": "465",
    "RETRIEVE_RETRY_INTERVAL": "5",
    "RETRIEVE_MAX_ATTEMPTS": "12",
}

for _name, _value in _SAFE_ENV.items():
    os.environ[_name] = _value
