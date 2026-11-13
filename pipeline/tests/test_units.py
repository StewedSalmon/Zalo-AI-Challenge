"""Unit tests: folding, table reading, typing, extraction, matching, execution, submission format.
Run from the pipeline folder:  python -m unittest discover -s tests -v   (with PYTHONPATH=src or via this file)
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
sys.path.insert(0, SRC)

from common import fold_text, fold_with_map  # noqa: E402
from execute import execute  # noqa: E402
from extract import extract  # noqa: E402
from ingest import column_decimal_comma, parse_date, parse_number  # noqa: E402
from lexicon import build_terms  # noqa: E402
from match import bind_free_spans, match, status_map_for  # noqa: E402
from tables import decode_bytes, detect_header  # noqa: E402

TERMS = build_terms()
for _t in TERMS:  # unit tests exercise confirmed entries (confirmation is tested in the precompute run)
    _t["status"] = "confirmed"


class Folding(unittest.TestCase):
    def test_dja_and_accents(self):
        self.assertEqual(fold_text("Đơn hàng Tháng 5/2025"), "don hang thang 5/2025")

    def test_numbers_keep_separators(self):
        self.assertEqual(fold_text("giá 55.000đ, 10%, 15/08/2026"), "gia 55.000d 10% 15/08/2026")

    def test_offsets_map_back(self):
        text = "Tháng 6/2025?"
        folded, imap = fold_with_map(text)
        self.assertEqual(len(folded), len(imap))
        self.assertEqual(imap, sorted(imap))            # offsets never go backwards
        self.assertEqual(text[imap[folded.index("6")]], "6")
        self.assertEqual(text[imap[folded.index("t")]], "T")


class Tables(unittest.TestCase):
    def test_bom_then_utf8_then_cp1258(self):
        self.assertEqual(decode_bytes(b"\xef\xbb\xbfa,b")[1], "utf-8-bom")
        self.assertEqual(decode_bytes("a,b".encode("utf-8"))[1], "utf-8")

    def test_header_row_detection_skips_title(self):
        rows = [["SỔ CHI TIẾT BÁN HÀNG"], ["", "", ""], ["Ngày", "Mã đơn", "Thành tiền"], [1, 2, 3]]
        idx, header = detect_header(rows)
        self.assertEqual(idx, 2)
        self.assertEqual(header, ["Ngày", "Mã đơn", "Thành tiền"])


class Typing(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(parse_date("15/08/2026"), dt.date(2026, 8, 15))
        self.assertEqual(parse_date("15/08/2026 10:30"), dt.date(2026, 8, 15))
        self.assertEqual(parse_date("2026-08-15"), dt.date(2026, 8, 15))
        self.assertEqual(parse_date("15 thg 8 2026"), dt.date(2026, 8, 15))
        self.assertEqual(parse_date("45658"), dt.date(2025, 1, 1))
        self.assertIsNone(parse_date("nói chung"))

    def test_numbers(self):
        self.assertEqual(parse_number("1,234,567"), 1234567.0)
        self.assertEqual(parse_number("55.000"), 55000.0)
        self.assertEqual(parse_number("12.5", decimal_comma=False), 12.5)
        self.assertTrue(column_decimal_comma(["12,5", "3,25"]))
        self.assertFalse(column_decimal_comma(["1.000", "25.000"]))


class Extraction(unittest.TestCase):
    def spans(self, text):
        return extract("q", text, TERMS)

    def test_month_year_and_metric(self):
        r = self.spans("Cho tôi biết doanh thu tháng 5/2025.")
        cats = [(s["category"], s["surface"]) for s in r["spans"]]
        self.assertIn(("metric", "doanh thu"), cats)
        self.assertIn(("date", "tháng 5/2025"), cats)

    def test_accent_pairs_give_same_term_ids(self):
        acc = self.spans("Doanh thu hôm qua là bao nhiêu?")
        un = self.spans("doanh thu hom qua la bao nhieu")
        self.assertEqual([s["term_id"] for s in acc["spans"] if s["term_id"]],
                         [s["term_id"] for s in un["spans"] if s["term_id"]])

    def test_comparator_superlative_and_free(self):
        r = self.spans("Mặt hàng nào bán nhiều nhất trên 10 cái?")
        cats = {s["category"] for s in r["spans"]}
        self.assertIn("superlative", cats)
        self.assertIn("number", cats)
        self.assertIn("free", cats)

    def test_multi_question_and_empty(self):
        self.assertIn("multi_question", self.spans("Doanh thu? Số đơn?")["flags"])
        self.assertIn("empty", self.spans("   ")["flags"])

    def test_deterministic(self):
        a = json.dumps(self.spans("thang 6/2025 so don"), ensure_ascii=False, sort_keys=True)
        b = json.dumps(self.spans("thang 6/2025 so don"), ensure_ascii=False, sort_keys=True)
        self.assertEqual(a, b)


class StatusAndBinding(unittest.TestCase):
    PARAMS = {"status_vocabulary": {
        "canonical": {"value": ["hoàn thành", "huỷ", "trả hàng", "đang giao"]},
        "synonyms": {"value": [
            {"raw": "Xong", "canonical": "hoàn thành", "status": "confirmed"},
            {"raw": "Đã hủy", "canonical": "huỷ", "status": "confirmed"},
            {"raw": "Đổi trả", "canonical": "trả hàng", "status": "proposed"},
            {"raw": "Đang giao", "canonical": "đang giao", "status": "confirmed"}]}}}

    def test_status_map_confirmed_only(self):
        m, miss = status_map_for(["xong", "da huy", "doi tra", "dang giao", "khong giao duoc"], self.PARAMS, True)
        self.assertEqual(m["xong"], "hoàn thành")
        self.assertNotIn("doi tra", m)
        self.assertEqual(miss, ["doi tra", "khong giao duoc"])

    def test_status_map_confirmation_mode_covers_proposed(self):
        m, miss = status_map_for(["doi tra"], self.PARAMS, False)
        self.assertEqual(m["doi tra"], "trả hàng")
        self.assertEqual(miss, [])

    def test_product_binding_unique_longest(self):
        ext = extract("q", "sting dau 330ml hom qua", TERMS)
        bound, left = bind_free_spans(ext["spans"], {"sting dau 330ml"})
        self.assertEqual(bound[0]["bound_key"], "sting dau 330ml")
        self.assertEqual(left, [])


def _ctx(summary, data_end="2026-08-24", as_of="2026-08-30", mode="scored"):
    return {"as_of": dt.date.fromisoformat(as_of), "params": StatusAndBinding.PARAMS | {
        "defaults": {}, "allowed_default_parameters": {"data_end_date": {}}},
            "lexicon_status": {t["term_id"]: "confirmed" for t in TERMS},
            "profile": [{"op": "set_default", "args": ["data_end_date", data_end]}],
            "summary": summary, "product_keys": set(summary["product_keys"]),
            "display_status": {"xong": "Xong", "da huy": "Đã hủy", "doi tra": "Đổi trả"}, "mode": mode}


LOC = [("f.csv", "<csv>", "Col")]
SUMMARY = {"concepts": {"order_id": LOC, "order_date": LOC, "order_status": LOC, "line_amount": LOC},
           "declared_absent": [], "ambiguous_concepts": [], "date_min": "2025-06-01", "date_max": "2026-08-24",
           "status_folded": {"xong": 5, "da huy": 2}, "product_keys": ["sting dau 330ml"]}


class Matching(unittest.TestCase):
    def run_q(self, text, as_of="2026-08-30", summary=None, data_end="2026-08-24", mode="scored"):
        ext = extract("q", text, TERMS)
        ctx = _ctx(summary or SUMMARY, data_end=data_end, as_of=as_of, mode=mode)
        return match(ext, ctx)

    def test_ok_month_revenue(self):
        s = self.run_q("doanh thu tháng 6/2025")
        self.assertEqual(s["status"], "ok", s["reason"])
        self.assertEqual(s["time"], {"kind": "month", "from": "2025-06-01", "to": "2025-06-30", "coverage": "full"})

    def test_month_without_year_uses_year_rule(self):
        s = self.run_q("doanh thu tháng 9", as_of="2026-08-30")
        self.assertEqual(s["time"]["from"], "2025-09-01")  # September 2026 is after today, so 2025

    def test_relative_day_clipped_to_as_of(self):
        s = self.run_q("doanh thu hôm nay", as_of="2026-08-24")
        self.assertEqual(s["time"]["from"], "2026-08-24")

    def test_partial_coverage(self):
        s = self.run_q("doanh thu tháng 6/2025 đến 15/06/2025", summary=SUMMARY)
        self.assertEqual(s["status"], "ok", s["reason"])
        self.assertEqual(s["time"]["coverage"], "full")
        s2 = self.run_q("doanh thu từ 01/05/2025 đến 10/06/2025")
        self.assertEqual(s2["time"]["coverage"], "partial")
        self.assertEqual(s2["time"]["from"], "2025-06-01")

    def test_outside_coverage_is_unanswerable(self):
        s = self.run_q("doanh thu tháng 3/2024")
        self.assertEqual(s["status"], "unanswerable")
        self.assertEqual(s["step"], "3.9")

    def test_unknown_coverage_is_ambiguous(self):
        s = self.run_q("doanh thu tháng 6/2025", data_end=None)
        self.assertEqual(s["status"], "ambiguous")

    def test_multi_question_unsupported(self):
        s = self.run_q("doanh thu tháng 6/2025? số đơn tháng 6/2025?")
        self.assertEqual(s["status"], "unsupported")
        self.assertEqual(s["step"], "3.1")

    def test_unmapped_status_unsupported(self):
        summ = dict(SUMMARY, status_folded={"xong": 1, "khong giao duoc": 1})
        s = self.run_q("doanh thu tháng 6/2025", summary=summ)
        self.assertEqual(s["status"], "unsupported")
        self.assertIn("khong giao duoc", s["reason"])

    def test_proposed_status_label_unsupported_in_scored_mode(self):
        summ = dict(SUMMARY, status_folded={"xong": 1, "doi tra": 1})
        s = self.run_q("doanh thu tháng 6/2025", summary=summ)
        self.assertEqual(s["status"], "unsupported")
        s2 = self.run_q("doanh thu tháng 6/2025", summary=summ, mode="confirm")
        self.assertEqual(s2["status"], "ok", s2["reason"])

    def test_absent_required_concept_unanswerable(self):
        summ = dict(SUMMARY, concepts={"order_id": [], "order_date": [], "line_amount": []})
        s = self.run_q("doanh thu tháng 6/2025", summary=summ)
        self.assertEqual(s["status"], "unanswerable")

    def test_proposed_term_not_used(self):
        ext = extract("q", "doanh thu tháng 6/2025", TERMS)
        ctx = _ctx(SUMMARY)
        ctx["lexicon_status"]["revenue"] = "proposed"
        s = match(ext, ctx)
        self.assertEqual(s["status"], "unsupported")

    def test_order_count_needs_order_id(self):
        summ = dict(SUMMARY, concepts={"order_date": [], "order_status": [], "line_amount": []})
        s = self.run_q("số đơn tháng 6/2025", summary=summ)
        self.assertEqual(s["status"], "unanswerable")


class Execution(unittest.TestCase):
    def units(self):
        d = dt.date
        raw = [
            # order A: two lines, completed
            {"order_key": "A", "date": d(2025, 6, 2), "status_raw": "Xong", "amount": 100.0, "product_key": "x"},
            {"order_key": "A", "date": d(2025, 6, 2), "status_raw": "Xong", "amount": 50.0, "product_key": "y"},
            # order B: one line completed and one returned -> whole order excluded
            {"order_key": "B", "date": d(2025, 6, 3), "status_raw": "Xong", "amount": 70.0, "product_key": "x"},
            {"order_key": "B", "date": d(2025, 6, 3), "status_raw": "Đổi trả", "amount": -70.0, "product_key": "x"},
            # order C: cancelled
            {"order_key": "C", "date": d(2025, 6, 4), "status_raw": "Đã hủy", "amount": 900.0, "product_key": "x"},
        ]
        units = sorted(raw, key=lambda u: u["date"])
        return units, [u["date"] for u in units]

    def spec(self, metric):
        return {"status": "ok", "metric": metric, "answer_type": "number",
                "time": {"from": "2025-06-01", "to": "2025-06-30"}, "entities": []}

    def status_of(self, raw):
        return {"Xong": "hoàn thành", "Đã hủy": "huỷ", "Đổi trả": "trả hàng"}.get(raw)

    def test_revenue_excludes_cancelled_and_whole_returned_orders(self):
        calls = []
        ans = execute(self.spec("revenue"), self.units(), self.status_of, calls)
        self.assertEqual(ans["answer"], 150)   # only order A

    def test_order_count(self):
        calls = []
        ans = execute(self.spec("order_count"), self.units(), self.status_of, calls)
        self.assertEqual(ans["answer"], 1)

    def test_missing_status_raises(self):
        units, dates = self.units()
        units[0] = dict(units[0], status_raw=None)
        with self.assertRaises(ValueError):
            execute(self.spec("revenue"), (units, [u["date"] for u in units]), self.status_of, [])


class Submission(unittest.TestCase):
    def test_line_shapes(self):
        from run import Pipeline
        p = Pipeline.__new__(Pipeline)
        ok = {"id": "q1", "status": "ok", "answer": {"type": "number", "answer": 5}, "matching": {}}
        self.assertEqual(p.submission_line(ok), {"id": "q1", "type": "number", "answer": 5})
        un = {"id": "q2", "status": "unanswerable", "answer": None, "matching": {"reason": "x"}}
        self.assertEqual(p.submission_line(un), {"id": "q2", "type": "unanswerable", "reason": "x"})
        ab = {"id": "q3", "status": "ambiguous", "answer": None, "matching": {"reason": "y"}}
        self.assertEqual(p.submission_line(ab), {"id": "q3", "type": "abstain"})


if __name__ == "__main__":
    unittest.main()
