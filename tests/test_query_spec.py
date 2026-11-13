"""Contract tests for Query Spec v1 dataclasses and serialization."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import query_spec as qs  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_schema_version(self):
        self.assertEqual(qs.SCHEMA_VERSION, 1)

    def test_operation_enum_is_closed(self):
        self.assertIn("percent_change", qs.OPERATIONS)
        self.assertNotIn("aggregate", qs.OPERATIONS)
        self.assertNotIn("shape", qs.OPERATIONS)

    def test_answer_type_enum_is_closed(self):
        self.assertIn("percent", qs.ANSWER_TYPES)
        self.assertNotIn("unanswerable", qs.ANSWER_TYPES)

    def test_empty_query_has_null_scalars_and_empty_lists(self):
        data = qs.query_to_dict(qs.Query())
        for key in ("operation", "metric", "distinct", "per", "having", "numerator",
                    "denominator", "time", "group_by", "sort_by", "sort_order",
                    "limit", "unit", "answer_type"):
            self.assertIsNone(data[key], key)
        self.assertEqual(data["filters"], [])
        self.assertEqual(data["entities"], [])
        self.assertEqual(data["notes"], [])

    def test_record_contains_full_query_and_envelope(self):
        rec = qs.empty_record({"id": "q1", "business_id": "b1", "as_of_date": "2026-08-19",
                               "question": "doanh thu tháng 6/2025"}, status="ambiguous",
                              reason="test")
        data = qs.record_to_dict(rec)
        self.assertEqual(data["question_id"], "q1")
        self.assertEqual(data["business_id"], "b1")
        self.assertEqual(data["as_of_date"], "2026-08-19")
        self.assertEqual(data["status"], "ambiguous")
        self.assertEqual(data["reason"], "test")
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(set(data["query"]), set(qs.query_to_dict(qs.Query())))


if __name__ == "__main__":
    unittest.main()
