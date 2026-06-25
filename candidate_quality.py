from __future__ import annotations

from collections import Counter
from datetime import datetime

from historical_backtest import fetch_eastmoney_limit_up_pool
from market_clock import ashare_session

STRATEGY_VERSION = "focus-v1"
LIMIT_POOL_CACHE: dict[str, object] = {"date": "", "ts": 0.0, "rows": [], "error": ""}

STRATEGIES = {
    "focus-v1": {
        "label": "强关注",
        "shadow": False,
        "min_rise_speed_pct": 1,
        "min_min2_amount": 10_000_000,
        "min_active_buy_ratio": 0.46,
        "sector_pulse_count": 3,
        "risk_max_rise_speed_pct": 4,
        "risk_min_active_buy_ratio": 0.45,
        "risk_max_turnover_rate": 14,
        "risk_max_change_pct": 7,
        "buy_bonus_ratio": 0.58,
        "volume_bonus_amount": 20_000_000,
    },
    "focus-v2-shadow": {
        "label": "强关注V2",
        "shadow": True,
        "min_rise_speed_pct": 1.2,
        "min_min2_amount": 20_000_000,
        "min_active_buy_ratio": 0.52,
        "sector_pulse_count": 3,
        "risk_max_rise_speed_pct": 3.2,
        "risk_min_active_buy_ratio": 0.48,
        "risk_max_turnover_rate": 12,
        "risk_max_change_pct": 6,
        "buy_bonus_ratio": 0.6,
        "volume_bonus_amount": 30_000_000,
    },
}


def candidate_score(item: dict) -> float:
    if item.get("candidate_score") is not None:
        return float(item.get("candidate_score") or 0)
    return (
        float(item.get("rise_speed_pct", 0)) * 35
        + min(float(item.get("min2_amount", 0)) / 1_000_000, 30)
        + float(item.get("vol_rise_speed_pct", 0)) * 2
        + float(item.get("short_turnover_pct", 0)) * 1.5
        + max(float(item.get("active_buy_ratio", 0)) - 0.5, 0) * 20
    )


def enrich_candidates(payload: dict) -> dict:
    candidates = payload.get("candidates", [])
    sector_heat = payload.get("sector_heat") or payload.get("health", {}).get("sector_heat") or []
    sector_counts = {item.get("sector"): int(item.get("count", 0)) for item in sector_heat if isinstance(item, dict)}
    limit_pool = current_limit_pool()
    annotate_limit_status(candidates, limit_pool.get("rows", []))
    hot_money = hot_money_context(candidates, sector_heat)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        item.update(hot_money_profile(item, hot_money))
    annotate_leader_ranks(candidates)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        item.update(hot_money_profile(item, hot_money))
        primary = candidate_quality(item, sector_counts, STRATEGY_VERSION)
        shadows = [
            candidate_quality(item, sector_counts, version)
            for version, rule in STRATEGIES.items()
            if rule.get("shadow")
        ]
        item.update(primary)
        item["shadow_strategies"] = shadows
    payload["hot_money"] = hot_money
    payload["leader_pool"] = leader_pool_summary(candidates, limit_pool)
    payload["strategy_funnel"] = strategy_funnel(candidates)
    return payload


def current_limit_pool() -> dict:
    session = ashare_session()
    trade_date = str(session.get("date") or datetime.now().strftime("%Y-%m-%d"))
    now_ts = datetime.now().timestamp()
    if LIMIT_POOL_CACHE.get("date") == trade_date and now_ts - float(LIMIT_POOL_CACHE.get("ts") or 0) < 60:
        return {
            "date": trade_date,
            "rows": LIMIT_POOL_CACHE.get("rows") or [],
            "error": LIMIT_POOL_CACHE.get("error") or "",
        }
    try:
        rows = fetch_eastmoney_limit_up_pool(trade_date)
        LIMIT_POOL_CACHE.update({"date": trade_date, "ts": now_ts, "rows": rows, "error": ""})
        return {"date": trade_date, "rows": rows, "error": ""}
    except Exception as error:
        LIMIT_POOL_CACHE.update({"date": trade_date, "ts": now_ts, "rows": [], "error": f"{error.__class__.__name__}: {error}"})
        return {"date": trade_date, "rows": [], "error": LIMIT_POOL_CACHE["error"]}


