"""Full train-set smoke test for Query Spec extraction and validation."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
QUESTIONS = ROOT / "train" / "questions.jsonl"
sys.path.insert(0, str(SRC))

import query_parser as qp  # noqa: E402
import query_validator as qv  # noqa: E402


@unittest.skipUnless(QUESTIONS.exists(), "train/questions.jsonl is not available")
class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "query_specs_train.jsonl"
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            qp.main(["--questions", str(QUESTIONS),
                     "--dictionary", str(SRC / "concept_dictionary.json"),
                     "--cues", str(SRC / "cues.json"),
                     "--out", str(cls.out)])
        cls.records = [json.loads(line) for line in cls.out.read_text(encoding="utf-8").splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_question_is_emitted(self):
        self.assertEqual(len(self.records), 3000)
        self.assertEqual(len({r["question_id"] for r in self.records}), 3000)

    def test_every_record_is_ok(self):
        statuses = {}
        for rec in self.records:
            statuses[rec["status"]] = statuses.get(rec["status"], 0) + 1
        self.assertEqual(statuses, {"ok": 3000})

    def test_every_record_validates(self):
        invalid = [(rec["question_id"], qv.validate_record(rec)) for rec in self.records]
        invalid = [(qid, issues) for qid, issues in invalid if issues]
        self.assertEqual(invalid, [])

    def test_core_fields_are_populated(self):
        self.assertGreater(sum(1 for r in self.records if r["query"]["metric"]), 2700)
        self.assertGreater(sum(1 for r in self.records if r["query"]["time"]), 2800)
        self.assertGreater(sum(1 for r in self.records if r["query"]["answer_type"]), 2900)

    def test_no_legacy_shape_fields(self):
        for rec in self.records:
            self.assertNotIn("shape", rec["query"])
            self.assertNotIn("threshold", rec["query"])
            self.assertNotIn("status_filter", rec["query"])


if __name__ == "__main__":
    unittest.main()
