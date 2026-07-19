"""Unit tests for update.py: the pure transforms and the header logic.

No gspread network access — ``ensure_header_row`` is exercised against a
``Mock`` sheet. Run with: pytest tests/test_update.py
(or, without pytest: python -m unittest tests.test_update)
"""

import unittest
from unittest import mock

import update


class NormalizeDateTests(unittest.TestCase):
    def test_iso_becomes_canonical(self):
        self.assertEqual(
            update.normalize_date("2024-05-15T09:30:00+00:00"),
            "2024-05-15 09:30:00",
        )

    def test_strips_trailing_comment_marker(self):
        # normalize_date defensively strips a leftover "-->" from the appended
        # HTML comment before parsing.
        self.assertEqual(
            update.normalize_date("2024-05-15 09:30:00 -->"),
            "2024-05-15 09:30:00",
        )

    def test_garbage_returned_as_is(self):
        self.assertEqual(update.normalize_date("not a date"), "not a date")

    def test_empty_string_returns_empty(self):
        self.assertEqual(update.normalize_date(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(update.normalize_date(None), "")


class FlattenTests(unittest.TestCase):
    def test_empty_dict_yields_all_columns(self):
        flat = update.flatten({}, "uuid-1")
        self.assertEqual(set(flat.keys()), set(update.COLUMN_ORDER))
        self.assertEqual(flat["uuid"], "uuid-1")
        # Every absent field is None (except the normalized empty date).
        self.assertEqual(flat["email_date"], "")
        self.assertIsNone(flat["name"])

    def test_nested_vouchers_and_valley_are_flattened(self):
        data = {
            "email_date": "2024-05-15T09:30:00+00:00",
            "name": "FarmerJoe",
            "vouchers": {"blue": 3, "green": 4, "purple": 5, "gold": 6},
            "valley": {
                "fuel": 12,
                "chickens": 34,
                "sanctuary_animals": 7,
                "sun_points": 9,
                "vouchers": {"blue": 1, "green": 2, "red": 8},
            },
        }
        flat = update.flatten(data, "uuid-2")

        self.assertEqual(flat["email_date"], "2024-05-15 09:30:00")
        self.assertEqual(flat["name"], "FarmerJoe")
        self.assertEqual(flat["vouchers_blue"], 3)
        self.assertEqual(flat["vouchers_green"], 4)
        self.assertEqual(flat["vouchers_purple"], 5)
        self.assertEqual(flat["vouchers_gold"], 6)
        self.assertEqual(flat["valley_fuel"], 12)
        self.assertEqual(flat["valley_chickens"], 34)
        self.assertEqual(flat["valley_sanctuary_animals"], 7)
        self.assertEqual(flat["valley_sun_points"], 9)
        self.assertEqual(flat["valley_vouchers_blue"], 1)
        self.assertEqual(flat["valley_vouchers_green"], 2)
        self.assertEqual(flat["valley_vouchers_red"], 8)

    def test_missing_nested_blocks_yield_none(self):
        flat = update.flatten({"name": "Solo"}, "uuid-3")
        self.assertIsNone(flat["vouchers_blue"])
        self.assertIsNone(flat["valley_vouchers_red"])


class SortKeyTests(unittest.TestCase):
    def test_canonical_dates_sort_before_unparseable(self):
        rows = [
            {"email_date": ""},
            {"email_date": "2024-05-15 09:30:00"},
            {"email_date": "garbage"},
            {"email_date": "2023-01-01 00:00:00"},
        ]
        rows.sort(key=update._sort_key)
        dates = [r["email_date"] for r in rows]
        # Canonical dates first, in chronological order; everything else after.
        self.assertEqual(dates[0], "2023-01-01 00:00:00")
        self.assertEqual(dates[1], "2024-05-15 09:30:00")
        self.assertEqual(set(dates[2:]), {"", "garbage"})

    def test_missing_email_date_key_is_treated_as_empty(self):
        # A row without the key must not raise; it sorts with the unparseable set.
        self.assertEqual(update._sort_key({}), (1, ""))

    def test_deterministic(self):
        row = {"email_date": "2024-05-15 09:30:00"}
        self.assertEqual(update._sort_key(row), update._sort_key(row))
        self.assertEqual(update._sort_key(row), (0, "2024-05-15 09:30:00"))


class EnsureHeaderRowTests(unittest.TestCase):
    def test_creates_header_when_sheet_empty(self):
        sheet = mock.Mock()
        sheet.row_values.return_value = []

        header = update.ensure_header_row(sheet, [])

        sheet.insert_row.assert_called_once()
        (inserted_header, index), _ = sheet.insert_row.call_args
        self.assertEqual(index, 1)
        self.assertEqual(inserted_header, update.COLUMN_ORDER)
        self.assertEqual(header, update.COLUMN_ORDER)
        sheet.update.assert_not_called()

    def test_appends_new_columns_without_reordering(self):
        # Existing sheet has the columns in a *different* order plus an extra
        # historical one. That order must be preserved; only genuinely new
        # columns get appended at the end.
        existing = ["uuid", "name", "legacy_col", "email_date"]
        sheet = mock.Mock()
        sheet.row_values.return_value = list(existing)

        header = update.ensure_header_row(sheet, ["brand_new"])

        # Existing columns keep their positions and order.
        self.assertEqual(header[: len(existing)], existing)
        # New columns from COLUMN_ORDER and required_fields are appended.
        self.assertIn("brand_new", header)
        self.assertIn("gems", header)
        self.assertEqual(header.index("brand_new"), len(header) - 1)

        sheet.insert_row.assert_not_called()
        sheet.update.assert_called_once()
        # gspread 6 argument order: values first, then the range.
        (rows, rng), _ = sheet.update.call_args
        self.assertEqual(rng, "1:1")
        self.assertEqual(rows, [header])

    def test_no_write_when_header_already_complete(self):
        # Existing header already contains everything: no update call at all.
        existing = list(update.COLUMN_ORDER)
        sheet = mock.Mock()
        sheet.row_values.return_value = existing

        header = update.ensure_header_row(sheet, [])

        self.assertEqual(header, existing)
        sheet.update.assert_not_called()
        sheet.insert_row.assert_not_called()

    def test_unreadable_header_is_treated_as_empty(self):
        sheet = mock.Mock()
        sheet.row_values.side_effect = RuntimeError("api down")

        header = update.ensure_header_row(sheet, [])

        # Falls back to treating the sheet as empty and creates the header.
        sheet.insert_row.assert_called_once()
        self.assertEqual(header, update.COLUMN_ORDER)


class ImportGuardTests(unittest.TestCase):
    """The flatten()/COLUMN_ORDER sync guard runs at import time; this documents
    that importing the module (done above) validated the invariant."""

    def test_flatten_keys_match_column_order(self):
        self.assertEqual(set(update.flatten({}, "")), set(update.COLUMN_ORDER))


if __name__ == "__main__":
    unittest.main()
