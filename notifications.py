from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


@dataclass
class Notification:
    ts: float
    kind: str
    code: str
    name: str
    title: str
    body: str
    channel: str
    sent: bool
    error: str = ""
    elapsed_ms: float = 0.0
    target: str = ""


class NotificationCenter:
    def __init__(self, path: Path, config_path: Path | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path = config_path or path.with_name("notification_config.json")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config = self._load_config()
        self.bark_url = str(self._config.get("bark_url") or os.environ.get("BARK_URL", "")).rstrip("/")
        self.backup_bark_urls = _split_urls(str(self._config.get("backup_bark_urls") or os.environ.get("BACKUP_BARK_URLS", "")))
        self.omni_bark_token = str(self._config.get("omni_bark_token") or os.environ.get("OMNI_BARK_TOKEN", "")).strip()
        self.omni_bark_channel_id = str(self._config.get("omni_bark_channel_id") or os.environ.get("OMNI_BARK_CHANNEL_ID", "")).strip()
        self._recent: list[Notification] = self._load()
        self._last_sent: dict[str, float] = self._restore_cooldowns()
        self._last_attempt: dict[str, float] = {}
        self._lock = threading.Lock()

    def notify_signal(self, signal: Any) -> None:
        if not self._rule_enabled("signal_a_enabled"):
            return
        grade = getattr(signal, "grade", "")
        if grade != "A":
            return
        item = signal.to_dict() if hasattr(signal, "to_dict") else dict(signal)
        title = f"A级冲高预警 {item.get('name', item.get('code', ''))}"
        body = f"{item.get('sector', '--')} · 评分{item.get('score', '--')} · 涨幅{float(item.get('change_pct', 0)):.2f}% · 1m{float(item.get('rise_1m_pct', 0)):.2f}% · 等回封/站稳，不直接追"
        self._emit("signal-a", str(item.get("code", "")), str(item.get("name", "")), title, body)

    def notify_watchlist_signal(self, signal: Any, watchlist: set[str]) -> None:
        if not self._rule_enabled("watchlist_signal_enabled"):
            return
        item = signal.to_dict() if hasattr(signal, "to_dict") else dict(signal)
        code = str(item.get("code", ""))
        if code not in watchlist:
            return
        title = f"关注异动 {item.get('name', code)}"
        body = f"{item.get('grade', '--')}级 · {item.get('sector', '--')} · 评分{item.get('score', '--')} · 1m{float(item.get('rise_1m_pct', 0)):.2f}%"
        self._emit("watchlist-signal", code, str(item.get("name", "")), title, body)

    def notify_focus_candidates(self, payload: dict[str, Any]) -> None:
        if not self._rule_enabled("focus_strong_enabled"):
            return
        for item in payload.get("candidates", []):
            if item.get("quality_level") != "strong":
                continue
            title = f"强关注 {item.get('name', item.get('code', ''))}"
            body = item.get("explanation") or f"{item.get('sector', '--')} · 评分{item.get('adjusted_score') or item.get('candidate_score', '--')}"
            self._emit("focus-strong", str(item.get("code", "")), str(item.get("name", "")), title, body)

    def notify_sector_pulse(self, payload: dict[str, Any]) -> None:
        if not self._rule_enabled("sector_pulse_enabled"):
            return
        sectors = payload.get("sector_heat") or payload.get("health", {}).get("sector_heat") or []
        for item in sectors:
            if not isinstance(item, dict):
                continue
            count = int(item.get("count", 0) or 0)
            sector = str(item.get("sector", ""))
            threshold = int(self._config["sector_pulse_threshold"])
            if not sector or count < threshold:
                continue
            active_top = int(item.get("active_top", 0) or 0)
            title = f"板块共振 {sector}"
            body = f"{count} 只候选{f' · {active_top} 只精盯' if active_top else ''} · 阈值 {threshold}"
            self._emit("sector-pulse", sector, sector, title, body)

    def notify_position_risk(self, item: dict[str, Any]) -> Notification:
        code = str(item.get("code", ""))
        name = str(item.get("name") or code)
        action = str(item.get("action") or "持仓风控")
        price = item.get("price", "--")
        reason = str(item.get("reason") or "")
        title = f"{action} {name}"
        body = f"{code} · 现价{price} · {reason}".strip()
        return self._emit("position-risk", code, name, title, body)

    def notify_execution_alert(self, item: dict[str, Any]) -> Notification:
        code = str(item.get("code", ""))
        name = str(item.get("name") or code)
        action = str(item.get("action") or "执行提醒")
        price = item.get("price", "--")
        reason = str(item.get("reason") or "")
        title = f"{action} {name}"
        body = f"{code} · 现价{price} · {reason}".strip()
        return self._emit("execution-alert", code, name, title, body)

    def notify_limit_up_signal(self, item: dict[str, Any]) -> Notification:
        if not self._rule_enabled("limit_up_signal_enabled"):
            return self._disabled("limit-up-signal", item, "打板信号", "打板信号提醒已关闭")
        code = str(item.get("code", ""))
        name = str(item.get("name") or code)
        action = str(item.get("action") or "打板观察")
        price = item.get("price", "--")
        score = item.get("score", "--")
        reasons = "；".join(str(reason) for reason in item.get("reasons", [])[:3])
        risk_note = str(item.get("risk_note") or "")
        title = f"{action} {name}"
        body = f"{code} · 现价{price} · 分{score} · {reasons} · {risk_note}".strip()
        return self._emit("limit-up-signal", code, name, title, body)

    def notify_limit_up_focus_report(self, payload: dict[str, Any]) -> Notification:
        if not self._rule_enabled("limit_up_focus_enabled"):
            return self._disabled("limit-up-focus", {"code": str(payload.get("date") or ""), "name": "明日重点"}, "明日重点", "明日重点提醒已关闭")
        summary = payload.get("summary") or {}
        focus = payload.get("focus") or []
        sectors = payload.get("sectors") or []
        openclaw = payload.get("openclaw_review") or {}
        if not int(summary.get("zt_count") or 0) and not focus and not sectors:
            return self._disabled(
                "limit-up-focus",
                {"code": str(payload.get("date") or ""), "name": "明日重点"},
                "明日重点",
                "复盘数据为空，已阻止空报告推送",
            )
        sector = sectors[0] if sectors else {}
        core = [item for item in focus if item.get("openclaw_tier") == "core"]
        watch = [item for item in focus if item.get("openclaw_tier") == "watch"]
        avoid = [item for item in focus if item.get("openclaw_tier") == "avoid"]
        display_focus = core or focus
        top_focus = "；".join(_focus_label(item) for item in display_focus[:8]) or "暂无重点候选"
        top_sector = f"{sector.get('sector')} {sector.get('limit_count')}只" if sector else "暂无主线"
        title = f"明日核心 {payload.get('next_date', '')}" if core else f"明日重点 {payload.get('next_date', '')}"
        openclaw_available = bool(openclaw.get("available"))
        market_view = str(openclaw.get("market_view") or openclaw.get("summary") or "") if openclaw_available else ""
        fallback_part = "OpenClaw未完成，已按规则复盘 · " if openclaw and not openclaw_available else ""
        ai_part = (
            f"核心{len(core)} / 观察{len(watch)} / 剔除{len(avoid)} · "
            if openclaw_available
            else f"规则重点{len(focus)} · "
        )
        view_part = f"{market_view[:90]} · " if market_view else fallback_part
        body = (
            f"今日涨停{summary.get('zt_count', 0)} / 明日重点{summary.get('focus_count', 0)} / 强板块{summary.get('strong_sector_count', 0)} · "
            f"{ai_part}"
            f"主线：{top_sector} · "
            f"重点：{top_focus} · "
            f"{view_part}"
            "明日纪律：只盯昨日涨停池，出现强承接/封板确认再推买点"
        )
        return self._emit("limit-up-focus", str(payload.get("date") or ""), "明日重点", title, body, bypass_cooldown=True)

    def notify_next_day_buy_signal(self, item: dict[str, Any]) -> Notification:
        if not self._rule_enabled("next_day_buy_enabled"):
            return self._disabled("next-day-buy", item, "隔日买点", "次日买点提醒已关闭")
        code = str(item.get("code", ""))
        name = str(item.get("name") or code)
        state = str(item.get("state") or "买点确认")
        tier = str(item.get("openclaw_tier") or "")
        rank = int(item.get("official_rank") or 0)
        price = item.get("price", "--")
        score = item.get("score", "--")
        reasons = "；".join(str(reason) for reason in item.get("reasons", [])[:4])
        source = _kline_source_label(str(item.get("kline_source") or ""))
        minute = str(item.get("kline_last_time") or "")
        minute_part = f" · 分时{source}{' ' + minute[-5:] if minute else ''}" if source else ""
        sealed_stage = bool(item.get("sealed_today") or state in {"首封确认", "回封确认"})
        stage = "正式买点" if sealed_stage else "试探买点"
        if tier == "avoid":
            if not self._rule_enabled("next_day_risk_enabled"):
                return Notification(time.time(), "next-day-risk", code, name, "剔除票异动", "风险观察提醒已关闭", "disabled", False, "风险观察提醒已关闭")
            title = f"剔除票异动 {name}"
            body = f"{code} · {state} · 现价{price} · 分{score} · 风险剔除票，仅观察不追 · {reasons}".strip()
            return self._emit("next-day-risk", code, name, title, body)
        prefix = f"{stage}#{rank}" if rank else ("封板确认" if sealed_stage else "试探观察")
        if tier == "core":
            title = f"{prefix} 核心 {name}"
        elif tier == "watch":
            title = f"{prefix} 观察 {name}"
        else:
            title = f"{prefix} {name}"
        if rank:
            trigger = str(item.get("official_trigger_time") or item.get("today_first_limit_time") or minute[-5:] or "")
            entry = item.get("official_entry_price") or item.get("price") or "--"
            title = f"{stage}#{rank} {name} {state}"
            discipline = "封板/回封确认，可按纪律执行" if sealed_stage else "早盘试探，不封板或跌破开盘价立刻放弃"
            body = f"{code} · {trigger} · 入场{entry} · 现价{price} · 分{score}{minute_part} · {reasons} · {discipline}".strip()
        else:
            body = f"{code} · {state} · 现价{price} · 分{score}{minute_part} · {reasons} · {item.get('risk_note', '')}".strip()
        return self._emit("next-day-buy", code, name, title, body, bypass_cooldown=bool(rank), critical=bool(rank))

    def notify_next_day_cancel_signal(self, item: dict[str, Any], t1_locked: bool = False) -> Notification:
        if not self._rule_enabled("next_day_buy_enabled"):
            return self._disabled("next-day-cancel", item, "买点撤退", "次日买点提醒已关闭")
        code = str(item.get("code", ""))
        name = str(item.get("name") or code)
        state = str(item.get("state") or "买点转弱")
        price = item.get("price", "--")
        reasons = "；".join(str(reason) for reason in item.get("reasons", [])[:4])
        if t1_locked:
            title = f"T+1风险观察 {name}"
            body = f"{code} · 今日已成交不可卖 · {state} · 现价{price} · 明日按开盘承接处理 · {reasons}".strip()
            return self._emit("next-day-t1-risk", code, name, title, body, bypass_cooldown=True, critical=True)
        title = f"取消买点 {name}"
        body = f"{code} · {state} · 现价{price} · 分时/承接转弱，未成交则放弃 · {reasons}".strip()
        return self._emit("next-day-cancel", code, name, title, body, bypass_cooldown=True, critical=True)

    def latest(self, limit: int = 50) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._recent[:limit]]

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        bool_keys = {
            "enabled",
            "signal_a_enabled",
            "focus_strong_enabled",
            "watchlist_signal_enabled",
            "sector_pulse_enabled",
            "execution_alert_enabled",
            "limit_up_signal_enabled",
            "limit_up_focus_enabled",
            "next_day_buy_enabled",
            "next_day_risk_enabled",
        }
        int_bounds = {
            "cooldown_sec": (30, 86400),
            "failed_retry_sec": (3, 600),
            "sector_pulse_threshold": (1, 50),
        }
        for key in bool_keys:
            if key in values:
                self._config[key] = parse_bool(values[key])
        if "bark_url" in values:
            self._config["bark_url"] = _normalize_bark_url(str(values.get("bark_url") or ""))
            self.bark_url = self._config["bark_url"]
        if "backup_bark_urls" in values:
            self._config["backup_bark_urls"] = ",".join(_normalize_bark_url(url) for url in _split_urls(str(values.get("backup_bark_urls") or "")) if _normalize_bark_url(url))
            self.backup_bark_urls = _split_urls(self._config["backup_bark_urls"])
        if "omni_bark_token" in values:
            self._config["omni_bark_token"] = str(values.get("omni_bark_token") or "").strip()
            self.omni_bark_token = self._config["omni_bark_token"]
        if "omni_bark_channel_id" in values:
            self._config["omni_bark_channel_id"] = str(values.get("omni_bark_channel_id") or "").strip()
            self.omni_bark_channel_id = self._config["omni_bark_channel_id"]
        if "omni_bark_sender" in values:
            self._config["omni_bark_sender"] = str(values.get("omni_bark_sender") or "").strip() or "GuPiao"
        if "omni_bark_api_base" in values:
            self._config["omni_bark_api_base"] = str(values.get("omni_bark_api_base") or "").strip().rstrip("/") or "http://www.ggsuper.com.cn/push/api/v1"
        if "critical_sound" in values:
            self._config["critical_sound"] = str(values.get("critical_sound") or "").strip()
        for key, (min_value, max_value) in int_bounds.items():
            if key in values:
                self._config[key] = clamp_int(values[key], min_value, max_value, self._config[key])
        self._save_config()
        self._last_sent = self._restore_cooldowns()
        return dict(self._config)

    def test(self) -> Notification:
        now_label = time.strftime("%H:%M:%S")
        return self._emit("test", f"test-{int(time.time())}", "测试推送", "雷达测试推送", f"Bark 通道验证 {now_label}", bypass_cooldown=True)

    def status(self, watchlist_count: int = 0) -> dict[str, Any]:
        enabled = bool(self._config["enabled"])
        return {
            "enabled": enabled,
            "bark_configured": bool(self._push_configured()),
            "bark_url": mask_url(self.bark_url),
            "omni_bark_configured": bool(self.omni_bark_token),
            "omni_bark_token": mask_token(self.omni_bark_token),
            "omni_bark_channel_id": mask_token(self.omni_bark_channel_id),
            "backup_bark_count": len(self.backup_bark_urls),
            "cooldown_sec": self._config["cooldown_sec"],
            "failed_retry_sec": self._config["failed_retry_sec"],
            "recent_count": len(self._recent),
            "cooldown_key_count": len(self._last_sent),
            "sector_pulse_threshold": self._config["sector_pulse_threshold"],
            "notification_health": self._health(),
            "config": dict(self._config),
            "rules": [
                {"key": "signal-a", "label": "首次 A 级预警", "enabled": enabled and self._config["signal_a_enabled"], "description": "首次 A 级只作冲高预警，默认关闭远程推送"},
                {"key": "focus-strong", "label": "强关注候选", "enabled": enabled and self._config["focus_strong_enabled"], "description": "候选池出现强关注标的时提醒"},
                {
                    "key": "watchlist-signal",
                    "label": "关注股异动",
                    "enabled": enabled and self._config["watchlist_signal_enabled"] and watchlist_count > 0,
                    "description": f"关注池 {watchlist_count} 只，出现实时异动时提醒",
                },
                {
                    "key": "sector-pulse",
                    "label": "板块共振",
                    "enabled": enabled and self._config["sector_pulse_enabled"],
                    "description": f"候选板块数量达到 {self._config['sector_pulse_threshold']} 只时提醒",
                },
                {"key": "execution-alert", "label": "执行池提醒", "enabled": enabled and self._config["execution_alert_enabled"], "description": "今日执行池出现可参与或持仓风控时提醒"},
                {"key": "limit-up-signal", "label": "打板总开关", "enabled": enabled and self._config["limit_up_signal_enabled"], "description": "控制全部打板相关推送"},
                {"key": "limit-up-focus", "label": "明日核心", "enabled": self._rule_enabled("limit_up_focus_enabled"), "description": "收盘后推送 OpenClaw 核心盯盘和市场观点"},
                {"key": "next-day-buy", "label": "次日买点", "enabled": self._rule_enabled("next_day_buy_enabled"), "description": "昨日涨停池出现核心/观察买点时推送"},
                {"key": "next-day-risk", "label": "剔除票异动", "enabled": self._rule_enabled("next_day_risk_enabled"), "description": "风险剔除票异动时仅作风险提醒"},
            ],
        }

    def _disabled(self, kind: str, item: dict[str, Any], title: str, body: str) -> Notification:
        notification = Notification(
            time.time(),
            kind,
            str(item.get("code", "")),
            str(item.get("name", "")),
            title,
            body,
            "disabled",
            False,
            body,
        )
        self._record(notification)
        return notification

    def _emit(self, kind: str, code: str, name: str, title: str, body: str, bypass_cooldown: bool = False, critical: bool = False) -> Notification:
        if not code or not self._config["enabled"]:
            notification = Notification(time.time(), kind, code, name, title, body, "disabled", False, "通知总开关关闭")
            self._record(notification)
            return notification
        key = f"{kind}:{code}"
        now = time.time()
        if not bypass_cooldown and now - self._last_sent.get(key, 0) < self._config["cooldown_sec"]:
            return Notification(now, kind, code, name, title, body, "cooldown", False, "冷却中")
        if not bypass_cooldown and now - self._last_attempt.get(key, 0) < self._config["failed_retry_sec"]:
            return Notification(now, kind, code, name, title, body, "cooldown", False, "失败重试冷却中")
        self._last_attempt[key] = now

        sent = False
        error = ""
        elapsed_ms = 0.0
        target = ""
        if self._push_configured():
            try:
                elapsed_ms, target = self._send_push(title, body, critical=critical)
                sent = True
                self._last_sent[key] = now
                self._last_attempt.pop(key, None)
            except Exception as exc:  # noqa: BLE001 - notification failures should not break market loop
                error = f"{exc.__class__.__name__}: {exc}"
        else:
            self._last_sent[key] = now

        notification = Notification(now, kind, code, name, title, body, "bark" if self._push_configured() else "record", sent, error, round(elapsed_ms, 1), target)
        self._record(notification)
        return notification

    def _send_push(self, title: str, body: str, critical: bool = False) -> tuple[float, str]:
        last_error: Exception | None = None
        attempts = max(1, int(os.environ.get("BARK_ATTEMPTS", "1")))
        timeout = max(1.0, float(os.environ.get("BARK_TIMEOUT_SEC", "2")))
        started = time.time()
        for index, base in enumerate(self._bark_urls(), 1):
            url = _bark_request_url(base, title, body, critical=critical, sound=str(self._config.get("critical_sound") or "alarm"))
            request = Request(url, headers={"User-Agent": "GuPiao-Radar/1.0"})
            for attempt in range(attempts):
                try:
                    with urlopen(request, timeout=timeout) as response:
                        response.read()
                    return (time.time() - started) * 1000, f"bark#{index}"
                except Exception as error:
                    last_error = error
                    if attempt + 1 < attempts:
                        time.sleep(0.3 * (attempt + 1))
        if self.omni_bark_token:
            for attempt in range(attempts):
                try:
                    self._send_omni_bark(title, body, timeout=timeout)
                    return (time.time() - started) * 1000, "omni-bark"
                except Exception as error:
                    last_error = error
                    if attempt + 1 < attempts:
                        time.sleep(0.3 * (attempt + 1))
        if last_error:
            raise last_error
        raise RuntimeError("未配置 Bark 或全能消息推送 Bark")

    def _send_omni_bark(self, title: str, body: str, timeout: float) -> None:
        base = str(self._config.get("omni_bark_api_base") or "http://www.ggsuper.com.cn/push/api/v1").rstrip("/")
        sender = str(self._config.get("omni_bark_sender") or "GuPiao")
        if self.omni_bark_channel_id:
            endpoint = f"{base}/sendChannelMsg2_New.php"
            payload = {
                "title": title,
                "msg": body,
                "token": self.omni_bark_token,
                "channel_id": self.omni_bark_channel_id,
            }
            request = Request(
                endpoint,
                data=urlencode(payload).encode("utf-8"),
                headers={"User-Agent": "GuPiao-Radar/1.0", "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                method="POST",
            )
        else:
            endpoint = f"{base}/sendMsg3_New.php"
            payload = {
                "title": title,
                "msg": body,
                "url": "",
                "token": self.omni_bark_token,
                "issecure": 0,
                "sender": sender,
            }
            request = Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"User-Agent": "GuPiao-Radar/1.0", "Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "ignore")
        _validate_omni_bark_response(raw)

    def _bark_urls(self) -> list[str]:
        return [url for url in [self.bark_url, *self.backup_bark_urls] if url]

    def _push_configured(self) -> bool:
        return bool(self._bark_urls() or self.omni_bark_token)

    def _health(self) -> dict[str, Any]:
        remote = [item for item in self._recent if item.channel == "bark"]
        checked = remote[:50]
        success = len([item for item in checked if item.sent])
        failed = len([item for item in checked if item.error])
        elapsed = [float(item.elapsed_ms or 0) for item in checked if item.elapsed_ms]
        consecutive_failures = 0
        for item in remote:
            if item.sent:
                break
            if item.error:
                consecutive_failures += 1
        last_failure = next((item for item in remote if item.error), None)
        last_success = next((item for item in remote if item.sent), None)
        total = success + failed
        return {
            "sample_count": total,
            "success_count": success,
            "failure_count": failed,
            "success_rate": round(success / total * 100, 1) if total else 0,
            "avg_elapsed_ms": round(sum(elapsed) / len(elapsed), 1) if elapsed else 0,
            "consecutive_failures": consecutive_failures,
            "last_error": last_failure.error if last_failure else "",
            "last_error_ts": last_failure.ts if last_failure else 0,
            "last_success_ts": last_success.ts if last_success else 0,
        }

    def _record(self, notification: Notification) -> None:
        with self._lock:
            self._recent.insert(0, notification)
            self._recent = self._recent[:200]
            self.path.write_text(
                json.dumps([asdict(item) for item in self._recent], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _load(self) -> list[Notification]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return []
            return [Notification(**item) for item in payload if isinstance(item, dict)]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return []

    def _restore_cooldowns(self) -> dict[str, float]:
        restored: dict[str, float] = {}
        now = time.time()
        for item in self._recent:
            if now - item.ts > self._config["cooldown_sec"]:
                continue
            key = f"{item.kind}:{item.code}"
            restored[key] = max(restored.get(key, 0), item.ts)
        return restored

    def _rule_enabled(self, key: str) -> bool:
        if key in {"limit_up_focus_enabled", "next_day_buy_enabled", "next_day_risk_enabled"}:
            return bool(self._config["enabled"] and self._config.get("limit_up_signal_enabled", True) and self._config.get(key, True))
        return bool(self._config["enabled"] and self._config.get(key, True))

    def _load_config(self) -> dict[str, Any]:
        config = {
            "enabled": os.environ.get("NOTIFY_ENABLED", "1") != "0",
            "signal_a_enabled": os.environ.get("NOTIFY_SIGNAL_A_ENABLED", "0") != "0",
            "focus_strong_enabled": os.environ.get("NOTIFY_FOCUS_STRONG_ENABLED", "1") != "0",
            "watchlist_signal_enabled": os.environ.get("NOTIFY_WATCHLIST_SIGNAL_ENABLED", "1") != "0",
            "sector_pulse_enabled": os.environ.get("NOTIFY_SECTOR_PULSE_ENABLED", "1") != "0",
            "execution_alert_enabled": os.environ.get("NOTIFY_EXECUTION_ALERT_ENABLED", "1") != "0",
            "limit_up_signal_enabled": os.environ.get("NOTIFY_LIMIT_UP_SIGNAL_ENABLED", "1") != "0",
            "limit_up_focus_enabled": os.environ.get("NOTIFY_LIMIT_UP_FOCUS_ENABLED", "1") != "0",
            "next_day_buy_enabled": os.environ.get("NOTIFY_NEXT_DAY_BUY_ENABLED", "1") != "0",
            "next_day_risk_enabled": os.environ.get("NOTIFY_NEXT_DAY_RISK_ENABLED", "1") != "0",
            "cooldown_sec": int(os.environ.get("NOTIFY_COOLDOWN_SEC", "300")),
            "failed_retry_sec": int(os.environ.get("NOTIFY_FAILED_RETRY_SEC", "10")),
            "sector_pulse_threshold": int(os.environ.get("NOTIFY_SECTOR_PULSE_THRESHOLD", "3")),
            "bark_url": os.environ.get("BARK_URL", "").rstrip("/"),
            "backup_bark_urls": os.environ.get("BACKUP_BARK_URLS", ""),
            "omni_bark_token": os.environ.get("OMNI_BARK_TOKEN", ""),
            "omni_bark_channel_id": os.environ.get("OMNI_BARK_CHANNEL_ID", ""),
            "omni_bark_sender": os.environ.get("OMNI_BARK_SENDER", "GuPiao"),
            "omni_bark_api_base": os.environ.get("OMNI_BARK_API_BASE", "http://www.ggsuper.com.cn/push/api/v1").rstrip("/"),
            "critical_sound": os.environ.get("BARK_CRITICAL_SOUND", "alarm"),
        }
        if not self.config_path.exists():
            return config
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                config.update({key: payload[key] for key in config if key in payload})
        except (OSError, json.JSONDecodeError):
            pass
        return config

    def _save_config(self) -> None:
        self.config_path.write_text(json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on", "开启"}


def clamp_int(value: Any, min_value: int, max_value: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(min_value, min(max_value, number))


def _focus_label(item: dict[str, Any]) -> str:
    name = item.get("name") or item.get("code") or ""
    if item.get("openclaw_score"):
        return f"{name} AI{item.get('openclaw_score')}"
    try:
        score = int(float(item.get("focus_score") or 0))
    except (TypeError, ValueError):
        score = 0
    return f"{name}{score}"


def _kline_source_label(value: str) -> str:
    return {
        "sina": "新浪",
        "cache": "缓存",
        "eastmoney-trends": "东财",
        "eastmoney-kline": "东财K",
        "tdx": "TDX",
    }.get(value, "")


def mask_url(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 18:
        return "***"
    return f"{value[:12]}...{value[-6:]}"


def mask_token(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _split_urls(value: str) -> list[str]:
    urls: list[str] = []
    for raw in value.replace("\n", ",").split(","):
        url = raw.strip().rstrip("/")
        if url:
            urls.append(url)
    return urls


def _normalize_bark_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    if not url:
        return ""
    marker = "api.day.app/"
    if marker not in url:
        return url
    prefix, rest = url.split(marker, 1)
    key = rest.split("/", 1)[0].strip()
    return f"{prefix}{marker}{key}".rstrip("/") if key else ""


def _bark_request_url(base: str, title: str, body: str, critical: bool = False, sound: str = "alarm") -> str:
    url = f"{base.rstrip('/')}/{quote(title, safe='')}/{quote(body, safe='')}"
    if not critical:
        return url
    params = {
        "level": "critical",
        "volume": "10",
        "group": "打板买点",
    }
    if sound:
        params["sound"] = sound
    return f"{url}?{urlencode(params)}"


def _validate_omni_bark_response(raw: str) -> None:
    text = (raw or "").strip()
    if not text:
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return
    code = str(payload.get("code") or "")
    if code and code != "80000000":
        message = str(payload.get("msg") or payload.get("message") or text)
        raise RuntimeError(f"全能消息推送 Bark 返回失败: {code} {message}")
