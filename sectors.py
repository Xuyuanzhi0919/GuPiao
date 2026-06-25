from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SECTORS = {
    "算力": ["300750", "002230", "688111", "000977", "603019"],
    "机器人": ["300024", "002747", "688017", "603728", "002031"],
    "半导体": ["688981", "603986", "300604", "002371", "688012"],
    "低空经济": ["002085", "300699", "600038", "002179", "300900"],
    "券商": ["600030", "000776", "601688", "600837", "601066"],
}

SECTORS_PATH = Path(__file__).parent / "data" / "sectors.json"


def load_sectors() -> dict[str, list[str]]:
    if not SECTORS_PATH.exists():
        save_sectors(DEFAULT_SECTORS)
    with SECTORS_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return {str(name): [str(code) for code in codes] for name, codes in payload.items()}


def save_sectors(sectors: dict[str, list[str]]) -> None:
    SECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SECTORS_PATH.open("w", encoding="utf-8") as file:
        json.dump(sectors, file, ensure_ascii=False, indent=2)


def add_sector_code(sector: str, code: str) -> dict[str, list[str]]:
    sectors = load_sectors()
    sector = sector.strip()
    code = code.strip()
    if not sector or not code:
        return sectors
    codes = set(sectors.get(sector, []))
    codes.add(code)
    sectors[sector] = sorted(codes)
    save_sectors(sectors)
    return sectors


def remove_sector_code(sector: str, code: str) -> dict[str, list[str]]:
    sectors = load_sectors()
    if sector in sectors:
        sectors[sector] = [item for item in sectors[sector] if item != code]
        if not sectors[sector]:
            del sectors[sector]
        save_sectors(sectors)
    return sectors
