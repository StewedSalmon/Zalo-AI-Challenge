"""Time-resolution tests for the Query Spec v1 resolver."""
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import date_resolver  # noqa: E402
import query_parser as qp  # noqa: E402

LEX = qp.Lexicon(SRC / "concept_dictionary.json", SRC / "cues.json")
AS_OF = "2026-09-07"


def resolve(text, as_of=AS_OF):
    tokens = qp.tokenize(text)
    claimed = [False] * len(tokens)

    def claim(i, j, *args, **kwargs):
        for k in range(i, j):
            claimed[k] = True

    def free(i):
        return not claimed[i]

    periods, err = date_resolver.resolve_periods(
        tokens, date.fromisoformat(as_of), LEX, claim, free, [])
    return periods, err


class ExplicitTimeTests(unittest.TestCase):
    def test_month_with_year(self):
        periods, err = resolve("doanh thu tháng 6/2025")
        self.assertIsNone(err)
        p = periods[0][1]
        self.assertEqual((p.kind, p.month, p.year, p.start, p.end),
                         ("time:month", 6, 2025, "2025-06-01", "2025-06-30"))

    def test_month_without_year_uses_last_occurrence(self):
        periods, err = resolve("doanh thu tháng 6", as_of="2026-03-15")
        self.assertIsNone(err)
        self.assertEqual(periods[0][1].year, 2025)
        periods, err = resolve("doanh thu tháng 6", as_of="2026-09-15")
        self.assertIsNone(err)
        self.assertEqual(periods[0][1].year, 2026)

    def test_quarter(self):
        periods, err = resolve("doanh thu quý 2/2026")
        self.assertIsNone(err)
        p = periods[0][1]
        self.assertEqual((p.start, p.end, p.anchor), ("2026-04-01", "2026-06-30", "Q2"))

    def test_year(self):
        periods, err = resolve("doanh thu năm 2025")
        self.assertIsNone(err)
        p = periods[0][1]
        self.assertEqual((p.kind, p.start, p.end), ("year", "2025-01-01", "2025-12-31"))

    def test_day(self):
        periods, err = resolve("doanh thu ngày 16/02/2026")
        self.assertIsNone(err)
        p = periods[0][1]
        self.assertEqual((p.kind, p.date, p.start, p.end),
                         ("time:day", "2026-02-16", "2026-02-16", "2026-02-16"))


class RelativeTimeTests(unittest.TestCase):
    def test_today_and_yesterday(self):
        p, err = resolve("doanh thu hôm nay")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].start, p[0][1].end), ("2026-09-07", "2026-09-07"))
        p, err = resolve("doanh thu hôm qua")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].start, p[0][1].end), ("2026-09-06", "2026-09-06"))

    def test_week_current_and_previous(self):
        p, err = resolve("doanh thu tuần này")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].anchor, p[0][1].start, p[0][1].end),
                         ("current", "2026-09-07", "2026-09-07"))
        p, err = resolve("doanh thu tuần rồi")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].anchor, p[0][1].start, p[0][1].end),
                         ("previous", "2026-08-31", "2026-09-06"))

    def test_month_to_date(self):
        p, err = resolve("doanh thu tháng này")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].kind, p[0][1].start, p[0][1].end),
                         ("month_to_date", "2026-09-01", "2026-09-07"))

    def test_explicit_month_to_date_range(self):
        p, err = resolve("doanh thu từ đầu tháng này đến hết hôm nay")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].kind, p[0][1].start, p[0][1].end),
                         ("month_to_date", "2026-09-01", "2026-09-07"))

    def test_year_to_date(self):
        p, err = resolve("doanh thu từ đầu năm đến hết hôm nay")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].kind, p[0][1].start, p[0][1].end),
                         ("year_to_date", "2026-01-01", "2026-09-07"))

    def test_last_n_days(self):
        p, err = resolve("doanh thu 30 ngày gần nhất")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].kind, p[0][1].start, p[0][1].end),
                         ("last_n_days", "2026-08-09", "2026-09-07"))

    def test_previous_year(self):
        p, err = resolve("doanh thu năm trước")
        self.assertIsNone(err)
        self.assertEqual((p[0][1].start, p[0][1].end), ("2025-01-01", "2025-12-31"))


class ErrorTests(unittest.TestCase):
    def test_invalid_month_returns_error(self):
        _, err = resolve("doanh thu tháng 13/2025")
        self.assertIsNotNone(err)
        self.assertIn("invalid month", err)


if __name__ == "__main__":
    unittest.main()
