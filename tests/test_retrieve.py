"""Unit tests for retrieve.py, focused on the filesystem-dedup / skip path
and state read/write roundtrip.

Run with: python -m unittest test_retrieve
"""

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from email.message import EmailMessage
from unittest import mock

import retrieve


def _build_email(
    url: str | None, date: str = "Mon, 01 Jan 2024 12:00:00 +0000"
) -> bytes:
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
    process_email."""

    def __init__(self, raw_email: bytes):
        self._raw = raw_email

    def fetch(self, email_id, spec):
        return "OK", [(b"1 (RFC822 {N}", self._raw)]


class ProcessEmailDedupTests(unittest.TestCase):
    URL = "https://mydata.supercell.com/data/abc123DEF-_.html"
    FILENAME = "abc123DEF-_.html"
    STUB_FILENAME = "abc123DEF-_.expired"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Point downloads at a temp dir for the duration of each test.
        patcher = mock.patch.object(retrieve, "DOWNLOAD_DIR", self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_downloads_when_file_absent(self):
        mail = FakeMail(_build_email(self.URL))

        fake_resp = mock.Mock(status_code=200, content=b"<html></html>")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp) as get:
            email_date, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "downloaded")
        get.assert_called_once()
        self.assertTrue(
            os.path.exists(os.path.join(self._tmp.name, self.FILENAME)),
            "expected the export file to be written to the downloads dir",
        )
        # The atomic-write temp file must not be left behind.
        self.assertFalse(
            os.path.exists(os.path.join(self._tmp.name, self.FILENAME + ".tmp"))
        )

    def test_skips_when_file_already_exists(self):
        # Pre-create the file so it looks already-downloaded.
        with open(os.path.join(self._tmp.name, self.FILENAME), "w") as f:
            f.write("existing")

        mail = FakeMail(_build_email(self.URL))

        with mock.patch.object(retrieve.requests, "get") as get:
            email_date, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "skipped")
        get.assert_not_called()  # dedup must avoid the HTTP request entirely

    def test_expired_link_writes_stub_but_does_not_count_as_downloaded(self):
        mail = FakeMail(_build_email(self.URL))

        fake_resp = mock.Mock(status_code=403, content=b"")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp):
            email_date, status = retrieve.process_email(mail, b"1")

        # An expired link is a failure (the run should keep polling / raise),
        # but a stub is written so the dead link is never re-requested.
        self.assertEqual(status, "failed")
        stub_path = os.path.join(self._tmp.name, self.STUB_FILENAME)
        self.assertTrue(os.path.exists(stub_path))
        with open(stub_path, encoding="utf-8") as f:
            self.assertIn("EXPIRED", f.read())

        # The stub must NOT be a .html file, or process() would pick it up and
        # fail parsing it on every run.
        self.assertFalse(
            os.path.exists(os.path.join(self._tmp.name, self.FILENAME)),
            "expired stub must not be written with a .html extension",
        )

        # A second pass must dedup on the stub and not hit the network again.
        with mock.patch.object(retrieve.requests, "get") as get:
            _, status_again = retrieve.process_email(mail, b"1")
        self.assertEqual(status_again, "skipped")
        get.assert_not_called()

    def test_transient_error_leaves_no_file(self):
        mail = FakeMail(_build_email(self.URL))

        fake_resp = mock.Mock(status_code=500, content=b"")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp):
            email_date, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "failed")
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, self.FILENAME)))

    def test_no_link_in_body(self):
        mail = FakeMail(_build_email(None))

        with mock.patch.object(retrieve.requests, "get") as get:
            email_date, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "failed")
        get.assert_not_called()


class RetrieveOnceStatusTests(unittest.TestCase):
    """Option-B semantics: a download ends the poll; a skip only repairs state."""

    DATE = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

    def _run(self, statuses):
        # statuses: list of (email_date, status) that process_email returns.
        with (
            mock.patch.object(retrieve, "connect_imap", return_value=mock.Mock()),
            mock.patch.object(
                retrieve, "search_emails", return_value=[b"1"] * len(statuses)
            ),
            mock.patch.object(retrieve, "process_email", side_effect=statuses),
            mock.patch.object(retrieve, "write_last_email_date") as write,
        ):
            downloaded = retrieve._retrieve_once("sender", retrieve.DEFAULT_SINCE)
        return downloaded, write

    def test_download_counts_and_writes_state(self):
        downloaded, write = self._run([(self.DATE, "downloaded")])
        self.assertEqual(downloaded, 1)
        write.assert_called_once()

    def test_skip_repairs_state_but_does_not_end_poll(self):
        # A skip advances/writes state but returns 0, so retrieve() keeps polling.
        downloaded, write = self._run([(self.DATE, "skipped")])
        self.assertEqual(downloaded, 0)
        write.assert_called_once()

    def test_failure_writes_nothing(self):
        downloaded, write = self._run([(None, "failed")])
        self.assertEqual(downloaded, 0)
        write.assert_not_called()


class StateRoundtripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_file = os.path.join(self._tmp.name, "state.json")
        patcher = mock.patch.object(retrieve, "STATE_FILE", self.state_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_write_then_read(self):
        dt = datetime(2025, 6, 1, 8, 30, tzinfo=UTC)
        retrieve.write_last_email_date(dt)

        with open(self.state_file, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(list(payload.keys()), ["last_email_date"])

        self.assertEqual(retrieve.read_last_email_date(), dt)

    def test_missing_state_returns_default(self):
        self.assertEqual(retrieve.read_last_email_date(), retrieve.DEFAULT_SINCE)

    def test_legacy_state_with_extra_fields_is_read(self):
        # Old state files carried a downloaded_ids map; it must be ignored,
        # not crash, and not trigger any migration.
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "last_email_date": "2025-06-01T08:30:00+00:00",
                    "downloaded_ids": {"<x@y>": "2025-06-01T08:30:00+00:00"},
                },
                f,
            )
        self.assertEqual(
            retrieve.read_last_email_date(), datetime(2025, 6, 1, 8, 30, tzinfo=UTC)
        )


if __name__ == "__main__":
    unittest.main()
