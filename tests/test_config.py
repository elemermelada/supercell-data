"""Unit tests for the config validators in config.py."""

import os
import unittest
from unittest import mock

import config
from config import (
    BROWSER_CHOICES,
    ConfigError,
    Settings,
    _parse_choice_env,
    _parse_int_env,
    _require,
)


class ParseIntEnvTests(unittest.TestCase):
    def test_uses_default_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_parse_int_env("N", 7, minimum=0), 7)

    def test_uses_default_when_blank(self):
        with mock.patch.dict(os.environ, {"N": "   "}, clear=True):
            self.assertEqual(_parse_int_env("N", 7, minimum=0), 7)

    def test_parses_valid_value(self):
        with mock.patch.dict(os.environ, {"N": " 42 "}, clear=True):
            self.assertEqual(_parse_int_env("N", 7, minimum=0), 42)

    def test_clamps_to_minimum(self):
        with mock.patch.dict(os.environ, {"N": "-3"}, clear=True):
            self.assertEqual(_parse_int_env("N", 7, minimum=1), 1)

    def test_default_below_minimum_is_clamped(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_parse_int_env("N", 0, minimum=1), 1)

    def test_malformed_raises_config_error(self):
        with mock.patch.dict(os.environ, {"N": "not-an-int"}, clear=True):
            with self.assertRaises(ConfigError) as ctx:
                _parse_int_env("N", 7, minimum=0)
        self.assertIn("N", str(ctx.exception))
        self.assertIn("integer", str(ctx.exception))


class ParseChoiceEnvTests(unittest.TestCase):
    def test_uses_default_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _parse_choice_env("B", "firefox", BROWSER_CHOICES), "firefox"
            )

    def test_lowercases_value(self):
        with mock.patch.dict(os.environ, {"B": "CHROME"}, clear=True):
            self.assertEqual(
                _parse_choice_env("B", "firefox", BROWSER_CHOICES), "chrome"
            )

    def test_invalid_choice_raises_config_error(self):
        with mock.patch.dict(os.environ, {"B": "safari"}, clear=True):
            with self.assertRaises(ConfigError) as ctx:
                _parse_choice_env("B", "firefox", BROWSER_CHOICES)
        self.assertIn("safari", str(ctx.exception))
        self.assertIn("firefox", str(ctx.exception))


class RequireTests(unittest.TestCase):
    def test_returns_values_in_order(self):
        self.assertEqual(_require(A="x", B="y"), ["x", "y"])

    def test_missing_names_are_listed(self):
        with self.assertRaises(ConfigError) as ctx:
            _require(A="x", B=None, C="")
        message = str(ctx.exception)
        self.assertIn("B", message)
        self.assertIn("C", message)
        self.assertNotIn(" A", message)


class SettingsRequireTests(unittest.TestCase):
    def _settings(self, **overrides):
        base = dict(
            imap_server="imap.example.com",
            email_user="user",
            email_pass="pass",
            sender_filter="from@example.com",
            state_file="state.json",
            retrieve_retry_interval=5,
            retrieve_max_attempts=12,
            user_agent="UA",
            browser_type="firefox",
            smtp_server="smtp.example.com",
            smtp_port=465,
            alert_email="alert@example.com",
            spreadsheet_id="sheet-id",
            sheet_name="Sheet1",
        )
        base.update(overrides)
        return Settings(**base)

    def test_require_imap_returns_triplet(self):
        self.assertEqual(
            self._settings().require_imap(),
            ("imap.example.com", "user", "pass"),
        )

    def test_require_imap_names_missing(self):
        with self.assertRaises(ConfigError) as ctx:
            self._settings(email_pass=None).require_imap()
        self.assertIn("EMAIL_PASS", str(ctx.exception))

    def test_require_sender_filter(self):
        self.assertEqual(self._settings().require_sender_filter(), "from@example.com")
        with self.assertRaises(ConfigError):
            self._settings(sender_filter=None).require_sender_filter()

    def test_require_sheets(self):
        self.assertEqual(self._settings().require_sheets(), ("sheet-id", "Sheet1"))
        with self.assertRaises(ConfigError) as ctx:
            self._settings(spreadsheet_id="", sheet_name=None).require_sheets()
        self.assertIn("SPREADSHEET_ID", str(ctx.exception))
        self.assertIn("SHEET_NAME", str(ctx.exception))


class SettingsLoadTests(unittest.TestCase):
    def test_load_reads_and_clamps(self):
        env = {
            "RETRIEVE_MAX_ATTEMPTS": "0",  # clamped up to 1
            "RETRIEVE_RETRY_INTERVAL": "-5",  # clamped up to 1
            "BROWSER_TYPE": "chrome",
            "SMTP_PORT": "587",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            s = config.Settings.load()
        self.assertEqual(s.retrieve_max_attempts, 1)
        self.assertEqual(s.retrieve_retry_interval, 1)
        self.assertEqual(s.browser_type, "chrome")
        self.assertEqual(s.smtp_port, 587)

    def test_alert_email_falls_back_to_email_user(self):
        with mock.patch.dict(os.environ, {"EMAIL_USER": "u@example.com"}, clear=True):
            s = config.Settings.load()
        self.assertEqual(s.alert_email, "u@example.com")

    def test_load_raises_on_malformed_int(self):
        with mock.patch.dict(os.environ, {"SMTP_PORT": "abc"}, clear=True):
            with self.assertRaises(ConfigError):
                config.Settings.load()


if __name__ == "__main__":
    unittest.main()
