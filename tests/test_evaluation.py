import json
import unittest
from pathlib import Path

from backend.domain import DomainError, analyze, plan_question
from tests.fixtures import metric_data


class FrozenEvaluationTests(unittest.TestCase):
    def test_30_frozen_values_scopes_dates_and_reports(self):
        fixture = json.loads(Path("tests/evaluation/monthly-v1.json").read_text())
        self.assertEqual(len(fixture["cases"]), 30)
        data = metric_data()
        for expected in fixture["cases"]:
            with self.subTest(id=expected["id"], question=expected["question"]):
                plan = plan_question(
                    expected["question"], data, ["A", "B", "C"], *fixture["coverage"]
                )
                result = analyze(data, ["A", "B", "C"], plan, "frozen-v1")
                self.assertEqual(plan["metrics"], expected["metrics"])
                self.assertEqual(
                    (plan["start"], plan["end"]), (expected["start"], expected["end"])
                )
                self.assertEqual(
                    sorted(result["evidence"]["scope"]), sorted(expected["scope"])
                )
                self.assertEqual(
                    [m["current"] for m in result["metric_results"]],
                    expected["current"],
                )
                if expected["previous"] is not None:
                    self.assertEqual(
                        (plan["previous_start"], plan["previous_end"]),
                        ("2026-07-01", "2026-07-31"),
                    )
                    self.assertEqual(
                        [m["previous"] for m in result["metric_results"]],
                        expected["previous"],
                    )
                    self.assertEqual(
                        result["delta"],
                        expected["current"][0] - expected["previous"][0],
                    )
                else:
                    self.assertFalse(plan["compare"])
                if expected["ranking"]:
                    self.assertEqual(
                        [
                            r["store_id"]
                            for r in result["metric_results"][0]["rankings"]
                        ],
                        expected["ranking"],
                    )
                if expected["abnormal"] is not None:
                    self.assertEqual(result["abnormal"], expected["abnormal"])
                    self.assertIn("不能据此确认业务原因", result["limits"])
                    self.assertTrue(result["summary"])

    def test_separate_ambiguity_missing_and_permissions_set(self):
        for q, allowed, status in [
            ("最近如何", ["A", "B", "C"], 422),
            ("2026 年 8 月华东和华南销售额", ["A", "B", "C"], 422),
            ("2026 年 8 月净销售额同比", ["A", "B", "C"], 422),
            ("2026 年 8 月毛利", ["A", "B", "C"], 422),
            ("2026 年 8 月P净销售额", ["A", "B", "C"], 422),
            ("2026 年 8 月乙门店净销售额", ["A"], 403),
        ]:
            with self.subTest(q=q), self.assertRaises(DomainError) as raised:
                plan_question(q, metric_data(), allowed, "2026-07-01", "2026-08-31")
            self.assertEqual(raised.exception.status, status)
