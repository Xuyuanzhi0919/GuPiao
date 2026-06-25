from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from dataclasses import dataclass
from typing import AsyncIterator, Protocol
from urllib.request import urlopen

from sectors import load_sectors
from universe import filter_codes, filter_ticks


@dataclass(frozen=True)
class Tick:
    code: str
    name: str
    sector: str
    board: str
    ts: float
    price: float
    prev_close: float
    volume: int
    turnover: float
    active_buy_ratio: float
    bid_amount: float
    ask_amount: float


class MarketDataSource(Protocol):
    async def stream(self) -> AsyncIterator[list[Tick]]:
        ...


def create_market_data_source() -> MarketDataSource:
    source = os.environ.get("DATA_SOURCE", "sim").strip().lower()
    if source == "http":
        url = os.environ.get("MARKET_HTTP_URL", "")
        if not url:
            raise RuntimeError("DATA_SOURCE=http 需要设置 MARKET_HTTP_URL")
        interval = float(os.environ.get("MARKET_HTTP_INTERVAL", "1"))
        return HttpJsonMarketDataSource(url=url, interval=interval)
    return SimulatedMarketDataSource()


class HttpJsonMarketDataSource:
    """Poll a JSON endpoint that returns a list of Tick-compatible objects."""

    def __init__(self, url: str, interval: float = 1.0) -> None:
        self.url = url
        self.interval = interval
        self.bad_row_count = 0
        self.last_bad_row_error = ""
        self.upstream_health: dict = {}

    async def stream(self) -> AsyncIterator[list[Tick]]:
        while True:
            rows = await asyncio.to_thread(self._fetch_rows)
            ticks = []
            for row in rows:
                try:
                    ticks.append(self._to_tick(row))
                except (KeyError, TypeError, ValueError) as error:
                    self.bad_row_count += 1
                    self.last_bad_row_error = f"{error.__class__.__name__}: {error}"
            yield filter_ticks(ticks)
            await asyncio.sleep(self.interval)

    def _fetch_rows(self) -> list[dict]:
        with urlopen(self.url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict):
            health = payload.get("health")
            self.upstream_health = health if isinstance(health, dict) else {}
            payload = payload.get("ticks", [])
        if not isinstance(payload, list):
            raise ValueError("行情接口必须返回数组，或包含 ticks 数组的对象")
        return payload

    def _to_tick(self, row: dict) -> Tick:
        price = float(row["price"])
        prev_close = float(row["prev_close"])
        volume = int(float(row.get("volume", 0)))
        turnover = float(row.get("turnover", price * volume))
        if price <= 0 or prev_close <= 0:
            raise ValueError("price and prev_close must be positive")
        return Tick(
            code=str(row["code"]),
            name=str(row.get("name", row["code"])),
            sector=str(row.get("sector", "未分组")),
            board=str(row.get("board", self._infer_board(str(row["code"])))),
            ts=float(row.get("ts", time.time())),
            price=price,
            prev_close=prev_close,
            volume=volume,
            turnover=turnover,
            active_buy_ratio=float(row.get("active_buy_ratio", 0.5)),
            bid_amount=float(row.get("bid_amount", 0)),
            ask_amount=float(row.get("ask_amount", 0)),
        )

    def _infer_board(self, code: str) -> str:
        if code.startswith("30"):
            return "gem"
        if code.startswith("68"):
            return "star"
        if code.startswith("8") or code.startswith("4"):
            return "bj"
        return "main"


class SimulatedMarketDataSource:
    def __init__(self) -> None:
        self._rng = random.Random(20260522)
        self._stocks = self._build_universe()
        self._tick = 0

    def _build_universe(self) -> list[dict]:
        names = {
            "300750": "宁德时代",
            "002230": "科大讯飞",
            "688111": "金山办公",
            "000977": "浪潮信息",
            "603019": "中科曙光",
            "300024": "机器人",
            "002747": "埃斯顿",
            "688017": "绿的谐波",
            "603728": "鸣志电器",
            "002031": "巨轮智能",
            "688981": "中芯国际",
            "603986": "兆易创新",
            "300604": "长川科技",
            "002371": "北方华创",
            "688012": "中微公司",
            "002085": "万丰奥威",
            "300699": "光威复材",
            "600038": "中直股份",
            "002179": "中航光电",
            "300900": "广联航空",
            "600030": "中信证券",
            "000776": "广发证券",
            "601688": "华泰证券",
            "600837": "海通证券",
            "601066": "中信建投",
        }
        sectors = load_sectors()
        stocks: list[dict] = []
        for sector, codes in sectors.items():
            for code in filter_codes(codes):
                board = "gem" if code.startswith("30") else "star" if code.startswith("68") else "main"
                prev_close = self._rng.uniform(8, 88)
                stocks.append(
                    {
                        "code": code,
                        "name": names.get(code, code),
                        "sector": sector,
                        "board": board,
                        "prev_close": prev_close,
                        "price": prev_close * self._rng.uniform(0.985, 1.018),
                        "base_volume": self._rng.randint(25_000, 150_000),
                        "today_turnover": 0.0,
                        "pulse": 0,
                    }
                )
        return stocks

    async def stream(self) -> AsyncIterator[list[Tick]]:
        while True:
            self._tick += 1
            if self._tick % 23 == 0:
                sector = self._rng.choice(list(load_sectors()))
                for stock in self._stocks:
                    if stock["sector"] == sector and self._rng.random() < 0.55:
                        stock["pulse"] = self._rng.randint(12, 30)

            ticks = [self._next_tick(stock) for stock in self._stocks]
            yield ticks
            await asyncio.sleep(1)

    def _next_tick(self, stock: dict) -> Tick:
        pulse = stock["pulse"]
        drift = self._rng.gauss(0, 0.0012)
        active_buy_ratio = self._rng.uniform(0.46, 0.57)
        volume_multiplier = self._rng.uniform(0.65, 1.25)

        if pulse > 0:
            phase = pulse / 30
            drift += self._rng.uniform(0.002, 0.008) * (0.6 + phase)
            active_buy_ratio = self._rng.uniform(0.58, 0.78)
            volume_multiplier = self._rng.uniform(2.2, 6.5)
            stock["pulse"] -= 1

        price = max(1.0, stock["price"] * (1 + drift))
        limit = 1.2 if stock["board"] in {"gem", "star"} else 1.1
        price = min(stock["prev_close"] * limit * 0.995, price)
        volume = int(stock["base_volume"] * volume_multiplier * (1 + 0.2 * math.sin(time.time() / 11)))
        turnover = volume * price
        stock["price"] = price
        stock["today_turnover"] += turnover

        bid_base = turnover * self._rng.uniform(0.8, 2.5)
        ask_base = turnover * self._rng.uniform(0.7, 2.2)
        if active_buy_ratio > 0.6:
            bid_base *= self._rng.uniform(1.2, 2.1)

        return Tick(
            code=stock["code"],
            name=stock["name"],
            sector=stock["sector"],
            board=stock["board"],
            ts=time.time(),
            price=round(price, 2),
            prev_close=round(stock["prev_close"], 2),
            volume=volume,
            turnover=round(turnover, 2),
            active_buy_ratio=round(active_buy_ratio, 3),
            bid_amount=round(bid_base, 2),
            ask_amount=round(ask_base, 2),
        )
