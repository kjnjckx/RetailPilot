import copy
import unittest

from backend.domain import (
    DomainError,
    aggregate,
    analyze,
    cents,
    plan_question,
    validate_import,
)
from tests.fixtures import demo_files, metric_data


class RetailMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.issues, _ = validate_import(demo_files())
        cls.allowed = ["SH01", "HZ01", "SZ01", "GZ01"]

    def test_demo_is_valid_and_deterministic(self):
        self.assertEqual(self.issues, [])
        self.assertEqual(demo_files(), demo_files())

    def test_prd_benchmark(self):
        plan = plan_question(
            "2026 年 8 月华东净销售额相比 7 月为什么下降？",
            self.data,
            self.allowed,
            "2026-07-01",
            "2026-08-31",
        )
        result = analyze(self.data, self.allowed, plan, "v1")
        self.assertEqual(result["current"]["net"], 8_000_000)
        self.assertEqual(result["previous"]["net"], 10_000_000)
        self.assertEqual(result["delta"], -2_000_000)
        self.assertEqual(result["rate"], -20)
        self.assertEqual(
            [s["delta"] for s in result["contributions"]], [-1_500_000, -500_000]
        )
        self.assertTrue(result["abnormal"])

    def test_refunds_cancelled_and_quantities(self):
        data = copy.deepcopy(self.data)
        data["orders"] = data["orders"][:4]
        data["items"] = data["items"][:4]
        for index, (paid, refund, status, qty, returned) in enumerate(
            [
                (10000, 0, "已支付", 10, 0),
                (20000, 5000, "部分退款", 10, 2),
                (30000, 30000, "全额退款", 10, 10),
                (40000, 0, "已取消", 10, 0),
            ]
        ):
            data["orders"][index].update(
                paid_amount=paid, refund_amount=refund, status=status
            )
            data["items"][index].update(
                paid_amount=paid,
                refund_amount=refund,
                quantity=qty,
                returned_quantity=returned,
            )
        out = aggregate(data, ["SH01"], "2026-07-01", "2026-07-31")
        self.assertEqual(out["net"], 25000)
        self.assertEqual(out["orders"], 3)
        self.assertEqual(out["quantity"], 18)
        self.assertEqual(out["average"], 8333)

    def test_precision_and_invalid_money(self):
        self.assertEqual(cents("0.29"), 29)
        for value in ["NaN", "Infinity", "-1", "1.001", "abc"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                cents(value)

    def test_duplicate_foreign_key_and_refund_validation(self):
        files = demo_files()
        files["items"] += "\n" + files["items"].splitlines()[1]
        self.assertTrue(any("重复" in x["message"] for x in validate_import(files)[1]))
        files = demo_files()
        files["items"] = files["items"].replace(",P01,", ",MISSING,")
        self.assertTrue(any("外键" in x["message"] for x in validate_import(files)[1]))
        files = demo_files()
        first = files["items"].splitlines()[1]
        parts = first.split(",")
        parts[-1] = "9999999.00"
        files["items"] = files["items"].replace(first, ",".join(parts), 1)
        self.assertTrue(
            any("退款金额超过" in x["message"] for x in validate_import(files)[1])
        )

    def test_field_mapping(self):
        files = demo_files()
        files["stores"] = files["stores"].replace(
            "store_id,name,region", "门店编号,名称,地区"
        )
        data, issues, _ = validate_import(
            files,
            {"stores": {"store_id": "门店编号", "name": "名称", "region": "地区"}},
        )
        self.assertEqual(issues, [])
        self.assertEqual(len(data["stores"]), 4)

    def test_scope_enforced(self):
        value = aggregate(self.data, ["SH01"], "2026-08-01", "2026-08-31")
        self.assertEqual(value["net"], 4_500_000)
        with self.assertRaises(DomainError):
            aggregate(self.data, ["SH01"], "2026-08-01", "2026-08-31", "HZ01")
        with self.assertRaises(DomainError):
            plan_question(
                "2026 年 8 月杭州西湖店净销售额",
                self.data,
                ["SH01"],
                "2026-07-01",
                "2026-08-31",
            )

    def test_clarification_and_missing_coverage(self):
        for q in [
            "最近卖得如何",
            "2026 年 9 月净销售额",
            "2026 年 8 月毛利分析",
            "2026 年 7 月净销售额环比",
            "2026 年 8 月食品饮料销售额",
        ]:
            with self.subTest(q=q), self.assertRaises(DomainError):
                plan_question(q, self.data, self.allowed, "2026-07-01", "2026-08-31")

    def test_empty_is_distinct_from_zero(self):
        out = aggregate(self.data, ["SH01"], "2026-09-01", "2026-09-30")
        self.assertTrue(out["empty"])
        self.assertIsNone(out["average"])
        data = copy.deepcopy(self.data)
        for item in data["items"]:
            item["refund_amount"] = item["paid_amount"]
        out = aggregate(data, ["SH01"], "2026-08-01", "2026-08-31")
        self.assertFalse(out["empty"])
        self.assertEqual(out["average"], 0)

    def test_zero_base_has_no_growth_rate(self):
        data = copy.deepcopy(self.data)
        july = {o["order_id"] for o in data["orders"] if o["date"] < "2026-08-01"}
        for item in data["items"]:
            if item["order_id"] in july:
                item["refund_amount"] = item["paid_amount"]
        plan = plan_question(
            "2026 年 8 月净销售额相比 7 月",
            data,
            self.allowed,
            "2026-07-01",
            "2026-08-31",
        )
        self.assertIsNone(analyze(data, self.allowed, plan, "v1")["rate"])

    def test_daily_series_keeps_empty_days_aligned(self):
        data = copy.deepcopy(self.data)
        data["orders"] = [o for o in data["orders"] if o["date"] != "2026-08-02"]
        result = aggregate(data, self.allowed, "2026-08-01", "2026-08-31")
        self.assertEqual(len(result["daily"]), 31)
        self.assertEqual(result["daily"][1]["date"], "2026-08-02")
        self.assertEqual(result["daily"][1]["net"], 0)
        self.assertIsNone(result["daily"][1]["average"])

    def test_category_contributions_reconcile(self):
        plan = plan_question(
            "2026 年 8 月华东净销售额相比 7 月",
            self.data,
            self.allowed,
            "2026-07-01",
            "2026-08-31",
        )
        result = analyze(self.data, self.allowed, plan, "v1")
        self.assertEqual(
            sum(c["delta"] for c in result["category_contributions"]), -2000000
        )

    def test_unknown_scope_and_date_are_not_silently_ignored(self):
        for q in [
            "2026 年 8 月华北净销售额",
            "2026 年 8 月上海店销售额",
            "2026 年 8 月 15 日销售额",
            "2026 年 7 月至 8 月销售额",
        ]:
            with self.subTest(q=q), self.assertRaises(DomainError):
                plan_question(q, self.data, self.allowed, "2026-07-01", "2026-08-31")


class AnalysisCorrectnessTests(unittest.TestCase):
    def setUp(self):
        self.data = metric_data()
        self.allowed = ["A", "B", "C"]

    def result(self, question, data=None, start="2026-07-01", end="2026-08-31"):
        data = data or self.data
        plan = plan_question(question, data, self.allowed, start, end)
        return analyze(data, self.allowed, plan, "fixed-version")

    def test_each_metric_compares_its_own_values_and_units(self):
        cases = [
            ("净销售额", "net", 5000, 14000, -9000, "元"),
            ("有效订单数", "orders", 3, 2, 1, "笔"),
            ("客单价", "average", 1667, 7000, -5333, "元"),
            ("净销量", "quantity", 6, 3, 3, "件"),
        ]
        for label, key, current, previous, delta, unit in cases:
            with self.subTest(metric=key):
                result = self.result(f"2026 年 8 月{label}相比 7 月变化多少？")
                metric = result["metric_results"][0]
                self.assertEqual(
                    (
                        metric["metric"],
                        metric["current"],
                        metric["previous"],
                        metric["delta"],
                        metric["unit"],
                    ),
                    (key, current, previous, delta, unit),
                )
                self.assertEqual(result["delta"], delta)
                self.assertAlmostEqual(metric["rate"], round(delta / previous * 100, 2))
                self.assertIn(label, result["summary"])
                if key in ("orders", "quantity"):
                    self.assertNotIn("元", result["summary"])
                    self.assertNotIn("销售额", result["summary"])

    def test_multiple_metrics_keep_independent_comparisons(self):
        result = self.result(
            "2026 年 8 月有效订单数、客单价和净销量相比 7 月变化多少？"
        )
        self.assertEqual(
            [m["metric"] for m in result["metric_results"]],
            ["orders", "average", "quantity"],
        )
        self.assertEqual([m["delta"] for m in result["metric_results"]], [1, -5333, 3])
        self.assertEqual(
            result["evidence"]["units"],
            {"orders": "笔", "average": "人民币分", "quantity": "件"},
        )
        self.assertEqual(result["metric_results"], result["evidence"]["metric_results"])

    def test_rankings_use_selected_metric_and_average_is_weighted(self):
        orders = self.result("2026 年 8 月各门店有效订单数排名如何？")[
            "metric_results"
        ][0]
        sales = self.result("2026 年 8 月各门店净销售额排名如何？")["metric_results"][0]
        average = self.result("2026 年 8 月各门店客单价排名如何？")["metric_results"][0]
        self.assertEqual([r["store_id"] for r in orders["rankings"]], ["B", "A", "C"])
        self.assertEqual([r["store_id"] for r in sales["rankings"]], ["A", "B", "C"])
        self.assertEqual(average["current"], 1667)
        self.assertEqual([r["value"] for r in average["rankings"]], [3000, 1000, None])
        self.assertEqual(average["contributions"], [])

    def test_additive_contributions_include_store_with_no_current_orders(self):
        self.data["orders"] = [o for o in self.data["orders"] if o["order_id"] != "O2"]
        for label in ["净销售额", "有效订单数", "净销量"]:
            with self.subTest(metric=label):
                metric = self.result(f"2026 年 8 月{label}相比 7 月")["metric_results"][
                    0
                ]
                self.assertEqual(
                    sum(row["delta"] for row in metric["contributions"]),
                    metric["delta"],
                )

    def test_category_orders_deduplicate_and_do_not_sum_across_categories(self):
        self.data["products"].append(
            {"product_id": "Q", "name": "另一商品", "category": "食品"}
        )
        self.data["items"][2].update(paid_amount=1000, quantity=2)
        self.data["items"].append(
            {**self.data["items"][2], "line_id": "2", "quantity": 1}
        )
        self.data["items"].append(
            {**self.data["items"][2], "line_id": "3", "product_id": "Q", "quantity": 1}
        )
        metric = self.result("2026 年 8 月订单数相比 7 月")["metric_results"][0]
        self.assertEqual(metric["current"], 3)
        self.assertEqual(
            {r["name"]: r["value"] for r in metric["category_values"]},
            {"日用": 3, "食品": 1},
        )
        self.assertEqual(metric["category_contributions"], [])

    def test_disappeared_category_contributions_reconcile(self):
        self.data["products"].append(
            {"product_id": "Q", "name": "另一商品", "category": "食品"}
        )
        for item in self.data["items"][2:]:
            item["product_id"] = "Q"
        for label in ["净销售额", "净销量"]:
            with self.subTest(metric=label):
                metric = self.result(f"2026 年 8 月{label}相比 7 月")["metric_results"][
                    0
                ]
                self.assertEqual(
                    sum(row["delta"] for row in metric["category_contributions"]),
                    metric["delta"],
                )
                missing = next(
                    row
                    for row in metric["category_contributions"]
                    if row["name"] == "日用"
                )
                self.assertEqual(missing["value"], 0)
                self.assertEqual(missing["net"], 0)

    def test_yoy_requires_last_year_not_last_month(self):
        with self.assertRaises(DomainError) as error:
            self.result("2026 年 8 月净销售额同比变化多少？")
        self.assertEqual(error.exception.status, 422)
        self.assertIn("2025-08-01", error.exception.message)
        for order in self.data["orders"]:
            if order["date"].startswith("2026-07"):
                order["date"] = "2025-08-01"
        for question in [
            "2026 年 8 月净销售额同比",
            "2026 年 8 月净销售额相比 2025 年 8 月",
        ]:
            with self.subTest(question=question):
                result = self.result(question, start="2025-08-01")
                self.assertEqual(
                    result["evidence"]["plan"]["previous_start"], "2025-08-01"
                )
                self.assertEqual(result["delta"], -9000)

    def test_january_mom_uses_previous_december(self):
        for order in self.data["orders"]:
            order["date"] = (
                order["date"]
                .replace("2026-07", "2025-12")
                .replace("2026-08", "2026-01")
            )
        result = self.result(
            "2026 年 1 月订单数环比", start="2025-12-01", end="2026-01-31"
        )
        self.assertEqual(result["evidence"]["plan"]["previous_start"], "2025-12-01")
        self.assertEqual(result["delta"], 1)

    def test_ambiguous_scope_or_comparison_is_clarified(self):
        questions = [
            "2026 年 8 月华东和华南净销售额",
            "2026 年 8 月甲门店华南净销售额",
            "2026 年 8 月净销售额同比和环比",
            "2026 年 8 月净销售额环比相比 6 月",
            "2026 年 8 月净销售额同比相比 7 月",
            "2026 年 7 月净销售额相比 8 月",
            "2026 年 8 月和 7 月净销售额合计是多少？",
        ]
        for question in questions:
            with (
                self.subTest(question=question),
                self.assertRaises(DomainError) as error,
            ):
                self.result(question)
            self.assertEqual(error.exception.status, 422)

    def test_zero_base_and_empty_periods_are_distinct_for_every_metric(self):
        for item in self.data["items"][:2]:
            item["refund_amount"] = item["paid_amount"]
            item["returned_quantity"] = item["quantity"]
        for label in ["净销售额", "客单价", "净销量"]:
            metric = self.result(f"2026 年 8 月{label}相比 7 月")["metric_results"][0]
            self.assertEqual(metric["previous"], 0)
            self.assertIsNone(metric["rate"])
            self.assertIsNotNone(metric["delta"])
        self.data["orders"] = [
            o for o in self.data["orders"] if o["date"].startswith("2026-08")
        ]
        result = self.result("2026 年 8 月净销售额、订单数、客单价和销量相比 7 月")
        for metric in result["metric_results"]:
            self.assertIsNone(metric["previous"])
            self.assertIsNone(metric["delta"])
            self.assertIsNone(metric["rate"])

    def test_metric_version_is_pinned_and_old_plans_remain_supported(self):
        plan = plan_question(
            "2026 年 8 月销售额", self.data, self.allowed, "2026-07-01", "2026-08-31"
        )
        del plan["metrics"]
        result = analyze(
            self.data, self.allowed, plan, "old-version", "historical-metrics"
        )
        self.assertEqual(result["metric_results"][0]["metric"], "net")
        self.assertEqual(result["evidence"]["metric_version"], "historical-metrics")
