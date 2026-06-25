from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, AsyncIterator

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backtest import focus_backtest
from runtime_config import load_monitor_config, reset_monitor_config, update_monitor_config
from sectors import add_sector_code, load_sectors, remove_sector_code
from stock_search import lookup_stocks, search_stocks
from server import (
    DATA,
    STATIC,
    STATE,
    candidate_payload,
    export_tracks_csv,
    full_health_payload,
    get_history_backtest_job,
    history_backtest_params,
    limit_up_payload,
    limit_up_focus_loop,
    limit_up_system_review_payload,
    market_loop,
    maybe_monitor_next_day_buy_signals,
    notification_payload,
    snapshot_payload,
    start_history_backtest_job,
)
from historical_backtest import fetch_eastmoney_minute_bars, rapid_rise_history_backtest, rapid_rise_multi_date_backtest
from market_quotes import fetch_market_quotes
from openclaw_review import run_openclaw_strategy_review
from market_clock import is_trading_date
from universe import add_code, remove_code, universe_payload


LIMIT_UP_OPENCLAW_JOBS: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    market_task = asyncio.create_task(market_loop())
    limit_up_task = asyncio.create_task(limit_up_focus_loop())
    try:
        yield
    finally:
        market_task.cancel()
        limit_up_task.cancel()
        await asyncio.gather(market_task, limit_up_task, return_exceptions=True)


app = FastAPI(
    title="A股实盘雷达 API",
    description="Multi-client API for web, mobile, desktop, and notification clients.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/snapshot")
async def api_snapshot() -> dict[str, Any]:
    return snapshot_payload()


@app.get("/api/report")
async def api_report() -> dict[str, Any]:
    report = STATE.monitor.report()
    if not report["tracked_alerts"]:
        report["tracked_alerts"] = STATE.track_store.latest(limit=100)[:10]
    return {"report": report, "runtime": STATE.runtime_status()}


@app.get("/api/candidates")
async def api_candidates() -> dict[str, Any]:
    return await candidate_payload()


@app.get("/api/radar/detail")
async def api_radar_detail(code: str) -> dict[str, Any]:
    normalized = code.strip()
    snapshot = snapshot_payload()
    candidates = await candidate_payload()
    signals = snapshot.get("signals", [])
    tracks = snapshot.get("tracked_alerts", [])
    candidate_items = candidates.get("candidates", [])
    sector_heat = _normalize_sector_heat(snapshot, candidates)

    candidate = _find_by_code(candidate_items, normalized)
    signal = _find_by_code(signals, normalized)
    track = _find_by_code(tracks, normalized)
    sector = (candidate or signal or {}).get("sector", "")
    sector_info = next((item for item in sector_heat if item.get("sector") == sector), None)
    same_sector_candidates = [
        item
        for item in candidate_items
        if sector and item.get("sector") == sector
    ][:12]

    return {
        "code": normalized,
        "candidate": candidate,
        "signal": signal,
        "track": track,
        "sector": sector_info,
        "same_sector_candidates": same_sector_candidates,
        "runtime": snapshot.get("runtime", {}),
    }


@app.get("/api/market/kline")
async def api_market_kline(code: str, date: str = "", limit: int = 240) -> dict[str, Any]:
    normalized = _normalize_symbol(code)
    target_date = _latest_trading_date(date)
    try:
        bars = await asyncio.to_thread(fetch_eastmoney_minute_bars, normalized, target_date)
        rows = [
            {
                "ts": item.ts,
                "open": item.open,
                "close": item.close,
                "high": item.high,
                "low": item.low,
                "volume": item.volume,
                "amount": item.amount,
                "prev_close": item.prev_close,
            }
            for item in bars[-max(1, min(limit, 300)) :]
        ]
        return {
            "code": normalized.split(".", 1)[0],
            "symbol": normalized,
            "date": target_date,
            "bars": rows,
            "source": "eastmoney",
        }
    except Exception as error:
        return {
            "code": normalized.split(".", 1)[0],
            "symbol": normalized,
            "date": target_date,
            "bars": [],
            "source": "eastmoney",
            "error": f"{error.__class__.__name__}: {error}",
        }


@app.get("/api/market/quotes")
async def api_market_quotes(codes: str = "") -> dict[str, Any]:
    try:
        return await asyncio.to_thread(fetch_market_quotes, codes)
    except Exception as error:
        return {"quotes": {}, "source": "eastmoney", "ts": time.time(), "error": f"{error.__class__.__name__}: {error}"}


@app.get("/api/stocks/search")
async def api_stocks_search(q: str = "", limit: int = 20) -> dict[str, Any]:
    return search_stocks(q, limit)


