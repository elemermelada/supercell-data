"""Unit tests for update.py: the pure transforms and the header logic.

No gspread network access — ``ensure_header_row`` is exercised against a
``Mock`` sheet. Run with: pytest tests/test_update.py
(or, without pytest: python -m unittest tests.test_update)
"""

import json
import os
import tempfile
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


class UpdateEntryPointTests(unittest.TestCase):
    """The update() entry point: auth wiring, sort, dedup and batch write.

    gspread and the service-account credentials are fully mocked; the sheet is a
    ``Mock`` so no network access happens.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

        settings_mock = mock.patch.object(update, "settings").start()
        settings_mock.require_sheets.return_value = ("sheet-id", "Tab1")
        mock.patch.object(update, "Credentials").start()
        self.addCleanup(mock.patch.stopall)

        # gspread.authorize(...) -> gc.open_by_key(...).worksheet(...) -> sheet
        self.sheet = mock.Mock()
        # A fresh (empty) sheet: header gets created, no pre-existing uuids.
        self.sheet.row_values.return_value = []
        self.sheet.col_values.return_value = []
        gc = mock.Mock()
        gc.open_by_key.return_value.worksheet.return_value = self.sheet
        mock.patch.object(update.gspread, "authorize", return_value=gc).start()

    def _write_json(self, name, data):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_missing_directory_warns_and_returns(self):
        missing = os.path.join(self.dir, "nope")
        # No auth, no write — just a graceful no-op like process().
        update.update(directory=missing)
        self.sheet.append_rows.assert_not_called()

    def test_no_json_files_raises(self):
        with self.assertRaises(RuntimeError):
            update.update(directory=self.dir)
        self.sheet.append_rows.assert_not_called()

    def test_appends_new_rows_sorted_by_date(self):
        self._write_json(
            "uuid-b.json", {"email_date": "2024-03-09T08:00:00+00:00", "name": "B"}
        )
        self._write_json(
            "uuid-a.json", {"email_date": "2024-01-01T12:00:00+00:00", "name": "A"}
        )

        update.update(directory=self.dir)

        self.sheet.append_rows.assert_called_once()
        (rows,), kwargs = self.sheet.append_rows.call_args
        self.assertEqual(kwargs.get("value_input_option"), "USER_ENTERED")
        header = update.COLUMN_ORDER
        date_col = header.index("email_date")
        name_col = header.index("name")
        # Rows are sorted chronologically before writing.
        self.assertEqual(
            [r[date_col] for r in rows],
            ["2024-01-01 12:00:00", "2024-03-09 08:00:00"],
        )
        self.assertEqual([r[name_col] for r in rows], ["A", "B"])

    def test_skips_uuids_already_in_sheet(self):
        self._write_json("dupe.json", {"name": "already-there"})
        self._write_json("fresh.json", {"name": "new"})
        # uuid column already contains "dupe" (plus the header cell).
        uuid_col = update.COLUMN_ORDER.index("uuid") + 1

        def col_values(col):
            return ["uuid", "dupe"] if col == uuid_col else []

        self.sheet.col_values.side_effect = col_values

        update.update(directory=self.dir)

        (rows,), _ = self.sheet.append_rows.call_args
        uuid_idx = update.COLUMN_ORDER.index("uuid")
        written_uuids = [r[uuid_idx] for r in rows]
        self.assertEqual(written_uuids, ["fresh"])

    def test_all_uuids_present_raises(self):
        self._write_json("dupe.json", {"name": "x"})
        uuid_col = update.COLUMN_ORDER.index("uuid") + 1
        self.sheet.col_values.side_effect = lambda col: (
            ["uuid", "dupe"] if col == uuid_col else []
        )

        with self.assertRaises(RuntimeError):
            update.update(directory=self.dir)
        self.sheet.append_rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
