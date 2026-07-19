"""Unit tests for process.py: the pure HTML parser and the directory walker.

Run with: pytest tests/test_process.py
(or, without pytest: python -m unittest tests.test_process)
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import process

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class ExtractHayDayDataTests(unittest.TestCase):
    def setUp(self):
        # extract_hay_day_data writes a .json next to the source HTML, so work
        # on a copy in a temp dir rather than mutating the checked-in fixture.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def _write_html(self, name: str, body: str) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_full_fixture_parses_all_fields(self):
        src = os.path.join(FIXTURES, "hayday_full.html")
        html_path = os.path.join(self.dir, "abc123.html")
        shutil.copy(src, html_path)

        data = process.extract_hay_day_data(html_path)

        # EMAIL_DATE comment appended to the export is picked up. The greedy
        # capture keeps the space the export leaves before the "-->" — this is
        # the real parser behaviour (see a checked-in downloads/*.json, e.g.
        # "...+00:00 "); update's normalize_date strips it later. Assert the
        # exact raw value, like ``locked``/``neighborhood`` below.
        self.assertEqual(data["email_date"], "2024-05-15T09:30:00+00:00 ")

        # Scalar fields.
        self.assertEqual(data["name"], "TestFarmer")
        self.assertEqual(data["age"], 30)
        self.assertEqual(data["farm_created"], "2023-01-15 10:00:00")
        self.assertEqual(data["farm_country"], "Germany")
        self.assertEqual(data["farm_ip"], "198.51.100.23")
        self.assertEqual(data["banned"], "not banned")
        # The regex ends at ``\.`` and get_text joins the trailing "." as its own
        # token, so ``locked`` keeps a trailing space — this is the real parser
        # behaviour (see a checked-in downloads/*.json), documented here.
        self.assertEqual(data["locked"], "not locked ")
        self.assertEqual(data["total_sessions"], 500)
        # Like ``locked``, the neighborhood name ends before a "." token and so
        # keeps a trailing space (real parser behaviour, see downloads/*.json).
        self.assertEqual(data["neighborhood"], "TestVille ")
        self.assertEqual(data["rank"], "leader")
        self.assertEqual(data["gems"], 42)
        self.assertEqual(data["reputation_level"], 3)
        self.assertEqual(data["experience_points"], 1500)
        self.assertEqual(data["level"], 25)
        self.assertEqual(data["coins"], 99999)
        self.assertEqual(data["gamecenter"], "U:0000fakegamecenterid0000")

        # Nested structures.
        self.assertEqual(
            data["vouchers"], {"blue": 10, "green": 20, "purple": 5, "gold": 1}
        )
        self.assertEqual(
            data["valley"],
            {
                "fuel": 7,
                "chickens": 3,
                "sanctuary_animals": 2,
                "sun_points": 4,
                "vouchers": {"blue": 6, "green": 8, "red": 9},
            },
        )

        # The Hay Day section comes *after* the Clash of Clans one, which has
        # its own name and "Connected social accounts" GameCenter line. The
        # parse starts at the Hay Day <h2> and walks forward, so none of the
        # Clash-only values may leak into the Hay Day result.
        self.assertNotEqual(data["name"], "TestPlayer")  # Clash name
        self.assertNotEqual(
            data["gamecenter"], "U:1111fakeclashgamecenter1111"
        )  # Clash GameCenter

    def test_writes_json_next_to_html(self):
        src = os.path.join(FIXTURES, "hayday_full.html")
        html_path = os.path.join(self.dir, "abc123.html")
        shutil.copy(src, html_path)

        data = process.extract_hay_day_data(html_path)

        json_path = os.path.join(self.dir, "abc123.json")
        self.assertTrue(os.path.exists(json_path))
        with open(json_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)

    def test_stops_at_next_section_header(self):
        # extract_hay_day_data collects <p> tags from the Hay Day <h2> and
        # breaks at the next <h2> (process.py:40). The fixture has a "Boom
        # Beach" section after Hay Day whose values must not leak in.
        src = os.path.join(FIXTURES, "hayday_trailing_section.html")
        html_path = os.path.join(self.dir, "trailing.html")
        shutil.copy(src, html_path)

        data = process.extract_hay_day_data(html_path)

        self.assertEqual(data["name"], "TestFarmer")
        self.assertEqual(data["gems"], 42)
        self.assertEqual(data["level"], 25)

    def test_missing_core_fields_raises(self):
        # No name/level/gems: has a Hay Day section but not the core fields.
        html = "<html><body><h2>Hay Day</h2><p>Nothing useful here.</p></body></html>"
        html_path = self._write_html("x.html", html)
        with self.assertRaises(ValueError) as ctx:
            process.extract_hay_day_data(html_path)
        message = str(ctx.exception)
        self.assertIn("name", message)
        self.assertIn("level", message)
        self.assertIn("gems", message)
        # A failed parse must not leave a JSON file behind.
        self.assertFalse(os.path.exists(os.path.join(self.dir, "x.json")))

    def test_no_hay_day_section_raises(self):
        html = "<html><body><h2>Clash of Clans</h2><p>Nope.</p></body></html>"
        html_path = self._write_html("y.html", html)
        with self.assertRaises(ValueError) as ctx:
            process.extract_hay_day_data(html_path)
        self.assertIn("no Hay Day section", str(ctx.exception))

    def test_optional_fields_absent_is_ok(self):
        # Core fields present but no neighborhood/rank/gamecenter lines: these
        # are optional and their absence must not raise, just be absent.
        html = (
            "<html><body><h2>Hay Day</h2>"
            "<p>Your name is Solo and age is 30.</p>"
            "<p>You have 10 gems available.</p>"
            "<p>Your experience level is 5.</p>"
            "</body></html>"
        )
        html_path = self._write_html("z.html", html)
        data = process.extract_hay_day_data(html_path)

        self.assertEqual(data["name"], "Solo")
        self.assertEqual(data["gems"], 10)
        self.assertEqual(data["level"], 5)
        self.assertNotIn("neighborhood", data)
        self.assertNotIn("rank", data)
        self.assertNotIn("gamecenter", data)


class ProcessDirectoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def _touch(self, name: str, content: str = "x") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_missing_directory_warns_and_returns(self):
        missing = os.path.join(self.dir, "not-there")
        with mock.patch.object(process, "extract_hay_day_data") as extract:
            # Must not raise, and must not attempt any parsing.
            process.process(missing)
        extract.assert_not_called()

    def test_skips_files_with_existing_json(self):
        self._touch("done.html")
        self._touch("done.json", "{}")
        with mock.patch.object(process, "extract_hay_day_data") as extract:
            process.process(self.dir)
        extract.assert_not_called()

    def test_ignores_expired_stubs(self):
        # .expired stubs are not .html, so process() must never touch them.
        self._touch("dead.expired", "EXPIRED")
        with mock.patch.object(process, "extract_hay_day_data") as extract:
            process.process(self.dir)
        extract.assert_not_called()

    def test_processes_html_missing_json(self):
        self._touch("new.html")
        with mock.patch.object(process, "extract_hay_day_data") as extract:
            process.process(self.dir)
        extract.assert_called_once_with(os.path.join(self.dir, "new.html"))

    def test_aggregates_failures_into_one_valueerror(self):
        self._touch("a.html")
        self._touch("b.html")
        with mock.patch.object(
            process, "extract_hay_day_data", side_effect=ValueError("boom")
        ):
            with self.assertRaises(ValueError) as ctx:
                process.process(self.dir)
        message = str(ctx.exception)
        # Both failing files named, and the count reported.
        self.assertIn("a.html", message)
        self.assertIn("b.html", message)
        self.assertIn("2 HTML file(s)", message)

    def test_one_failure_does_not_stop_the_rest(self):
        self._touch("ok.html")
        self._touch("bad.html")

        def fake_extract(path):
            if path.endswith("bad.html"):
                raise ValueError("boom")

        with mock.patch.object(
            process, "extract_hay_day_data", side_effect=fake_extract
        ) as extract:
            with self.assertRaises(ValueError):
                process.process(self.dir)
        # Both files were attempted even though one failed.
        self.assertEqual(extract.call_count, 2)


if __name__ == "__main__":
    unittest.main()
