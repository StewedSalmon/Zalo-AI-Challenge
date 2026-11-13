"""CLI integration: extract Query Specs and validate the emitted JSONL."""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import query_parser as qp  # noqa: E402
import query_validator as qv  # noqa: E402

QUESTIONS = [
    {"id": "q1", "business_id": "b1", "as_of_date": "2026-08-19", "question": "doanh thu tháng 6/2025 là bao nhiêu"},
    {"id": "q2", "business_id": "b1", "as_of_date": "2026-08-19", "question": "khách ghi nợ thêm bao nhiêu"},
    {"id": "q3", "business_id": "b1", "as_of_date": "2026-08-19", "question": "khách còn nợ trên 1 triệu"},
    {"id": "q4", "business_id": "b1", "as_of_date": "2026-08-19", "question": "tỉ lệ huỷ tháng 6/2025 là bao nhiêu"},
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CliPipelineTests(unittest.TestCase):
    def test_extract_validate_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            qpath = tmp / "q.jsonl"
            qpath.write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in QUESTIONS) + "\n",
                             encoding="utf-8")
            out_a, out_b = tmp / "a.jsonl", tmp / "b.jsonl"
            args = ["--questions", str(qpath),
                    "--dictionary", str(SRC / "concept_dictionary.json"),
                    "--cues", str(SRC / "cues.json")]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(qp.main(args + ["--out", str(out_a)]), 0)
                self.assertEqual(qp.main(args + ["--out", str(out_b)]), 0)

            self.assertEqual(sha(out_a), sha(out_b))
            records = {r["question_id"]: r for r in map(json.loads, out_a.read_text(encoding="utf-8").splitlines())}
            self.assertEqual(set(records), {"q1", "q2", "q3", "q4"})
            self.assertTrue(all(r["status"] == "ok" for r in records.values()))
            self.assertEqual(records["q1"]["query"]["metric"], "revenue")
            self.assertEqual(records["q2"]["query"]["operation"], "delta")
            self.assertEqual(records["q3"]["query"]["filters"][0]["value"], 1000000)
            self.assertEqual(records["q4"]["query"]["operation"], "ratio")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(qv.main(["--query-specs", str(out_a)]), 0)

    def test_rules_argument_is_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            qpath = tmp / "q.jsonl"
            qpath.write_text(json.dumps(QUESTIONS[0], ensure_ascii=False) + "\n", encoding="utf-8")
            out = tmp / "out.jsonl"
            args = ["--questions", str(qpath),
                    "--dictionary", str(SRC / "concept_dictionary.json"),
                    "--cues", str(SRC / "cues.json"),
                    "--rules", str(SRC / "removed_shape_rules.json"),
                    "--out", str(out)]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(qp.main(args), 0)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
