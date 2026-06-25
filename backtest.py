from __future__ import annotations

from collections import defaultdict
from typing import Iterable


INTRADAY_EXITS = {
    "m1": ("intraday_m1_return_pct", "1分钟"),
    "m3": ("intraday_m3_return_pct", "3分钟"),
    "m5": ("intraday_m5_return_pct", "5分钟"),
    "m10": ("intraday_m10_return_pct", "10分钟"),
    "intraday_current": ("intraday_current_return_pct", "盘中当前"),
    "intraday_high": ("intraday_max_return_pct", "盘中最高"),
}

NEXT_DAY_EXITS = {
    "next_open": ("gap_pct", "次日开盘"),
    "next_high": ("next_high_return_pct", "次日最高"),
    "next_close": ("next_return_pct", "次日收盘"),
}


def focus_backtest(records: Iterable[dict], params: dict) -> dict:
    include_shadow = bool(params.get("include_shadow", False))
    entry = str(params.get("entry", "trigger"))
    exit_mode = str(params.get("exit", "m5"))
    min_intraday_score = float(params.get("min_intraday_score", 0) or 0)
    min_review_score = float(params.get("min_review_score", 0) or 0)
    min_score = float(params.get("min_score", 0) or 0)
    limit = int(params.get("limit", 1000) or 1000)

    rows = []
    for row in records:
        if row.get("shadow") and not include_shadow:
            continue
        if float(row.get("intraday_score") or 0) < min_intraday_score:
            continue
        if float(row.get("review_score") or 0) < min_review_score:
            continue
        if float(row.get("score") or 0) < min_score:
            continue
        trade = _trade_from_row(row, entry, exit_mode)
        if trade:
            rows.append(trade)

    rows = sorted(rows, key=lambda item: (item["trigger_date"], item["return_pct"]), reverse=True)[:limit]
    return {
        "params": {
            "entry": entry,
            "exit": exit_mode,
            "entry_label": "触发价" if entry == "trigger" else "次日开盘",
            "exit_label": _exit_label(exit_mode),
            "include_shadow": include_shadow,
            "min_intraday_score": min_intraday_score,
            "min_review_score": min_review_score,
            "min_score": min_score,
        },
        "summary": _summary(rows),
        "by_sector": _group_summary(rows, "sector"),
        "by_strategy": _group_summary(rows, "strategy_version"),
        "trades": rows,
    }


def _trade_from_row(row: dict, entry: str, exit_mode: str) -> dict | None:
    if entry == "next_open":
        if not row.get("next_day_date"):
            return None
        entry_pct = _num(row.get("gap_pct"))
    else:
        entry_pct = 0.0

    raw_exit = _raw_exit_return(row, exit_mode)
    if raw_exit is None:
        return None
    return_pct = round(raw_exit - entry_pct, 2)
    return {
        "trigger_date": row.get("trigger_date", ""),
        "code": row.get("code", ""),
        "name": row.get("name", row.get("code", "")),
        "sector": row.get("sector", "未分组"),
        "strategy_version": row.get("strategy_version") or "focus-v1",
        "shadow": bool(row.get("shadow")),
        "entry": entry,
        "exit": exit_mode,
        "entry_label": "触发价" if entry == "trigger" else "次日开盘",
        "exit_label": _exit_label(exit_mode),
        "return_pct": return_pct,
        "raw_exit_return_pct": raw_exit,
        "score": round(_num(row.get("score")), 2),
        "intraday_score": round(_num(row.get("intraday_score")), 1),
        "review_score": round(_num(row.get("review_score")), 1),
        "intraday_label": row.get("intraday_label", ""),
        "review_label": row.get("review_label", ""),
        "next_day_date": row.get("next_day_date", ""),
    }


def _raw_exit_return(row: dict, exit_mode: str) -> float | None:
    if exit_mode in INTRADAY_EXITS:
        value = row.get(INTRADAY_EXITS[exit_mode][0])
        if value in ("", None):
            return None
        return _num(value)
    if exit_mode in NEXT_DAY_EXITS:
        if not row.get("next_day_date"):
            return None
        return _num(row.get(NEXT_DAY_EXITS[exit_mode][0]))
    return None


def _exit_label(exit_mode: str) -> str:
    if exit_mode in INTRADAY_EXITS:
        return INTRADAY_EXITS[exit_mode][1]
    if exit_mode in NEXT_DAY_EXITS:
        return NEXT_DAY_EXITS[exit_mode][1]
    return exit_mode


def _summary(rows: list[dict]) -> dict:
    if not rows:
        return {
            "sample_count": 0,
            "win_rate": 0,
            "avg_return_pct": 0,
            "best_return_pct": 0,
            "worst_return_pct": 0,
            "profit_factor": 0,
            "expectancy_pct": 0,
        }
    returns = [float(row["return_pct"]) for row in rows]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "sample_count": len(rows),
        "win_rate": round(len(wins) / len(rows) * 100, 1),
        "avg_return_pct": round(sum(returns) / len(returns), 2),
        "best_return_pct": round(max(returns), 2),
        "worst_return_pct": round(min(returns), 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2),
        "expectancy_pct": round(sum(returns) / len(returns), 2),
    }


def _group_summary(rows: list[dict], key: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "--")].append(row)
    result = []
    for name, items in grouped.items():
        summary = _summary(items)
        summary[key] = name
        result.append(summary)
    return sorted(result, key=lambda item: (item["sample_count"], item["avg_return_pct"]), reverse=True)[:20]


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