def annotate_limit_status(candidates: list[dict], limit_rows: list[dict]) -> None:
    limit_by_code = {str(item.get("code") or ""): item for item in limit_rows if isinstance(item, dict)}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        pool_row = limit_by_code.get(code)
        threshold = limit_threshold(code)
        change = float(item.get("change_pct") or 0)
        price = float(item.get("price") or 0)
        prev_close = float(item.get("prev_close") or 0)
        estimated_limit = prev_close > 0 and price >= prev_close * (1 + threshold / 100) * 0.997
        is_limit_up = bool(pool_row) or estimated_limit or change >= threshold - 0.25
        streak = int((pool_row or {}).get("consecutive_limit_count") or (pool_row or {}).get("limit_count") or (1 if is_limit_up else 0))
        item["limit_up"] = is_limit_up
        item["limit_up_streak"] = streak
        item["limit_up_threshold_pct"] = threshold
        item["first_limit_time"] = (pool_row or {}).get("first_limit_time") or ""
        item["last_limit_time"] = (pool_row or {}).get("last_limit_time") or ""
        item["limit_up_amount"] = float((pool_row or {}).get("amount") or 0)
        item["seal_amount"] = float((pool_row or {}).get("seal_amount") or 0)
        item["open_board_count"] = int((pool_row or {}).get("open_board_count") or 0)
        item["distance_to_limit_pct"] = max(0.0, threshold - change)


def annotate_leader_ranks(candidates: list[dict]) -> None:
    valid = [item for item in candidates if isinstance(item, dict)]
    for item in valid:
        item["leader_score"] = round(leader_score(item), 2)
    ranked = sorted(valid, key=lambda item: (int(item.get("limit_up_streak") or 0), float(item.get("leader_score") or 0)), reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["market_height_rank"] = index
    sectors = {str(item.get("sector") or "未分组") for item in valid}
    for sector in sectors:
        sector_ranked = [item for item in ranked if str(item.get("sector") or "未分组") == sector]
        for index, item in enumerate(sector_ranked, start=1):
            item["theme_leader_rank"] = index
    for item in valid:
        item["leader_role"] = leader_role(item)


def leader_score(item: dict) -> float:
    streak = int(item.get("limit_up_streak") or 0)
    limit_bonus = 180 if item.get("limit_up") else 0
    first_limit = str(item.get("first_limit_time") or "")
    early_bonus = 35 if first_limit and first_limit <= "10:00" else 18 if first_limit else 0
    theme_rank = int(item.get("theme_rank") or 99)
    theme_bonus = max(0, 8 - theme_rank) * 18
    amount = min(float(item.get("min2_amount") or 0) / 1_000_000, 80)
    active_buy = float(item.get("active_buy_ratio") or 0) * 55
    score = float(item.get("candidate_score") or candidate_score(item)) * 0.75
    return streak * 120 + limit_bonus + early_bonus + theme_bonus + amount + active_buy + score


def leader_role(item: dict) -> str:
    market_rank = int(item.get("market_height_rank") or 99)
    theme_rank = int(item.get("theme_leader_rank") or 99)
    streak = int(item.get("limit_up_streak") or 0)
    amount = float(item.get("min2_amount") or 0)
    if market_rank == 1 and (streak >= 2 or item.get("limit_up")):
        return "市场总龙头"
    if theme_rank == 1 and (streak >= 1 or item.get("limit_up") or int(item.get("theme_rank") or 99) <= 3):
        return "题材龙头"
    if amount >= 80_000_000 and float(item.get("change_pct") or 0) <= 6:
        return "容量中军"
    if int(item.get("theme_rank") or 99) <= 6 and float(item.get("leader_score") or 0) >= 120:
        return "前排助攻"
    if int(item.get("theme_rank") or 99) <= 6:
        return "补涨候选"
    return "后排跟风"


def leader_pool_summary(candidates: list[dict], limit_pool: dict) -> dict:
    valid = [item for item in candidates if isinstance(item, dict)]
    limit_rows = [item for item in limit_pool.get("rows", []) if isinstance(item, dict)]
    limit_leaders = sorted(
        limit_rows,
        key=lambda item: (
            int(item.get("consecutive_limit_count") or item.get("limit_count") or 0),
            -time_to_rank(item.get("first_limit_time", "")),
            float(item.get("seal_amount") or 0),
        ),
        reverse=True,
    )
    market_height = max([int(item.get("consecutive_limit_count") or item.get("limit_count") or 0) for item in limit_rows] or [0])
    limit_themes = limit_theme_summary(limit_rows)
    emotion = emotion_strategy(limit_rows, limit_themes, market_height)
    return {
        "date": limit_pool.get("date", ""),
        "limit_pool_count": len(limit_rows),
        "limit_pool_error": limit_pool.get("error", ""),
        "market_height": market_height,
        "emotion": emotion,
        "height_leaders": [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "sector": item.get("sector", "未分组"),
                "limit_up_streak": int(item.get("consecutive_limit_count") or item.get("limit_count") or 0),
                "first_limit_time": item.get("first_limit_time", ""),
                "last_limit_time": item.get("last_limit_time", ""),
                "open_board_count": item.get("open_board_count", 0),
                "seal_amount": item.get("seal_amount", 0),
            }
            for item in limit_leaders
        ],
        "limit_themes": limit_themes,
        "leaders": [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "sector": item.get("sector"),
                "leader_role": item.get("leader_role"),
                "limit_up_streak": item.get("limit_up_streak"),
                "leader_score": item.get("leader_score"),
            }
            for item in sorted(valid, key=lambda row: float(row.get("leader_score") or 0), reverse=True)[:8]
        ],
    }