@app.get("/api/stocks/lookup")
async def api_stocks_lookup(codes: str = "") -> dict[str, Any]:
    return lookup_stocks(codes)


@app.get("/api/ai/trade-review")
async def api_ai_trade_review(code: str) -> dict[str, Any]:
    normalized = code.strip()
    started = time.time()
    if not os.environ.get("MX_APIKEY"):
        return {
            "code": normalized,
            "available": False,
            "summary": "未检测到 MX_APIKEY，无法调用妙想 AI 复核。",
            "points": [],
            "source": "mx-search",
            "elapsed_ms": 0,
        }

    detail = await api_radar_detail(normalized)
    item = detail.get("signal") or detail.get("candidate") or {"code": normalized}
    name = item.get("name") or normalized
    sector = item.get("sector") or "--"
    score = item.get("score") or item.get("adjusted_score") or item.get("candidate_score") or "--"
    change = item.get("change_pct", "--")
    query = (
        f"{name} {normalized} 最新公告 研报 新闻 风险 机构观点，"
        f"结合A股实盘短线交易，板块{sector}，当前涨幅{change}%，评分{score}，"
        "只总结影响今日或最近交易决策的利好、利空和风险。"
    )
    result = await asyncio.to_thread(_run_mx_search, query)
    recommendation = _build_ai_recommendation(item, result.get("points", []))
    result.update(
        {
            "code": normalized,
            "name": name,
            "sector": sector,
            "query": query,
            "recommendation": recommendation,
            "elapsed_ms": round((time.time() - started) * 1000),
        }
    )
    return result


@app.get("/api/openclaw/review")
async def api_openclaw_review(code: str, include_position: bool = True) -> dict[str, Any]:
    normalized = code.strip().split(".", 1)[0]
    context = await _openclaw_stock_context(normalized, include_position=include_position)
    return await asyncio.to_thread(run_openclaw_strategy_review, context)


@app.get("/api/health/full")
async def api_full_health() -> dict[str, Any]:
    return await full_health_payload()


@app.get("/api/notifications/recent")
async def api_notifications_recent(limit: int = 50) -> dict[str, Any]:
    return notification_payload(limit)


@app.get("/api/notifications/config")
async def api_notifications_config(
    enabled: str | None = None,
    signal_a_enabled: str | None = None,
    focus_strong_enabled: str | None = None,
    watchlist_signal_enabled: str | None = None,
    sector_pulse_enabled: str | None = None,
    execution_alert_enabled: str | None = None,
    limit_up_signal_enabled: str | None = None,
    limit_up_focus_enabled: str | None = None,
    next_day_buy_enabled: str | None = None,
    next_day_risk_enabled: str | None = None,
    cooldown_sec: str | None = None,
    failed_retry_sec: str | None = None,
    sector_pulse_threshold: str | None = None,
    bark_url: str | None = None,
    backup_bark_urls: str | None = None,
    omni_bark_token: str | None = None,
    omni_bark_channel_id: str | None = None,
    omni_bark_sender: str | None = None,
    omni_bark_api_base: str | None = None,
    critical_sound: str | None = None,
) -> dict[str, Any]:
    values = {
        key: value
        for key, value in {
            "enabled": enabled,
            "signal_a_enabled": signal_a_enabled,
            "focus_strong_enabled": focus_strong_enabled,
            "watchlist_signal_enabled": watchlist_signal_enabled,
            "sector_pulse_enabled": sector_pulse_enabled,
            "execution_alert_enabled": execution_alert_enabled,
            "limit_up_signal_enabled": limit_up_signal_enabled,
            "limit_up_focus_enabled": limit_up_focus_enabled,
            "next_day_buy_enabled": next_day_buy_enabled,
            "next_day_risk_enabled": next_day_risk_enabled,
            "cooldown_sec": cooldown_sec,
            "failed_retry_sec": failed_retry_sec,
            "sector_pulse_threshold": sector_pulse_threshold,
            "bark_url": bark_url,
            "backup_bark_urls": backup_bark_urls,
            "omni_bark_token": omni_bark_token,
            "omni_bark_channel_id": omni_bark_channel_id,
            "omni_bark_sender": omni_bark_sender,
            "omni_bark_api_base": omni_bark_api_base,
            "critical_sound": critical_sound,
        }.items()
        if value is not None
    }
    STATE.notifications.update_config(values)
    return notification_payload()


@app.get("/api/notifications/test")
async def api_notifications_test() -> dict[str, Any]:
    result = STATE.notifications.test()
    payload = notification_payload()
    payload["test"] = result.__dict__
    return payload


