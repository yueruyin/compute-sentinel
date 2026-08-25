#!/usr/bin/env python3
"""Read-only A/H-share monitor for the Token compute-chain watchlist."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CST = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WATCHLIST = SCRIPT_DIR.parent / "references" / "watchlist.json"
USER_AGENT = "Mozilla/5.0 (compatible; CodexTokenComputeWatch/1.0)"
KLINE_DAYS = 60
SECTOR_GROUP_ALIASES = {
    "光模块": "光模块/光通信",
    "光通信": "光模块/光通信",
}
DECISION_LEVELS = [
    {
        "key": "priority_candidate",
        "label": "优先观察",
        "condition": "盈亏比≥1.5，且现价位于均线参考区",
        "research_action": "等待量价与催化确认",
    },
    {
        "key": "favorable_wait",
        "label": "赔率较优，等待位置确认",
        "condition": "盈亏比≥1.5，但现价不在均线参考区",
        "research_action": "等待回到参考区或突破结构确认",
    },
    {
        "key": "wait_improvement",
        "label": "等待改善",
        "condition": "1≤盈亏比<1.5",
        "research_action": "等待风险收窄或目标空间扩大",
    },
    {
        "key": "reward_risk_weak",
        "label": "风险收益偏弱",
        "condition": "盈亏比<1",
        "research_action": "降低研究优先级，避免追价",
    },
    {
        "key": "structure_risk",
        "label": "结构风险",
        "condition": "现价不高于波动止损参考",
        "research_action": "风险观察优先，等待结构修复",
    },
    {
        "key": "data_incomplete",
        "label": "数据不足",
        "condition": "行情或指标缺失、过期，无法计算",
        "research_action": "不做方向性判断",
    },
]


@dataclass
class FetchError(Exception):
    source: str
    message: str

    def __str__(self) -> str:
        return f"{self.source}: {self.message}"


def now_cst() -> datetime:
    return datetime.now(CST)


def iso_time(value: datetime | None) -> str | None:
    return value.astimezone(CST).isoformat(timespec="seconds") if value else None


def request_bytes(url: str, timeout: float, source: str, headers: dict[str, str] | None = None) -> bytes:
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        curl = shutil.which("curl")
        if not curl:
            raise FetchError(source, str(exc)) from exc
        command = [curl, "-fsSL", "--max-time", str(timeout), "--user-agent", USER_AGENT]
        for key, value in (headers or {}).items():
            command.extend(["-H", f"{key}: {value}"])
        command.append(url)
        completed = subprocess.run(command, capture_output=True, timeout=timeout + 2, check=False)
        if completed.returncode == 0:
            return completed.stdout
        detail = completed.stderr.decode("utf-8", errors="replace").strip() or str(exc)
        raise FetchError(source, detail) from exc


def request_json(url: str, timeout: float, source: str) -> dict[str, Any]:
    raw = request_bytes(url, timeout, source)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(source, f"响应不是有效JSON: {exc}") from exc


def load_watchlist(path: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取关注池 {path}: {exc}") from exc
    stocks = payload.get("stocks")
    if not isinstance(stocks, list) or not stocks:
        raise SystemExit("关注池必须包含非空 stocks 数组")
    seen: set[str] = set()
    for stock in stocks:
        required = {"code", "name", "market", "sector", "role"}
        missing = required - stock.keys()
        if missing:
            raise SystemExit(f"关注池条目缺少字段 {sorted(missing)}: {stock}")
        if stock["market"] not in {"a", "hk"}:
            raise SystemExit(f"不支持的市场 {stock['market']}: {stock['code']}")
        key = f"{stock['market']}:{stock['code']}"
        if key in seen:
            raise SystemExit(f"关注池代码重复: {key}")
        seen.add(key)
    return stocks


def scaled(value: Any, divisor: float = 100.0) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return round(float(value) / divisor, 2)
    except (TypeError, ValueError):
        return None


def parse_epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=CST)
    except (TypeError, ValueError, OSError):
        return None


def fetch_a_quotes(stocks: list[dict[str, str]], timeout: float) -> dict[str, dict[str, Any]]:
    if not stocks:
        return {}
    secids = [f"{'1' if s['code'].startswith('6') else '0'}.{s['code']}" for s in stocks]
    params = urllib.parse.urlencode({
        "fields": "f2,f3,f12,f14,f15,f16,f124",
        "secids": ",".join(secids),
    })
    data = request_json(
        f"https://push2.eastmoney.com/api/qt/ulist.np/get?{params}", timeout, "东方财富A股行情"
    )
    diff = (data.get("data") or {}).get("diff")
    if data.get("rc") != 0 or not isinstance(diff, list):
        raise FetchError("东方财富A股行情", f"异常响应 rc={data.get('rc')}")
    results: dict[str, dict[str, Any]] = {}
    for item in diff:
        code = str(item.get("f12", ""))
        results[code] = {
            "price": scaled(item.get("f2")),
            "change_pct": scaled(item.get("f3")),
            "high": scaled(item.get("f15")),
            "low": scaled(item.get("f16")),
            "quote_time": iso_time(parse_epoch(item.get("f124"))),
            "source": "东方财富push2",
        }
    return results


def decode_cn(raw: bytes) -> str:
    for encoding in ("gbk", "gb2312", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_a_quotes_sina(stocks: list[dict[str, str]], timeout: float) -> dict[str, dict[str, Any]]:
    code_map: dict[str, str] = {}
    symbols: list[str] = []
    for stock in stocks:
        symbol = f"{'sh' if stock['code'].startswith('6') else 'sz'}{stock['code']}"
        symbols.append(symbol)
        code_map[symbol] = stock["code"]
    raw = request_bytes(
        f"https://hq.sinajs.cn/list={','.join(symbols)}",
        timeout,
        "新浪A股行情",
        {"Referer": "https://finance.sina.com.cn"},
    )
    results: dict[str, dict[str, Any]] = {}
    for line in decode_cn(raw).split(";"):
        if "=" not in line or '"' not in line:
            continue
        symbol = line.split("=", 1)[0].strip().replace("var ", "").replace("hq_str_", "")
        code = code_map.get(symbol)
        fields = line.split('"')[1].split(",")
        if not code or len(fields) < 32:
            continue
        try:
            current = float(fields[3])
            previous = float(fields[2])
            if current == 0 and previous:
                current = previous
            timestamp = datetime.strptime(f"{fields[30]} {fields[31]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
            results[code] = {
                "price": current,
                "change_pct": round((current - previous) / previous * 100, 2) if previous else None,
                "high": float(fields[4]) or current,
                "low": float(fields[5]) or current,
                "quote_time": iso_time(timestamp),
                "source": "新浪行情fallback",
            }
        except (ValueError, IndexError):
            continue
    if not results:
        raise FetchError("新浪A股行情", "未解析到任何标的")
    return results


def fetch_hk_quotes(stocks: list[dict[str, str]], timeout: float) -> dict[str, dict[str, Any]]:
    symbols = [f"r_hk{s['code']}" for s in stocks]
    raw = request_bytes(f"https://qt.gtimg.cn/q={','.join(symbols)}", timeout, "腾讯港股行情")
    results: dict[str, dict[str, Any]] = {}
    for line in decode_cn(raw).split(";"):
        if "=" not in line or '"' not in line:
            continue
        fields = line.split('"')[1].split("~")
        if len(fields) < 35:
            continue
        try:
            code = fields[2]
            price = float(fields[3])
            previous = float(fields[4])
            timestamp = datetime.strptime(fields[30], "%Y/%m/%d %H:%M:%S").replace(tzinfo=CST)
            results[code] = {
                "price": price,
                "change_pct": round((price - previous) / previous * 100, 2) if previous else None,
                "high": float(fields[33]),
                "low": float(fields[34]),
                "quote_time": iso_time(timestamp),
                "source": "腾讯港股行情",
            }
        except (ValueError, IndexError):
            continue
    if not results:
        raise FetchError("腾讯港股行情", "未解析到任何标的")
    return results


def fetch_a_klines(code: str, days: int, timeout: float) -> list[dict[str, Any]]:
    secid = f"{'1' if code.startswith('6') else '0'}.{code}"
    params = urllib.parse.urlencode({
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "klt": 101,
        "fqt": 1,
        "end": 20991231,
        "lmt": days,
    })
    data = request_json(
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{params}", timeout, f"东方财富K线:{code}"
    )
    rows = (data.get("data") or {}).get("klines")
    if not isinstance(rows, list):
        raise FetchError(f"东方财富K线:{code}", "无K线数据")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, str):
            raise FetchError(f"东方财富K线:{code}", f"第{index}根K线格式异常")
        parts = row.split(",")
        if len(parts) < 5:
            raise FetchError(f"东方财富K线:{code}", f"第{index}根K线缺少必需价格字段")
        try:
            result.append({
                "date": parts[0], "open": float(parts[1]), "close": float(parts[2]),
                "high": float(parts[3]), "low": float(parts[4]),
            })
        except (TypeError, ValueError) as exc:
            raise FetchError(f"东方财富K线:{code}", f"第{index}根K线价格非数值") from exc
    return result


def fetch_hk_klines(code: str, days: int, timeout: float) -> list[dict[str, Any]]:
    params = urllib.parse.quote(f"hk{code},day,,,{days},qfq", safe=",")
    data = request_json(
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={params}", timeout, f"腾讯港股K线:{code}"
    )
    market_data = (data.get("data") or {}).get(f"hk{code}") or {}
    rows = market_data.get("day") or market_data.get("qfqday") or []
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            raise FetchError(f"腾讯港股K线:{code}", f"第{index}根K线缺少必需价格字段")
        try:
            result.append({
                "date": row[0], "open": float(row[1]), "close": float(row[2]),
                "high": float(row[3]), "low": float(row[4]),
            })
        except (TypeError, ValueError) as exc:
            raise FetchError(f"腾讯港股K线:{code}", f"第{index}根K线价格非数值") from exc
    if not result:
        raise FetchError(f"腾讯港股K线:{code}", "无K线数据")
    return result


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def normalize_klines(klines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate daily bars and return a copy ordered by trading date."""
    normalized: list[tuple[datetime, dict[str, Any]]] = []
    seen_dates: set[str] = set()
    for index, row in enumerate(klines, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"第{index}根K线不是对象")
        raw_date = row.get("date")
        try:
            parsed_date = datetime.strptime(str(raw_date), "%Y-%m-%d")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第{index}根K线日期不可解析: {raw_date!r}") from exc
        date_text = parsed_date.date().isoformat()
        if date_text in seen_dates:
            raise ValueError(f"K线日期重复: {date_text}")
        seen_dates.add(date_text)

        clean_row = {**row, "date": date_text}
        for field in ("close", "high", "low"):
            if field not in row:
                raise ValueError(f"第{index}根K线缺少必需字段: {field}")
            try:
                value = float(row[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"第{index}根K线字段{field}非数值: {row[field]!r}") from exc
            if not math.isfinite(value):
                raise ValueError(f"第{index}根K线字段{field}不是有限数值: {row[field]!r}")
            clean_row[field] = value
        normalized.append((parsed_date, clean_row))
    normalized.sort(key=lambda item: item[0])
    return [row for _, row in normalized]


def compute_metrics(klines: list[dict[str, Any]], price: float) -> dict[str, Any]:
    klines = normalize_klines(klines)
    if len(klines) < 21:
        raise ValueError(f"K线不足21根，实际{len(klines)}根")
    closes = [float(row["close"]) for row in klines]
    ma10 = mean(closes[-10:])
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:]) if len(closes) >= 60 else None
    true_ranges: list[float] = []
    for previous, current in zip(klines[:-1], klines[1:]):
        true_ranges.append(max(
            float(current["high"]) - float(current["low"]),
            abs(float(current["high"]) - float(previous["close"])),
            abs(float(current["low"]) - float(previous["close"])),
        ))
    atr14 = mean(true_ranges[-14:])
    reference_low = min(ma10, ma20) * 0.98
    reference_high = max(ma10, ma20) * 1.02
    support_low = ma20 - 1.5 * atr14
    support_high = ma20 - 0.5 * atr14
    stop_reference = ma20 - 2.5 * atr14
    recent_high = max(float(row["high"]) for row in klines[-20:])
    target_1 = recent_high
    target_2 = recent_high + 1.5 * atr14
    risk = price - stop_reference
    reward = max(target_1 - price, 0.0)
    reward_risk = reward / risk if risk > 0 else None
    return {
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2) if ma60 is not None else None,
        "atr14": round(atr14, 2),
        "reference_zone": [round(reference_low, 2), round(reference_high, 2)],
        "support_zone": [round(support_low, 2), round(support_high, 2)],
        "stop_reference": round(stop_reference, 2),
        "target_1": round(target_1, 2),
        "target_2": round(target_2, 2),
        "reward_risk": round(reward_risk, 2) if reward_risk is not None else None,
        "last_bar_date": klines[-1]["date"],
    }


