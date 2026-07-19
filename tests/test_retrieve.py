"""Unit tests for retrieve.py: the pure helpers, the filesystem-dedup / skip
path, the single-attempt state logic and the polling wrapper.

Run with: pytest tests/test_retrieve.py
(or, without pytest: python -m unittest tests.test_retrieve)
"""

import imaplib
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from email.message import EmailMessage
from unittest import mock

import retrieve
from tests.helpers import FakeMail, TempDownloadDirMixin, build_email


class ExtractDownloadLinkTests(unittest.TestCase):
    def test_accepts_supercell_html_link(self):
        url = "https://mydata.supercell.com/data/abc123DEF-_.html"
        self.assertEqual(retrieve.extract_download_link(f"see {url} now"), url)

    def test_rejects_non_supercell_host(self):
        body = "https://evil.example.com/data/abc.html"
        self.assertIsNone(retrieve.extract_download_link(body))

    def test_rejects_http_scheme(self):
        body = "http://mydata.supercell.com/data/abc.html"
        self.assertIsNone(retrieve.extract_download_link(body))

    def test_strips_query_string_tail(self):
        # A trailing query string is not part of the on-disk filename; the
        # regex must stop at ``.html`` and return only the clean URL.
        clean = "https://mydata.supercell.com/data/abc123.html"
        self.assertEqual(
            retrieve.extract_download_link(f"{clean}?token=evil&x=1"), clean
        )

    def test_rejects_path_traversal_tail(self):
        # Slashes and dots outside the filename char class must not match, so a
        # traversal attempt can't produce a link (and thus a filename).
        body = "https://mydata.supercell.com/data/../../etc/passwd.html"
        self.assertIsNone(retrieve.extract_download_link(body))


class ExtractPlaintextTests(unittest.TestCase):
    def test_plain_message(self):
        msg = EmailMessage()
        msg.set_content("just text\n")
        self.assertIn("just text", retrieve.extract_plaintext(msg))

    def test_multipart_prefers_text_plain(self):
        msg = EmailMessage()
        msg.set_content("the plain part\n")
        msg.add_alternative("<p>the html part</p>", subtype="html")
        self.assertTrue(msg.is_multipart())
        body = retrieve.extract_plaintext(msg)
        self.assertIn("the plain part", body)
        self.assertNotIn("html part", body)


class EnsureAwareTests(unittest.TestCase):
    def test_naive_becomes_utc(self):
        naive = datetime(2024, 1, 1, 12, 0)
        aware = retrieve.ensure_aware(naive)
        self.assertEqual(aware.tzinfo, UTC)

    def test_aware_is_unchanged(self):
        aware = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        self.assertIs(retrieve.ensure_aware(aware), aware)


class ConvertDateTests(unittest.TestCase):
    def test_iso_to_imap_format(self):
        self.assertEqual(retrieve.convert_date("2024-01-15"), "15-Jan-2024")


class SearchEmailsTests(unittest.TestCase):
    def test_escapes_quotes_and_backslashes_in_sender(self):
        mail = mock.Mock()
        mail.search.return_value = ("OK", [b"1 2"])

        retrieve.search_emails(mail, sender='a"b\\c', since_date="2024-01-01")

        mail.select.assert_called_once_with("INBOX")
        (_, query), _ = mail.search.call_args
        # Backslash doubled, quote backslash-escaped, so neither can break out
        # of the IMAP quoted string.
        self.assertIn('FROM "a\\"b\\\\c"', query)

    def test_raises_when_search_not_ok(self):
        mail = mock.Mock()
        mail.search.return_value = ("NO", [b""])
        with self.assertRaises(RuntimeError):
            retrieve.search_emails(mail, sender="x@y.com", since_date="2024-01-01")


