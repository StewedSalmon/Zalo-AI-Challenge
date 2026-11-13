"""Behavioural tests for Query Spec v1 extraction."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import query_parser as qp  # noqa: E402

LEX = qp.Lexicon(SRC / "concept_dictionary.json", SRC / "cues.json")
AS_OF = "2026-08-19"


def run(text, as_of=AS_OF):
    return qp.extract_record({"id": "t", "business_id": "b", "as_of_date": as_of,
                              "question": text}, LEX)


class AggregateTests(unittest.TestCase):
    def test_sum_revenue_month(self):
        query = run("doanh thu tháng 6/2025 là bao nhiêu")["query"]
        self.assertEqual(query["operation"], "sum")
        self.assertEqual(query["metric"], "revenue")
        self.assertEqual(query["answer_type"], "number")
        self.assertEqual(query["time"]["periods"][0]["start"], "2025-06-01")

    def test_count_orders_with_revenue_filter(self):
        query = run("Tháng 6/2025 có bao nhiêu đơn hàng có dthu từ 270.000 đồng trở lên?")["query"]
        self.assertEqual(query["operation"], "count")
        self.assertEqual(query["metric"], "order_id")
        self.assertEqual(query["filters"],
                         [{"field": "revenue", "op": "gte", "value": 270000, "unit": None}])
        self.assertEqual(query["answer_type"], "number")

    def test_average_per_order(self):
        query = run("Trung bình mỗi đơn tuần trước mang về bao nhiêu dthu?")["query"]
        self.assertEqual(query["operation"], "average")
        self.assertEqual(query["metric"], "revenue")
        self.assertEqual(query["per"], "order_id")
        self.assertEqual(query["time"]["periods"][0]["anchor"], "previous")

    def test_debt_movement_is_delta(self):
        query = run("khách ghi nợ thêm bao nhiêu")["query"]
        self.assertEqual(query["operation"], "delta")
        self.assertEqual(query["metric"], "debt_balance")

    def test_debt_threshold_is_a_filter(self):
        query = run("khách còn nợ trên 1 triệu")["query"]
        self.assertEqual(query["operation"], "sum")
        self.assertEqual(query["metric"], "debt_balance")
        self.assertEqual(query["filters"],
                         [{"field": "debt_balance", "op": "gt", "value": 1000000, "unit": None}])

    def test_ratio_orders_uses_status_filter_component(self):
        query = run("tỉ lệ huỷ tháng 6/2025 là bao nhiêu")["query"]
        self.assertEqual(query["operation"], "ratio")
        self.assertEqual(query["metric"], "order_id")
        self.assertEqual(query["answer_type"], "percent")
        self.assertEqual(query["numerator"]["filters"],
                         [{"field": "order_status", "op": "eq", "value": "huỷ", "unit": None}])
        self.assertEqual(query["denominator"]["filters"], [])


class RankingTests(unittest.TestCase):
    def test_top_k_products(self):
        query = run("3 mặt hàng bán chạy nhất tháng 3/2025 là gì")["query"]
        self.assertEqual(query["operation"], "rank")
        self.assertEqual(query["metric"], "quantity_base")
        self.assertEqual(query["group_by"], "product_name")
        self.assertEqual((query["sort_by"], query["sort_order"], query["limit"]),
                         ("quantity_base", "desc", 3))
        self.assertEqual(query["answer_type"], "list")

    def test_argmax_day(self):
        query = run("tháng 12/2025 ngày nào cửa hàng bán được nhiều tiền nhất")["query"]
        self.assertEqual(query["operation"], "argmax")
        self.assertEqual(query["metric"], "revenue")
        self.assertEqual(query["group_by"], "group:day")
        self.assertEqual(query["answer_type"], "date")


class HavingTests(unittest.TestCase):
    def test_customer_count_having_not_top_k(self):
        query = run("Bao nhiêu khách mua ít nhất 11 đơn trong tháng 10 năm 2025?")["query"]
        self.assertEqual(query["operation"], "count")
        self.assertEqual(query["metric"], "customer_name")
        self.assertEqual(query["group_by"], "customer_name")
        self.assertEqual(query["having"],
                         {"aggregation": "count", "field": "order_id", "op": "gte",
                          "value": 11, "distinct": True})
        self.assertIsNone(query["limit"])
        self.assertIsNone(query["sort_order"])


class ComparisonTests(unittest.TestCase):
    def test_percent_change_same_period_last_year(self):
        query = run("so với cùng kỳ năm trước, doanh thu tháng 5/2026 tăng hay giảm mấy %")["query"]
        self.assertEqual(query["operation"], "percent_change")
        self.assertEqual(query["answer_type"], "percent")
        self.assertEqual(query["time"]["periods"][0]["start"], "2026-05-01")
        comparison = query["time"]["comparison"]
        self.assertEqual(comparison["base"], "same_period_last_year")
        self.assertEqual(comparison["direction"], "either")
        self.assertEqual((comparison["periods"][0]["start"], comparison["periods"][0]["end"]),
                         ("2025-05-01", "2025-05-31"))

    def test_explicit_comparison_periods(self):
        query = run("doanh thu tháng 4 năm 2026 tăng trưởng bao nhiêu % so với tháng 4 năm 2025?")["query"]
        comparison = query["time"]["comparison"]
        self.assertEqual(comparison["base"], "explicit")
        self.assertEqual(comparison["direction"], "increase")
        self.assertEqual(comparison["periods"][0]["start"], "2025-04-01")

    def test_same_period_last_month_preserves_partial_month(self):
        query = run("so với cùng kỳ tháng trước, doanh thu từ đầu tháng này đến hết hôm nay tăng hay giảm bao nhiêu %",
                    as_of="2026-09-07")["query"]
        self.assertEqual(query["time"]["periods"][0]["kind"], "month_to_date")
        self.assertEqual((query["time"]["periods"][0]["start"], query["time"]["periods"][0]["end"]),
                         ("2026-09-01", "2026-09-07"))
        comparison = query["time"]["comparison"]
        self.assertEqual(comparison["base"], "same_period_last_month")
        self.assertEqual((comparison["periods"][0]["start"], comparison["periods"][0]["end"]),
                         ("2026-08-01", "2026-08-07"))


class EntityTests(unittest.TestCase):
    def test_raw_accented_entity_text_is_preserved(self):
        query = run("xem giúp dthu của Lẩu nấm hải sản trong tháng 5 năm 2025 là bn")["query"]
        self.assertEqual(query["metric"], "revenue")
        self.assertEqual(query["entities"][0]["text"], "Lẩu nấm hải sản")

    def test_product_nam_is_not_time(self):
        query = run("Giá nhập bình quân một thùng Nước mắm Nam Ngư 750ml trong tháng 6 năm 2025 là bao nhiêu?")["query"]
        self.assertEqual(query["time"]["periods"][0]["month"], 6)
        self.assertIn("Nam Ngư", query["entities"][0]["text"])

    def test_query_has_no_shape_or_threshold(self):
        query = run("doanh thu tháng 6/2025")["query"]
        self.assertNotIn("shape", query)
        self.assertNotIn("threshold", query)
        self.assertNotIn("status_filter", query)


if __name__ == "__main__":
    unittest.main()