@app.get("/api/limit-up/state")
async def api_limit_up_state(force: bool = False) -> dict[str, Any]:
    return await limit_up_payload(force=force, notify=False)


@app.get("/api/limit-up/refresh")
async def api_limit_up_refresh(notify: bool = False) -> dict[str, Any]:
    return await limit_up_payload(force=True, notify=notify)


@app.get("/api/limit-up/tomorrow-focus")
async def api_limit_up_tomorrow_focus(date: str = "", notify: bool = False) -> dict[str, Any]:
    payload = await asyncio.to_thread(STATE.limit_up_monitor.build_tomorrow_focus, date or None, True)
    if STATE.db_store:
        await asyncio.to_thread(STATE.db_store.save_limit_up_focus, payload)
    if notify:
        notification = STATE.notifications.notify_limit_up_focus_report(payload)
        payload["notification"] = notification.__dict__
    return payload


@app.get("/api/limit-up/openclaw-review")
async def api_limit_up_openclaw_review(date: str = "", max_items: int = 120, timeout: int = 600, notify: bool = False) -> dict[str, Any]:
    payload = await asyncio.to_thread(
        STATE.limit_up_monitor.review_tomorrow_focus_with_openclaw,
        date or None,
        max_items,
        timeout,
    )
    if STATE.db_store:
        await asyncio.to_thread(STATE.db_store.save_limit_up_focus, payload)
    if notify:
        notification = STATE.notifications.notify_limit_up_focus_report(payload)
        payload["notification"] = notification.__dict__
    return payload


@app.get("/api/limit-up/openclaw-review/start")
async def api_limit_up_openclaw_review_start(date: str = "", max_items: int = 120, timeout: int = 600, notify: bool = False) -> dict[str, Any]:
    payload = await asyncio.to_thread(STATE.limit_up_monitor.build_tomorrow_focus, date or None, True)
    if STATE.db_store:
        await asyncio.to_thread(STATE.db_store.save_limit_up_focus, payload)

    trade_date = str(payload.get("date") or date or "")
    limit = max(3, min(int(max_items or 120), 120))
    existing = _latest_running_limit_up_openclaw_job(trade_date)
    if existing:
        return {"job": existing, "payload": payload}

    job_id = f"limit-up-openclaw:{trade_date or 'latest'}:{uuid.uuid4().hex[:8]}"
    job = {
        "id": job_id,
        "status": "queued",
        "date": trade_date,
        "max_items": limit,
        "timeout": max(60, int(timeout or 600)),
        "notify": bool(notify),
        "created_at": time.time(),
        "started_at": 0,
        "finished_at": 0,
        "elapsed_ms": 0,
        "summary": "OpenClaw 后台复核排队中",
        "error": "",
    }
    LIMIT_UP_OPENCLAW_JOBS[job_id] = job
    asyncio.create_task(_run_limit_up_openclaw_job(job_id))
    return {"job": dict(job), "payload": payload}


@app.get("/api/limit-up/openclaw-review/status")
async def api_limit_up_openclaw_review_status(job_id: str) -> dict[str, Any]:
    job = LIMIT_UP_OPENCLAW_JOBS.get(job_id)
    if not job:
        return {"job": {"id": job_id, "status": "missing", "summary": "未找到 OpenClaw 后台任务"}}
    response: dict[str, Any] = {"job": dict(job)}
    payload = job.get("payload")
    if isinstance(payload, dict):
        response["payload"] = payload
    return response


@app.get("/api/limit-up/next-day-monitor")
async def api_limit_up_next_day_monitor(date: str = "", notify: bool = False) -> dict[str, Any]:
    payload = await asyncio.to_thread(STATE.limit_up_monitor.monitor_yesterday_pool, date or None, True)
    if STATE.db_store:
        await asyncio.to_thread(STATE.db_store.save_next_day_monitor, payload)
    sent = []
    if notify:
        for item in payload.get("buy_signals", [])[:10]:
            notification = STATE.notifications.notify_next_day_buy_signal(item)
            sent.append(notification.__dict__)
    payload["notifications"] = sent
    return payload


@app.get("/api/limit-up/system-review")
async def api_limit_up_system_review(date: str = "") -> dict[str, Any]:
    return await asyncio.to_thread(limit_up_system_review_payload, date)


@app.get("/api/preferences")
async def api_preferences() -> dict[str, Any]:
    return {"preferences": STATE.preferences.payload()}


@app.get("/api/preferences/add")
async def api_preferences_add(list: str = "watchlist", code: str = "") -> dict[str, Any]:  # noqa: A002 - API query name
    return {"preferences": STATE.preferences.add(list, code)}