class ConnectImapTests(unittest.TestCase):
    def test_connects_logs_in_and_returns_mail(self):
        # connect_imap must SSL-connect to the configured server and log in with
        # the configured credentials, returning the live connection object.
        conn = mock.Mock()
        with (
            mock.patch.object(retrieve, "settings") as settings_mock,
            mock.patch.object(retrieve.imaplib, "IMAP4_SSL", return_value=conn) as ssl,
        ):
            settings_mock.require_imap.return_value = (
                "imap.example.com",
                "user@example.com",
                "hunter2",
            )
            result = retrieve.connect_imap()

        ssl.assert_called_once_with("imap.example.com")
        conn.login.assert_called_once_with("user@example.com", "hunter2")
        self.assertIs(result, conn)


class DownloadFileTests(TempDownloadDirMixin):
    URL = "https://mydata.supercell.com/data/xyz789.html"
    FILENAME = "xyz789.html"

    def test_passes_http_timeout(self):
        # The request must be bounded by HTTP_TIMEOUT so a hung server can't
        # stall the whole pipeline.
        fake_resp = mock.Mock(status_code=200, content=b"<html></html>")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp) as get:
            retrieve.download_file(self.URL)

        _, kwargs = get.call_args
        self.assertEqual(kwargs.get("timeout"), retrieve.HTTP_TIMEOUT)

    def test_timeout_returns_failed_and_leaves_no_file(self):
        # A requests timeout is a transient failure: no file, no stub, no tmp.
        with mock.patch.object(
            retrieve.requests, "get", side_effect=retrieve.requests.exceptions.Timeout
        ):
            status, path = retrieve.download_file(self.URL)

        self.assertEqual(status, "failed")
        self.assertIsNone(path)
        self.assertEqual(os.listdir(self.download_dir), [])

    def test_atomic_write_replaces_from_tmp_and_writes_full_content(self):
        # The export must be written to a ``.tmp`` sibling and then os.replace'd
        # into place, so a crash mid-write can never leave a truncated .html
        # that dedup would treat as complete forever.
        filepath = os.path.join(self.download_dir, self.FILENAME)
        tmp_path = f"{filepath}.tmp"

        real_replace = os.replace
        seen = {}

        def spy_replace(src, dst):
            # At replace time the payload lives in the tmp file, not the target.
            seen["src"] = src
            seen["src_exists_before"] = os.path.exists(src)
            seen["dst_exists_before"] = os.path.exists(dst)
            return real_replace(src, dst)

        fake_resp = mock.Mock(status_code=200, content=b"<html>full</html>")
        with (
            mock.patch.object(retrieve.requests, "get", return_value=fake_resp),
            mock.patch.object(retrieve.os, "replace", side_effect=spy_replace),
        ):
            status, path = retrieve.download_file(self.URL)

        self.assertEqual(status, "downloaded")
        self.assertEqual(path, filepath)
        self.assertEqual(seen["src"], tmp_path)
        self.assertTrue(seen["src_exists_before"])
        self.assertFalse(seen["dst_exists_before"])
        # Final file holds the whole payload; the tmp file is gone.
        with open(filepath, "rb") as f:
            self.assertEqual(f.read(), b"<html>full</html>")
        self.assertFalse(os.path.exists(tmp_path))