def classify(metrics: dict[str, Any], price: float) -> tuple[str, str]:
    rr = metrics["reward_risk"]
    ref_low, ref_high = metrics["reference_zone"]
    support_low, support_high = metrics["support_zone"]
    if price <= metrics["stop_reference"]:
        return "risk_break", "跌破波动止损参考，结构风险升高"
    if rr is not None and rr < 1:
        return "reward_risk_weak", "目标1对应的风险收益偏弱"
    if ref_low <= price <= ref_high and rr is not None and rr >= 1.5:
        return "reference_candidate", "均线参考区内且风险收益较优，等待量价确认"
    if support_low <= price <= support_high:
        return "support_watch", "进入支撑观察区，等待确认"
    if price >= metrics["target_1"]:
        return "extended", "达到近20日高位，关注突破有效性与追高风险"
    return "watch", "未触发关键阈值，继续观察"


def quote_freshness(quote_time: str | None, generated_at: datetime) -> str:
    if not quote_time:
        return "unknown"
    try:
        observed = datetime.fromisoformat(quote_time)
    except ValueError:
        return "unknown"
    age = generated_at - observed.astimezone(CST)
    threshold = timedelta(hours=72 if generated_at.weekday() >= 5 else 24)
    return "fresh" if -timedelta(minutes=5) <= age <= threshold else "stale"