def emotion_strategy(limit_rows: list[dict], themes: list[dict], market_height: int) -> dict:
    limit_count = len(limit_rows)
    open_board_count = sum(int(item.get("open_board_count") or 0) for item in limit_rows)
    fail_rate = open_board_count / max(limit_count + open_board_count, 1)
    high_count = sum(1 for item in limit_rows if int(item.get("consecutive_limit_count") or item.get("limit_count") or 0) >= 3)
    second_count = sum(1 for item in limit_rows if int(item.get("consecutive_limit_count") or item.get("limit_count") or 0) >= 2)
    early_count = sum(1 for item in limit_rows if str(item.get("first_limit_time") or "") <= "10:00")
    main_theme = themes[0] if themes else {}
    mainline = int(main_theme.get("limit_count") or 0)
    raw_score = (
        min(limit_count, 120) * 0.45
        + market_height * 10
        + high_count * 8
        + second_count * 3
        + min(mainline, 12) * 4
        + early_count * 0.6
        - fail_rate * 55
    )
    score = max(0, min(100, raw_score))
    if fail_rate >= 0.62:
        score = min(score, 35)
    elif fail_rate >= 0.5:
        score = min(score, 55)
    if market_height >= 5 and limit_count >= 70 and fail_rate <= 0.35:
        cycle = "主升"
        action = "可出手"
        position = "3成"
        mode = "龙头分歧低吸 / 前排打板"
    elif market_height >= 4 and limit_count >= 45 and fail_rate <= 0.45:
        cycle = "回暖"
        action = "只做前排"
        position = "2成"
        mode = "题材龙头 / 容量中军"
    elif limit_count >= 35 and fail_rate <= 0.55:
        cycle = "混沌"
        action = "轻仓试错"
        position = "1成"
        mode = "只做主线确认"
    elif fail_rate >= 0.62 or market_height <= 2:
        cycle = "退潮"
        action = "控仓"
        position = "0-1成"
        mode = "不追高，等冰点修复"
    else:
        cycle = "分歧"
        action = "谨慎"
        position = "1成"
        mode = "等龙头分歧承接"
    if score < 28:
        cycle = "冰点"
        action = "空仓观察"
        position = "0成"
        mode = "等新主线"
    return {
        "cycle": cycle,
        "score": round(score, 1),
        "action": action,
        "position": position,
        "mode": mode,
        "fail_rate": round(fail_rate * 100, 1),
        "open_board_count": open_board_count,
        "high_count": high_count,
        "second_count": second_count,
        "early_count": early_count,
        "mainline": main_theme.get("sector", ""),
        "reason": f"{limit_count}只涨停，{market_height}板高度，炸板率{fail_rate * 100:.1f}%",
    }