class ProcessEmailDedupTests(TempDownloadDirMixin):
    URL = "https://mydata.supercell.com/data/abc123DEF-_.html"
    FILENAME = "abc123DEF-_.html"
    STUB_FILENAME = "abc123DEF-_.expired"

    def test_fetches_requested_id_with_rfc822_spec(self):
        # process_email must fetch the exact id it is given, with the (RFC822)
        # spec — a wrong spec would fetch headers only and break body parsing.
        mail = FakeMail(build_email(self.URL))
        fake_resp = mock.Mock(status_code=200, content=b"<html></html>")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp):
            retrieve.process_email(mail, b"42")

        self.assertEqual(mail.fetch_calls, [(b"42", "(RFC822)")])

    def test_downloads_when_file_absent(self):
        mail = FakeMail(build_email(self.URL))

        fake_resp = mock.Mock(status_code=200, content=b"<html></html>")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp) as get:
            _, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "downloaded")
        get.assert_called_once()
        self.assertTrue(
            os.path.exists(os.path.join(self.download_dir, self.FILENAME)),
            "expected the export file to be written to the downloads dir",
        )
        # The atomic-write temp file must not be left behind.
        self.assertFalse(
            os.path.exists(os.path.join(self.download_dir, self.FILENAME + ".tmp"))
        )

    def test_creates_download_dir_when_missing(self):
        # os.makedirs runs only after the HTTP call, so a missing DOWNLOAD_DIR
        # must still end up created and the file written into it.
        missing_dir = os.path.join(self.download_dir, "nested", "not-yet")
        self.assertFalse(os.path.exists(missing_dir))

        mail = FakeMail(build_email(self.URL))
        fake_resp = mock.Mock(status_code=200, content=b"<html></html>")
        with (
            mock.patch.object(retrieve, "DOWNLOAD_DIR", missing_dir),
            mock.patch.object(retrieve.requests, "get", return_value=fake_resp),
        ):
            _, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "downloaded")
        self.assertTrue(
            os.path.exists(os.path.join(missing_dir, self.FILENAME)),
            "download_file must create DOWNLOAD_DIR before writing the export",
        )

    def test_returns_parsed_tz_aware_date(self):
        mail = FakeMail(build_email(self.URL, date="Wed, 15 May 2024 09:30:00 +0200"))
        fake_resp = mock.Mock(status_code=200, content=b"<html></html>")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp):
            email_date, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "downloaded")
        self.assertIsNotNone(email_date)
        self.assertIsNotNone(email_date.tzinfo)
        # +0200 local -> 07:30 UTC.
        self.assertEqual(
            email_date.astimezone(UTC),
            datetime(2024, 5, 15, 7, 30, tzinfo=UTC),
        )

    def test_fetch_failure_returns_failed(self):
        # A non-"OK" fetch status short-circuits before any parsing/download.
        mail = FakeMail(build_email(self.URL), fetch_status="NO")
        with mock.patch.object(retrieve.requests, "get") as get:
            email_date, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "failed")
        self.assertIsNone(email_date)
        get.assert_not_called()

    def test_skips_when_file_already_exists(self):
        # Pre-create the file so it looks already-downloaded.
        with open(os.path.join(self.download_dir, self.FILENAME), "w") as f:
            f.write("existing")

        mail = FakeMail(build_email(self.URL))

        with mock.patch.object(retrieve.requests, "get") as get:
            _, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "skipped")
        get.assert_not_called()  # dedup must avoid the HTTP request entirely

    def test_expired_link_writes_stub_but_does_not_count_as_downloaded(self):
        mail = FakeMail(build_email(self.URL))

        fake_resp = mock.Mock(status_code=403, content=b"")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp):
            _, status = retrieve.process_email(mail, b"1")

        # An expired link is a failure (the run should keep polling / raise),
        # but a stub is written so the dead link is never re-requested.
        self.assertEqual(status, "failed")
        stub_path = os.path.join(self.download_dir, self.STUB_FILENAME)
        self.assertTrue(os.path.exists(stub_path))
        with open(stub_path, encoding="utf-8") as f:
            self.assertIn("EXPIRED", f.read())

        # The stub must NOT be a .html file, or process() would pick it up and
        # fail parsing it on every run.
        self.assertFalse(
            os.path.exists(os.path.join(self.download_dir, self.FILENAME)),
            "expired stub must not be written with a .html extension",
        )

        # A second pass must dedup on the stub and not hit the network again.
        with mock.patch.object(retrieve.requests, "get") as get:
            _, status_again = retrieve.process_email(mail, b"1")
        self.assertEqual(status_again, "skipped")
        get.assert_not_called()

    def test_transient_error_leaves_no_file(self):
        mail = FakeMail(build_email(self.URL))

        fake_resp = mock.Mock(status_code=500, content=b"")
        with mock.patch.object(retrieve.requests, "get", return_value=fake_resp):
            _, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "failed")
        self.assertFalse(os.path.exists(os.path.join(self.download_dir, self.FILENAME)))

    def test_no_link_in_body(self):
        mail = FakeMail(build_email(None))

        with mock.patch.object(retrieve.requests, "get") as get:
            _, status = retrieve.process_email(mail, b"1")

        self.assertEqual(status, "failed")
        get.assert_not_called()