@app.get("/api/preferences/remove")
async def api_preferences_remove(list: str = "watchlist", code: str = "") -> dict[str, Any]:  # noqa: A002 - API query name
    return {"preferences": STATE.preferences.remove(list, code)}


@app.get("/api/trade-marks")
async def api_trade_marks() -> dict[str, Any]:
    return STATE.trade_marks.payload()


@app.get("/api/trade-marks/set")
async def api_trade_marks_set(code: str = "", mark: str = "") -> dict[str, Any]:
    return STATE.trade_marks.set(code, mark)


@app.get("/api/trade-marks/remove")
async def api_trade_marks_remove(code: str = "") -> dict[str, Any]:
    return STATE.trade_marks.remove(code)


@app.get("/api/positions")
async def api_positions() -> dict[str, Any]:
    return STATE.positions.payload()


@app.get("/api/positions/upsert")
async def api_positions_upsert(code: str = "", name: str = "", sector: str = "", price: str = "0", shares: str = "0", source: str = "") -> dict[str, Any]:
    return STATE.positions.upsert(code=code, name=name, sector=sector, price=price, shares=shares, source=source)


@app.get("/api/positions/remove")
async def api_positions_remove(code: str = "") -> dict[str, Any]:
    return STATE.positions.remove(code)


@app.get("/api/trade-records")
async def api_trade_records(limit: int = 100) -> dict[str, Any]:
    return STATE.trade_records.payload(limit=limit)


@app.get("/api/trade-records/add")
async def api_trade_records_add(
    code: str = "",
    name: str = "",
    sector: str = "",
    side: str = "",
    price: str = "0",
    shares: str = "0",
    reason: str = "",
    source: str = "",
) -> dict[str, Any]:
    return STATE.trade_records.add(code=code, name=name, sector=sector, side=side, price=price, shares=shares, reason=reason, source=source)


@app.get("/api/notifications/position-risk")
async def api_position_risk_notification(code: str = "", name: str = "", action: str = "", price: str = "", reason: str = "") -> dict[str, Any]:
    notification = STATE.notifications.notify_position_risk({"code": code, "name": name, "action": action, "price": price, "reason": reason})
    return {"notification": notification.__dict__}


@app.get("/api/notifications/execution-alert")
async def api_execution_alert_notification(code: str = "", name: str = "", action: str = "", price: str = "", reason: str = "") -> dict[str, Any]:
    notification = STATE.notifications.notify_execution_alert({"code": code, "name": name, "action": action, "price": price, "reason": reason})
    return {"notification": notification.__dict__}


@app.get("/api/focus/next-day")
async def api_focus_next_day(limit: int = 100, include_shadow: bool = False) -> dict[str, Any]:
    return {
        "records": STATE.focus_store.latest(
            limit=max(1, min(limit, 1000)),
            include_shadow=include_shadow,
        )
    }


@app.get("/api/focus/strategy")
async def api_focus_strategy(days: int = 30) -> dict[str, Any]:
    return STATE.focus_store.strategy_summary(limit_days=max(1, min(days, 120)))


@app.get("/api/focus/advice")
async def api_focus_advice(limit: int = 300) -> dict[str, Any]:
    return STATE.focus_store.advice_summary(limit=max(1, min(limit, 2000)))


@app.get("/api/backtest/focus")
async def api_focus_backtest(
    limit: int = 1000,
    entry: str = "trigger",
    exit: str = "m5",
    include_shadow: bool = False,
    min_intraday_score: float = 0,
    min_review_score: float = 0,
    min_score: float = 0,
) -> dict[str, Any]:
    params = {
        "entry": entry,
        "exit": exit,
        "include_shadow": include_shadow,
        "min_intraday_score": min_intraday_score,
        "min_review_score": min_review_score,
        "min_score": min_score,
        "limit": max(1, min(limit, 5000)),
    }
    return focus_backtest(STATE.focus_store.latest(limit=10000, include_shadow=True), params)


@app.get("/api/backtest/history-rapid/start")
async def api_history_backtest_start(
    date: str = "",
    dates: str = "",
    codes: str = "",
    max_symbols: int = 50,
    rise_1m: float = 0.7,
    rise_3m: float = 1.2,
    min_amount_2m: float = 5_000_000,
    max_day_change: float = 7.5,
    cooldown_min: int = 10,
    max_signals: int = 300,
    include_bj: bool = False,
    include_gem: bool = False,
    include_star: bool = False,
    require_limit_up: bool = False,
) -> dict[str, Any]:
    query = _legacy_query(
        date=date,
        dates=dates,
        codes=codes,
        max_symbols=max_symbols,
        rise_1m=rise_1m,
        rise_3m=rise_3m,
        min_amount_2m=min_amount_2m,
        max_day_change=max_day_change,
        cooldown_min=cooldown_min,
        max_signals=max_signals,
        include_bj=include_bj,
        include_gem=include_gem,
        include_star=include_star,
        require_limit_up=require_limit_up,
    )
    job = start_history_backtest_job(history_backtest_params(query))
    return {"job_id": job["id"], "job": job}


