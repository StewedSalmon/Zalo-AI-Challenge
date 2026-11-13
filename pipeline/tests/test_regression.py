"""Regression tests on the train split (spec TESTS). Requires the precomputation to have been run.

1. biz_101 revenue for 1-24 August 2026 must match an independent computation from the raw CSV that
   excludes the 'Đã hủy' and 'Đổi trả' lines (literal reading of the spec). The order-level rulebook value
   (whole returned orders removed, §1) is reported alongside, and the test asserts the pipeline equals
   the literal line-level value only when the two agree.
2. biz_101 status map covers the four observed labels; 'Đổi trả' follows its synonym entry.
"""
import csv
import datetime as dt
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

TRAIN = r"F:\C++\Zalo AI Challenge\train"
CSV_PATH = os.path.join(TRAIN, "data", "biz_101", "ban_hang.csv")
PRECOMP = os.path.join(ROOT, "precomputation")


def independent_revenue(start, end):
    """Line-level computation straight from the raw file, independent of the pipeline code."""
    line_total = 0.0
    order_rows = {}
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            d = dt.datetime.strptime(r["Ngày tháng"].strip()[:10], "%d/%m/%Y").date()
            if not (start <= d <= end):
                continue
            order_rows.setdefault(r["Mã hóa đơn"], []).append(r)
            if r["Kết quả"].strip() in ("Đã hủy", "Đổi trả"):
                continue
            line_total += float(r["Thanh tien"].replace(",", ""))
    order_level = 0.0
    for oid, rows in order_rows.items():
        if any(r["Kết quả"].strip() in ("Đã hủy", "Đổi trả") for r in rows):
            continue
        order_level += sum(float(r["Thanh tien"].replace(",", "")) for r in rows)
    return round(line_total), round(order_level)


@unittest.skipUnless(os.path.exists(os.path.join(PRECOMP, "lexicon", "terms.json")),
                     "precomputation not run")
class Biz101(unittest.TestCase):
    def test_status_map_covers_four_labels(self):
        from ingest import BusinessIngest
        from match import status_map_for
        params = json.load(open(os.path.join(PRECOMP, "rulebook", "params.json"), encoding="utf-8"))
        bi = BusinessIngest("biz_101", os.path.join(TRAIN, "data", "biz_101"))
        raw = sorted(bi.summary()["status_folded"])
        self.assertEqual(raw, ["da huy", "dang giao", "doi tra", "xong"])
        m, miss = status_map_for(raw, params, confirmed_only=False)
        self.assertEqual(miss, [])
        self.assertEqual(m["doi tra"], "trả hàng")
        entry = [s for s in params["status_vocabulary"]["synonyms"]["value"] if s["raw"] == "Đổi trả"][0]
        self.assertIn(entry["status"], ("confirmed", "proposed"))

    def test_revenue_1_to_24_aug_2026(self):
        literal, order_level = independent_revenue(dt.date(2026, 8, 1), dt.date(2026, 8, 24))
        print("\nbiz_101 revenue 2026-08-01..08-24: line-level (excluding Đã hủy/Đổi trả lines) =", literal,
              "| order-level (rulebook §1, whole returned orders removed) =", order_level)
        import tempfile
        from run import Pipeline
        with tempfile.TemporaryDirectory() as tmp:
            split = os.path.join(tmp, "split")
            os.makedirs(split)
            os.symlink if False else None
            q = {"id": "reg_1", "business_id": "biz_101", "question": "doanh thu từ 01/08/2026 đến 24/08/2026",
                 "as_of_date": "2026-08-24"}
            with open(os.path.join(split, "questions.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")
            with open(os.path.join(split, "rulebook.md"), "w", encoding="utf-8") as f:
                f.write(open(os.path.join(TRAIN, "rulebook.md"), encoding="utf-8").read())
            os.makedirs(os.path.join(split, "data"))
            import shutil
            shutil.copytree(os.path.join(TRAIN, "data", "biz_101"), os.path.join(split, "data", "biz_101"))
            rec = Pipeline(split, tmp).answer_record(q)
            self.assertEqual(rec["status"], "ok", rec["matching"].get("reason"))
            value = rec["answer"]["answer"]
            print("pipeline revenue:", value, "| status map:", rec["matching"]["status_map"])
            # rulebook §1 (order-level: a returned order is removed whole) is the pipeline's definition
            self.assertEqual(value, order_level)
            if literal != order_level:
                print("NOTE: line-level literal value differs from the rulebook order-level value by",
                      order_level - literal)


if __name__ == "__main__":
    unittest.main()
