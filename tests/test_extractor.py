"""Compatibility and contract tests for the feature_extractor entry point."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import feature_extractor as fe  # noqa: E402
import query_spec as qs  # noqa: E402

LEX = fe.Lexicon(SRC / "concept_dictionary.json", SRC / "cues.json")
AS_OF = "2026-08-19"


def run(text, as_of=AS_OF):
    return fe.extract_record({"id": "t", "business_id": "b", "as_of_date": as_of,
                              "question": text}, LEX)


class ContractTests(unittest.TestCase):
    def test_envelope_and_schema(self):
        rec = run("doanh thu tháng 6/2025 là bao nhiêu")
        self.assertEqual(rec["status"], "ok")
        self.assertIsNone(rec["reason"])
        self.assertEqual(rec["schema_version"], qs.SCHEMA_VERSION)
        self.assertEqual(rec["question_id"], "t")

    def test_query_has_full_v1_contract(self):
        query = run("doanh thu tháng 6/2025")["query"]
        self.assertEqual(set(query), set(qs.query_to_dict(qs.Query())))
        self.assertEqual(query["operation"], "sum")
        self.assertEqual(query["metric"], "revenue")

    def test_json_round_trip(self):
        rec = run("doanh thu tháng 6/2025")
        self.assertEqual(json.loads(json.dumps(rec, ensure_ascii=False)), rec)

    def test_old_rules_argument_is_ignored(self):
        lex = fe.Lexicon(SRC / "concept_dictionary.json", SRC / "cues.json",
                         rules_path=SRC / "does_not_exist.json")
        rec = fe.extract_record({"id": "t", "business_id": "b", "as_of_date": AS_OF,
                                 "question": "doanh thu tháng 6/2025"}, lex)
        self.assertEqual(rec["status"], "ok")


class BehaviourTests(unittest.TestCase):
    def test_debt_movement(self):
        query = run("khách ghi nợ thêm bao nhiêu")["query"]
        self.assertEqual((query["operation"], query["metric"]), ("delta", "debt_balance"))

    def test_threshold_filter(self):
        query = run("khách còn nợ trên 1 triệu")["query"]
        self.assertEqual(query["filters"][0]["field"], "debt_balance")
        self.assertEqual(query["filters"][0]["op"], "gt")
        self.assertEqual(query["filters"][0]["value"], 1000000)

    def test_quarter_is_a_range(self):
        period = run("doanh thu quý 2/2026")["query"]["time"]["periods"][0]
        self.assertEqual((period["start"], period["end"], period["anchor"]),
                         ("2026-04-01", "2026-06-30", "Q2"))

    def test_ranking(self):
        query = run("3 mặt hàng bán chạy nhất tháng 3/2025 là gì")["query"]
        self.assertEqual((query["operation"], query["group_by"], query["limit"]),
                         ("rank", "product_name", 3))


if __name__ == "__main__":
    unittest.main()