@app.get("/api/backtest/history-rapid")
async def api_history_backtest(
    date: str = "",
    dates: str = "",
    codes: str = "",
    max_symbols: int = 50,
    rise_1m: float = 0.7,
    rise_3m: float = 1.2,
    min_amount_2m: float = 5_000_000,
    max_day_change: float = 7.5,
    cooldown_min: int = 10,
    max_signals: int = 300,
    include_bj: bool = False,
    include_gem: bool = False,
    include_star: bool = False,
    require_limit_up: bool = False,
) -> dict[str, Any]:
    query = _legacy_query(
        date=date,
        dates=dates,
        codes=codes,
        max_symbols=max_symbols,
        rise_1m=rise_1m,
        rise_3m=rise_3m,
        min_amount_2m=min_amount_2m,
        max_day_change=max_day_change,
        cooldown_min=cooldown_min,
        max_signals=max_signals,
        include_bj=include_bj,
        include_gem=include_gem,
        include_star=include_star,
        require_limit_up=require_limit_up,
    )
    params = history_backtest_params(query)
    handler = rapid_rise_multi_date_backtest if params.get("dates") else rapid_rise_history_backtest
    return handler(params)


@app.get("/api/backtest/history-rapid/job")
async def api_history_backtest_job(id: str = Query("")) -> dict[str, Any]:
    return {"job": get_history_backtest_job(id)}


@app.get("/api/focus/next-day/export")
async def api_focus_next_day_export() -> FileResponse:
    csv_path = STATE.focus_store.export_csv(DATA / "focus_next_day.csv")
    return _download(csv_path, "text/csv; charset=utf-8")


@app.get("/api/signals/history")
async def api_signal_history(limit: int = 200) -> dict[str, Any]:
    return {"signals": STATE.store.latest(limit=max(1, min(limit, 1000)))}


@app.get("/api/signals/export")
async def api_signals_export() -> FileResponse:
    csv_path = STATE.store.export_csv(DATA / "signals.csv")
    return _download(csv_path, "text/csv; charset=utf-8")


@app.get("/api/tracks/export")
async def api_tracks_export() -> FileResponse:
    csv_path = export_tracks_csv(DATA / "tracks.csv")
    return _download(csv_path, "text/csv; charset=utf-8")


@app.get("/api/config/update")
async def api_config_update(
    min_price: str | None = None,
    min_turnover_1m: str | None = None,
    min_turnover_today: str | None = None,
    min_score: str | None = None,
    max_distance_to_limit_pct: str | None = None,
    rise_1m_pct: str | None = None,
    rise_3m_pct: str | None = None,
    rise_5m_pct: str | None = None,
    volume_spike_ratio: str | None = None,
    min_active_buy_ratio: str | None = None,
    min_order_book_bias: str | None = None,
    sector_signal_window_sec: str | None = None,
    signal_cooldown_sec: str | None = None,
    signal_rescore_delta: str | None = None,
) -> dict[str, Any]:
    values = {
        key: value
        for key, value in locals().items()
        if value is not None
    }
    before = dict(load_monitor_config())
    after = dict(update_monitor_config(values))
    STATE.config_changes.append(before, after, action="update")
    return {"config": after}


@app.get("/api/config/reset")
async def api_config_reset() -> dict[str, Any]:
    before = dict(load_monitor_config())
    after = dict(reset_monitor_config())
    STATE.config_changes.append(before, after, action="reset")
    return {"config": after}


@app.get("/api/config/changes")
async def api_config_changes(limit: int = 30) -> dict[str, Any]:
    return {"changes": STATE.config_changes.latest(limit=max(1, min(limit, 200)))}


@app.get("/api/universe")
async def api_universe() -> dict[str, Any]:
    return {"universe": universe_payload()}


@app.get("/api/universe/add")
async def api_universe_add(list_name: str = Query("include", alias="list"), code: str = "") -> dict[str, Any]:
    return {"universe": add_code(list_name, code)}


@app.get("/api/universe/remove")
async def api_universe_remove(list_name: str = Query("include", alias="list"), code: str = "") -> dict[str, Any]:
    return {"universe": remove_code(list_name, code)}


@app.get("/api/sectors")
async def api_sectors() -> dict[str, Any]:
    return {"sectors": load_sectors()}


