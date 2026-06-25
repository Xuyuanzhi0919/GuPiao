from __future__ import annotations

import json
from pathlib import Path

UNIVERSE_PATH = Path(__file__).parent / "data" / "watch_universe.json"


def load_universe() -> dict[str, set[str]]:
    if not UNIVERSE_PATH.exists():
        save_universe({"include": [], "exclude": []})
    with UNIVERSE_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return {
        "include": {str(code) for code in payload.get("include", [])},
        "exclude": {str(code) for code in payload.get("exclude", [])},
    }


def universe_payload() -> dict[str, list[str]]:
    universe = load_universe()
    return {
        "include": sorted(universe["include"]),
        "exclude": sorted(universe["exclude"]),
    }


def add_code(list_name: str, code: str) -> dict[str, list[str]]:
    payload = universe_payload()
    if list_name not in {"include", "exclude"}:
        raise ValueError("list must be include or exclude")
    code = code.strip()
    if not code:
        return payload
    payload[list_name] = sorted(set(payload[list_name]) | {code})
    other = "exclude" if list_name == "include" else "include"
    payload[other] = [item for item in payload[other] if item != code]
    save_universe(payload)
    return payload


def remove_code(list_name: str, code: str) -> dict[str, list[str]]:
    payload = universe_payload()
    if list_name not in {"include", "exclude"}:
        raise ValueError("list must be include or exclude")
    payload[list_name] = [item for item in payload[list_name] if item != code]
    save_universe(payload)
    return payload


def save_universe(payload: dict[str, list[str]]) -> None:
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with UNIVERSE_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def filter_codes(codes: list[str]) -> list[str]:
    universe = load_universe()
    include = universe["include"]
    exclude = universe["exclude"]
    return [code for code in codes if (not include or code in include) and code not in exclude]


def filter_ticks(ticks: list) -> list:
    universe = load_universe()
    include = universe["include"]
    exclude = universe["exclude"]
    return [tick for tick in ticks if (not include or tick.code in include) and tick.code not in exclude]
