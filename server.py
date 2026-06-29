from __future__ import annotations

import asyncio
import csv
import json
import mimetypes
import os
import signal
import threading
import time
import uuid
from datetime import datetime, time as dt_time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
from typing import Any

from market_data import create_market_data_source
from market_quotes import fetch_market_quotes
from market_clock import CN_TZ, ashare_session, next_trading_date
from monitor import IntradayMonitor
from focus_store import FocusStore
from candidate_quality import enrich_candidates
from config_change_store import ConfigChangeStore
from runtime_config import load_monitor_config, reset_monitor_config, update_monitor_config
from sectors import add_sector_code, load_sectors, remove_sector_code
from signal_store import SignalStore, TrackStore
from stock_search import lookup_stocks, search_stocks
from universe import add_code, remove_code, universe_payload
from backtest import focus_backtest
from historical_backtest import rapid_rise_history_backtest, rapid_rise_multi_date_backtest
from notifications import NotificationCenter
from position_store import PositionStore
from trade_records import TradeRecordStore
from trade_marks import TradeMarkStore
from user_preferences import UserPreferenceStore
from limit_up_monitor import LimitUpMonitor
from db_store import DatabaseStore

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8787"))
RECENT_ALERTS = DATA / "recent_alerts.json"
ALERT_KEEP_SEC = int(os.environ.get("ALERT_KEEP_SEC", "600"))
LIMIT_UP_FOCUS_STATE = DATA / "limit_up_focus_state.json"
LIMIT_UP_BUY_SIGNAL_STATE = DATA / "limit_up_buy_signal_state.json"
LIMIT_UP_POSITION_SIGNAL_STATE = DATA / "limit_up_position_signal_state.json"
LIMIT_UP_NOTIFICATION_LEDGER = DATA / "limit_up_notification_ledger.json"
LIMIT_UP_SYSTEM_REVIEW_HISTORY = DATA / "limit_up_system_review_history.json"
LIMIT_UP_SYSTEM_CAPITAL = 100000
LIMIT_UP_SYSTEM_FEE_RATE = 0.00025
LIMIT_UP_SYSTEM_SLIPPAGE_RATE = 0.001
LIMIT_UP_MONITOR_INTERVAL_SEC = float(os.environ.get("LIMIT_UP_MONITOR_INTERVAL_SEC", "5"))
LIMIT_UP_TICK_INTERVAL_SEC = float(os.environ.get("LIMIT_UP_TICK_INTERVAL_SEC", "1"))


class AppState:
    def __init__(self) -> None:
        load_monitor_config()
        self.monitor = IntradayMonitor()
        self.data_source = create_market_data_source()
        self.store = SignalStore(DATA / "signals.jsonl")
        self.track_store = TrackStore(DATA / "tracks.jsonl")
        self.focus_store = FocusStore(DATA / "focus_next_day.json")
        self.config_changes = ConfigChangeStore(DATA / "config_changes.jsonl")
        self.notifications = NotificationCenter(DATA / "notifications.json")
        self.preferences = UserPreferenceStore(DATA / "user_preferences.json")
        self.trade_marks = TradeMarkStore(DATA / "trade_marks.json")
        self.positions = PositionStore(DATA / "positions.json")
        self.trade_records = TradeRecordStore(DATA / "trade_records.json")
        self.limit_up_monitor = LimitUpMonitor(DATA)
        self.db_store = DatabaseStore.from_env()
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.limit_up_clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.limit_up_monitor_lock = asyncio.Lock()
        self.limit_up_notification_lock = asyncio.Lock()
        self.last_limit_up_tick_ts = 0.0
        self.last_limit_up_publish_ts = 0.0
        self.limit_up_publish_count = 0
        self.limit_up_ws_drop_count = 0
        self.started_at = time.time()
        self.last_batch_ts = 0.0
        self.batch_count = 0
        self.tick_count = 0
        self.source_name = self.data_source.__class__.__name__
        self.error_count = 0
        self.retry_count = 0
        self.last_error = ""
        self.last_error_ts = 0.0
        self.backtest_jobs: dict[str, dict[str, Any]] = {}
        self.backtest_job_lock = threading.Lock()

    async def publish(self, payload: dict[str, Any]) -> None:
        stale: list[asyncio.Queue] = []
        for queue in self.clients:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.clients.discard(queue)

    async def publish_limit_up(self, payload: dict[str, Any]) -> None:
        stale: list[asyncio.Queue] = []
        self.last_limit_up_publish_ts = time.time()
        self.limit_up_publish_count += 1
        for queue in self.limit_up_clients:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.limit_up_clients.discard(queue)
            self.limit_up_ws_drop_count += 1

    def mark_batch(self, tick_count: int) -> None:
        self.last_batch_ts = time.time()
        self.batch_count += 1
        self.tick_count += tick_count

    def mark_error(self, error: Exception) -> None:
        self.error_count += 1
        self.retry_count += 1
        self.last_error = f"{error.__class__.__name__}: {error}"
        self.last_error_ts = time.time()

    def runtime_status(self) -> dict[str, Any]:
        now = time.time()
        data_age = now - self.last_batch_ts if self.last_batch_ts else None
        recent_error = bool(self.last_error_ts and now - self.last_error_ts < 30)
        session = ashare_session()
        stale = data_age is None or (session["is_live"] and data_age > 5)
        status = "ERROR" if recent_error else "STALE" if stale else "OK"
        return {
            "status": status,
            "session": session,
            "source": self.source_name,
            "uptime_sec": int(now - self.started_at),
            "last_batch_ts": self.last_batch_ts,
            "data_age_sec": round(data_age, 2) if data_age is not None else None,
            "batch_count": self.batch_count,
            "tick_count": self.tick_count,
            "client_count": len(self.clients),
            "error_count": self.error_count,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "last_error_ts": self.last_error_ts,
            "bad_row_count": getattr(self.data_source, "bad_row_count", 0),
            "last_bad_row_error": getattr(self.data_source, "last_bad_row_error", ""),
            "upstream_health": getattr(self.data_source, "upstream_health", {}),
            "limit_up_stream": self.limit_up_stream_status(),
        }

    def limit_up_stream_status(self) -> dict[str, Any]:
        now = time.time()
        publish_age = now - self.last_limit_up_publish_ts if self.last_limit_up_publish_ts else None
        tick_age = now - self.last_limit_up_tick_ts if self.last_limit_up_tick_ts else None
        live = bool(publish_age is not None and publish_age <= max(8, LIMIT_UP_MONITOR_INTERVAL_SEC * 2))
        return {
            "status": "OK" if live else "STALE",
            "last_publish_ts": self.last_limit_up_publish_ts,
            "last_tick_ts": self.last_limit_up_tick_ts,
            "publish_age_sec": round(publish_age, 2) if publish_age is not None else None,
            "tick_age_sec": round(tick_age, 2) if tick_age is not None else None,
            "publish_count": self.limit_up_publish_count,
            "client_count": len(self.limit_up_clients),
            "drop_count": self.limit_up_ws_drop_count,
            "interval_sec": LIMIT_UP_TICK_INTERVAL_SEC,
        }


STATE = AppState()


async def market_loop() -> None:
    while True:
        try:
            async for ticks in STATE.data_source.stream():
                STATE.mark_batch(len(ticks))
                STATE.focus_store.update_ticks(ticks)
                signals = STATE.monitor.update(ticks)
                STATE.store.append(signals)
                watchlist = STATE.preferences.watchlist()
                for signal_item in signals:
                    STATE.notifications.notify_signal(signal_item)
                    STATE.notifications.notify_watchlist_signal(signal_item, watchlist)
                export_recent_alerts(signals)
                STATE.track_store.append(STATE.monitor.tracked_export_rows())
                payload = STATE.monitor.snapshot()
                payload["event"] = "market"
                payload["new_signals"] = [signal.to_dict() for signal in signals]
                payload["runtime"] = STATE.runtime_status()
                await STATE.publish(payload)
                await maybe_monitor_next_day_buy_signals(tick_driven=True)
                await maybe_monitor_position_risk_signals(tick_driven=True)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            STATE.mark_error(error)
            payload = snapshot_payload()
            payload["event"] = "market"
            payload["new_signals"] = []
            await STATE.publish(payload)
            await asyncio.sleep(min(10, 1 + STATE.retry_count))


async def limit_up_focus_loop() -> None:
    while True:
        try:
            await maybe_generate_tomorrow_focus()
            await maybe_monitor_next_day_buy_signals()
            await maybe_monitor_position_risk_signals()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            STATE.mark_error(error)
        await asyncio.sleep(max(2, LIMIT_UP_MONITOR_INTERVAL_SEC))