def trading_session(moment: datetime) -> str:
    if moment.weekday() >= 5:
        return "周末/非交易日"
    hhmm = moment.hour * 100 + moment.minute
    if hhmm < 930:
        return "盘前"
    if hhmm <= 1130:
        return "早盘"
    if hhmm < 1300:
        return "午间休市"
    if hhmm <= 1500:
        return "A股午盘/港股午盘"
    if hhmm <= 1600:
        return "A股收盘/港股尾盘"
    return "收盘后"


def summarize_sectors(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate watchlist entries into report-facing sector groups."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        group = SECTOR_GROUP_ALIASES.get(result["sector"], result["sector"])
        grouped.setdefault(group, []).append(result)

    summaries: list[dict[str, Any]] = []
    for group, members in grouped.items():
        changes = [
            float(member["change_pct"])
            for member in members
            if isinstance(member.get("change_pct"), (int, float))
            and math.isfinite(float(member["change_pct"]))
        ]
        ranked = [
            member for member in members
            if isinstance(member.get("change_pct"), (int, float))
            and math.isfinite(float(member["change_pct"]))
        ]
        leader = max(ranked, key=lambda item: float(item["change_pct"])) if ranked else None
        laggard = min(ranked, key=lambda item: float(item["change_pct"])) if ranked else None
        summaries.append({
            "sector": group,
            "constituents": [f"{member['code']} {member['name']}" for member in members],
            "total": len(members),
            "quotes_ok": sum(
                member.get("quote_status") in {"fresh", "stale", "unknown"} for member in members
            ),
            "metrics_ok": sum(member.get("metrics_status") == "ok" for member in members),
            "average_change_pct": round(mean(changes), 2) if changes else None,
            "advancers": sum(change > 0 for change in changes),
            "flat": sum(change == 0 for change in changes),
            "decliners": sum(change < 0 for change in changes),
            "leader": ({
                "code": leader["code"], "name": leader["name"],
                "change_pct": leader["change_pct"],
            } if leader else None),
            "laggard": ({
                "code": laggard["code"], "name": laggard["name"],
                "change_pct": laggard["change_pct"],
            } if laggard else None),
        })
    return summaries


def decision_level(result: dict[str, Any]) -> str:
    if result.get("quote_status") != "fresh" or result.get("metrics_status") != "ok":
        return "data_incomplete"
    if result.get("signal") == "risk_break":
        return "structure_risk"
    reward_risk = result.get("reward_risk")
    price = result.get("price")
    reference_zone = result.get("reference_zone") or []
    if not isinstance(reward_risk, (int, float)) or not math.isfinite(float(reward_risk)):
        return "data_incomplete"
    if reward_risk < 1:
        return "reward_risk_weak"
    if reward_risk < 1.5:
        return "wait_improvement"
    if (
        isinstance(price, (int, float))
        and len(reference_zone) == 2
        and reference_zone[0] <= price <= reference_zone[1]
    ):
        return "priority_candidate"
    return "favorable_wait"


def build_decision_matrix(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        level["key"]: [] for level in DECISION_LEVELS
    }
    for result in results:
        buckets[decision_level(result)].append({
            "code": result["code"],
            "name": result["name"],
            "sector": SECTOR_GROUP_ALIASES.get(result["sector"], result["sector"]),
            "reward_risk": result.get("reward_risk"),
            "signal": result.get("signal"),
        })
    return [
        {**level, "count": len(buckets[level["key"]]), "members": buckets[level["key"]]}
        for level in DECISION_LEVELS
    ]


def run_monitor(watchlist_path: Path, timeout: float) -> dict[str, Any]:
    generated_at = now_cst()
    stocks = load_watchlist(watchlist_path)
    a_stocks = [stock for stock in stocks if stock["market"] == "a"]
    hk_stocks = [stock for stock in stocks if stock["market"] == "hk"]
    errors: list[str] = []

    try:
        a_quotes = fetch_a_quotes(a_stocks, timeout)
    except FetchError as primary:
        errors.append(str(primary))
        try:
            a_quotes = fetch_a_quotes_sina(a_stocks, timeout)
        except FetchError as fallback:
            errors.append(str(fallback))
            a_quotes = {}
    try:
        hk_quotes = fetch_hk_quotes(hk_stocks, timeout)
    except FetchError as exc:
        errors.append(str(exc))
        hk_quotes = {}

    results: list[dict[str, Any]] = []
    for stock in stocks:
        quote = (a_quotes if stock["market"] == "a" else hk_quotes).get(stock["code"])
        entry: dict[str, Any] = {**stock, "ma60": None}
        if not quote or not isinstance(quote.get("price"), (int, float)):
            entry.update({
                "quote_status": "missing", "metrics_status": "skipped",
                "signal": "data_error", "signal_text": "行情缺失，不能评估",
            })
            errors.append(f"{stock['code']} {stock['name']}: 行情缺失")
            results.append(entry)
            continue
        entry.update(quote)
        entry["quote_status"] = quote_freshness(quote.get("quote_time"), generated_at)
        try:
            klines = (
                fetch_a_klines(stock["code"], KLINE_DAYS, timeout)
                if stock["market"] == "a"
                else fetch_hk_klines(stock["code"], KLINE_DAYS, timeout)
            )
            metrics = compute_metrics(klines, float(quote["price"]))
            signal, signal_text = classify(metrics, float(quote["price"]))
            entry.update(metrics)
            entry.update({"metrics_status": "ok", "signal": signal, "signal_text": signal_text})
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            entry.update({
                "metrics_status": "error", "signal": "data_error",
                "signal_text": "指标数据失败，不能评估",
            })
            errors.append(f"{stock['code']} {stock['name']}: {exc}")
        results.append(entry)

    quote_ok = sum(result.get("quote_status") in {"fresh", "stale", "unknown"} for result in results)
    metrics_ok = sum(result.get("metrics_status") == "ok" for result in results)
    return {
        "generated_at": iso_time(generated_at),
        "timezone": "Asia/Shanghai",
        "session": trading_session(generated_at),
        "watchlist_path": str(watchlist_path),
        "data_quality": {
            "total": len(stocks), "quotes_ok": quote_ok, "metrics_ok": metrics_ok,
            "fresh_quotes": sum(result.get("quote_status") == "fresh" for result in results),
            "errors": len(errors),
        },
        "sector_summary": summarize_sectors(results),
        "decision_matrix": build_decision_matrix(results),
        "results": results,
        "errors": errors,
        "disclaimer": "研究与风险监控用途，不构成投资建议或自动交易指令。",
    }


def fmt_num(value: Any, digits: int = 2) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "—"
    return f"{float(value):.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    quality = report["data_quality"]
    lines = [
        f"# Token算力链监控 | {report['generated_at']}",
        "",
        f"时段：{report['session']}｜行情成功 {quality['quotes_ok']}/{quality['total']}｜指标成功 {quality['metrics_ok']}/{quality['total']}｜错误 {quality['errors']}",
        "",
        "## 板块概览",
        "",
        "| 板块 | 成分 | 行情/指标 | 平均涨跌% | 上/平/下 | 领涨 | 领跌 |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for sector in report["sector_summary"]:
        leader = sector.get("leader") or {}
        laggard = sector.get("laggard") or {}
        leader_text = (
            f"{leader['name']} {fmt_num(leader.get('change_pct'))}%" if leader else "—"
        )
        laggard_text = (
            f"{laggard['name']} {fmt_num(laggard.get('change_pct'))}%" if laggard else "—"
        )
        lines.append(
            f"| {sector['sector']} | {'、'.join(sector['constituents'])} | "
            f"{sector['quotes_ok']}/{sector['metrics_ok']}/{sector['total']} | "
            f"{fmt_num(sector.get('average_change_pct'))} | "
            f"{sector['advancers']}/{sector['flat']}/{sector['decliners']} | "
            f"{leader_text} | {laggard_text} |"
        )
    lines.extend([
        "",
        "## 盈亏比决策矩阵",
        "",
        "| 层级 | 判定条件 | 研究动作 | 标的 |",
        "|---|---|---|---|",
    ])
    for level in report["decision_matrix"]:
        members = "、".join(
            f"{member['code']} {member['name']}(RR {fmt_num(member.get('reward_risk'))})"
            for member in level["members"]
        ) or "—"
        lines.append(
            f"| {level['label']}（{level['count']}） | {level['condition']} | "
            f"{level['research_action']} | {members} |"
        )
    lines.extend([
        "",
        "## 标的明细",
        "",
        "| 板块 | 市场 | 角色 | 标的 | 现价 | 涨跌% | 行情时间 | MA10/MA20/MA60 | 参考区 | 止损参考 | 目标1 | 盈亏比 | 信号 |",
        "|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
    ])
    for result in report["results"]:
        market = "A股" if result["market"] == "a" else "港股"
        sector = SECTOR_GROUP_ALIASES.get(result["sector"], result["sector"])
        zone = result.get("reference_zone") or []
        zone_text = f"{fmt_num(zone[0])}-{fmt_num(zone[1])}" if len(zone) == 2 else "—"
        ma60_text = fmt_num(result.get("ma60"))
        if result.get("metrics_status") == "ok" and result.get("ma60") is None:
            ma60_text = "—（历史不足60根）"
        ma_text = (
            f"{fmt_num(result.get('ma10'))}/{fmt_num(result.get('ma20'))}/{ma60_text}"
        )
        lines.append(
            f"| {sector} | {market} | {result['role']} | {result['code']} {result['name']} | "
            f"{fmt_num(result.get('price'))} | {fmt_num(result.get('change_pct'))} | "
            f"{result.get('quote_time') or '—'} ({result.get('quote_status', '—')}) | {ma_text} | {zone_text} | "
            f"{fmt_num(result.get('stop_reference'))} | {fmt_num(result.get('target_1'))} | "
            f"{fmt_num(result.get('reward_risk'))} | {result.get('signal_text', '—')} |"
        )
    if report["errors"]:
        lines.extend(["", "## 数据错误"])
        lines.extend(f"- {error}" for error in report["errors"])
    lines.extend(["", f"> {report['disclaimer']}"])
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Token算力链A/H股只读行情监控")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--state-output", type=Path, help="可选：把本次完整JSON结果写入指定路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = run_monitor(args.watchlist.resolve(), args.timeout)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.state_output:
        args.state_output.parent.mkdir(parents=True, exist_ok=True)
        args.state_output.write_text(payload + "\n", encoding="utf-8")
    print(payload if args.format == "json" else render_markdown(report))
    return 0 if report["data_quality"]["quotes_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
