#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("price_monitor.py")
SPEC = importlib.util.spec_from_file_location("price_monitor", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PriceMonitorTests(unittest.TestCase):
    def make_klines(self, closes):
        return [
            {"date": f"2026-07-{index + 1:02d}", "open": close - 0.5, "close": close,
             "high": close + 1.0, "low": close - 1.0}
            for index, close in enumerate(closes)
        ]

    def test_metrics_have_ordered_reference_levels(self):
        metrics = MODULE.compute_metrics(self.make_klines([100 + index for index in range(25)]), 121.0)
        self.assertLess(metrics["stop_reference"], metrics["support_zone"][0])
        self.assertLess(metrics["support_zone"][0], metrics["support_zone"][1])
        self.assertLess(metrics["reference_zone"][0], metrics["reference_zone"][1])
        self.assertGreaterEqual(metrics["target_2"], metrics["target_1"])

    def test_low_reward_risk_is_not_a_positive_candidate(self):
        metrics = MODULE.compute_metrics(self.make_klines([100 + index for index in range(25)]), 124.5)
        signal, _ = MODULE.classify(metrics, 124.5)
        self.assertEqual(signal, "reward_risk_weak")

    def test_watchlist_is_valid_and_unique(self):
        stocks = MODULE.load_watchlist(MODULE.DEFAULT_WATCHLIST)
        keys = {(stock["market"], stock["code"]) for stock in stocks}
        self.assertEqual(len(keys), len(stocks))
        self.assertGreaterEqual(len(stocks), 10)

    def test_sector_summary_combines_optical_and_keeps_storage(self):
        results = [
            {"code": "1", "name": "光模块A", "sector": "光模块", "change_pct": 2.0,
             "quote_status": "fresh", "metrics_status": "ok"},
            {"code": "2", "name": "光通信B", "sector": "光通信", "change_pct": -1.0,
             "quote_status": "fresh", "metrics_status": "ok"},
            {"code": "3", "name": "存储C", "sector": "存储芯片", "change_pct": 3.0,
             "quote_status": "fresh", "metrics_status": "ok"},
        ]
        summaries = {item["sector"]: item for item in MODULE.summarize_sectors(results)}
        self.assertEqual(summaries["光模块/光通信"]["total"], 2)
        self.assertEqual(summaries["光模块/光通信"]["average_change_pct"], 0.5)
        self.assertEqual(summaries["存储芯片"]["total"], 1)

    def test_decision_matrix_assigns_each_reward_risk_bucket(self):
        base = {
            "sector": "存储芯片", "quote_status": "fresh", "metrics_status": "ok",
            "signal": "watch", "reference_zone": [95.0, 105.0],
        }
        results = [
            {**base, "code": "1", "name": "优先", "price": 100.0, "reward_risk": 2.0},
            {**base, "code": "2", "name": "位置", "price": 110.0, "reward_risk": 2.0},
            {**base, "code": "3", "name": "等待", "price": 100.0, "reward_risk": 1.2},
            {**base, "code": "4", "name": "偏弱", "price": 100.0, "reward_risk": 0.8},
            {**base, "code": "5", "name": "风险", "price": 80.0, "reward_risk": None,
             "signal": "risk_break"},
            {**base, "code": "6", "name": "缺失", "price": None, "reward_risk": None,
             "quote_status": "missing", "metrics_status": "skipped"},
        ]
        matrix = {item["key"]: item for item in MODULE.build_decision_matrix(results)}
        self.assertTrue(all(item["count"] == 1 for item in matrix.values()))


if __name__ == "__main__":
    unittest.main()