def limit_theme_summary(limit_rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for item in limit_rows:
        sector = str(item.get("sector") or "未分组")
        groups.setdefault(sector, []).append(item)
    rows = []
    for sector, members in groups.items():
        leaders = sorted(members, key=lambda item: (int(item.get("consecutive_limit_count") or item.get("limit_count") or 0), -time_to_rank(item.get("first_limit_time", ""))), reverse=True)
        max_height = max([int(item.get("consecutive_limit_count") or item.get("limit_count") or 0) for item in members] or [0])
        early_count = sum(1 for item in members if str(item.get("first_limit_time") or "") <= "10:00")
        open_boards = sum(int(item.get("open_board_count") or 0) for item in members)
        seal_amount = sum(float(item.get("seal_amount") or 0) for item in members)
        score = len(members) * 18 + max_height * 35 + early_count * 8 + min(seal_amount / 100_000_000, 30) - open_boards * 2
        rows.append(
            {
                "sector": sector,
                "limit_count": len(members),
                "max_height": max_height,
                "early_count": early_count,
                "open_board_count": open_boards,
                "seal_amount": round(seal_amount, 2),
                "score": round(score, 2),
                "risk_flags": limit_theme_risks(len(members), max_height, early_count, open_boards, seal_amount),
                "leader": {
                    "code": leaders[0].get("code"),
                    "name": leaders[0].get("name"),
                    "limit_up_streak": int(leaders[0].get("consecutive_limit_count") or leaders[0].get("limit_count") or 0),
                    "first_limit_time": leaders[0].get("first_limit_time", ""),
                }
                if leaders
                else None,
                "stocks": [
                    {
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "limit_up_streak": int(item.get("consecutive_limit_count") or item.get("limit_count") or 0),
                        "first_limit_time": item.get("first_limit_time", ""),
                        "last_limit_time": item.get("last_limit_time", ""),
                        "open_board_count": int(item.get("open_board_count") or 0),
                        "seal_amount": float(item.get("seal_amount") or 0),
                        "role": limit_stock_role(item, leaders, len(members)),
                    }
                    for item in leaders
                ],
            }
        )
    return sorted(rows, key=lambda item: (item["score"], item["max_height"]), reverse=True)[:12]


def limit_theme_risks(limit_count: int, max_height: int, early_count: int, open_boards: int, seal_amount: float) -> list[str]:
    risks = []
    if limit_count <= 1 and max_height >= 3:
        risks.append("孤立高度")
    if open_boards >= max(4, limit_count):
        risks.append("分歧大")
    if max_height <= 1 and limit_count >= 6:
        risks.append("首板潮")
    if seal_amount < 80_000_000 and max_height >= 2:
        risks.append("封单偏弱")
    if early_count == 0 and limit_count >= 3:
        risks.append("启动偏晚")
    return risks[:3]


def limit_stock_role(item: dict, leaders: list[dict], theme_count: int) -> str:
    streak = int(item.get("consecutive_limit_count") or item.get("limit_count") or 0)
    first_time = str(item.get("first_limit_time") or "")
    open_boards = int(item.get("open_board_count") or 0)
    if leaders and item.get("code") == leaders[0].get("code"):
        return "龙头"
    if streak >= 2:
        return "前排"
    if first_time and first_time <= "10:00" and open_boards <= 1:
        return "助攻"
    if theme_count >= 5:
        return "后排"
    return "补涨"


def time_to_rank(value: object) -> int:
    text = str(value or "")
    if len(text) < 5:
        return 9999
    try:
        return int(text[:2]) * 60 + int(text[-2:])
    except ValueError:
        return 9999


def limit_threshold(code: str) -> float:
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("4", "8", "9")):
        return 30.0
    return 10.0