async def maybe_generate_tomorrow_focus() -> None:
    current = datetime.now(CN_TZ)
    session = ashare_session(current)
    if current.time() < dt_time(15, 0):
        return
    date_key = str(session.get("date") or current.strftime("%Y-%m-%d"))
    sent = _load_json_dict(LIMIT_UP_FOCUS_STATE)
    focus_key = f"focus:{date_key}"
    openclaw_key = f"openclaw:{date_key}"
    if openclaw_key in sent:
        return
    if focus_key not in sent:
        payload = await asyncio.to_thread(STATE.limit_up_monitor.build_tomorrow_focus, date_key, True)
        sent[focus_key] = {"ts": time.time(), "sent": False, "channel": "state", "error": ""}
        LIMIT_UP_FOCUS_STATE.write_text(json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = await asyncio.to_thread(STATE.limit_up_monitor.review_tomorrow_focus_with_openclaw, date_key, 120, 600)
    notification = STATE.notifications.notify_limit_up_focus_report(payload)
    if STATE.db_store:
        await asyncio.to_thread(STATE.db_store.save_limit_up_focus, payload)
    sent[openclaw_key] = {"ts": time.time(), "sent": notification.sent, "channel": notification.channel, "error": notification.error}
    LIMIT_UP_FOCUS_STATE.write_text(json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8")


async def maybe_monitor_next_day_buy_signals(tick_driven: bool = False) -> dict[str, Any] | None:
    current = datetime.now(CN_TZ)
    session = ashare_session(current)
    if session.get("code") not in {"CALL_AUCTION", "PRE_OPEN", "MORNING", "AFTERNOON", "CLOSING_AUCTION"}:
        return None
    if tick_driven:
        now = time.time()
        if now - STATE.last_limit_up_tick_ts < max(0.2, LIMIT_UP_TICK_INTERVAL_SEC):
            return None
        if STATE.limit_up_monitor_lock.locked():
            return None
        STATE.last_limit_up_tick_ts = now
    async with STATE.limit_up_monitor_lock:
        payload = await asyncio.to_thread(STATE.limit_up_monitor.monitor_yesterday_pool, str(session.get("date") or ""), True)
    if STATE.db_store:
        await asyncio.to_thread(STATE.db_store.save_next_day_monitor, payload)
    async with STATE.limit_up_notification_lock:
        sent = _load_json_dict(LIMIT_UP_BUY_SIGNAL_STATE)
        def notification_keys(item: dict[str, Any]) -> tuple[str, str, str]:
            stage = _next_day_notification_stage(item)
            primary = f"{payload.get('date')}:{item.get('code')}:{stage}"
            old_primary = f"{payload.get('date')}:{item.get('code')}"
            legacy = f"{payload.get('date')}:{item.get('code')}:{item.get('state')}"
            return primary, old_primary, legacy

        def seal_key(item: dict[str, Any]) -> str:
            return f"{payload.get('date')}:{item.get('code')}:seal"

        pending_notifications = [
            (notification_keys(item)[0], item)
            for item in payload.get("buy_signals", [])[:10]
            if _notification_due(sent.get(notification_keys(item)[0]))
            and (
                _next_day_notification_stage(item) != "entry"
                or (
                    seal_key(item) not in sent
                    and notification_keys(item)[1] not in sent
                    and notification_keys(item)[2] not in sent
                )
            )
        ]
        await _deliver_notifications(
            sent,
            "next-day-buy",
            pending_notifications,
            lambda item: STATE.notifications.notify_next_day_buy_signal(item),
            lambda item: {"state": item.get("state"), "rank": item.get("official_rank"), "stage": _next_day_notification_stage(item)},
        )
        cancel_candidates = [
            item for item in payload.get("rows", [])
            if item.get("official_buy") and _official_buy_should_cancel(item)
        ]
        sent_entry_codes = _sent_entry_codes(sent, str(payload.get("date") or ""))
        for item in payload.get("rows", []):
            code = str(item.get("code") or "")
            if (
                code in sent_entry_codes
                and not item.get("official_buy")
                and str(item.get("execution_status") or "") not in {"filled", "missed", "abandoned"}
                and _released_buy_should_cancel(item)
            ):
                cancel_candidates.append(item)
        cancel_pending = []
        for item in cancel_candidates:
            t1_locked = str(item.get("execution_status") or "") == "filled"
            key = f"{payload.get('date')}:{item.get('code')}:{'t1-risk' if t1_locked else 'cancel'}"
            if _notification_due(sent.get(key)):
                cancel_pending.append((key, item, t1_locked))
        await _deliver_notifications(
            sent,
            "next-day-t1-or-cancel",
            cancel_pending,
            lambda item, t1_locked=False: STATE.notifications.notify_next_day_cancel_signal(item, t1_locked),
            lambda item, t1_locked=False: {"state": item.get("state"), "t1_locked": t1_locked},
        )
        LIMIT_UP_BUY_SIGNAL_STATE.write_text(json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["event"] = "limit-up"
    payload["runtime"] = STATE.runtime_status()
    payload["tick_driven"] = tick_driven
    payload["permission"] = _limit_up_trade_permission(payload)
    payload["notification_reliability"] = _notification_reliability_payload()
    await STATE.publish_limit_up(payload)
    return payload


def _next_day_notification_stage(item: dict[str, Any]) -> str:
    state = str(item.get("state") or "")
    if state in {"首封确认", "回封确认"} or item.get("sealed_today"):
        return "seal"
    if item.get("kline_signal") == "weak" or state in {"风险观察", "放弃"}:
        return "risk"
    return "entry"


def _official_buy_should_cancel(item: dict[str, Any]) -> bool:
    execution = str(item.get("execution_status") or "")
    if execution in {"missed", "abandoned"}:
        return False
    if item.get("buy_unavailable") or str(item.get("state") or "") in {"买不到", "放弃", "风险观察"}:
        return True
    if item.get("kline_signal") == "weak" and not item.get("sealed_today"):
        return True
    if _number(item.get("close_from_open_pct")) <= -2.5 and not item.get("sealed_today"):
        return True
    if _number(item.get("score")) < 34 and not item.get("sealed_today"):
        return True
    return False


def _released_buy_should_cancel(item: dict[str, Any]) -> bool:
    if item.get("sealed_today"):
        return False
    state = str(item.get("state") or "")
    if state in {"观察承接", "放弃", "风险观察", "买不到"}:
        return True
    if _number(item.get("close_from_open_pct")) <= -0.8:
        return True
    if _number(item.get("score")) < 58:
        return True
    if str(item.get("kline_signal") or "") == "weak":
        return True
    return False


def _sent_entry_codes(state: dict[str, Any], trade_date: str) -> set[str]:
    codes: set[str] = set()
    prefix = f"{trade_date}:"
    for key, record in state.items():
        if not str(key).startswith(prefix) or not str(key).endswith(":entry"):
            continue
        if isinstance(record, dict) and (record.get("sent") or record.get("channel") in {"record", "disabled"}):
            code = str(record.get("code") or str(key).split(":")[1])
            if code:
                codes.add(code)
    return codes


async def _deliver_notifications(
    state: dict[str, Any],
    kind: str,
    pending: list[tuple[Any, ...]],
    sender: Any,
    metadata_builder: Any | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    ledger = _load_json_dict(LIMIT_UP_NOTIFICATION_LEDGER)
    for entry in pending:
        key = str(entry[0])
        args = entry[1:]
        item = args[0] if args and isinstance(args[0], dict) else {}
        started = time.time()
        previous = state.get(key) if isinstance(state.get(key), dict) else {}
        attempts = int(_number(previous.get("attempts"))) + 1
        try:
            notification = await asyncio.to_thread(sender, *args)
            record = {
                "key": key,
                "kind": kind,
                "code": str(item.get("code") or ""),
                "name": str(item.get("name") or ""),
                "title": notification.title,
                "ts": started,
                "first_ts": previous.get("first_ts") or started,
                "last_attempt_ts": started,
                "attempts": attempts,
                "sent": bool(notification.sent),
                "channel": notification.channel,
                "error": notification.error,
                "elapsed_ms": float(notification.elapsed_ms or round((time.time() - started) * 1000, 1)),
                "target": notification.target,
                "metadata": metadata_builder(*args) if metadata_builder else {},
            }
        except Exception as error:  # noqa: BLE001 - failed push must be recorded and retried later
            record = {
                "key": key,
                "kind": kind,
                "code": str(item.get("code") or ""),
                "name": str(item.get("name") or ""),
                "title": str(item.get("action") or kind),
                "ts": started,
                "first_ts": previous.get("first_ts") or started,
                "last_attempt_ts": started,
                "attempts": attempts,
                "sent": False,
                "channel": "error",
                "error": f"{error.__class__.__name__}: {error}",
                "elapsed_ms": round((time.time() - started) * 1000, 1),
                "target": "",
                "metadata": metadata_builder(*args) if metadata_builder else {},
            }
        state[key] = record
        ledger[key] = record
        results.append((item, record))
    if pending:
        _write_notification_ledger(ledger)
    return results


def _notification_due(record: Any) -> bool:
    if not isinstance(record, dict):
        return True
    if record.get("sent") or record.get("channel") in {"disabled", "record"}:
        return False
    notification_status = STATE.notifications.status()
    retry_sec = int(_number(notification_status.get("failed_retry_sec")) or 10)
    if record.get("channel") == "cooldown":
        retry_sec = int(_number(notification_status.get("cooldown_sec")) or retry_sec)
    last_attempt = _number(record.get("last_attempt_ts") or record.get("ts"))
    return time.time() - last_attempt >= retry_sec


def _write_notification_ledger(ledger: dict[str, Any]) -> None:
    rows = sorted((row for row in ledger.values() if isinstance(row, dict)), key=lambda item: _number(item.get("last_attempt_ts") or item.get("ts")), reverse=True)[:1000]
    payload = {str(row.get("key") or index): row for index, row in enumerate(rows)}
    LIMIT_UP_NOTIFICATION_LEDGER.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _notification_reliability_payload() -> dict[str, Any]:
    ledger = _load_json_dict(LIMIT_UP_NOTIFICATION_LEDGER)
    rows = [row for row in ledger.values() if isinstance(row, dict)]
    recent = sorted(rows, key=lambda item: _number(item.get("last_attempt_ts") or item.get("ts")), reverse=True)[:100]
    sent = len([row for row in recent if row.get("sent") or row.get("channel") in {"disabled", "record"}])
    failed = len([row for row in recent if not row.get("sent") and row.get("channel") not in {"disabled", "record"}])
    elapsed = [_number(row.get("elapsed_ms")) for row in recent if _number(row.get("elapsed_ms")) > 0]
    pending_retry = len([row for row in rows if _notification_due(row)])
    return {
        "sample_count": len(recent),
        "success_count": sent,
        "failure_count": failed,
        "success_rate": round(sent / len(recent) * 100, 1) if recent else 0,
        "avg_elapsed_ms": round(sum(elapsed) / len(elapsed), 1) if elapsed else 0,
        "pending_retry_count": pending_retry,
        "last_error": next((str(row.get("error") or "") for row in recent if row.get("error")), ""),
        "recent": recent[:20],
    }


def _limit_up_open_plan(open_pct: float, pnl_pct: float) -> dict[str, str]:
    if open_pct >= 2:
        return {"rule": "hold-high-open-support", "text": f"高开{open_pct:.2f}%，先看承接，站上开盘价和分时均价则继续持仓。"}
    if open_pct <= -2:
        return {"rule": "reduce-weak-open", "text": f"低开{open_pct:.2f}%，若不能快速站回开盘价，优先减仓/清仓。"}
    if pnl_pct <= -2:
        return {"rule": "reduce-near-cost-stop", "text": f"接近成本风控，若开盘后继续走弱，不恋战。"}
    return {"rule": "watch-open-support", "text": "平开附近，观察前15分钟承接，跌破开盘价或分时均价减仓。"}


async def maybe_monitor_position_risk_signals(tick_driven: bool = False) -> list[dict[str, Any]]:
    current = datetime.now(CN_TZ)
    session = ashare_session(current)
    if session.get("code") not in {"CALL_AUCTION", "PRE_OPEN", "MORNING", "AFTERNOON", "CLOSING_AUCTION"}:
        return []
    if tick_driven and STATE.limit_up_monitor_lock.locked():
        return []
    positions = [
        item
        for item in STATE.positions.payload().get("positions", [])
        if _is_stock_position(item)
    ]
    if not positions:
        return []
    codes = [str(item.get("code") or "") for item in positions]
    try:
        quote_payload = await asyncio.to_thread(fetch_market_quotes, codes)
    except Exception:
        return []
    quotes = quote_payload.get("quotes") or {}
    alerts = _build_position_risk_alerts(positions, quotes, str(session.get("code") or ""), str(session.get("date") or current.strftime("%Y-%m-%d")))
    if not alerts:
        return []

    state = _load_json_dict(LIMIT_UP_POSITION_SIGNAL_STATE)
    sent: list[dict[str, Any]] = []
    date_key = str(session.get("date") or current.strftime("%Y-%m-%d"))
    pending: list[tuple[str, dict[str, Any]]] = []
    for alert in alerts:
        key = f"{date_key}:{alert.get('code')}:{alert.get('kind')}"
        if _notification_due(state.get(key)):
            pending.append((key, alert))
    results = await _deliver_notifications(
        state,
        "position-risk",
        pending,
        lambda item: STATE.notifications.notify_position_risk(item),
        lambda item: {"kind": item.get("kind"), "action": item.get("action"), "sell_rule": item.get("sell_rule")},
    )
    sent = [item for item, result in results if result.get("sent") or result.get("channel") in {"disabled", "record"}]
    LIMIT_UP_POSITION_SIGNAL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return sent


def _is_stock_position(item: dict[str, Any]) -> bool:
    code = str(item.get("code") or "")
    name = str(item.get("name") or "").upper()
    sector = str(item.get("sector") or "").upper()
    if "ETF" in name or "ETF" in sector or code.startswith(("51", "56", "58")):
        return False
    return code.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))


def _build_position_risk_alerts(positions: list[dict[str, Any]], quotes: dict[str, Any], phase: str, trade_date: str = "") -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for position in positions:
        code = str(position.get("code") or "")
        quote = quotes.get(code) or {}
        buy_price = _number(position.get("buy_price"))
        current_price = _number(quote.get("price")) or buy_price
        open_price = _number(quote.get("open"))
        high_price = _number(quote.get("high")) or current_price
        low_price = _number(quote.get("low")) or current_price
        prev_close = _number(quote.get("prev_close"))
        amount = _number(quote.get("amount"))
        volume = _number(quote.get("volume"))
        intraday_avg = amount / volume / 100 if amount > 0 and volume > 0 else 0
        if not code or buy_price <= 0 or current_price <= 0:
            continue
        pnl_pct = (current_price / buy_price - 1) * 100
        open_pct = (open_price / prev_close - 1) * 100 if open_price and prev_close else 0
        stop_price = round(buy_price * 0.97, 3)
        source = str(position.get("source") or "manual")
        is_limit_up = source == "limit-up"
        is_t0_locked = is_limit_up and str(position.get("buy_date") or "") == trade_date
        name = str(position.get("name") or quote.get("name") or code)
        base = {
            "code": code,
            "name": name,
            "price": round(current_price, 3),
            "buy_price": round(buy_price, 3),
            "shares": position.get("shares"),
            "pnl_pct": round(pnl_pct, 2),
            "source": source,
            "buy_date": position.get("buy_date") or "",
            "t1_locked": is_t0_locked,
        }
        if is_t0_locked and phase in {"MORNING", "AFTERNOON", "CLOSING_AUCTION"}:
            if current_price < open_price * 0.98 or pnl_pct <= -3 or (high_price and current_price < high_price * 0.98):
                alerts.append({
                    **base,
                    "kind": "t1-risk",
                    "action": "T+1持仓风险",
                    "sell_rule": "t1-locked-watch",
                    "reason": f"今日新买入不可卖，现较成本{pnl_pct:.2f}%；只记录风险，明日按开盘承接处理。",
                })
            continue
        if is_limit_up and phase in {"CALL_AUCTION", "PRE_OPEN"}:
            plan = _limit_up_open_plan(open_pct, pnl_pct)
            alerts.append({
                **base,
                "kind": "open-plan",
                "action": "持仓开盘计划",
                "sell_rule": plan["rule"],
                "reason": f"{plan['text']} 成本{buy_price:.2f}，止损{stop_price:.2f}；明日卖点以开盘承接和分时均价为准。",
            })
            continue
        hard_stop_pct = -3 if is_limit_up else -5
        hard_stop_price = round(buy_price * (1 + hard_stop_pct / 100), 3)
        if current_price <= hard_stop_price or pnl_pct <= hard_stop_pct:
            alerts.append({
                **base,
                "kind": "hard-stop",
                "action": "清仓风控",
                "sell_rule": "clear-hard-stop",
                "reason": f"现价较成本{pnl_pct:.2f}%，触发{hard_stop_pct}%止损线{hard_stop_price:.2f}。",
            })
            continue
        if is_limit_up and intraday_avg and current_price < intraday_avg * 0.995 and current_price < open_price:
            alerts.append({
                **base,
                "kind": "break-vwap",
                "action": "跌破分时均线",
                "sell_rule": "reduce-below-vwap",
                "reason": f"现价{current_price:.2f}跌破分时均价{intraday_avg:.2f}且低于开盘价，按纪律减仓/清仓。",
            })
            continue
        if is_limit_up and open_price and current_price < open_price * 0.98 and low_price < open_price * 0.985:
            alerts.append({
                **base,
                "kind": "break-open",
                "action": "持仓减仓",
                "sell_rule": "reduce-below-open",
                "reason": f"跌破开盘价2%，开盘{open_price:.2f}，现价{current_price:.2f}，优先保护本金。",
            })
            continue
        high_profit_pct = (high_price / buy_price - 1) * 100 if high_price else pnl_pct
        drawdown_from_high = (current_price / high_price - 1) * 100 if high_price else 0
        if high_profit_pct >= (5 if is_limit_up else 8) and drawdown_from_high <= (-2 if is_limit_up else -4):
            alerts.append({
                **base,
                "kind": "profit-protect",
                "action": "冲高回落减仓",
                "sell_rule": "reduce-profit-drawdown",
                "reason": f"盘中最高盈利{high_profit_pct:.2f}%，现从高点回落{abs(drawdown_from_high):.2f}%，先锁利润。",
            })
            continue
        if is_limit_up and prev_close and high_price >= prev_close * 1.095 and current_price < prev_close * 1.085:
            alerts.append({
                **base,
                "kind": "open-board-fade",
                "action": "炸板回落",
                "sell_rule": "reduce-open-board-fade",
                "reason": f"盘中触及涨停附近后回落，现价{current_price:.2f}，未能回封先减仓。",
            })
            continue
        if is_limit_up and open_pct >= 2 and current_price >= open_price and pnl_pct > -1:
            if phase in {"MORNING", "AFTERNOON"} and not (intraday_avg and current_price < intraday_avg * 0.995):
                alerts.append({
                    **base,
                    "kind": "high-open-hold",
                    "action": "高开承接持有",
                    "sell_rule": "hold-while-above-open-vwap",
                    "reason": f"高开{open_pct:.2f}%且站上开盘价，先持有；跌破开盘价或分时均价再减。",
                })
                continue
        if pnl_pct >= (8 if is_limit_up else 12):
            alerts.append({
                **base,
                "kind": "profit-watch",
                "action": "持仓止盈观察",
                "sell_rule": "watch-profit-trail",
                "reason": f"当前盈利{pnl_pct:.2f}%，若明显回落先减仓。",
            })
        elif is_limit_up and open_pct <= -2 and phase in {"MORNING", "AFTERNOON"}:
            alerts.append({
                **base,
                "kind": "weak-open",
                "action": "低开弱承接",
                "sell_rule": "reduce-weak-open",
                "reason": f"低开{open_pct:.2f}%，若不能快速站回开盘价，按持仓减仓处理。",
            })
    return alerts


def limit_up_system_review_payload(date_key: str = "") -> dict[str, Any]:
    records = _load_limit_up_system_review_records()
    if not records:
        payload = {
            "date": date_key,
            "capital": LIMIT_UP_SYSTEM_CAPITAL,
            "max_positions": None,
            "selected": None,
            "history": [],
            "stats": _system_review_stats([]),
            "failure_attribution": [],
            "positions": [],
            "trades": [],
            "rules": _system_review_rules(None, []),
            "dates": [],
        }
        LIMIT_UP_SYSTEM_REVIEW_HISTORY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
    selected = next((item for item in records if str(item.get("date") or "") == date_key), records[-1])
    stats = _system_review_stats(records)
    payload = {
        "date": selected.get("date"),
        "capital": LIMIT_UP_SYSTEM_CAPITAL,
        "max_positions": None,
        "selected": selected,
        "history": records,
        "stats": stats,
        "failure_attribution": _system_failure_attribution(records),
        "positions": selected.get("ending_positions") or [],
        "trades": selected.get("trades") or [],
        "rules": _system_review_rules(selected, records),
        "dates": [record.get("date") for record in records],
    }
    LIMIT_UP_SYSTEM_REVIEW_HISTORY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _load_limit_up_system_review_records() -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for path in sorted(DATA.glob("limit_up_next_day_review_*.json")):
        if path.name.endswith("_latest.json"):
            continue
        payload = _read_json_file(path)
        if payload:
            record = _build_raw_system_day(payload)
            if record:
                by_date[str(record.get("date"))] = record
    state_payload = _read_json_file(DATA / "limit_up_next_day_state.json")
    if state_payload:
        record = _build_raw_system_day(state_payload)
        if record:
            by_date[str(record.get("date"))] = record
    return _simulate_limit_up_account([by_date[key] for key in sorted(by_date)])


def _build_raw_system_day(payload: dict[str, Any]) -> dict[str, Any] | None:
    rows = payload.get("rows") or payload.get("top_rows") or []
    if not isinstance(rows, list):
        return None
    date_key = str(payload.get("date") or "")
    rows_by_code = {str(row.get("code") or ""): row for row in rows if isinstance(row, dict)}
    official_rows = _official_rows_for_date(date_key, rows_by_code)
    if not official_rows:
        official_rows = [
            row for row in rows
            if isinstance(row, dict) and (row.get("official_buy") or _number(row.get("official_rank")) > 0)
        ]
    official_rows.sort(key=lambda item: _number(item.get("official_rank")) or 99)
    return {
        "date": date_key,
        "source_date": payload.get("source_date") or "",
        "ts": payload.get("ts") or time.time(),
        "rows": rows,
        "rows_by_code": rows_by_code,
        "official_rows": official_rows,
        "summary": payload.get("summary") or {},
    }


def _official_rows_for_date(date_key: str, rows_by_code: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not date_key:
        return []
    path = DATA / f"limit_up_official_buys_{date_key}.json"
    payload = _read_json_file(path)
    codes = [str(code) for code in payload.get("codes") or [] if str(code)]
    items = {str(item.get("code") or ""): item for item in payload.get("items") or [] if isinstance(item, dict)}
    official_rows: list[dict[str, Any]] = []
    for index, code in enumerate(codes, start=1):
        base = dict(rows_by_code.get(code) or {})
        item = items.get(code) or {}
        base.update({key: value for key, value in item.items() if value not in (None, "")})
        base["code"] = code
        base["official_buy"] = True
        base["official_rank"] = int(_number(base.get("official_rank")) or index)
        base["official_entry_price"] = _number(base.get("official_entry_price") or base.get("entry_price") or base.get("trigger_price") or base.get("price") or base.get("open"))
        base["official_trigger_price"] = _number(base.get("official_trigger_price") or base.get("trigger_price") or base.get("official_entry_price"))
        official_rows.append(base)
    return official_rows


def _simulate_limit_up_account(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cash = float(LIMIT_UP_SYSTEM_CAPITAL)
    peak = float(LIMIT_UP_SYSTEM_CAPITAL)
    positions: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    previous_equity = float(LIMIT_UP_SYSTEM_CAPITAL)
    for day in days:
        rows_by_code = day.get("rows_by_code") or {}
        trades: list[dict[str, Any]] = []
        review_rows: list[dict[str, Any]] = []
        start_equity = previous_equity
        updated_positions: list[dict[str, Any]] = []

        for position in positions:
            row = rows_by_code.get(str(position.get("code") or "")) or {}
            reviewed = _review_existing_system_position(position, row, str(day.get("date") or ""))
            review_rows.append(reviewed)
            if reviewed.get("exit"):
                cash += _number(reviewed.get("exit_amount")) - _number(reviewed.get("fee"))
                trades.append(_system_trade("sell", reviewed, day, reviewed.get("action") or "卖出"))
            else:
                updated_positions.append(_position_from_review_row(reviewed))
        positions = updated_positions

        buy_rows = []
        held_codes = {str(item.get("code") or "") for item in positions}
        for row in day.get("official_rows") or []:
            code = str(row.get("code") or "")
            if not code or code in held_codes:
                continue
            buy_rows.append(row)

        for index, row in enumerate(buy_rows):
            remaining_slots = max(1, len(buy_rows) - index)
            allocation = cash / remaining_slots
            bought = _build_system_buy_row(row, allocation, str(day.get("date") or ""))
            review_rows.append(bought)
            if bought.get("shares", 0) > 0:
                cash -= _number(bought.get("invested_amount")) + _number(bought.get("fee"))
                positions.append(_position_from_review_row(bought))
                trades.append(_system_trade("buy", bought, day, bought.get("action") or "买入"))

        ending_positions = [_mark_to_market_position(item, rows_by_code) for item in positions]
        market_value = sum(_number(item.get("market_value")) for item in ending_positions)
        equity = cash + market_value
        pnl_amount = equity - start_equity
        peak = max(peak, equity)
        active_rows = [row for row in review_rows if row.get("trade_action") in {"buy", "hold", "sell"}]
        seal_count = len([row for row in active_rows if row.get("sealed_today")])
        hold_count = len([row for row in active_rows if row.get("position_status") == "持有中"])
        clear_count = len([row for row in active_rows if row.get("position_status") == "已剔除"])
        rebalance_count = len([row for row in active_rows if row.get("position_status") == "待调仓"])
        record = {
            "date": day.get("date") or "",
            "source_date": day.get("source_date") or "",
            "ts": day.get("ts") or time.time(),
            "capital": LIMIT_UP_SYSTEM_CAPITAL,
            "start_equity": round(start_equity, 2),
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "equity": round(equity, 2),
            "pnl_amount": round(pnl_amount, 2),
            "pnl_pct": round((pnl_amount / start_equity) * 100, 2) if start_equity > 0 else 0,
            "total_return_pct": round((equity / LIMIT_UP_SYSTEM_CAPITAL - 1) * 100, 2),
            "drawdown_pct": round((equity / peak - 1) * 100, 2) if peak > 0 else 0,
            "position_count": len(ending_positions),
            "invested_amount": round(sum(_number(row.get("invested_amount")) for row in review_rows if row.get("trade_action") != "sell"), 2),
            "best_pnl_pct": round(max([_number(row.get("pnl_pct")) for row in active_rows], default=0), 2),
            "worst_pnl_pct": round(min([_number(row.get("pnl_pct")) for row in active_rows], default=0), 2),
            "seal_count": seal_count,
            "seal_rate": round((seal_count / len(active_rows)) * 100, 2) if active_rows else 0,
            "hold_count": hold_count,
            "rebalance_count": rebalance_count,
            "clear_count": clear_count,
            "buy_count": len([trade for trade in trades if trade.get("side") == "buy"]),
            "sell_count": len([trade for trade in trades if trade.get("side") == "sell"]),
            "rows": sorted(review_rows, key=lambda item: (str(item.get("trade_action") or ""), _number(item.get("rank")) or 99)),
            "ending_positions": ending_positions,
            "trades": trades,
        }
        record["decision"] = _system_daily_decision(record)
        records.append(record)
        previous_equity = equity
    return records


def _build_system_buy_row(row: dict[str, Any], allocation: float, trade_date: str) -> dict[str, Any]:
    raw_entry = _number(row.get("official_entry_price") or row.get("entry_price") or row.get("official_trigger_price") or row.get("trigger_price") or row.get("open") or row.get("price"))
    entry_price = raw_entry * (1 + LIMIT_UP_SYSTEM_SLIPPAGE_RATE) if raw_entry > 0 else 0
    price = _number(row.get("price")) or raw_entry or entry_price
    execution_status = str(row.get("execution_status") or "simulated")
    unavailable = bool(row.get("buy_unavailable")) or execution_status in {"missed", "abandoned"}
    shares = 0 if unavailable else int(allocation / max(entry_price, 0.01) / 100) * 100 if entry_price > 0 else 0
    invested_amount = shares * entry_price
    fee = _trade_fee(invested_amount)
    pnl_amount = shares * (price - entry_price) - fee
    pnl_pct = (price / entry_price - 1) * 100 if entry_price > 0 else 0
    status, action = _system_position_status(row, pnl_pct)
    if shares <= 0:
        status, action = "未成交", "买不到/放弃" if unavailable else "资金不足"
    failure_reason = _system_failure_reason(row, pnl_pct)
    if unavailable:
        failure_reason = "买不到" if execution_status == "missed" or row.get("buy_unavailable") else "盘中放弃"
    if shares > 0 and status == "已剔除":
        status, action = "持有中", "T+1持有"
        failure_reason = failure_reason or "当日买入T+1不可卖"
    return {
        "code": row.get("code") or "",
        "name": row.get("name") or row.get("code") or "",
        "sector": row.get("sector") or "--",
        "rank": int(_number(row.get("official_rank")) or 0),
        "trade_date": trade_date,
        "trade_action": "buy",
        "planned_action": "正式买点",
        "actual_action": _system_actual_action(execution_status, shares),
        "execution_status": execution_status,
        "t1_status": "当日买入不可卖" if shares > 0 else "",
        "entry_price": round(entry_price, 3),
        "price": round(price, 3),
        "allocated_capital": round(allocation, 2),
        "shares": shares,
        "invested_amount": round(invested_amount, 2),
        "fee": round(fee, 2),
        "market_value": round(shares * price, 2),
        "pnl_amount": round(pnl_amount, 2),
        "pnl_pct": round(pnl_pct, 2),
        "change_pct": round(_number(row.get("change_pct")), 2),
        "from_open_pct": round(_number(row.get("close_from_open_pct")) or ((price / _number(row.get("open")) - 1) * 100 if _number(row.get("open")) else 0), 2),
        "sealed_today": bool(row.get("sealed_today")),
        "state": row.get("state") or "",
        "action": action,
        "position_status": status,
        "failure_reason": failure_reason,
    }


def _review_existing_system_position(position: dict[str, Any], row: dict[str, Any], trade_date: str) -> dict[str, Any]:
    entry_price = _number(position.get("entry_price"))
    shares = int(_number(position.get("shares")))
    price = _number(row.get("price")) or _number(position.get("price")) or entry_price
    invested_amount = _number(position.get("invested_amount")) or shares * entry_price
    pnl_amount = shares * (price - entry_price)
    pnl_pct = (price / entry_price - 1) * 100 if entry_price > 0 else 0
    if row:
        status, action = _system_position_status(row, pnl_pct)
        failure_reason = _system_failure_reason(row, pnl_pct)
    else:
        status, action = "持有中", "缺少行情观察"
        failure_reason = "缺少行情"
    exit_position = status in {"已剔除", "待调仓"} and bool(row)
    exit_amount = shares * price if exit_position else 0
    fee = _trade_fee(exit_amount) if exit_position else 0
    return {
        "code": position.get("code") or row.get("code") or "",
        "name": position.get("name") or row.get("name") or "",
        "sector": position.get("sector") or row.get("sector") or "--",
        "rank": int(_number(position.get("rank")) or _number(row.get("official_rank")) or 0),
        "trade_date": trade_date,
        "opened_at": position.get("opened_at") or position.get("trade_date") or trade_date,
        "trade_action": "sell" if exit_position else "hold",
        "planned_action": "次日持仓处理",
        "actual_action": action,
        "execution_status": "held",
        "t1_status": "可卖出",
        "entry_price": round(entry_price, 3),
        "price": round(price, 3),
        "allocated_capital": round(_number(position.get("allocated_capital")) or invested_amount, 2),
        "shares": shares,
        "invested_amount": round(invested_amount, 2),
        "fee": round(fee, 2),
        "exit": exit_position,
        "exit_amount": round(exit_amount, 2),
        "market_value": round(0 if exit_position else shares * price, 2),
        "pnl_amount": round(pnl_amount - fee, 2),
        "pnl_pct": round(pnl_pct, 2),
        "change_pct": round(_number(row.get("change_pct")), 2),
        "from_open_pct": round(_number(row.get("close_from_open_pct")) or ((price / _number(row.get("open")) - 1) * 100 if _number(row.get("open")) else 0), 2),
        "sealed_today": bool(row.get("sealed_today")),
        "state": row.get("state") or ("无当日行情" if not row else ""),
        "action": action,
        "position_status": status,
        "failure_reason": failure_reason,
    }


def _position_from_review_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": row.get("code") or "",
        "name": row.get("name") or "",
        "sector": row.get("sector") or "--",
        "rank": int(_number(row.get("rank")) or 0),
        "entry_price": _number(row.get("entry_price")),
        "price": _number(row.get("price")),
        "shares": int(_number(row.get("shares"))),
        "invested_amount": _number(row.get("invested_amount")),
        "allocated_capital": _number(row.get("allocated_capital")),
        "opened_at": row.get("opened_at") or row.get("trade_date"),
        "execution_status": row.get("execution_status") or "",
    }


def _mark_to_market_position(position: dict[str, Any], rows_by_code: dict[str, dict[str, Any]]) -> dict[str, Any]:
    row = rows_by_code.get(str(position.get("code") or "")) or {}
    price = _number(row.get("price")) or _number(position.get("price")) or _number(position.get("entry_price"))
    entry_price = _number(position.get("entry_price"))
    shares = int(_number(position.get("shares")))
    market_value = shares * price
    pnl_amount = shares * (price - entry_price)
    pnl_pct = (price / entry_price - 1) * 100 if entry_price > 0 else 0
    marked = dict(position)
    marked.update({
        "price": round(price, 3),
        "market_value": round(market_value, 2),
        "pnl_amount": round(pnl_amount, 2),
        "pnl_pct": round(pnl_pct, 2),
        "state": row.get("state") or marked.get("state") or "",
        "sealed_today": bool(row.get("sealed_today")),
    })
    return marked


def _system_trade(side: str, row: dict[str, Any], day: dict[str, Any], reason: str) -> dict[str, Any]:
    amount = _number(row.get("invested_amount")) if side == "buy" else _number(row.get("exit_amount"))
    return {
        "date": day.get("date") or "",
        "side": side,
        "code": row.get("code") or "",
        "name": row.get("name") or "",
        "price": row.get("entry_price") if side == "buy" else row.get("price"),
        "shares": int(_number(row.get("shares"))),
        "amount": round(amount, 2),
        "fee": round(_number(row.get("fee")), 2),
        "reason": reason,
        "execution_status": row.get("execution_status") or "",
    }


def _system_actual_action(execution_status: str, shares: int) -> str:
    if execution_status == "filled":
        return "实盘已成交"
    if execution_status == "missed":
        return "买不到未成交"
    if execution_status == "abandoned":
        return "盘中放弃"
    if shares > 0:
        return "系统模拟成交"
    return "未成交"


def _trade_fee(amount: float) -> float:
    if amount <= 0:
        return 0
    return max(1.0, amount * LIMIT_UP_SYSTEM_FEE_RATE)


def _system_daily_decision(record: dict[str, Any]) -> dict[str, Any]:
    pnl_pct = _number(record.get("pnl_pct"))
    drawdown = _number(record.get("drawdown_pct"))
    clear_count = int(_number(record.get("clear_count")))
    hold_count = int(_number(record.get("hold_count")))
    if pnl_pct <= -2 or clear_count >= 2:
        action, level = "明日收缩", "danger"
        reason = "亏损或剔除偏多，只保留封板确认。"
    elif pnl_pct >= 2 and hold_count:
        action, level = "明日进攻", "good"
        reason = "账户和持仓状态较强，继续围绕昨日涨停池。"
    elif drawdown <= -5:
        action, level = "暂停扩仓", "danger"
        reason = "账户回撤扩大，先降频观察。"
    else:
        action, level = "正常盯盘", "warn"
        reason = "只做强承接和回封确认，不追弱转强失败票。"
    return {"action": action, "level": level, "reason": reason}


def _system_review_rules(selected: dict[str, Any] | None, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not selected:
        return [
            {"title": "账户", "badge": "等待", "level": "warn", "detail": "暂无系统打板账本。"},
            {"title": "仓位", "badge": "0只", "level": "warn", "detail": "等待正式买点。"},
            {"title": "纪律", "badge": "空仓", "level": "good", "detail": "没有买点时不强行交易。"},
        ]
    loss_streak = _system_review_stats(records).get("loss_streak", 0)
    return [
        {
            "title": "账户",
            "badge": format_system_badge(_number(selected.get("pnl_pct")), "%"),
            "level": selected.get("decision", {}).get("level", "warn"),
            "detail": selected.get("decision", {}).get("reason", ""),
        },
        {
            "title": "仓位",
            "badge": f"{selected.get('position_count', 0)}只",
            "level": "good" if int(_number(selected.get("position_count"))) else "warn",
            "detail": f"买入{selected.get('buy_count', 0)}，卖出{selected.get('sell_count', 0)}，现金{round(_number(selected.get('cash')), 2)}。",
        },
        {
            "title": "连续亏损",
            "badge": str(loss_streak),
            "level": "danger" if loss_streak >= 2 else "good",
            "detail": "连续亏损达到2天则次日只做核心封板确认。",
        },
    ]


def format_system_badge(value: float, suffix: str = "") -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}{suffix}"


def _legacy_system_review_record(payload: dict[str, Any]) -> dict[str, Any] | None:
    rows = payload.get("rows") or payload.get("top_rows") or []
    if not isinstance(rows, list):
        return None
    official_rows = [
        row for row in rows
        if isinstance(row, dict) and (row.get("official_buy") or _number(row.get("official_rank")) > 0)
    ]
    official_rows.sort(key=lambda item: _number(item.get("official_rank")) or 99)
    allocation = LIMIT_UP_SYSTEM_CAPITAL / len(official_rows) if official_rows else 0
    review_rows = [_build_system_review_row(row, allocation) for row in official_rows]
    pnl_amount = sum(_number(row.get("pnl_amount")) for row in review_rows)
    invested_amount = sum(_number(row.get("invested_amount")) for row in review_rows)
    seal_count = len([row for row in review_rows if row.get("sealed_today")])
    hold_count = len([row for row in review_rows if row.get("position_status") == "持有中"])
    clear_count = len([row for row in review_rows if row.get("position_status") == "已剔除"])
    rebalance_count = len([row for row in review_rows if row.get("position_status") == "待调仓"])
    return {
        "date": payload.get("date") or "",
        "source_date": payload.get("source_date") or "",
        "ts": payload.get("ts") or time.time(),
        "capital": LIMIT_UP_SYSTEM_CAPITAL,
        "position_count": len(review_rows),
        "invested_amount": round(invested_amount, 2),
        "cash": round(LIMIT_UP_SYSTEM_CAPITAL - invested_amount, 2),
        "pnl_amount": round(pnl_amount, 2),
        "pnl_pct": round((pnl_amount / LIMIT_UP_SYSTEM_CAPITAL) * 100, 2),
        "best_pnl_pct": round(max([_number(row.get("pnl_pct")) for row in review_rows], default=0), 2),
        "worst_pnl_pct": round(min([_number(row.get("pnl_pct")) for row in review_rows], default=0), 2),
        "seal_count": seal_count,
        "seal_rate": round((seal_count / len(review_rows)) * 100, 2) if review_rows else 0,
        "hold_count": hold_count,
        "rebalance_count": rebalance_count,
        "clear_count": clear_count,
        "rows": review_rows,
    }


def _build_system_review_row(row: dict[str, Any], allocation: float) -> dict[str, Any]:
    entry_price = _number(row.get("official_entry_price") or row.get("official_trigger_price") or row.get("open") or row.get("price"))
    price = _number(row.get("price")) or entry_price
    shares = int(allocation / entry_price / 100) * 100 if entry_price > 0 else 0
    invested_amount = shares * entry_price
    pnl_amount = shares * (price - entry_price)
    pnl_pct = (price / entry_price - 1) * 100 if entry_price > 0 else 0
    if shares <= 0 and entry_price > 0:
        status, action = "已剔除", "资金不足"
        failure_reason = "资金不足"
    else:
        status, action = _system_position_status(row, pnl_pct)
        failure_reason = _system_failure_reason(row, pnl_pct)
    return {
        "code": row.get("code") or "",
        "name": row.get("name") or row.get("code") or "",
        "sector": row.get("sector") or "--",
        "rank": int(_number(row.get("official_rank")) or 0),
        "entry_price": round(entry_price, 3),
        "price": round(price, 3),
        "allocated_capital": round(allocation, 2),
        "shares": shares,
        "invested_amount": round(invested_amount, 2),
        "pnl_amount": round(pnl_amount, 2),
        "pnl_pct": round(pnl_pct, 2),
        "change_pct": round(_number(row.get("change_pct")), 2),
        "from_open_pct": round(_number(row.get("close_from_open_pct")) or ((price / _number(row.get("open")) - 1) * 100 if _number(row.get("open")) else 0), 2),
        "sealed_today": bool(row.get("sealed_today")),
        "state": row.get("state") or "",
        "action": action,
        "position_status": status,
        "failure_reason": failure_reason,
    }


def _system_position_status(row: dict[str, Any], pnl_pct: float) -> tuple[str, str]:
    if row.get("sealed_today") and pnl_pct >= 0:
        return "持有中", "继续持仓"
    if row.get("action") == "PASS" or str(row.get("state") or "") == "放弃" or pnl_pct <= -3:
        return "已剔除", "剔除/清仓"
    return "待调仓", "调仓观察"


def _system_failure_reason(row: dict[str, Any], pnl_pct: float) -> str:
    if row.get("sealed_today"):
        return ""
    if row.get("buy_unavailable") or str(row.get("state") or "") == "买不到":
        return "买不到"
    if row.get("kline_signal") == "weak":
        return "分时弱化"
    if _number(row.get("open_pct")) > 2 and _number(row.get("close_from_open_pct")) < -2:
        return "高开低走"
    if _number(row.get("today_open_board_count")) > 0:
        return "炸板回落"
    if pnl_pct <= -3:
        return "止损触发"
    if not row.get("sealed_today"):
        return "未封板"
    return "其他"


def _system_review_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    active_records = [record for record in records if int(_number(record.get("position_count"))) > 0]
    if not active_records:
        return {
            "trade_days": 0,
            "equity": LIMIT_UP_SYSTEM_CAPITAL,
            "total_pnl": 0,
            "total_return_pct": 0,
            "win_rate": 0,
            "max_drawdown_pct": 0,
            "loss_streak": 0,
        }
    equity = _number(records[-1].get("equity")) or LIMIT_UP_SYSTEM_CAPITAL
    max_drawdown = min((_number(record.get("drawdown_pct")) for record in active_records), default=0)
    win_days = len([record for record in active_records if _number(record.get("pnl_amount")) > 0])
    loss_streak = 0
    current_streak = 0
    for record in active_records:
        if _number(record.get("pnl_amount")) < 0:
            current_streak += 1
            loss_streak = max(loss_streak, current_streak)
        else:
            current_streak = 0
    return {
        "trade_days": len(active_records),
        "equity": round(equity, 2),
        "total_pnl": round(equity - LIMIT_UP_SYSTEM_CAPITAL, 2),
        "total_return_pct": round((equity / LIMIT_UP_SYSTEM_CAPITAL - 1) * 100, 2),
        "win_rate": round((win_days / len(active_records)) * 100, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "loss_streak": loss_streak,
    }


def _system_failure_attribution(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        for row in record.get("rows") or []:
            reason = str(row.get("failure_reason") or "")
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return [{"reason": key, "count": value} for key, value in sorted(counts.items(), key=lambda item: item[1], reverse=True)]


def _limit_up_trade_permission(payload: dict[str, Any]) -> dict[str, Any]:
    review = limit_up_system_review_payload(str(payload.get("date") or ""))
    stats = review.get("stats") or {}
    selected = review.get("selected") or {}
    loss_streak = int(_number(stats.get("loss_streak")))
    drawdown = _number(stats.get("max_drawdown_pct"))
    failed_today = len([row for row in payload.get("rows") or [] if row.get("official_buy") and _official_buy_should_cancel(row)])
    if loss_streak >= 2 or drawdown <= -5 or failed_today >= 2:
        status, label, level = "blocked", "今日停手", "danger"
        reason = "连续亏损/回撤/盘中失败触发总闸门，只观察不新增打板。"
    elif loss_streak == 1 or failed_today == 1:
        status, label, level = "reduced", "收缩出手", "warn"
        reason = "只做核心票封板确认，非核心分时买点不再追。"
    else:
        status, label, level = "normal", "正常出手", "good"
        reason = "按系统纪律执行：不限固定只数，只做强承接/封板确认。"
    return {
        "status": status,
        "label": label,
        "level": level,
        "reason": reason,
        "loss_streak": loss_streak,
        "max_drawdown_pct": drawdown,
        "failed_today": failed_today,
        "remaining_slots": None,
        "equity": selected.get("equity") or stats.get("equity") or LIMIT_UP_SYSTEM_CAPITAL,
    }


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        writer.close()
        await writer.wait_closed()
        return

    request_line = request.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
    parts = request_line.split()
    raw_path = parts[1] if len(parts) >= 2 else "/"
    parsed = urlparse(raw_path)
    path = parsed.path
    query = parse_qs(parsed.query)

    if path == "/events":
        await stream_events(writer)
        return
    if path == "/api/snapshot":
        await send_json(writer, snapshot_payload())
        return
    if path == "/api/report":
        report = STATE.monitor.report()
        if not report["tracked_alerts"]:
            report["tracked_alerts"] = STATE.track_store.latest(limit=100)[:10]
        await send_json(writer, {"report": report, "runtime": STATE.runtime_status()})
        return
    if path == "/api/candidates":
        await send_json(writer, await candidate_payload())
        return
    if path == "/api/health/full":
        await send_json(writer, await full_health_payload())
        return
    if path == "/api/notifications/recent":
        limit = int(query.get("limit", ["50"])[0])
        await send_json(writer, notification_payload(limit))
        return
    if path == "/api/notifications/config":
        values = {key: value[0] for key, value in query.items()}
        STATE.notifications.update_config(values)
        await send_json(writer, notification_payload())
        return
    if path == "/api/notifications/test":
        result = STATE.notifications.test()
        payload = notification_payload()
        payload["test"] = result.__dict__
        await send_json(writer, payload)
        return
    if path == "/api/limit-up/state":
        force = query.get("force", ["0"])[0] == "1"
        await send_json(writer, await limit_up_payload(force=force, notify=False))
        return
    if path == "/api/limit-up/refresh":
        notify = query.get("notify", ["0"])[0] == "1"
        await send_json(writer, await limit_up_payload(force=True, notify=notify))
        return
    if path == "/api/limit-up/tomorrow-focus":
        trade_date = query.get("date", [""])[0] or None
        notify = query.get("notify", ["0"])[0] == "1"
        payload = await asyncio.to_thread(STATE.limit_up_monitor.build_tomorrow_focus, trade_date, True)
        if STATE.db_store:
            await asyncio.to_thread(STATE.db_store.save_limit_up_focus, payload)
        if notify:
            notification = STATE.notifications.notify_limit_up_focus_report(payload)
            payload["notification"] = notification.__dict__
        await send_json(writer, payload)
        return
    if path == "/api/limit-up/openclaw-review":
        trade_date = query.get("date", [""])[0] or None
        notify = query.get("notify", ["0"])[0] == "1"
        max_items = int(query.get("max_items", ["120"])[0] or 120)
        timeout = int(query.get("timeout", ["600"])[0] or 600)
        payload = await asyncio.to_thread(STATE.limit_up_monitor.review_tomorrow_focus_with_openclaw, trade_date, max_items, timeout)
        if STATE.db_store:
            await asyncio.to_thread(STATE.db_store.save_limit_up_focus, payload)
        if notify:
            notification = STATE.notifications.notify_limit_up_focus_report(payload)
            payload["notification"] = notification.__dict__
        await send_json(writer, payload)
        return
    if path == "/api/limit-up/next-day-monitor":
        trade_date = query.get("date", [""])[0] or None
        notify = query.get("notify", ["0"])[0] == "1"
        payload = await asyncio.to_thread(STATE.limit_up_monitor.monitor_yesterday_pool, trade_date, True)
        if STATE.db_store:
            await asyncio.to_thread(STATE.db_store.save_next_day_monitor, payload)
        sent = []
        if notify:
            for item in payload.get("buy_signals", [])[:10]:
                notification = STATE.notifications.notify_next_day_buy_signal(item)
                sent.append(notification.__dict__)
        payload["notifications"] = sent
        payload["permission"] = _limit_up_trade_permission(payload)
        await send_json(writer, payload)
        return
    if path == "/api/limit-up/system-review":
        trade_date = query.get("date", [""])[0]
        await send_json(writer, await asyncio.to_thread(limit_up_system_review_payload, trade_date))
        return
    if path == "/api/limit-up/execution":
        trade_date = query.get("date", [""])[0] or str(ashare_session().get("date") or "")
        code = query.get("code", [""])[0]
        status = query.get("status", ["triggered"])[0]
        price = _number(query.get("price", ["0"])[0])
        shares = int(_number(query.get("shares", ["0"])[0]))
        note = query.get("note", [""])[0]
        payload = await asyncio.to_thread(STATE.limit_up_monitor.update_official_execution, trade_date, code, status, price, shares, note)
        if status == "filled":
            item = next((row for row in payload.get("items") or [] if row.get("code") == code), {})
            name = str(item.get("name") or code)
            sector = str(item.get("sector") or "--")
            trade_price = price or _number(item.get("execution_price") or item.get("entry_price") or item.get("price"))
            trade_shares = shares or int(_number(item.get("execution_shares")))
            if trade_price > 0 and trade_shares > 0:
                STATE.positions.upsert(code=code, name=name, sector=sector, price=trade_price, shares=trade_shares, source="limit-up", buy_date=trade_date)
                STATE.trade_records.add(code=code, name=name, sector=sector, side="buy", price=trade_price, shares=trade_shares, reason=note or "系统打板成交", source="limit-up-execution")
        await send_json(writer, {"date": trade_date, "code": code, "status": status, "official": payload, "positions": STATE.positions.payload()})
        return
    if path == "/api/preferences":
        await send_json(writer, {"preferences": STATE.preferences.payload()})
        return
    if path == "/api/preferences/add":
        list_name = query.get("list", ["watchlist"])[0]
        code = query.get("code", [""])[0]
        await send_json(writer, {"preferences": STATE.preferences.add(list_name, code)})
        return
    if path == "/api/preferences/remove":
        list_name = query.get("list", ["watchlist"])[0]
        code = query.get("code", [""])[0]
        await send_json(writer, {"preferences": STATE.preferences.remove(list_name, code)})
        return
    if path == "/api/trade-marks":
        await send_json(writer, STATE.trade_marks.payload())
        return
    if path == "/api/trade-marks/set":
        code = query.get("code", [""])[0]
        mark = query.get("mark", [""])[0]
        await send_json(writer, STATE.trade_marks.set(code, mark))
        return
    if path == "/api/trade-marks/remove":
        code = query.get("code", [""])[0]
        await send_json(writer, STATE.trade_marks.remove(code))
        return
    if path == "/api/market/quotes":
        codes = query.get("codes", [""])[0]
        try:
            await send_json(writer, await asyncio.to_thread(fetch_market_quotes, codes))
        except Exception as error:
            await send_json(writer, {"quotes": {}, "source": "eastmoney", "ts": time.time(), "error": f"{error.__class__.__name__}: {error}"})
        return
    if path == "/api/positions":
        await send_json(writer, STATE.positions.payload())
        return
    if path == "/api/positions/upsert":
        await send_json(
            writer,
            STATE.positions.upsert(
                code=query.get("code", [""])[0],
                name=query.get("name", [""])[0],
                sector=query.get("sector", [""])[0],
                price=query.get("price", ["0"])[0],
                shares=query.get("shares", ["0"])[0],
                source=query.get("source", [""])[0],
                buy_date=query.get("buy_date", [""])[0],
            ),
        )
        return
    if path == "/api/positions/remove":
        code = query.get("code", [""])[0]
        await send_json(writer, STATE.positions.remove(code))
        return
    if path == "/api/trade-records":
        limit = int(query.get("limit", ["100"])[0])
        await send_json(writer, STATE.trade_records.payload(limit=limit))
        return
    if path == "/api/trade-records/add":
        await send_json(
            writer,
            STATE.trade_records.add(
                code=query.get("code", [""])[0],
                name=query.get("name", [""])[0],
                sector=query.get("sector", [""])[0],
                side=query.get("side", [""])[0],
                price=query.get("price", ["0"])[0],
                shares=query.get("shares", ["0"])[0],
                reason=query.get("reason", [""])[0],
                source=query.get("source", [""])[0],
            ),
        )
        return
    if path == "/api/notifications/position-risk":
        item = {
            "code": query.get("code", [""])[0],
            "name": query.get("name", [""])[0],
            "action": query.get("action", [""])[0],
            "price": query.get("price", [""])[0],
            "reason": query.get("reason", [""])[0],
        }
        notification = STATE.notifications.notify_position_risk(item)
        await send_json(writer, {"notification": notification.__dict__})
        return
    if path == "/api/notifications/execution-alert":
        item = {
            "code": query.get("code", [""])[0],
            "name": query.get("name", [""])[0],
            "action": query.get("action", [""])[0],
            "price": query.get("price", [""])[0],
            "reason": query.get("reason", [""])[0],
        }
        notification = STATE.notifications.notify_execution_alert(item)
        await send_json(writer, {"notification": notification.__dict__})
        return
    if path == "/api/stocks/search":
        q = query.get("q", [""])[0]
        limit = int(query.get("limit", ["20"])[0])
        await send_json(writer, search_stocks(q, limit))
        return
    if path == "/api/stocks/lookup":
        codes = query.get("codes", [""])[0]
        await send_json(writer, lookup_stocks(codes))
        return
    if path == "/api/focus/next-day":
        limit = int(query.get("limit", ["100"])[0])
        include_shadow = query.get("include_shadow", ["0"])[0] == "1"
        await send_json(writer, {"records": STATE.focus_store.latest(limit=max(1, min(limit, 1000)), include_shadow=include_shadow)})
        return
    if path == "/api/focus/strategy":
        limit = int(query.get("days", ["30"])[0])
        await send_json(writer, STATE.focus_store.strategy_summary(limit_days=max(1, min(limit, 120))))
        return
    if path == "/api/focus/advice":
        limit = int(query.get("limit", ["300"])[0])
        await send_json(writer, STATE.focus_store.advice_summary(limit=max(1, min(limit, 2000))))
        return
    if path == "/api/backtest/focus":
        limit = int(query.get("limit", ["1000"])[0])
        params = {
            "entry": query.get("entry", ["trigger"])[0],
            "exit": query.get("exit", ["m5"])[0],
            "include_shadow": query.get("include_shadow", ["0"])[0] == "1",
            "min_intraday_score": float(query.get("min_intraday_score", ["0"])[0] or 0),
            "min_review_score": float(query.get("min_review_score", ["0"])[0] or 0),
            "min_score": float(query.get("min_score", ["0"])[0] or 0),
            "limit": max(1, min(limit, 5000)),
        }
        await send_json(writer, focus_backtest(STATE.focus_store.latest(limit=10000, include_shadow=True), params))
        return
    if path == "/api/backtest/history-rapid":
        params = history_backtest_params(query)
        try:
            handler = rapid_rise_multi_date_backtest if params.get("dates") else rapid_rise_history_backtest
            await send_json(writer, handler(params))
        except Exception as error:
            await send_json(writer, {"error": f"{error.__class__.__name__}: {error}", "params": params}, status="500 Internal Server Error")
        return
    if path == "/api/backtest/history-rapid/start":
        params = history_backtest_params(query)
        job = start_history_backtest_job(params)
        await send_json(writer, {"job_id": job["id"], "job": job})
        return
    if path == "/api/backtest/history-rapid/job":
        job_id = query.get("id", [""])[0]
        await send_json(writer, {"job": get_history_backtest_job(job_id)})
        return
    if path == "/api/focus/next-day/export":
        csv_path = STATE.focus_store.export_csv(DATA / "focus_next_day.csv")
        await send_file(writer, csv_path, "text/csv; charset=utf-8")
        return
    if path == "/api/signals/history":
        limit = int(query.get("limit", ["200"])[0])
        await send_json(writer, {"signals": STATE.store.latest(limit=max(1, min(limit, 1000)))})
        return
    if path == "/api/signals/export":
        csv_path = STATE.store.export_csv(DATA / "signals.csv")
        await send_file(writer, csv_path, "text/csv; charset=utf-8")
        return
    if path == "/api/tracks/export":
        csv_path = export_tracks_csv(DATA / "tracks.csv")
        await send_file(writer, csv_path, "text/csv; charset=utf-8")
        return
    if path == "/api/config/update":
        values = {key: value[0] for key, value in query.items()}
        before = dict(load_monitor_config())
        after = dict(update_monitor_config(values))
        STATE.config_changes.append(before, after, action="update")
        await send_json(writer, {"config": after})
        return
    if path == "/api/config/reset":
        before = dict(load_monitor_config())
        after = dict(reset_monitor_config())
        STATE.config_changes.append(before, after, action="reset")
        await send_json(writer, {"config": after})
        return
    if path == "/api/config/changes":
        limit = int(query.get("limit", ["30"])[0])
        await send_json(writer, {"changes": STATE.config_changes.latest(limit=max(1, min(limit, 200)))})
        return
    if path == "/api/universe":
        await send_json(writer, {"universe": universe_payload()})
        return
    if path == "/api/universe/add":
        list_name = query.get("list", ["include"])[0]
        code = query.get("code", [""])[0]
        await send_json(writer, {"universe": add_code(list_name, code)})
        return
    if path == "/api/universe/remove":
        list_name = query.get("list", ["include"])[0]
        code = query.get("code", [""])[0]
        await send_json(writer, {"universe": remove_code(list_name, code)})
        return
    if path == "/api/sectors":
        await send_json(writer, {"sectors": load_sectors()})
        return
    if path == "/api/sectors/add":
        sector = query.get("sector", [""])[0]
        code = query.get("code", [""])[0]
        await send_json(writer, {"sectors": add_sector_code(sector, code)})
        return
    if path == "/api/sectors/remove":
        sector = query.get("sector", [""])[0]
        code = query.get("code", [""])[0]
        await send_json(writer, {"sectors": remove_sector_code(sector, code)})
        return

    await send_static(writer, path)


async def stream_events(writer: asyncio.StreamWriter) -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5)
    STATE.clients.add(queue)
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/event-stream; charset=utf-8\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: keep-alive\r\n"
        "Access-Control-Allow-Origin: *\r\n\r\n"
    )
    writer.write(headers.encode("utf-8"))
    writer.write(b"event: snapshot\n")
    writer.write(f"data: {json.dumps(snapshot_payload(), ensure_ascii=False)}\n\n".encode("utf-8"))
    await writer.drain()

    try:
        while True:
            payload = await queue.get()
            writer.write(b"event: market\n")
            writer.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        STATE.clients.discard(queue)
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass


async def send_json(writer: asyncio.StreamWriter, payload: dict[str, Any], status: str = "200 OK") -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    writer.write(
        f"HTTP/1.1 {status}\r\n".encode("utf-8")
        + b"Content-Type: application/json; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def send_file(writer: asyncio.StreamWriter, file_path: Path, content_type: str) -> None:
    body = file_path.read_bytes()
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        + f"Content-Type: {content_type}\r\n".encode("utf-8")
        + f"Content-Length: {len(body)}\r\n".encode("utf-8")
        + f"Content-Disposition: attachment; filename={file_path.name}\r\n\r\n".encode("utf-8")
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def export_tracks_csv(target: Path) -> Path:
    rows = STATE.monitor.tracked_export_rows()
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "trigger_ts",
        "age_sec",
        "grade",
        "score",
        "code",
        "name",
        "sector",
        "trigger_price",
        "current_price",
        "current_return_pct",
        "max_return_pct",
        "min_return_pct",
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    return target


def snapshot_payload() -> dict[str, Any]:
    payload = STATE.monitor.snapshot()
    payload["runtime"] = STATE.runtime_status()
    return payload


def notification_payload(limit: int = 50) -> dict[str, Any]:
    return {
        "notifications": STATE.notifications.latest(limit=max(1, min(limit, 200))),
        "status": STATE.notifications.status(watchlist_count=len(STATE.preferences.watchlist())),
        "reliability": _notification_reliability_payload(),
    }


async def limit_up_payload(force: bool = False, notify: bool = False) -> dict[str, Any]:
    payload = await asyncio.to_thread(
        STATE.limit_up_monitor.payload,
        STATE.preferences.watchlist(),
        force,
        notify,
    )
    sent = []
    if notify:
        for item in payload.get("signals", [])[:8]:
            notification = STATE.notifications.notify_limit_up_signal(item)
            sent.append(notification.__dict__)
    payload["notifications"] = sent
    if STATE.db_store:
        await asyncio.to_thread(STATE.db_store.save_limit_up_payload, payload)
        payload["database"] = STATE.db_store.status()
    return payload


async def candidate_payload() -> dict[str, Any]:
    url = getattr(STATE.data_source, "url", "")
    if not url:
        return {"candidates": [], "health": {}, "error": "当前数据源不支持候选池详情"}
    candidate_url = url.replace("/ticks", "/candidates")
    try:
        payload = enrich_candidates(await asyncio.to_thread(fetch_json, candidate_url))
        STATE.focus_store.record_candidates(payload)
        STATE.notifications.notify_focus_candidates(payload)
        STATE.notifications.notify_sector_pulse(payload)
        return payload
    except Exception as error:
        return {"candidates": [], "health": {}, "error": f"{error.__class__.__name__}: {error}"}


async def full_health_payload() -> dict[str, Any]:
    runtime = STATE.runtime_status()
    candidate = await candidate_payload()
    tdx = await optional_health(os.environ.get("TDX_HEALTH_URL", "http://127.0.0.1:9002/health"))
    tickdb = await optional_health(os.environ.get("TICKDB_HEALTH_URL", "http://127.0.0.1:9001/health"))
    focus_records = STATE.focus_store.latest(limit=200, include_shadow=True)
    session = runtime.get("session", {})
    components = {
        "main": component(runtime.get("status") != "ERROR", runtime.get("status", "--"), f"delay={runtime.get('data_age_sec')}s"),
        "candidates": component(not candidate.get("error") and len(candidate.get("candidates", [])) > 0, f"{len(candidate.get('candidates', []))} candidates", candidate.get("error", "")),
        "tdx": component(bool(tdx.get("ok")), tdx.get("label", "--"), tdx.get("error", "")),
        "tickdb": component(bool(tickdb.get("ok")), tickdb.get("label", "--"), tickdb.get("error", ""), required=False),
        "database": component(not STATE.db_store or not STATE.db_store.last_error, "postgres enabled" if STATE.db_store else "file storage", STATE.db_store.last_error if STATE.db_store else "", required=False),
        "calendar": component(True, f"{session.get('date')} -> {next_trading_date(session.get('date')) if session.get('date') else '--'}", session.get("label", "")),
        "focus_next_day": component(bool(focus_records), f"{len(focus_records)} records", "waiting samples" if not focus_records else ""),
    }
    required_ok = all(item["ok"] for item in components.values() if item["required"])
    return {
        "status": "OK" if required_ok else "WARN",
        "components": components,
        "runtime": runtime,
        "candidate_health": candidate.get("health", {}),
        "strategy_funnel": candidate.get("strategy_funnel", []),
    }


async def optional_health(url: str) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(fetch_json, url)
        label = payload.get("source") or payload.get("status") or "ok"
        connected = payload.get("connected")
        ok = not payload.get("last_error") and connected is not False
        return {"ok": ok, "label": str(label), "payload": payload}
    except Exception as error:
        return {"ok": False, "label": "unreachable", "error": f"{error.__class__.__name__}: {error}"}


def component(ok: bool, label: str, detail: str = "", required: bool = True) -> dict[str, Any]:
    return {"ok": ok, "label": label, "detail": detail, "required": required}


def fetch_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("候选池接口必须返回对象")
    return payload


def history_backtest_params(query: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "date": query.get("date", [""])[0],
        "dates": query.get("dates", [""])[0],
        "codes": query.get("codes", [""])[0],
        "max_symbols": int(query.get("max_symbols", ["50"])[0] or 50),
        "rise_1m": float(query.get("rise_1m", ["0.7"])[0] or 0.7),
        "rise_3m": float(query.get("rise_3m", ["1.2"])[0] or 1.2),
        "min_amount_2m": float(query.get("min_amount_2m", ["5000000"])[0] or 0),
        "max_day_change": float(query.get("max_day_change", ["7.5"])[0] or 7.5),
        "cooldown_min": int(query.get("cooldown_min", ["10"])[0] or 10),
        "max_signals": int(query.get("max_signals", ["300"])[0] or 300),
        "include_bj": query.get("include_bj", ["0"])[0] == "1",
        "include_gem": query.get("include_gem", ["0"])[0] == "1",
        "include_star": query.get("include_star", ["0"])[0] == "1",
        "require_limit_up": query.get("require_limit_up", ["0"])[0] == "1",
    }


def start_history_backtest_job(params: dict[str, Any]) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "RUNNING",
        "created_at": time.time(),
        "updated_at": time.time(),
        "progress": 0,
        "scanned": 0,
        "total": max(1, int(params.get("max_symbols", 1) or 1)),
        "message": "准备开始",
        "params": params,
        "result": None,
        "error": "",
    }
    with STATE.backtest_job_lock:
        STATE.backtest_jobs[job_id] = job
        if len(STATE.backtest_jobs) > 20:
            oldest = sorted(STATE.backtest_jobs.values(), key=lambda item: item.get("created_at", 0))[0]
            STATE.backtest_jobs.pop(oldest["id"], None)

    thread = threading.Thread(target=run_history_backtest_job, args=(job_id, params), daemon=True)
    thread.start()
    return dict(job)


def get_history_backtest_job(job_id: str) -> dict[str, Any]:
    with STATE.backtest_job_lock:
        job = STATE.backtest_jobs.get(job_id)
        if not job:
            return {"id": job_id, "status": "NOT_FOUND", "progress": 0, "message": "任务不存在"}
        return dict(job)


def run_history_backtest_job(job_id: str, params: dict[str, Any]) -> None:
    def update_progress(**state: Any) -> None:
        scanned = int(state.get("scanned") or 0)
        total = max(1, int(state.get("total") or params.get("max_symbols", 1) or 1))
        with STATE.backtest_job_lock:
            job = STATE.backtest_jobs.get(job_id)
            if not job:
                return
            job["scanned"] = scanned
            job["total"] = total
            job["progress"] = min(99, round(scanned / total * 100))
            job["message"] = str(state.get("message") or job.get("message") or "")
            job["updated_at"] = time.time()

    try:
        handler = rapid_rise_multi_date_backtest if params.get("dates") else rapid_rise_history_backtest
        result = handler(params, progress=update_progress)
        with STATE.backtest_job_lock:
            job = STATE.backtest_jobs[job_id]
            job["status"] = "DONE"
            job["progress"] = 100
            job["result"] = result
            job["message"] = "完成"
            job["updated_at"] = time.time()
    except Exception as error:
        with STATE.backtest_job_lock:
            job = STATE.backtest_jobs.get(job_id)
            if job:
                job["status"] = "ERROR"
                job["error"] = f"{error.__class__.__name__}: {error}"
                job["message"] = "失败"
                job["updated_at"] = time.time()


def export_recent_alerts(signals: list[Any]) -> None:
    now = time.time()
    existing: dict[str, dict[str, Any]] = {}
    if RECENT_ALERTS.exists():
        try:
            payload = json.loads(RECENT_ALERTS.read_text(encoding="utf-8"))
            for item in payload.get("alerts", []):
                if now - float(item.get("ts", 0)) <= ALERT_KEEP_SEC:
                    existing[str(item.get("code", ""))] = item
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            existing = {}

    for signal in signals:
        item = signal.to_dict()
        existing[item["code"]] = {
            "code": item["code"],
            "name": item["name"],
            "grade": item["grade"],
            "score": item["score"],
            "ts": now,
        }

    RECENT_ALERTS.parent.mkdir(parents=True, exist_ok=True)
    RECENT_ALERTS.write_text(
        json.dumps({"updated_at": now, "keep_sec": ALERT_KEEP_SEC, "alerts": list(existing.values())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def send_static(writer: asyncio.StreamWriter, path: str) -> None:
    if path == "/":
        path = "/index.html"
    file_path = (STATIC / path.lstrip("/")).resolve()
    if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.exists():
        body = b"Not found"
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\n" + body)
    else:
        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Type: {content_type}; charset=utf-8\r\n".encode("utf-8")
            + f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
            + body
        )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main() -> None:
    server = await asyncio.start_server(handle_client, HOST, PORT)
    market_task = asyncio.create_task(market_loop())
    limit_up_task = asyncio.create_task(limit_up_focus_loop())
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    print(f"分时异动雷达已启动: http://{HOST}:{PORT}")
    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        await stop_event.wait()
        server.close()
        await server.wait_closed()
        serve_task.cancel()
        market_task.cancel()
        limit_up_task.cancel()
        await asyncio.gather(serve_task, market_task, limit_up_task, return_exceptions=True)
    print("分时异动雷达已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