class RetrieveOnceStatusTests(unittest.TestCase):
    """Option-B semantics: a download ends the poll; a skip only repairs state."""

    DATE = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

    def _run(self, statuses, last_date=None):
        # statuses: list of (email_date, status) that process_email returns.
        last_date = last_date if last_date is not None else retrieve.DEFAULT_SINCE
        with (
            mock.patch.object(retrieve, "connect_imap", return_value=mock.Mock()),
            mock.patch.object(
                retrieve, "search_emails", return_value=[b"1"] * len(statuses)
            ),
            mock.patch.object(retrieve, "process_email", side_effect=statuses),
            mock.patch.object(retrieve, "write_last_email_date") as write,
        ):
            downloaded = retrieve._retrieve_once("sender", last_date)
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

    def test_writes_the_newest_date_across_emails(self):
        older = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        newest = datetime(2024, 3, 9, 8, 0, tzinfo=UTC)
        middle = datetime(2024, 2, 1, 0, 0, tzinfo=UTC)
        downloaded, write = self._run(
            [
                (older, "downloaded"),
                (newest, "downloaded"),
                (middle, "downloaded"),
            ]
        )
        self.assertEqual(downloaded, 3)
        write.assert_called_once_with(newest)

    def test_state_never_goes_backwards(self):
        # An email older than the resumed state must not rewind the saved date.
        last_date = datetime(2025, 1, 1, tzinfo=UTC)
        older = datetime(2024, 6, 1, tzinfo=UTC)
        downloaded, write = self._run([(older, "downloaded")], last_date=last_date)
        self.assertEqual(downloaded, 1)
        write.assert_called_once_with(last_date)


class RetrievePollingTests(unittest.TestCase):
    """The retrieve() polling wrapper: retries, fail-fast and early stop."""

    MAX_ATTEMPTS = 3

    def setUp(self):
        # settings is a frozen dataclass, so replace the whole object rather
        # than trying to setattr a method onto a frozen instance.
        settings_mock = mock.patch.object(retrieve, "settings").start()
        settings_mock.require_sender_filter.return_value = "sender"
        self.addCleanup(mock.patch.stopall)

        mock.patch.object(
            retrieve, "read_last_email_date", return_value=retrieve.DEFAULT_SINCE
        ).start()
        mock.patch.object(retrieve, "RETRIEVE_MAX_ATTEMPTS", self.MAX_ATTEMPTS).start()
        mock.patch.object(retrieve, "RETRIEVE_RETRY_INTERVAL", 0).start()
        self.sleep = mock.patch.object(retrieve, "sleep").start()

    def _patch_once(self, side_effect):
        return mock.patch.object(
            retrieve, "_retrieve_once", side_effect=side_effect
        ).start()

    def test_transient_errors_retry_then_raise(self):
        err = imaplib.IMAP4.error("temporary")
        once = self._patch_once([err, err, err])

        with self.assertRaises(RuntimeError):
            retrieve.retrieve()

        self.assertEqual(once.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)  # between attempts, not after

    def test_permission_error_propagates_immediately(self):
        once = self._patch_once(PermissionError("read-only"))

        with self.assertRaises(PermissionError):
            retrieve.retrieve()

        self.assertEqual(once.call_count, 1)  # no retry on a permanent local failure
        self.sleep.assert_not_called()

    def test_success_on_later_attempt_stops_polling(self):
        once = self._patch_once([0, 0, 1])

        retrieve.retrieve()  # returns without raising

        self.assertEqual(once.call_count, 3)  # stopped as soon as one downloaded


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