def hot_money_context(candidates: list[dict], sector_heat: list[dict]) -> dict:
    valid = [item for item in candidates if isinstance(item, dict)]
    strong_count = sum(1 for item in valid if float(item.get("candidate_score") or candidate_score(item)) >= 100)
    high_count = sum(1 for item in valid if float(item.get("change_pct", 0)) >= 5)
    active_top = sum(int(item.get("active_top", 0)) for item in sector_heat if isinstance(item, dict))
    avg_speed = _avg([float(item.get("rise_speed_pct", 0)) for item in valid])
    avg_amount = _avg([float(item.get("min2_amount", 0)) for item in valid])
    heat_score = min(100, strong_count * 5 + high_count * 3 + active_top * 8 + avg_speed * 8 + avg_amount / 3_000_000)
    if heat_score >= 72:
        mood = "主升"
    elif heat_score >= 52:
        mood = "回暖"
    elif heat_score >= 32:
        mood = "混沌"
    else:
        mood = "退潮"

    sector_rank: dict[str, dict] = {}
    for rank, row in enumerate(sector_heat, start=1):
        if not isinstance(row, dict):
            continue
        sector = str(row.get("sector") or "未分组")
        score = (
            int(row.get("count") or 0) * 18
            + int(row.get("active_top") or 0) * 22
            + float(row.get("max_score") or 0) * 0.28
            + float(row.get("avg_score") or 0) * 0.16
            + min(float(row.get("min2_amount") or 0) / 10_000_000, 30)
        )
        sector_rank[sector] = {
            "rank": rank,
            "score": round(min(100, score), 1),
            "count": int(row.get("count") or 0),
            "active_top": int(row.get("active_top") or 0),
        }
    return {
        "mood": mood,
        "emotion_score": round(heat_score, 1),
        "strong_count": strong_count,
        "high_count": high_count,
        "active_top": active_top,
        "sector_rank": sector_rank,
    }


def hot_money_profile(item: dict, context: dict) -> dict:
    sector = str(item.get("sector") or "未分组")
    sector_info = (context.get("sector_rank") or {}).get(sector, {})
    theme_rank = int(sector_info.get("rank") or 99)
    theme_score = float(sector_info.get("score") or 0)
    score = float(item.get("candidate_score") or candidate_score(item))
    change = float(item.get("change_pct", 0))
    amount = float(item.get("min2_amount", 0))
    active_buy = float(item.get("active_buy_ratio", 0))
    top_status = item.get("top_status")
    leader = item.get("leader_role")
    streak = int(item.get("limit_up_streak") or 0)
    tags = []
    if theme_rank <= 3 and theme_score >= 60:
        tags.append("主线题材")
    elif theme_rank <= 6:
        tags.append("支线活跃")
    else:
        tags.append("非主线")
    if leader == "市场总龙头":
        role = "精盯龙头"
        tags.extend(["市场高度", f"{streak}板" if streak else "涨停池"])
    elif leader == "题材龙头":
        role = "题材前排"
        tags.append("题材龙头")
    elif leader == "容量中军":
        role = "趋势中军"
        tags.append("容量中军")
    elif top_status == "active":
        role = "题材前排" if theme_rank <= 3 else "补涨候选"
        tags.append("资金精盯")
    elif theme_rank <= 3 and score >= 120 and amount >= 20_000_000:
        role = "题材前排"
        tags.append("前排强度")
    elif amount >= 80_000_000 and change <= 5.5:
        role = "趋势中军"
        tags.append("容量中军")
    elif theme_rank <= 6 and score >= 90:
        role = "补涨候选"
        tags.append("补涨弹性")
    else:
        role = "后排跟风"
        tags.append("地位不足")
    if context.get("mood") in {"退潮", "混沌"} and change >= 5:
        tags.append("情绪风险")
    if active_buy >= 0.58:
        tags.append("主买强")
    return {
        "market_mood": context.get("mood", "混沌"),
        "emotion_score": context.get("emotion_score", 0),
        "theme_rank": theme_rank,
        "theme_score": round(theme_score, 1),
        "hot_money_role": role,
        "buy_pattern": buy_pattern(item, role, context.get("mood", "混沌"), theme_rank),
        "hot_money_tags": tags[:5],
    }