@app.get("/api/sectors/add")
async def api_sector_add(sector: str = "", code: str = "") -> dict[str, Any]:
    return {"sectors": add_sector_code(sector, code)}


@app.get("/api/sectors/remove")
async def api_sector_remove(sector: str = "", code: str = "") -> dict[str, Any]:
    return {"sectors": remove_sector_code(sector, code)}


@app.get("/events")
async def sse_events() -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5)
        STATE.clients.add(queue)
        try:
            yield _sse("snapshot", snapshot_payload())
            while True:
                payload = await queue.get()
                yield _sse("market", payload)
        finally:
            STATE.clients.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.websocket("/ws/radar")
async def radar_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5)
    STATE.clients.add(queue)
    try:
        await websocket.send_json({"event": "snapshot", **snapshot_payload()})
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        STATE.clients.discard(queue)


@app.websocket("/ws/limit-up")
async def limit_up_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=3)
    try:
        initial = await maybe_monitor_next_day_buy_signals(tick_driven=False)
        if initial:
            await websocket.send_json(initial)
        else:
            payload = await asyncio.to_thread(STATE.limit_up_monitor.monitor_yesterday_pool, None, True)
            payload["event"] = "limit-up"
            payload["runtime"] = STATE.runtime_status()
            payload["tick_driven"] = False
            await websocket.send_json(payload)
        STATE.limit_up_clients.add(queue)
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        STATE.limit_up_clients.discard(queue)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def _download(path: Path, media_type: str) -> FileResponse:
    return FileResponse(path, media_type=media_type, filename=path.name)


def _latest_running_limit_up_openclaw_job(trade_date: str) -> dict[str, Any] | None:
    for job in sorted(LIMIT_UP_OPENCLAW_JOBS.values(), key=lambda item: float(item.get("created_at") or 0), reverse=True):
        if job.get("date") == trade_date and job.get("status") in {"queued", "running"}:
            return dict(job)
    return None


async def _run_limit_up_openclaw_job(job_id: str) -> None:
    job = LIMIT_UP_OPENCLAW_JOBS.get(job_id)
    if not job:
        return
    started = time.time()
    job.update({"status": "running", "started_at": started, "summary": "OpenClaw 后台复核中"})
    try:
        payload = await asyncio.to_thread(
            STATE.limit_up_monitor.review_tomorrow_focus_with_openclaw,
            job.get("date") or None,
            int(job.get("max_items") or 120),
            int(job.get("timeout") or 600),
        )
        if STATE.db_store:
            await asyncio.to_thread(STATE.db_store.save_limit_up_focus, payload)
        if job.get("notify"):
            notification = STATE.notifications.notify_limit_up_focus_report(payload)
            payload["notification"] = notification.__dict__

        review = payload.get("openclaw_review") if isinstance(payload, dict) else {}
        available = bool((review or {}).get("available"))
        summary = str((review or {}).get("summary") or ("OpenClaw 复核完成" if available else "OpenClaw 超时，已按规则兜底"))
        job.update(
            {
                "status": "done" if available else "fallback",
                "finished_at": time.time(),
                "elapsed_ms": round((time.time() - started) * 1000),
                "summary": summary,
                "payload": payload,
            }
        )
    except Exception as error:
        job.update(
            {
                "status": "failed",
                "finished_at": time.time(),
                "elapsed_ms": round((time.time() - started) * 1000),
                "summary": "OpenClaw 后台复核失败，已保留规则结果",
                "error": f"{error.__class__.__name__}: {error}",
            }
        )


async def _openclaw_stock_context(code: str, include_position: bool = True) -> dict[str, Any]:
    detail = await api_radar_detail(code)
    item = detail.get("signal") or detail.get("candidate") or {"code": code}
    quote_payload = await api_market_quotes(code)
    quote = (quote_payload.get("quotes") or {}).get(code, {})
    positions = STATE.positions.payload().get("positions", [])
    position = next((row for row in positions if row.get("code") == code), None) if include_position else None
    track = detail.get("track")
    candidate = detail.get("candidate")
    signal = detail.get("signal")
    return {
        "code": code,
        "name": item.get("name") or quote.get("name") or (position or {}).get("name") or code,
        "sector": item.get("sector") or (position or {}).get("sector") or "--",
        "price": quote.get("price") or item.get("price") or (track or {}).get("current_price"),
        "change_pct": quote.get("change_pct") if quote else item.get("change_pct"),
        "score": item.get("score") or item.get("adjusted_score") or item.get("candidate_score"),
        "candidate": _compact_market_item(candidate),
        "signal": _compact_market_item(signal),
        "track": _compact_market_item(track),
        "position": position,
        "quote": quote,
        "runtime": detail.get("runtime", {}),
    }


