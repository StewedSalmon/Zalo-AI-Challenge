"""Unit tests for the six-shape value executor (src/executor). Hand-computed expectations.

Fixture business biz_fx (tests/fixtures/executor): orders on 2025-03-01, 03-05 (x2 identical, plus one
cancelled), 03-10 (returned), 03-31 (in delivery), 04-15, and one unmapped status row on 03-06.
Expected March 2025: revenue 100000 + 250000 + 40000 = 390000 over 3 orders.
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from executor import ingest  # noqa: E402
from executor.aggregate import execute  # noqa: E402
from executor.reader import read_business  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "executor"


def table(as_of_data=None):
    return ingest.load_orders("biz_fx", FX / "data", FX / "mappings")


def month(m, y, shape="aggregate", kind="month"):
    return {"shape": shape, "common": {"metric": "revenue" if shape.startswith("aggregate") else "order_id",
                                       "filters": [], "time": {"kind": "month", "slots": {"month": m, "year": y}}},
            "shape_fields": {}}


def day(d, shape="aggregate_day"):
    return {"shape": shape, "common": {"metric": "revenue" if shape.startswith("aggregate") else "order_id",
                                       "filters": [], "time": {"kind": "day", "slots": {"date": d}}},
            "shape_fields": {}}


def rng(s, e, shape="aggregate_range"):
    return {"shape": shape, "common": {"metric": "revenue" if shape.startswith("aggregate") else "order_id",
                                       "filters": [], "time": {"kind": "range", "slots": {"start": s, "end": e}}},
            "shape_fields": {}}


class ParsingTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(ingest.parse_money("100,000"), 100000)
        self.assertEqual(ingest.parse_money("1.500.000"), 1500000)
        self.assertEqual(ingest.parse_money(42.0), 42)
        self.assertIsNone(ingest.parse_money(None))
        with self.assertRaises(ValueError):
            ingest.parse_money("abc")

    def test_date(self):
        self.assertEqual(ingest.parse_date("05/03/2025"), date(2025, 3, 5))
        self.assertEqual(ingest.parse_date("2025-03-05 06:10:00"), date(2025, 3, 5))
        self.assertEqual(ingest.parse_date(45658), date(2025, 1, 1))
        self.assertIsNone(ingest.parse_date("nope"))

    def test_reader_reads_utf8_csv_with_headers(self):
        sheets = read_business(FX / "data" / "biz_fx", files=["orders.csv"])
        self.assertEqual(len(sheets), 1)
        self.assertIn("Trạng thái", sheets[0].header)
        self.assertEqual(len(sheets[0].rows), 8)


class IngestTests(unittest.TestCase):
    def test_statuses_duplicates_and_unmapped_are_counted(self):
        t = table()
        self.assertEqual(t.report["rows_duplicate_identical"], 1)
        self.assertEqual(t.report["rows_unknown_status"], 1)
        self.assertEqual(sorted({o.status for o in t.orders.values()}),
                         sorted(["huỷ", "hoàn thành", "trả hàng", "đang giao"]))
        self.assertEqual(len(t.orders), 6)          # O1 O2 O3 O4 O5 O6; O8 unmapped, duplicate O2 counted once
        self.assertEqual(t.data_start, date(2025, 3, 1))
        self.assertEqual(t.data_end, date(2025, 4, 15))

    def test_missing_mapping_is_an_error_not_a_guess(self):
        with self.assertRaises(ingest.IngestError):
            ingest.load_orders("biz_none", FX / "data", FX / "mappings")


class ExecuteTests(unittest.TestCase):
    AS_OF = date(2026, 6, 30)

    def test_month_with_unmapped_row_inside_period_is_flagged_not_guessed(self):
        # O8 (06/03, status 'Đổi trả') is unmapped and lies inside March: no value is produced.
        r = execute(month(3, 2025), "aggregate", self.AS_OF, table())
        self.assertEqual(r.status, "error")
        self.assertIn("unmapped status", r.error)

    def test_day_and_range_not_touching_the_unmapped_row(self):
        r = execute(day("2025-03-05"), "aggregate_day", self.AS_OF, table())
        self.assertEqual((r.status, r.value), ("ok", 250000))             # O2 only; O3 cancelled
        c = execute(day("2025-03-05", shape="count_day"), "count_day", self.AS_OF, table())
        self.assertEqual((c.status, c.value), ("ok", 1))
        r2 = execute(rng("2025-03-01", "2025-03-05"), "aggregate_range", self.AS_OF, table())
        self.assertEqual((r2.status, r2.value), ("ok", 350000))           # O1 + O2
        c2 = execute(rng("2025-03-01", "2025-03-05", shape="count_range"), "count_range", self.AS_OF, table())
        self.assertEqual((c2.status, c2.value), ("ok", 2))

    def test_as_of_clips_the_period(self):
        r = execute(month(3, 2025), "aggregate", date(2025, 3, 4), table())
        self.assertEqual((r.status, r.value), ("ok", 100000))             # only O1 is on or before 03-04
        c = execute(month(3, 2025, shape="count"), "count", date(2025, 3, 5), table())
        self.assertEqual((c.status, c.value), ("ok", 2))                  # O1, O2; 03-06 row is after as_of

    def test_period_after_as_of_is_an_error(self):
        r = execute(day("2025-03-20"), "aggregate_day", date(2025, 3, 10), table())
        self.assertEqual(r.status, "error")

    def test_period_outside_data_window_is_unanswerable(self):
        r = execute(month(1, 2024), "aggregate", self.AS_OF, table())
        self.assertEqual(r.status, "unanswerable")

    def test_unit_slot_and_filters_are_out_of_scope(self):
        body = day("2025-03-01")
        body["shape_fields"] = {"unit": "chai"}
        self.assertEqual(execute(body, "aggregate_day", self.AS_OF, table()).status, "error")
        body2 = day("2025-03-01")
        body2["common"]["filters"] = [{"field": "order_status", "op": "eq", "value": "huỷ"}]
        self.assertEqual(execute(body2, "aggregate_day", self.AS_OF, table()).status, "error")

    def test_unresolved_year_is_an_error(self):
        body = month(3, "sheet_rule")
        self.assertEqual(execute(body, "aggregate", self.AS_OF, table()).status, "error")

    def test_deterministic(self):
        a = execute(day("2025-03-05"), "aggregate_day", self.AS_OF, table())
        b = execute(day("2025-03-05"), "aggregate_day", self.AS_OF, table())
        self.assertEqual(json.dumps(a.__dict__, default=str), json.dumps(b.__dict__, default=str))


if __name__ == "__main__":
    unittest.main()