def buy_pattern(item: dict, role: str, mood: str, theme_rank: int) -> str:
    change = float(item.get("change_pct", 0))
    speed = float(item.get("rise_speed_pct", 0))
    amount = float(item.get("min2_amount", 0))
    active_buy = float(item.get("active_buy_ratio", 0))
    streak = int(item.get("limit_up_streak") or 0)
    if item.get("leader_role") == "市场总龙头" and streak >= 2:
        return "龙头分歧低吸"
    if item.get("leader_role") == "题材龙头" and item.get("limit_up"):
        return "题材龙头打板"
    if role == "后排跟风":
        return "后排不参与"
    if mood in {"退潮", "混沌"} and change >= 5:
        return "情绪风险不追"
    if role == "精盯龙头" and speed >= 1.2 and amount >= 20_000_000:
        return "龙头分歧转一致"
    if role == "题材前排" and theme_rank <= 3 and active_buy >= 0.52:
        return "前排弱转强"
    if role == "趋势中军" and amount >= 80_000_000 and change <= 5.5:
        return "趋势中军低吸"
    if role == "补涨候选":
        return "补涨套利"
    if speed >= 1 and amount >= 10_000_000:
        return "启动观察"
    return "等待买点"


def candidate_quality(item: dict, sector_counts: dict[str, int], version: str = STRATEGY_VERSION) -> dict:
    rule = STRATEGIES[version]
    flags = risk_flags(item, rule)
    has_volume = float(item.get("min2_amount", 0)) >= rule["min_min2_amount"]
    has_buy = float(item.get("active_buy_ratio", 0)) >= rule["min_active_buy_ratio"]
    has_start = float(item.get("rise_speed_pct", 0)) >= rule["min_rise_speed_pct"]
    pulse_count = sector_counts.get(item.get("sector"), 0)
    has_pulse = pulse_count >= rule["sector_pulse_count"]
    level = "watch"
    label = "观察"
    if flags:
        level = "caution"
        label = "谨慎"
    elif has_start and has_volume and (has_pulse or has_buy):
        level = "strong"
        label = rule["label"]

    adjusted_score = candidate_score(item)
    adjusted_score += 6 if has_pulse else 0
    adjusted_score += 4 if float(item.get("active_buy_ratio", 0)) >= rule["buy_bonus_ratio"] else 0
    adjusted_score += 3 if float(item.get("min2_amount", 0)) >= rule["volume_bonus_amount"] else 0
    adjusted_score += max(0, 7 - int(item.get("theme_rank") or 99)) * 1.5
    adjusted_score += 8 if item.get("hot_money_role") in {"精盯龙头", "题材前排", "趋势中军"} else 0
    adjusted_score -= 8 if item.get("hot_money_role") == "后排跟风" else 0
    adjusted_score -= len(flags) * 10

    return {
        "strategy_version": version,
        "quality_rule": rule_snapshot(rule),
        "quality_level": level,
        "quality_label": label,
        "risk_flags": flags,
        "miss_reasons": miss_reasons(item, rule, flags, has_start, has_volume, has_buy, has_pulse),
        "adjusted_score": round(adjusted_score, 2),
        "explanation": explanation(item, sector_counts, flags, level, pulse_count, rule),
        "shadow": bool(rule.get("shadow")),
    }


def rule_snapshot(rule: dict) -> dict:
    return {key: value for key, value in rule.items() if key not in {"label", "shadow"}}


