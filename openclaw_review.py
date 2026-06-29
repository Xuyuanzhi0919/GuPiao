from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


LOG_PATH = Path(__file__).parent / "logs" / "openclaw.log"


def run_openclaw_strategy_review(context: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    command = shutil.which("openclaw")
    if not command:
        return _unavailable(context, "未找到 openclaw 命令")

    prompt = _build_prompt(context)
    session_key = f"agent:main:gupiao-stock-review-{context.get('code') or 'unknown'}-{int(time.time() * 1000)}"
    agent_timeout = max(20, timeout - 10)
    process_timeout = timeout + 30
    started = time.time()
    try:
        completed = subprocess.run(
            [
                command,
                "agent",
                "--local",
                "--agent",
                "main",
                "--session-key",
                session_key,
                "--json",
                "--thinking",
                "minimal",
                "--timeout",
                str(agent_timeout),
                "--message",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )
    except subprocess.TimeoutExpired:
        return _unavailable(context, "OpenClaw 分析超时")
    except Exception as error:
        return _unavailable(context, f"{error.__class__.__name__}: {error}")

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "OpenClaw 调用失败").strip()
        return _unavailable(context, _openclaw_error_summary(message, "OpenClaw 调用失败"))

    try:
        output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
        outer = _parse_outer_json(output)
        text = str((outer.get("payloads") or [{}])[0].get("text") or "")
        if not _looks_like_decision_text(text):
            return _unavailable(context, _openclaw_payload_error_summary(text, outer, completed.stderr))
        decision = _parse_decision_text(text)
    except Exception as error:
        return _unavailable(context, f"OpenClaw 返回解析失败: {error.__class__.__name__}: {error}")

    return _normalize_decision(context, decision, elapsed_ms=int((time.time() - started) * 1000))


def run_openclaw_limit_up_focus_review(context: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
    command = shutil.which("openclaw")
    if not command:
        return _batch_unavailable(context, "未找到 openclaw 命令")

    timeout = timeout or int(os.environ.get("OPENCLAW_LIMIT_UP_TIMEOUT_SEC", "600"))
    prompt = _build_limit_up_focus_prompt(context)
    session_key = f"agent:main:gupiao-limit-up-{context.get('date') or 'latest'}-{int(time.time() * 1000)}"
    agent_timeout = max(20, timeout - 10)
    process_timeout = timeout + 30
    started = time.time()
    try:
        completed = subprocess.run(
            [
                command,
                "agent",
                "--local",
                "--agent",
                "main",
                "--session-key",
                session_key,
                "--json",
                "--thinking",
                "minimal",
                "--timeout",
                str(agent_timeout),
                "--message",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )
    except subprocess.TimeoutExpired:
        _log_openclaw_event("limit-up-timeout", {"timeout": timeout, "context_date": context.get("date")})
        return _batch_unavailable(context, "OpenClaw 涨停复盘超时")
    except Exception as error:
        _log_openclaw_event("limit-up-exception", {"error": f"{error.__class__.__name__}: {error}", "context_date": context.get("date")})
        return _batch_unavailable(context, f"{error.__class__.__name__}: {error}")

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "OpenClaw 涨停复盘调用失败").strip()
        _log_openclaw_event("limit-up-returncode", {"returncode": completed.returncode, "message": message[-2000:], "context_date": context.get("date")})
        return _batch_unavailable(context, _openclaw_error_summary(message, "OpenClaw 涨停复盘调用失败"))

    try:
        output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
        outer = _parse_outer_json(output)
        text = _extract_openclaw_text(outer)
        if not _looks_like_decision_text(text):
            _log_openclaw_event("limit-up-invalid-output", {"text": text[-2000:], "stderr": (completed.stderr or "")[-2000:], "meta": outer.get("meta"), "context_date": context.get("date")})
            return _batch_unavailable(context, _openclaw_payload_error_summary(text, outer, completed.stderr))
        decision = _parse_decision_text(text)
    except Exception as error:
        _log_openclaw_event("limit-up-parse-error", {"error": f"{error.__class__.__name__}: {error}", "stdout": (completed.stdout or "")[-2000:], "stderr": (completed.stderr or "")[-2000:], "context_date": context.get("date")})
        return _batch_unavailable(context, f"OpenClaw 涨停复盘解析失败: {error.__class__.__name__}: {error}")

    return _normalize_limit_up_focus_decision(context, decision, elapsed_ms=int((time.time() - started) * 1000))


def _build_prompt(context: dict[str, Any]) -> str:
    compact_context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    has_position = bool(context.get("position"))
    position_rule = (
        "当前股票已有持仓，必须按持仓管理输出次日交易预案："
        "position_advice必须包含次日处理规则，至少覆盖高开/平开/低开、冲高不过观察位、跌破止损或成本、站稳关键价后的持有或减仓动作；"
        "watch字段必须写成可执行观察条件，例如'次日能否站稳xx以上/跌破xx减仓'，不能只写单个价格；"
        "entry字段应表达加仓或不加仓条件，stop字段必须给清晰止损/减仓位。"
        if has_position
        else
        "当前股票未确认持仓，按候选/关注标的输出是否可观察、可试、放弃，以及买点、止损和观察条件。"
    )
    return (
        "你是A股实盘策略复核助手。请只返回JSON，不要markdown，不要解释JSON外文本。"
        "你不会直接下单，只给交易复核建议。"
        "分析时必须优先结合你自己的策略文档、长期记忆、A股/量化/选股相关skills和可用工具经验；"
        "如果这些资料中没有明确规则，就明确基于输入上下文推断，不能编造不存在的策略依据。"
        "重点按实盘短线交易审查：买点质量、板块共振、量价承接、持仓成本、止损纪律、仓位风险、疑似出货风险。"
        f"{position_rule}"
        "输出字段必须包含："
        "action(hold/add/reduce/sell/watch), confidence(0-100), summary, entry, stop, watch, "
        "position_advice, risk_level(normal/caution/high), reasons数组, risks数组。"
        f"输入股票上下文：{compact_context}"
    )


def _build_limit_up_focus_prompt(context: dict[str, Any]) -> str:
    compact_context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return (
        "你是A股短线打板收盘复盘agent。只返回JSON，不要markdown。"
        "快速模式：禁止工具、联网、读文件，只基于输入JSON。"
        "目标：先扫描输入里的全部limit_up_pool涨停股票，再从全量中选明日盯盘票。"
        "用户无300/301/688/689/920/8/4开头交易权限，这些代码不得进core/watch。"
        "宁缺毋滥，核心可给1到5只，观察可给2到8只；次日符合条件的盘中机会都提醒，系统复盘只记录前2只；风险剔除最多5只。"
        "输出必须是紧凑JSON：summary,market_view,items。"
        "items每项只含code,name,tier(core/watch/avoid),confidence,action(watch/avoid),next_day_plan,entry,stop,watch,risk_level,summary。"
        "每个文字字段不超过45个汉字；不要输出reasons/risks数组。"
        f"输入收盘上下文：{compact_context}"
    )


def _parse_decision_text(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _parse_outer_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError("empty OpenClaw output")


def _extract_openclaw_text(outer: dict[str, Any]) -> str:
    payloads = outer.get("payloads") if isinstance(outer, dict) else None
    text = str((payloads or [{}])[0].get("text") or "")
    if _looks_like_decision_text(text):
        return text
    meta = outer.get("meta") if isinstance(outer, dict) else {}
    if isinstance(meta, dict):
        for key in ("finalAssistantVisibleText", "finalAssistantRawText"):
            candidate = str(meta.get(key) or "")
            if _looks_like_decision_text(candidate):
                return candidate
    return text


def _normalize_decision(context: dict[str, Any], decision: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    action = str(decision.get("action") or "watch").lower()
    if action not in {"hold", "add", "reduce", "sell", "watch"}:
        action = "watch"
    risk_level = str(decision.get("risk_level") or "normal").lower()
    if risk_level not in {"normal", "caution", "high"}:
        risk_level = "normal"
    confidence = _number(decision.get("confidence"), 0)
    return {
        "code": context.get("code", ""),
        "name": context.get("name", ""),
        "available": True,
        "source": "openclaw",
        "elapsed_ms": elapsed_ms,
        "decision": {
            "action": action,
            "confidence": max(0, min(100, int(confidence))),
            "summary": str(decision.get("summary") or "OpenClaw 已完成策略复核。"),
            "entry": _stringify(decision.get("entry")),
            "stop": _stringify(decision.get("stop")),
            "watch": _stringify(decision.get("watch")),
            "position_advice": str(decision.get("position_advice") or ""),
            "risk_level": risk_level,
            "reasons": _string_list(decision.get("reasons")),
            "risks": _string_list(decision.get("risks")),
        },
    }


def _normalize_limit_up_focus_decision(context: dict[str, Any], decision: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    raw_items = decision.get("items")
    if not isinstance(raw_items, list):
        raw_items = decision.get("focus") if isinstance(decision.get("focus"), list) else []
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        code = "".join(ch for ch in str(raw.get("code") or "") if ch.isdigit())[:6]
        if not code:
            continue
        tier = str(raw.get("tier") or "watch").lower()
        if tier not in {"core", "watch", "avoid"}:
            tier = "watch"
        risk_level = str(raw.get("risk_level") or "normal").lower()
        if risk_level not in {"normal", "caution", "high"}:
            risk_level = "normal"
        action = str(raw.get("action") or ("avoid" if tier == "avoid" else "watch")).lower()
        if action not in {"watch", "avoid"}:
            action = "watch"
        confidence = _number(raw.get("confidence"), 0)
        items.append(
            {
                "code": code,
                "name": str(raw.get("name") or code),
                "tier": tier,
                "confidence": max(0, min(100, int(confidence))),
                "action": action,
                "next_day_plan": _stringify(raw.get("next_day_plan")),
                "entry": _stringify(raw.get("entry")),
                "stop": _stringify(raw.get("stop")),
                "watch": _stringify(raw.get("watch")),
                "risk_level": risk_level,
                "summary": str(raw.get("summary") or ""),
                "reasons": _string_list(raw.get("reasons")),
                "risks": _string_list(raw.get("risks")),
            }
        )
    return {
        "available": True,
        "source": "openclaw",
        "elapsed_ms": elapsed_ms,
        "date": context.get("date", ""),
        "next_date": context.get("next_date", ""),
        "summary": str(decision.get("summary") or "OpenClaw 已完成涨停复盘。"),
        "market_view": str(decision.get("market_view") or ""),
        "items": items,
    }


def _unavailable(context: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "code": context.get("code", ""),
        "name": context.get("name", ""),
        "available": False,
        "source": "openclaw",
        "error": message,
        "decision": None,
    }


def _batch_unavailable(context: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "available": False,
        "source": "openclaw",
        "date": context.get("date", ""),
        "next_date": context.get("next_date", ""),
        "error": message,
        "summary": message,
        "market_view": "",
        "items": [],
    }


def _openclaw_error_summary(message: str, fallback: str) -> str:
    text = (message or "").strip()
    if not text:
        return fallback
    lower = text.lower()
    if "econnreset" in lower or "und_err_socket" in lower or "network connection error" in lower or "connection error" in lower:
        return "OpenClaw provider 网络连接失败，当前模型通道断开或被重置"
    if "timeout" in lower:
        return "OpenClaw provider 响应超时"
    if "auth" in lower or "unauthorized" in lower:
        return "OpenClaw 认证或额度异常"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else text)[-300:]


def _looks_like_decision_text(text: str) -> bool:
    stripped = (text or "").lstrip()
    return stripped.startswith("{") or stripped.startswith("[") or stripped.startswith("```")


def _openclaw_payload_error_summary(text: str, outer: dict[str, Any], stderr: str | None = None) -> str:
    combined = "\n".join(part for part in [text or "", stderr or ""] if part)
    meta = outer.get("meta") if isinstance(outer, dict) else {}
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else {}
    timeout_phase = str(agent_meta.get("timeoutPhase") or "")
    liveness = str(agent_meta.get("livenessState") or "")
    if timeout_phase == "provider" or "request timed out before a response was generated" in combined.lower():
        return "OpenClaw provider 响应超时，模型没有生成有效结果"
    if liveness == "blocked":
        return "OpenClaw 当前模型通道阻塞，未生成有效结果"
    return _openclaw_error_summary(combined, "OpenClaw 未生成有效结果")


def _log_openclaw_event(event: str, payload: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.time(), "event": event, **payload}
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _number(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stringify(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value if item)
    if value is None:
        return "--"
    return str(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()][:8]
    if value:
        return [str(value)]
    return []
