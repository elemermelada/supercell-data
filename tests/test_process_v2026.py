"""Unit tests for process.py against the 2026-07 export layout.

Supercell reworked the data export around 2026-07: game sections dropped from
<h2> to <h3> (subsections <h3> -> <h4>), field lines moved from <p> tags into
<ul>/<li>, and most sentences were reworded (e.g. "Player name is ..." /
"Player age is ..." split across two lines, "Played N sessions",
"Reputation level is N ..."). process.py keeps both layout schemas and selects
whichever parses; these tests pin the new one. The legacy layout stays covered
by test_process.py.

Run with: pytest tests/test_process_v2026.py
(or, without pytest: python -m unittest tests.test_process_v2026)
"""

import json
import os
import shutil
import tempfile
import unittest

import process

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class ExtractHayDayDataV2026Tests(unittest.TestCase):
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

    def _copy_fixture(self, name: str = "abc123.html") -> str:
        src = os.path.join(FIXTURES, "hayday_v2026.html")
        html_path = os.path.join(self.dir, name)
        shutil.copy(src, html_path)
        return html_path

    def test_full_fixture_parses_all_fields(self):
        data = process.extract_hay_day_data(self._copy_fixture())

        # EMAIL_DATE comment appended to the export is picked up. The greedy
        # capture keeps the trailing space before "-->", exactly as in the
        # legacy layout (update.normalize_date strips it later).
        self.assertEqual(data["email_date"], "2024-05-15T09:30:00+00:00 ")

        # Scalar fields. name/age now arrive on two separate <li> lines.
        self.assertEqual(data["name"], "TestFarmer")
        self.assertEqual(data["age"], 30)
        self.assertEqual(data["farm_created"], "2023-01-15 10:00:00")
        self.assertEqual(data["farm_country"], "Germany")
        self.assertEqual(data["farm_ip"], "198.51.100.23")
        self.assertEqual(data["banned"], "not banned")
        # As in the legacy layout, the regex ends at "\." and get_text joins the
        # trailing "." as its own token, so ``locked`` keeps a trailing space.
        self.assertEqual(data["locked"], "not locked ")
        self.assertEqual(data["total_sessions"], 500)
        # Neighborhood name likewise ends before a "." token, keeping a space.
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

        # The Hay Day <h3> comes *after* the Clash of Clans <h3>, which has its
        # own player name and GameCenter line. The parse starts at the Hay Day
        # heading and stops at the next same-level (<h3>) heading, so no
        # Clash-only value may leak into the Hay Day result.
        self.assertNotEqual(data["name"], "TestPlayer")  # Clash name
        self.assertNotEqual(
            data["gamecenter"], "U:1111fakeclashgamecenter1111"
        )  # Clash GameCenter

    def test_writes_json_next_to_html(self):
        html_path = self._copy_fixture()
        data = process.extract_hay_day_data(html_path)

        json_path = os.path.join(self.dir, "abc123.json")
        self.assertTrue(os.path.exists(json_path))
        with open(json_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)

    def test_stops_at_subsection_but_not_at_h4(self):
        # Sanity check on the level-aware boundary: fields that live under the
        # Hay Day <h4> subsections (gems, level, coins, valley) must still be
        # collected, i.e. the walk does NOT stop at the deeper <h4> headings.
        data = process.extract_hay_day_data(self._copy_fixture())
        self.assertIn("gems", data)
        self.assertIn("level", data)
        self.assertIn("coins", data)
        self.assertIn("valley", data)

    def test_missing_core_fields_raises(self):
        # A Hay Day <h3> section with no usable field lines must fail loudly and
        # leave no JSON behind, just like the legacy layout.
        html = (
            "<html><body><h3>Hay Day</h3>"
            "<ul><li>Nothing useful here.</li></ul></body></html>"
        )
        html_path = self._write_html("x.html", html)
        with self.assertRaises(ValueError) as ctx:
            process.extract_hay_day_data(html_path)
        message = str(ctx.exception)
        self.assertIn("name", message)
        self.assertIn("level", message)
        self.assertIn("gems", message)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "x.json")))

    def test_optional_fields_absent_is_ok(self):
        # Core fields present but no neighborhood/rank/gamecenter lines: these
        # are optional and their absence must not raise, just be absent.
        html = (
            "<html><body><h3>Hay Day</h3>"
            "<ul>"
            "<li>Player name is <b>Solo</b></li>"
            "<li><b>10</b> gems</li>"
            "<li>Experience level is <b>5</b></li>"
            "</ul></body></html>"
        )
        html_path = self._write_html("z.html", html)
        data = process.extract_hay_day_data(html_path)

        self.assertEqual(data["name"], "Solo")
        self.assertEqual(data["gems"], 10)
        self.assertEqual(data["level"], 5)
        self.assertNotIn("neighborhood", data)
        self.assertNotIn("rank", data)
        self.assertNotIn("gamecenter", data)


if __name__ == "__main__":
    unittest.main()
