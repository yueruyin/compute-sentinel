#!/usr/bin/env python3

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("price_monitor.py")
SPEC = importlib.util.spec_from_file_location("price_monitor", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PriceMonitorTests(unittest.TestCase):
    def make_klines(self, closes):
        first_date = datetime(2026, 1, 1)
        return [
            {"date": (first_date + timedelta(days=index)).date().isoformat(),
             "open": close - 0.5, "close": close,
             "high": close + 1.0, "low": close - 1.0}
            for index, close in enumerate(closes)
        ]

    def make_quote(self, price=121.0):
        return {
            "price": price, "change_pct": 1.0, "high": price + 1.0,
            "low": price - 1.0, "quote_time": "2026-08-25T10:00:00+08:00",
            "source": "测试行情",
        }

    def run_monitor_with_mocks(self, stocks, a_quotes, hk_quotes, a_klines, hk_klines):
        fixed_now = datetime(2026, 8, 25, 10, 30, tzinfo=MODULE.CST)
        with (
            patch.object(MODULE, "now_cst", return_value=fixed_now),
            patch.object(MODULE, "load_watchlist", return_value=stocks),
            patch.object(MODULE, "fetch_a_quotes", return_value=a_quotes),
            patch.object(MODULE, "fetch_hk_quotes", return_value=hk_quotes),
            patch.object(MODULE, "fetch_a_klines", side_effect=a_klines) as fetch_a,
            patch.object(MODULE, "fetch_hk_klines", side_effect=hk_klines) as fetch_hk,
        ):
            report = MODULE.run_monitor(Path("fixture-watchlist.json"), 1.0)
        return report, fetch_a, fetch_hk

    def test_metrics_have_ordered_reference_levels(self):
        metrics = MODULE.compute_metrics(self.make_klines([100 + index for index in range(25)]), 121.0)
        self.assertLess(metrics["stop_reference"], metrics["support_zone"][0])
        self.assertLess(metrics["support_zone"][0], metrics["support_zone"][1])
        self.assertLess(metrics["reference_zone"][0], metrics["reference_zone"][1])
        self.assertGreaterEqual(metrics["target_2"], metrics["target_1"])

    def test_ma60_uses_exactly_sixty_raw_closes(self):
        closes = [100.001 + index * 0.137 for index in range(60)]
        metrics = MODULE.compute_metrics(self.make_klines(closes), 108.0)
        self.assertIsInstance(metrics["ma60"], float)
        self.assertEqual(metrics["ma60"], round(sum(closes) / 60, 2))

    def test_ma60_uses_only_latest_sixty_bars(self):
        klines = self.make_klines([float(index) for index in range(1, 71)])
        baseline = MODULE.compute_metrics(klines, 70.0)["ma60"]

        changed_old_bar = [dict(row) for row in klines]
        changed_old_bar[0]["close"] += 6000.0
        self.assertEqual(MODULE.compute_metrics(changed_old_bar, 70.0)["ma60"], baseline)

        changed_window_bar = [dict(row) for row in klines]
        changed_window_bar[10]["close"] += 60.0
        self.assertEqual(MODULE.compute_metrics(changed_window_bar, 70.0)["ma60"], baseline + 1.0)

    def test_fifty_nine_bars_leave_ma60_unavailable(self):
        metrics = MODULE.compute_metrics(self.make_klines(range(59)), 59.0)
        self.assertIsNone(metrics["ma60"])

    def test_twenty_one_bar_compatibility_fixture(self):
        metrics = MODULE.compute_metrics(self.make_klines(range(100, 121)), 121.0)
        expected = {
            "ma10": 115.50,
            "ma20": 110.50,
            "ma60": None,
            "atr14": 2.00,
            "reference_zone": [108.29, 117.81],
            "support_zone": [107.50, 109.50],
            "stop_reference": 105.50,
            "target_1": 121.00,
            "target_2": 124.00,
            "reward_risk": 0.00,
        }
        for field, value in expected.items():
            self.assertEqual(metrics[field], value, field)

    def test_existing_metrics_signal_and_decision_remain_compatible(self):
        metrics = MODULE.compute_metrics(self.make_klines(range(100, 170)), 166.0)
        expected = {
            "ma10": 164.50,
            "ma20": 159.50,
            "atr14": 2.00,
            "reference_zone": [156.31, 167.79],
            "support_zone": [156.50, 158.50],
            "stop_reference": 154.50,
            "target_1": 170.00,
            "target_2": 173.00,
            "reward_risk": 0.35,
        }
        for field, value in expected.items():
            self.assertEqual(metrics[field], value, field)
        signal, _ = MODULE.classify(metrics, 166.0)
        result = {
            "code": "1", "name": "兼容性", "sector": "AI芯片", "price": 166.0,
            "quote_status": "fresh", "metrics_status": "ok", "signal": signal, **metrics,
        }
        self.assertEqual(signal, "reward_risk_weak")
        self.assertEqual(MODULE.decision_level(result), "reward_risk_weak")

    def test_unordered_bars_are_sorted_before_all_metrics(self):
        ordered = self.make_klines(range(100, 165))
        expected = MODULE.compute_metrics(ordered, 160.0)
        actual = MODULE.compute_metrics(list(reversed(ordered)), 160.0)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["last_bar_date"], ordered[-1]["date"])

    def test_invalid_bars_fail_with_locatable_validation_reason(self):
        base = self.make_klines(range(100, 160))
        cases = []

        duplicate = [dict(row) for row in base]
        duplicate[-1]["date"] = duplicate[-2]["date"]
        cases.append((duplicate, "日期重复"))

        bad_date = [dict(row) for row in base]
        bad_date[-1]["date"] = "not-a-date"
        cases.append((bad_date, "日期不可解析"))

        missing = [dict(row) for row in base]
        missing[-1].pop("close")
        cases.append((missing, "缺少必需字段: close"))

        non_numeric = [dict(row) for row in base]
        non_numeric[-1]["high"] = "bad"
        cases.append((non_numeric, "字段high非数值"))

        nan_value = [dict(row) for row in base]
        nan_value[-1]["close"] = float("nan")
        cases.append((nan_value, "字段close不是有限数值"))

        infinite = [dict(row) for row in base]
        infinite[-1]["low"] = float("inf")
        cases.append((infinite, "字段low不是有限数值"))

        for klines, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                MODULE.compute_metrics(klines, 160.0)

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

    def test_monitor_requests_sixty_bars_for_both_markets(self):
        stocks = [
            {"code": "600000", "name": "A测试", "market": "a", "sector": "AI芯片", "role": "核心"},
            {"code": "00001", "name": "H测试", "market": "hk", "sector": "云厂商", "role": "核心"},
        ]
        klines = self.make_klines(range(100, 160))
        report, fetch_a, fetch_hk = self.run_monitor_with_mocks(
            stocks,
            {"600000": self.make_quote()},
            {"00001": self.make_quote()},
            lambda *_: klines,
            lambda *_: klines,
        )
        self.assertGreaterEqual(fetch_a.call_args.args[1], 60)
        self.assertGreaterEqual(fetch_hk.call_args.args[1], 60)
        self.assertTrue(all("ma60" in result for result in report["results"]))
        self.assertTrue(all(isinstance(result["ma60"], float) for result in report["results"]))

    def test_fifty_nine_bars_keep_success_quality_and_json_null(self):
        stock = {"code": "600000", "name": "A测试", "market": "a", "sector": "AI芯片", "role": "核心"}
        report, _, _ = self.run_monitor_with_mocks(
            [stock], {"600000": self.make_quote(159.0)}, {},
            lambda *_: self.make_klines(range(100, 159)), lambda *_: [],
        )
        result = report["results"][0]
        self.assertEqual(result["metrics_status"], "ok")
        self.assertIsNone(result["ma60"])
        self.assertEqual(report["data_quality"]["metrics_ok"], 1)
        self.assertEqual(report["data_quality"]["errors"], 0)
        self.assertNotEqual(MODULE.decision_level(result), "data_incomplete")
        data_incomplete = next(
            level for level in report["decision_matrix"] if level["key"] == "data_incomplete"
        )
        self.assertEqual(data_incomplete["count"], 0)
        serialized = json.dumps(report, ensure_ascii=False, allow_nan=False)
        self.assertIsNone(json.loads(serialized)["results"][0]["ma60"])

    def test_missing_failed_and_short_history_results_keep_ma60_null(self):
        stocks = [
            {"code": "600001", "name": "缺行情", "market": "a", "sector": "AI芯片", "role": "核心"},
            {"code": "600002", "name": "取数失败", "market": "a", "sector": "AI芯片", "role": "核心"},
            {"code": "600003", "name": "不足21", "market": "a", "sector": "AI芯片", "role": "核心"},
        ]

        def fetch_klines(code, _days, _timeout):
            if code == "600002":
                raise MODULE.FetchError("测试K线", "获取失败")
            return self.make_klines(range(100, 120))

        report, _, _ = self.run_monitor_with_mocks(
            stocks,
            {"600002": self.make_quote(), "600003": self.make_quote()},
            {}, fetch_klines, lambda *_: [],
        )
        results = {result["code"]: result for result in report["results"]}
        self.assertEqual(results["600001"]["metrics_status"], "skipped")
        self.assertEqual(results["600002"]["metrics_status"], "error")
        self.assertEqual(results["600003"]["metrics_status"], "error")
        self.assertTrue(all(result["ma60"] is None for result in results.values()))
        self.assertTrue(any("获取失败" in error for error in report["errors"]))
        self.assertTrue(any("K线不足21根" in error for error in report["errors"]))

    def test_monitor_surfaces_each_kline_validation_failure(self):
        stock = {"code": "600000", "name": "校验", "market": "a", "sector": "AI芯片", "role": "核心"}
        base = self.make_klines(range(100, 160))
        cases = []

        duplicate = [dict(row) for row in base]
        duplicate[-1]["date"] = duplicate[-2]["date"]
        cases.append((duplicate, "日期重复"))

        bad_date = [dict(row) for row in base]
        bad_date[-1]["date"] = "bad-date"
        cases.append((bad_date, "日期不可解析"))

        missing = [dict(row) for row in base]
        missing[-1].pop("high")
        cases.append((missing, "缺少必需字段: high"))

        non_numeric = [dict(row) for row in base]
        non_numeric[-1]["low"] = "bad"
        cases.append((non_numeric, "字段low非数值"))

        nan_value = [dict(row) for row in base]
        nan_value[-1]["close"] = float("nan")
        cases.append((nan_value, "字段close不是有限数值"))

        infinite = [dict(row) for row in base]
        infinite[-1]["high"] = float("inf")
        cases.append((infinite, "字段high不是有限数值"))

        for klines, reason in cases:
            with self.subTest(reason=reason):
                report, _, _ = self.run_monitor_with_mocks(
                    [stock], {"600000": self.make_quote()}, {},
                    lambda *_, bars=klines: bars, lambda *_: [],
                )
                result = report["results"][0]
                self.assertEqual(result["metrics_status"], "error")
                self.assertIsNone(result["ma60"])
                self.assertTrue(any(reason in error for error in report["errors"]))
                json.dumps(report, ensure_ascii=False, allow_nan=False)

    def test_markdown_renders_available_short_and_failed_ma60(self):
        base = {
            "market": "a", "sector": "AI芯片", "role": "核心", "price": 121.0,
            "change_pct": 1.0, "quote_time": "2026-08-25T10:00:00+08:00",
            "quote_status": "fresh", "ma10": 115.5, "ma20": 110.5,
            "reference_zone": [108.29, 117.81], "stop_reference": 105.5,
            "target_1": 121.0, "reward_risk": 0.0, "signal_text": "测试",
        }
        results = [
            {**base, "code": "1", "name": "可用", "metrics_status": "ok", "ma60": 108.25},
            {**base, "code": "2", "name": "历史不足", "metrics_status": "ok", "ma60": None},
            {**base, "code": "3", "name": "指标失败", "metrics_status": "error", "ma60": None},
        ]
        report = {
            "generated_at": "2026-08-25T10:30:00+08:00", "session": "早盘",
            "data_quality": {"quotes_ok": 3, "total": 3, "metrics_ok": 2, "errors": 1},
            "sector_summary": [], "decision_matrix": [], "results": results,
            "errors": [], "disclaimer": "测试声明",
        }
        markdown = MODULE.render_markdown(report)
        self.assertIn("MA10/MA20/MA60", markdown)
        self.assertIn("115.50/110.50/108.25", markdown)
        self.assertIn("115.50/110.50/—（历史不足60根）", markdown)
        self.assertIn("115.50/110.50/—", markdown)
        self.assertEqual(markdown.count("—（历史不足60根）"), 1)


if __name__ == "__main__":
    unittest.main()