def strategy_funnel(candidates: list[dict]) -> list[dict]:
    rows = []
    for version in STRATEGIES:
        qualities = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("strategy_version") == version:
                qualities.append(item)
                continue
            for shadow in item.get("shadow_strategies") or []:
                if shadow.get("strategy_version") == version:
                    qualities.append(shadow)
                    break
        total = len(qualities)
        strong = sum(1 for item in qualities if item.get("quality_level") == "strong")
        watch = sum(1 for item in qualities if item.get("quality_level") == "watch")
        caution = sum(1 for item in qualities if item.get("quality_level") == "caution")
        miss_counter = Counter(reason for item in qualities for reason in item.get("miss_reasons", []))
        risk_counter = Counter(reason for item in qualities for reason in item.get("risk_flags", []))
        rows.append(
            {
                "strategy_version": version,
                "label": STRATEGIES[version]["label"],
                "shadow": bool(STRATEGIES[version].get("shadow")),
                "total": total,
                "strong": strong,
                "watch": watch,
                "caution": caution,
                "miss_reasons": dict(miss_counter),
                "risk_reasons": dict(risk_counter),
                "top_miss_reason": miss_counter.most_common(1)[0][0] if miss_counter else "",
            }
        )
    return rows


def miss_reasons(item: dict, rule: dict, flags: list[str], has_start: bool, has_volume: bool, has_buy: bool, has_pulse: bool) -> list[str]:
    if not flags and has_start and has_volume and (has_pulse or has_buy):
        return []
    reasons = []
    if not has_start:
        reasons.append("涨速不足")
    if not has_volume:
        reasons.append("2分钟成交额不足")
    if not has_buy:
        reasons.append("主买不足")
    if not has_pulse:
        reasons.append("无板块共振")
    reasons.extend(flags)
    return list(dict.fromkeys(reasons))


def risk_flags(item: dict, rule: dict) -> list[str]:
    flags = []
    if item.get("market_mood") in {"退潮", "混沌"} and float(item.get("change_pct", 0)) >= 5:
        flags.append("情绪退潮追高")
    if item.get("hot_money_role") == "后排跟风" and float(item.get("change_pct", 0)) >= 4:
        flags.append("后排跟风")
    if float(item.get("rise_speed_pct", 0)) >= rule["risk_max_rise_speed_pct"]:
        flags.append("瞬时尖峰")
    if float(item.get("active_buy_ratio", 0)) < rule["risk_min_active_buy_ratio"]:
        flags.append("主买不足")
    if float(item.get("turnover_rate", 0)) >= rule["risk_max_turnover_rate"]:
        flags.append("换手偏高")
    if float(item.get("change_pct", 0)) >= rule["risk_max_change_pct"]:
        flags.append("位置偏高")
    return flags


def explanation(item: dict, sector_counts: dict[str, int], flags: list[str], level: str, pulse_count: int, rule: dict) -> str:
    positives = []
    sector = item.get("sector", "")
    role = item.get("hot_money_role")
    if role in {"精盯龙头", "题材前排", "趋势中军"}:
        positives.append(role)
    if pulse_count >= rule["sector_pulse_count"]:
        positives.append(f"{sector}{pulse_count}只共振")
    if float(item.get("rise_speed_pct", 0)) >= rule["min_rise_speed_pct"]:
        positives.append("涨速启动")
    if float(item.get("min2_amount", 0)) >= rule["min_min2_amount"]:
        positives.append("2分钟放量")
    if float(item.get("active_buy_ratio", 0)) >= rule["buy_bonus_ratio"]:
        positives.append("主买占优")
    change_pct = float(item.get("change_pct", 0))
    if 1 <= change_pct <= 5:
        positives.append("涨幅适中")
    if float(item.get("turnover_rate", 0)) <= 10:
        positives.append("换手可控")

    lead = "谨慎" if flags else "板块共振" if pulse_count >= rule["sector_pulse_count"] else rule["label"] if level == "strong" else "观察"
    body = " + ".join(positives[:4]) or "等待量价确认"
    caution = f"；{'、'.join(flags[:2])}" if flags else ""
    return f"{lead}：{body}{caution}"


def _avg(values: list[float]) -> float:
    clean = [value for value in values if value == value]
    return sum(clean) / len(clean) if clean else 0.0
