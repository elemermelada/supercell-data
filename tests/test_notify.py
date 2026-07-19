"""Unit tests for notify.py: the failure-email body and the SMTP send path.

No real network/SMTP — ``smtplib.SMTP_SSL`` is mocked. Run with:
pytest tests/test_notify.py
(or, without pytest: python -m unittest tests.test_notify)
"""

import os
import tempfile
import types
import unittest
from unittest import mock

import notify


def _failures():
    return [
        ("request", RuntimeError("boom"), "Traceback: request blew up\n  line 1"),
        ("update", ValueError("nope"), "Traceback: update blew up\n  line 2"),
    ]


class BuildBodyTests(unittest.TestCase):
    def test_lists_every_step_name_and_traceback(self):
        body = notify._build_body(_failures())

        self.assertIn("2 failed step(s)", body)
        # Every step name appears.
        self.assertIn("Step: request", body)
        self.assertIn("Step: update", body)
        # Exception type + message rendered.
        self.assertIn("RuntimeError: boom", body)
        self.assertIn("ValueError: nope", body)
        # Full traceback text included.
        self.assertIn("request blew up", body)
        self.assertIn("update blew up", body)


class SendFailureEmailTests(unittest.TestCase):
    def setUp(self):
        # notify reads settings.* at call time; patch the whole settings object
        # so the tests don't depend on the real environment. Ensure creds are
        # present unless a test overrides them.
        self.settings = types.SimpleNamespace(
            email_user="sender@example.com",
            email_pass="secret",
            alert_email="alerts@example.com",
            smtp_server="smtp.example.com",
            smtp_port=465,
        )
        patcher = mock.patch.object(notify, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _log_file(self, contents="run log line\n"):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        )
        tmp.write(contents)
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.remove(tmp.name))
        return tmp.name

    def test_noop_on_empty_failures(self):
        with mock.patch.object(notify.smtplib, "SMTP_SSL") as smtp:
            notify.send_failure_email([], self._log_file())
        smtp.assert_not_called()

    def test_returns_when_creds_missing(self):
        self.settings.email_pass = None
        with mock.patch.object(notify.smtplib, "SMTP_SSL") as smtp:
            notify.send_failure_email(_failures(), self._log_file())
        # No attempt to connect when a credential is missing.
        smtp.assert_not_called()

    def test_sends_email_with_expected_headers_and_attachment(self):
        log_file = self._log_file("full run contents\n")
        with mock.patch.object(notify.smtplib, "SMTP_SSL") as smtp:
            notify.send_failure_email(_failures(), log_file)

        # Connected to the configured server/port and authenticated.
        smtp.assert_called_once()
        (server_arg, port_arg), _ = smtp.call_args
        self.assertEqual(server_arg, "smtp.example.com")
        self.assertEqual(port_arg, 465)

        server = smtp.return_value.__enter__.return_value
        server.login.assert_called_once_with("sender@example.com", "secret")
        server.send_message.assert_called_once()

        (msg,), _ = server.send_message.call_args
        self.assertEqual(msg["From"], "sender@example.com")
        self.assertEqual(msg["To"], "alerts@example.com")
        # Subject names the failed steps.
        self.assertIn("request", msg["Subject"])
        self.assertIn("update", msg["Subject"])

        # The log file is attached under its basename.
        attachments = list(msg.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), os.path.basename(log_file))
        # The attached payload is the log file's contents.
        content = attachments[0].get_content()
        if isinstance(content, bytes):
            content = content.decode()
        self.assertIn("full run contents", content)

    def test_missing_log_file_still_sends_email(self):
        missing = os.path.join(tempfile.gettempdir(), "does-not-exist-xyz.log")
        self.assertFalse(os.path.exists(missing))

        with mock.patch.object(notify.smtplib, "SMTP_SSL") as smtp:
            notify.send_failure_email(_failures(), missing)

        # Email is still sent, just without an attachment.
        server = smtp.return_value.__enter__.return_value
        server.send_message.assert_called_once()
        (msg,), _ = server.send_message.call_args
        self.assertEqual(list(msg.iter_attachments()), [])

    def test_smtp_error_is_swallowed(self):
        # A send failure must not propagate (it would mask the original errors).
        with mock.patch.object(
            notify.smtplib, "SMTP_SSL", side_effect=OSError("connection refused")
        ):
            notify.send_failure_email(_failures(), self._log_file())


if __name__ == "__main__":
    unittest.main()
