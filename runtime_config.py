from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import MONITOR_CONFIG

CONFIG_PATH = Path(__file__).parent / "data" / "monitor_config.json"
DEFAULT_CONFIG = dict(MONITOR_CONFIG)
CONFIG_LIMITS = {
    "min_score": (0, 100),
    "rise_1m_pct": (0, 10),
    "rise_3m_pct": (0, 20),
    "rise_5m_pct": (0, 30),
    "volume_spike_ratio": (0.1, 20),
    "signal_cooldown_sec": (0, 600),
}


def load_monitor_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_monitor_config(MONITOR_CONFIG)
        return MONITOR_CONFIG
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        saved = json.load(file)
    MONITOR_CONFIG.update({key: saved[key] for key in saved if key in MONITOR_CONFIG})
    return MONITOR_CONFIG


def update_monitor_config(values: dict[str, str]) -> dict[str, Any]:
    for key, value in values.items():
        if key not in CONFIG_LIMITS:
            continue
        original = MONITOR_CONFIG[key]
        if isinstance(original, int) and not isinstance(original, bool):
            parsed = int(float(value))
        elif isinstance(original, float):
            parsed = float(value)
        else:
            parsed = value
        MONITOR_CONFIG[key] = _clamp(key, parsed)
    save_monitor_config(MONITOR_CONFIG)
    return MONITOR_CONFIG


def reset_monitor_config() -> dict[str, Any]:
    MONITOR_CONFIG.clear()
    MONITOR_CONFIG.update(DEFAULT_CONFIG)
    save_monitor_config(MONITOR_CONFIG)
    return MONITOR_CONFIG


def _clamp(key: str, value: Any) -> Any:
    low, high = CONFIG_LIMITS[key]
    return max(low, min(high, value))


def save_monitor_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