def _compact_market_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    keys = [
        "code",
        "name",
        "sector",
        "price",
        "change_pct",
        "score",
        "adjusted_score",
        "candidate_score",
        "rise_1m_pct",
        "rise_speed_pct",
        "min2_amount",
        "turnover_1m",
        "active_buy_ratio",
        "turnover_rate",
        "quality_label",
        "quality_level",
        "explanation",
        "candidate_reasons",
        "risk_flags",
        "miss_reasons",
        "market_mood",
        "emotion_score",
        "theme_rank",
        "theme_score",
        "hot_money_role",
        "leader_role",
        "leader_score",
        "market_height_rank",
        "theme_leader_rank",
        "limit_up",
        "limit_up_streak",
        "limit_up_threshold_pct",
        "first_limit_time",
        "last_limit_time",
        "limit_up_amount",
        "seal_amount",
        "open_board_count",
        "distance_to_limit_pct",
        "buy_pattern",
        "hot_money_tags",
        "current_return_pct",
        "max_return_pct",
        "min_return_pct",
        "age_sec",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _find_by_code(items: list[dict[str, Any]], code: str) -> dict[str, Any] | None:
    return next((item for item in items if str(item.get("code", "")) == code), None)


def _normalize_symbol(value: str) -> str:
    text = "".join(ch for ch in value.strip().upper() if ch.isalnum() or ch == ".")
    if "." in text:
        return text
    if text.startswith(("6", "9")):
        return f"{text}.SH"
    if text.startswith(("4", "8")):
        return f"{text}.BJ"
    return f"{text}.SZ"


def _latest_trading_date(value: str = "") -> str:
    if value:
        return value
    session_date = str(STATE.runtime_status().get("session", {}).get("date") or "")
    current = date.fromisoformat(session_date) if session_date else date.today()
    for _ in range(30):
        if is_trading_date(current):
            return current.strftime("%Y-%m-%d")
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def _normalize_sector_heat(snapshot: dict[str, Any], candidates: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_sectors = candidates.get("sector_heat") or candidates.get("health", {}).get("sector_heat") or []
    if isinstance(candidate_sectors, list) and candidate_sectors:
        return candidate_sectors
    return [
        {"sector": sector, "count": count}
        for sector, count in (snapshot.get("sector_heat") or {}).items()
    ]


def _run_mx_search(query: str) -> dict[str, Any]:
    skill_script = Path.home() / ".codex" / "skills" / "mx-search" / "mx_search.py"
    if not skill_script.exists():
        return {
            "available": False,
            "summary": "未安装 mx-search skill，无法进行 AI 复核。",
            "points": [],
            "source": "mx-search",
        }
    python_bin = Path.home() / ".codex" / "skills" / ".mx-venv" / "bin" / "python"
    executable = str(python_bin if python_bin.exists() else Path(sys.executable))
    with TemporaryDirectory(prefix="mx-trade-review-") as temp_dir:
        try:
            completed = subprocess.run(
                [executable, str(skill_script), query, temp_dir],
                check=False,
                capture_output=True,
                env=os.environ.copy(),
                text=True,
                timeout=35,
            )
        except subprocess.TimeoutExpired:
            return {
                "available": False,
                "summary": "妙想资讯搜索超时，稍后可重试。",
                "points": [],
                "source": "mx-search",
            }

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "妙想资讯搜索失败").strip()
            return {
                "available": False,
                "summary": message[:180],
                "points": [],
                "source": "mx-search",
            }

        text_files = sorted(Path(temp_dir).glob("mx_search_*.txt"))
        content = text_files[0].read_text(encoding="utf-8", errors="ignore") if text_files else completed.stdout
    points = _extract_review_points(content)
    return {
        "available": True,
        "summary": points[0] if points else "妙想已完成检索，但暂无可提炼的交易相关信息。",
        "points": points[:5],
        "source": "mx-search",
    }


def _extract_review_points(content: str) -> list[str]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        structured = _collect_review_strings(parsed)
        if structured:
            return structured[:5]

    cleaned: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip(" \t\r\n-•#，。,\"")
        if line.startswith(("title\":", "content\":")):
            line = line.split(":", 1)[-1].strip(" \t\r\n-•#，。,\"")
        line = _clean_review_text(line.replace("\\n", " "))
        if not line or len(line) < 8:
            continue
        if line.startswith(("✅", "📄", "搜索结果", "日期:", "类型:", "证券:", "机构:")):
            continue
        if any(term in line for term in ("利好", "利空", "风险", "公告", "研报", "机构", "业绩", "资金", "监管", "订单", "政策", "减持", "增持", "分红")):
            cleaned.append(line[:120])
        if len(cleaned) >= 6:
            break
    if cleaned:
        return cleaned
    fallback = [
        _clean_review_text(line.strip())[:120]
        for line in content.splitlines()
        if len(_clean_review_text(line.strip())) >= 12 and not line.strip().startswith(("✅", "📄"))
    ]
    return fallback[:5]


def _collect_review_strings(value: Any) -> list[str]:
    candidates: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"title", "content", "summary", "answer"} and isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    result: list[str] = []
    for text in candidates:
        normalized = _clean_review_text(text)
        if len(normalized) < 10:
            continue
        if normalized in result:
            continue
        result.append(normalized[:120])
        if len(result) >= 6:
            break
    return result


