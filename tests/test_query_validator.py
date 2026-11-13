"""Structural validator tests for Query Spec v1."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import query_validator as qv  # noqa: E402


def record():
    return {
        "question_id": "q1",
        "business_id": "b1",
        "as_of_date": "2026-08-19",
        "question": "doanh thu tháng 6/2025",
        "status": "ok",
        "reason": None,
        "schema_version": 1,
        "query": {
            "operation": "sum",
            "metric": "revenue",
            "distinct": None,
            "per": None,
            "filters": [],
            "having": None,
            "numerator": None,
            "denominator": None,
            "entities": [],
            "time": {
                "kind": "time:month",
                "periods": [{"kind": "time:month", "month": 6, "year": 2025, "date": None,
                             "start": "2025-06-01", "end": "2025-06-30", "anchor": None,
                             "raw": "tháng 6/2025"}],
                "shift": None,
                "comparison": None,
            },
            "group_by": None,
            "sort_by": None,
            "sort_order": None,
            "limit": None,
            "unit": None,
            "answer_type": "number",
            "notes": [],
        },
    }


class ValidatorTests(unittest.TestCase):
    def test_valid_record(self):
        self.assertEqual(qv.validate_record(record()), [])

    def test_empty_query_is_valid(self):
        rec = record()
        rec["query"]["operation"] = None
        rec["query"]["metric"] = None
        rec["query"]["time"] = None
        rec["query"]["answer_type"] = None
        self.assertEqual(qv.validate_record(rec), [])

    def test_invalid_operation(self):
        rec = record()
        rec["query"]["operation"] = "aggregate"
        self.assertTrue(any("operation" in e for e in qv.validate_record(rec)))

    def test_invalid_filter_op_and_missing_value(self):
        rec = record()
        rec["query"]["filters"] = [{"field": "revenue", "op": "between"}]
        issues = qv.validate_record(rec)
        self.assertTrue(any(".op" in e for e in issues))
        self.assertTrue(any("must contain value" in e for e in issues))

    def test_invalid_period_order(self):
        rec = record()
        rec["query"]["time"]["periods"][0]["start"] = "2025-07-01"
        self.assertTrue(any("start must be <= end" in e for e in qv.validate_record(rec)))

    def test_invalid_entity_role(self):
        rec = record()
        rec["query"]["entities"] = [{"text": "Lẩu nấm", "concept": "product_name", "role": "filter_by"}]
        self.assertTrue(any("role" in e for e in qv.validate_record(rec)))

    def test_invalid_limit(self):
        rec = record()
        rec["query"]["limit"] = 0
        self.assertTrue(any("limit" in e for e in qv.validate_record(rec)))

    def test_invalid_status(self):
        rec = record()
        rec["status"] = "done"
        self.assertTrue(any("status" in e for e in qv.validate_record(rec)))

    def test_strict_raises(self):
        rec = record()
        rec["schema_version"] = 99
        with self.assertRaises(ValueError):
            qv.validate_record(rec, strict=True)


if __name__ == "__main__":
    unittest.main()
