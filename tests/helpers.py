"""Shared test fixtures/helpers.

These are used across the retrieve tests today and are expected to be reused by
future test modules, so they live here rather than being duplicated per file.
"""

import tempfile
import unittest
from email.message import EmailMessage
from unittest import mock

import retrieve


def build_email(
    url: str | None, date: str = "Mon, 01 Jan 2024 12:00:00 +0000"
) -> bytes:
    """Build a raw RFC822 email with an optional download link in the body."""
    msg = EmailMessage()
    msg["From"] = "noreply@supercell.com"
    msg["Subject"] = "Your data export"
    msg["Date"] = date
    body = "Hello.\n"
    if url:
        body += f"Download here: {url}\n"
    msg.set_content(body)
    return msg.as_bytes()


class FakeMail:
    """Minimal stand-in for an imaplib connection: only fetch() is used by
    process_email.

    ``fetch_status`` defaults to ``"OK"``; pass e.g. ``"NO"`` to exercise the
    fetch-failure branch in process_email.
    """

    def __init__(self, raw_email: bytes, fetch_status: str = "OK"):
        self._raw = raw_email
        self._fetch_status = fetch_status

    def fetch(self, email_id, spec):
        return self._fetch_status, [(b"1 (RFC822 {N}", self._raw)]


class TempDownloadDirMixin(unittest.TestCase):
    """Point ``retrieve.DOWNLOAD_DIR`` at a fresh temp dir for each test.

    Exposes the directory as ``self.download_dir``. ``download_file`` reads the
    module-global ``retrieve.DOWNLOAD_DIR``, so that is the name patched here.
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.download_dir = self._tmp.name
        patcher = mock.patch.object(retrieve, "DOWNLOAD_DIR", self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