def _clean_review_text(text: str) -> str:
    value = re.sub(r"<[^>]+>", " ", text)
    value = value.replace("|", " ").replace("\\n", " ")
    value = re.sub(r"\s+", " ", value).strip(" -，。,.;；")
    boilerplates = [
        "本公司及董事会全体成员保证",
        "不存在虚假记载",
        "对其内容的真实性",
    ]
    for marker in boilerplates:
        index = value.find(marker)
        if index > 12:
            value = value[:index].strip(" -，。,.;；")
    return value[:120]


def _build_ai_recommendation(item: dict[str, Any], points: list[str]) -> dict[str, Any]:
    price = _safe_float(item.get("price") or item.get("current_price") or item.get("prev_close"))
    prev_close = _safe_float(item.get("prev_close")) or price
    change = _safe_float(item.get("change_pct"))
    score = _safe_float(item.get("score") or item.get("adjusted_score") or item.get("candidate_score"))
    rise_speed = _safe_float(item.get("rise_1m_pct") or item.get("rise_speed_pct"))
    amount = _safe_float(item.get("turnover_1m") or item.get("min2_amount"))
    active_buy = _safe_float(item.get("active_buy_ratio"))
    turnover = _safe_float(item.get("turnover_rate"))
    text = " ".join(str(point) for point in points)
    positive_words = ("增持", "回购", "中标", "订单", "分红", "利好", "政策", "需求", "超预期", "改善", "上调")
    negative_words = ("减持", "处罚", "监管", "诉讼", "亏损", "下滑", "承压", "取消", "利空", "风险", "警示", "问询")
    positive_hits = [word for word in positive_words if word in text]
    negative_hits = [word for word in negative_words if word in text]
    event_bias = len(positive_hits) - len(negative_hits) * 1.3

    base_points = 0
    if score >= 78:
        base_points += 2
    if rise_speed >= 0.5:
        base_points += 1
    if amount >= 5_000_000:
        base_points += 1
    if active_buy >= 0.52:
        base_points += 1
    if change >= 7 or turnover >= 18:
        base_points -= 2
    if active_buy and active_buy < 0.45:
        base_points -= 1
    adjusted = base_points + event_bias

    action = "可盯"
    if change >= 7.5:
        action = "不追"
    elif adjusted >= 4:
        action = "可试"
    elif adjusted <= 0:
        action = "等确认"
    if negative_hits and action == "可试":
        action = "等回踩"
    if negative_hits and adjusted < 1:
        action = "不追"

    entry_low = price * (0.997 if action == "可试" else 0.988)
    entry_high = price * (1.002 if action == "可试" else 0.998)
    stop = price * (0.972 if negative_hits else 0.976)
    watch = price * (1.018 if action == "可试" else 1.012)
    reason = "资讯面无明显负反馈，按量价条件执行。"
    if positive_hits and not negative_hits:
        reason = f"资讯面偏正向：{', '.join(positive_hits[:2])}，允许按计划试错。"
    elif negative_hits:
        reason = f"资讯面提示风险：{', '.join(negative_hits[:2])}，买点需后移或等待回踩确认。"
    elif not points:
        reason = "暂未检索到有效资讯，AI 不加分，维持谨慎。"

    return {
        "action": action,
        "entry": f"{entry_low:.2f}-{entry_high:.2f}" if price else "--",
        "stop": f"{stop:.2f}" if price else "--",
        "watch": f"{watch:.2f}" if price else "--",
        "bias": "positive" if event_bias > 0 else "negative" if event_bias < 0 else "neutral",
        "reason": reason,
        "positive_hits": positive_hits[:3],
        "negative_hits": negative_hits[:3],
    }


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _legacy_query(**params: Any) -> dict[str, list[str]]:
    query: dict[str, list[str]] = {}
    for key, value in params.items():
        if isinstance(value, bool):
            query[key] = ["1" if value else "0"]
        else:
            query[key] = [str(value)]
    return query
