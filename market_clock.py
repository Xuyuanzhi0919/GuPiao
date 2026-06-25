from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
CALENDAR_PATH = Path(__file__).parent / "data" / "trading_calendar.json"


def ashare_session(now: datetime | None = None) -> dict:
    current = now.astimezone(CN_TZ) if now else datetime.now(CN_TZ)
    calendar = _load_calendar()
    date_key = current.strftime("%Y-%m-%d")
    weekday = current.weekday()
    clock = current.time()

    if date_key in calendar.get("special_open_dates", []):
        return _intraday_session(current, clock)
    if date_key in calendar.get("closed_dates", []):
        return _payload(current, "CLOSED", "交易日历休市", False)
    if weekday >= 5:
        return _payload(current, "CLOSED", "非交易日", False)
    return _intraday_session(current, clock)


def is_trading_date(value: str | date) -> bool:
    current = date.fromisoformat(value) if isinstance(value, str) else value
    calendar = _load_calendar()
    date_key = current.strftime("%Y-%m-%d")
    if date_key in calendar.get("special_open_dates", []):
        return True
    if date_key in calendar.get("closed_dates", []):
        return False
    return current.weekday() < 5


def next_trading_date(value: str | date) -> str:
    current = date.fromisoformat(value) if isinstance(value, str) else value
    probe = current + timedelta(days=1)
    for _ in range(370):
        if is_trading_date(probe):
            return probe.strftime("%Y-%m-%d")
        probe += timedelta(days=1)
    raise RuntimeError("无法在 370 天内找到下一个交易日")


def _intraday_session(current: datetime, clock: time) -> dict:
    if time(9, 15) <= clock < time(9, 25):
        return _payload(current, "CALL_AUCTION", "集合竞价", True)
    if time(9, 25) <= clock < time(9, 30):
        return _payload(current, "PRE_OPEN", "竞价撮合", True)
    if time(9, 30) <= clock < time(11, 30):
        return _payload(current, "MORNING", "上午连续竞价", True)
    if time(11, 30) <= clock < time(13, 0):
        return _payload(current, "LUNCH", "午间休市", False)
    if time(13, 0) <= clock < time(14, 57):
        return _payload(current, "AFTERNOON", "下午连续竞价", True)
    if time(14, 57) <= clock < time(15, 0):
        return _payload(current, "CLOSING_AUCTION", "收盘集合竞价", True)
    if time(15, 0) <= clock < time(15, 30):
        return _payload(current, "POST_CLOSE", "盘后整理", False)
    if clock < time(9, 15):
        return _payload(current, "PRE_MARKET", "盘前", False)
    return _payload(current, "CLOSED", "收盘", False)


def _load_calendar() -> dict:
    if not CALENDAR_PATH.exists():
        return {"closed_dates": [], "special_open_dates": []}
    with CALENDAR_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _payload(current: datetime, code: str, label: str, is_live: bool) -> dict:
    return {
        "code": code,
        "label": label,
        "is_live": is_live,
        "time": current.strftime("%H:%M:%S"),
        "date": current.strftime("%Y-%m-%d"),
    }
