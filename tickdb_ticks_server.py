from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import websocket
except ImportError as error:  # pragma: no cover - depends on local env
    raise RuntimeError("需要安装 websocket-client: pip install websocket-client") from error

from market_data import Tick
from sectors import load_sectors
from universe import load_universe


HOST = "127.0.0.1"
PORT = int(os.environ.get("TICKDB_PORT", "9001"))
WS_URL = os.environ.get("TICKDB_WS_URL", "wss://api.tickdb.ai/v1/realtime")
SYMBOLS_URL = os.environ.get("TICKDB_SYMBOLS_URL", "https://api.tickdb.ai/v1/symbols/available")
EASTMONEY_SYMBOLS_URL = os.environ.get("EASTMONEY_SYMBOLS_URL", "https://push2.eastmoney.com/api/qt/clist/get")
SINA_SYMBOL_COUNT_URL = os.environ.get(
    "SINA_SYMBOL_COUNT_URL",
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount",
)
SINA_SYMBOLS_URL = os.environ.get(
    "SINA_SYMBOLS_URL",
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
)
SYMBOLS_CACHE = Path(os.environ.get("TICKDB_SYMBOLS_CACHE", "data/tickdb_cn_symbols.json"))
DEFAULT_SYMBOLS = ["600000.SH", "000001.SZ", "600030.SH", "300750.SZ", "688981.SH"]
A_SHARE_SYMBOL = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


class TickDBBridge:
    def __init__(self) -> None:
        self.api_key = os.environ.get("TICKDB_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("请先设置 TICKDB_API_KEY 环境变量")

        self.last_error = ""
        self.last_raw_message = ""
        self.symbol_meta: dict[str, dict] = {}
        self.symbols_file = os.environ.get("TICKDB_SYMBOLS_FILE", "").strip()
        self.symbols_file_mtime = 0.0
        self.symbols = self._load_symbols()
        self.latest: dict[str, dict] = {}
        self.prev_close: dict[str, float] = {}
        self.code_to_sector = self._build_sector_index()
        self.connected = False
        self.message_count = 0
        self.last_message_ts = 0.0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._watch_thread: threading.Thread | None = None
        self._ws_app: websocket.WebSocketApp | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, name="tickdb-ws", daemon=True)
        self._thread.start()
        if self.symbols_file:
            self._watch_thread = threading.Thread(target=self._watch_symbols_file, name="tickdb-symbol-watch", daemon=True)
            self._watch_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.latest.values())

    def health(self) -> dict:
        with self._lock:
            return {
                "source": "tickdb",
                "symbols": self.symbols[:50],
                "symbol_count": len(self.symbols),
                "connected": self.connected,
                "last_message_ts": self.last_message_ts,
                "message_count": self.message_count,
                "last_error": self.last_error,
                "last_raw_message": self.last_raw_message,
                "tick_count": len(self.latest),
            }

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            ws_url = f"{WS_URL}?api_key={self.api_key}"
            app = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws_app = app
            app.run_forever(ping_interval=20, ping_timeout=10)
            with self._lock:
                self.connected = False
                self._ws_app = None
            if not self._stop_event.wait(3):
                continue

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        with self._lock:
            self.connected = True
            self.last_error = ""
        for message in self._subscribe_messages():
            ws.send(json.dumps(message, ensure_ascii=False))
            time.sleep(float(os.environ.get("TICKDB_SUBSCRIBE_DELAY", "0.05")))

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
            with self._lock:
                self.last_raw_message = _short_json(payload)
                if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
                    self.last_error = self._first_text(payload, ("message", "msg", "error")) or self.last_raw_message
            ticks = []
            for row in self._extract_rows(payload):
                tick = self._to_tick(row)
                if tick:
                    ticks.append(tick)
            if not ticks:
                return
            now = time.time()
            with self._lock:
                self.message_count += 1
                self.last_message_ts = now
                for tick in ticks:
                    self.latest[tick.code] = tick.__dict__
        except (TypeError, ValueError, KeyError) as error:
            with self._lock:
                self.last_error = f"{error.__class__.__name__}: {error}"

    def _on_error(self, _ws: websocket.WebSocketApp, error: object) -> None:
        with self._lock:
            self.last_error = str(error)

    def _on_close(self, _ws: websocket.WebSocketApp, _code: int, _message: str) -> None:
        with self._lock:
            self.connected = False

    def _subscribe_messages(self) -> list[dict]:
        chunk_size = max(1, int(os.environ.get("TICKDB_SUBSCRIBE_CHUNK", "200")))
        with self._lock:
            symbols = list(self.symbols)
        return [
            {"cmd": "subscribe", "data": {"channel": "ticker", "symbols": symbols[index : index + chunk_size]}}
            for index in range(0, len(symbols), chunk_size)
        ]

    def _extract_rows(self, payload: object) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []

        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
            return [data]
        return [payload]

    def _to_tick(self, row: dict) -> Tick | None:
        symbol = self._first_text(row, ("symbol", "s", "ticker", "code"))
        if not symbol:
            return None
        code = symbol.split(".", 1)[0]
        price = self._first_float(row, ("last_price", "last", "price", "close", "p"))
        if not price or price <= 0:
            return None

        prev_close = self._first_float(row, ("prev_close", "pre_close", "previous_close"))
        if not prev_close or prev_close <= 0:
            prev_close = self.prev_close.get(code, price)
        self.prev_close[code] = prev_close

        volume = int(self._first_float(row, ("volume", "vol", "v")) or 0)
        turnover = self._first_float(row, ("turnover", "amount", "a"))
        if turnover is None:
            turnover = price * volume
        active_buy_ratio = self._first_float(row, ("active_buy_ratio", "buy_ratio")) or 0.5
        bid_amount = self._first_float(row, ("bid_amount", "bid_vol", "bid_volume")) or 0.0
        ask_amount = self._first_float(row, ("ask_amount", "ask_vol", "ask_volume")) or 0.0

        return Tick(
            code=code,
            name=self._first_text(row, ("name", "stock_name", "n")) or self._name_for_code(code) or code,
            sector=self.code_to_sector.get(code, "未分组"),
            board=self._infer_board(code),
            ts=self._normalize_ts(self._first_float(row, ("timestamp", "ts", "time"))),
            price=round(price, 3),
            prev_close=round(prev_close, 3),
            volume=volume,
            turnover=round(turnover, 2),
            active_buy_ratio=round(active_buy_ratio, 3),
            bid_amount=round(bid_amount, 2),
            ask_amount=round(ask_amount, 2),
        )

    def _load_symbols(self) -> list[str]:
        if self.symbols_file:
            return self._load_symbols_from_file()

        raw = os.environ.get("TICKDB_SYMBOLS", "").strip()
        if raw:
            if raw.upper() in {"CN", "A", "A_SHARE", "A_SHARES", "ALL"}:
                return self._load_cn_symbols()
            return [self._normalize_symbol(item) for item in raw.split(",") if item.strip()]

        universe = load_universe()
        include = universe.get("include", [])
        if include:
            return [self._normalize_symbol(code) for code in include[:80]]
        return DEFAULT_SYMBOLS

    def _load_symbols_from_file(self) -> list[str]:
        path = Path(self.symbols_file)
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("symbols", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("TICKDB_SYMBOLS_FILE 必须包含 symbols 数组")
        symbols = []
        for item in rows:
            if isinstance(item, dict):
                symbol = self._first_text(item, ("symbol", "code"))
                if "name" in item:
                    normalized = self._normalize_symbol(symbol)
                    self.symbol_meta[normalized] = item
            else:
                symbol = str(item)
            if symbol:
                symbols.append(self._normalize_symbol(symbol))
        if not symbols:
            raise ValueError("TICKDB_SYMBOLS_FILE 没有可订阅代码")
        self.symbols_file_mtime = path.stat().st_mtime
        return _dedupe(symbols)

    def _watch_symbols_file(self) -> None:
        interval = float(os.environ.get("TICKDB_SYMBOLS_FILE_INTERVAL", "3"))
        while not self._stop_event.wait(interval):
            path = Path(self.symbols_file)
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            if mtime <= self.symbols_file_mtime:
                continue
            try:
                new_symbols = self._load_symbols_from_file()
            except (OSError, ValueError, json.JSONDecodeError) as error:
                with self._lock:
                    self.last_error = f"symbols file reload failed: {error.__class__.__name__}: {error}"
                continue
            with self._lock:
                old_symbols = list(self.symbols)
                if new_symbols == old_symbols:
                    continue
                self.symbols = new_symbols
                self.latest = {code: tick for code, tick in self.latest.items() if self._normalize_symbol(code) in new_symbols}
                app = self._ws_app
                self.last_error = "symbols file changed, reconnecting"
            if app:
                app.close()

    def _load_cn_symbols(self) -> list[str]:
        symbols = self._fetch_cn_symbols()
        max_symbols = int(os.environ.get("TICKDB_MAX_SYMBOLS", "0"))
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        if not symbols:
            raise RuntimeError("未能获取 TickDB A 股代码池，请检查 API 权限或 data/tickdb_cn_symbols.json 缓存")
        return symbols

    def _fetch_cn_symbols(self) -> list[str]:
        try:
            symbols = self._fetch_cn_symbols_from_api()
            self._write_symbol_cache(symbols)
            return symbols
        except BaseException as tickdb_error:
            sina_error_text = ""
            eastmoney_error_text = ""
            try:
                symbols = self._fetch_cn_symbols_from_sina()
                self._write_symbol_cache(symbols)
                self.last_error = f"symbols api failed, using sina pool: {tickdb_error.__class__.__name__}: {tickdb_error}"
                return symbols
            except BaseException as sina_error:
                sina_error_text = f"{sina_error.__class__.__name__}: {sina_error}"
            try:
                symbols = self._fetch_cn_symbols_from_eastmoney()
                self._write_symbol_cache(symbols)
                self.last_error = f"symbols api failed, using eastmoney pool: {tickdb_error.__class__.__name__}: {tickdb_error}"
                return symbols
            except BaseException as eastmoney_error:
                eastmoney_error_text = f"{eastmoney_error.__class__.__name__}: {eastmoney_error}"
                error = eastmoney_error
            cached = self._read_symbol_cache()
            if cached:
                self.last_error = f"symbols api failed, using cache: {error.__class__.__name__}: {error}"
                return cached
            raise RuntimeError(
                "无法获取 A 股代码池: "
                f"tickdb={tickdb_error.__class__.__name__}: {tickdb_error}; "
                f"sina={sina_error_text}; "
                f"eastmoney={eastmoney_error_text}"
            ) from tickdb_error

    def _fetch_cn_symbols_from_api(self) -> list[str]:
        page = 1
        limit = int(os.environ.get("TICKDB_SYMBOL_PAGE_LIMIT", "1000"))
        symbols: list[str] = []
        while True:
            query = urlencode({"market": "CN", "page": page, "limit": limit})
            request = Request(f"{SYMBOLS_URL}?{query}", headers={"X-API-Key": self.api_key})
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else {}
            rows = data.get("symbols", []) if isinstance(data, dict) else []
            for row in rows:
                symbol = self._first_text(row, ("symbol",))
                if A_SHARE_SYMBOL.match(symbol):
                    symbols.append(symbol)
                    self.symbol_meta[symbol] = row
            total = int(data.get("total", len(symbols))) if isinstance(data, dict) else len(symbols)
            if not rows or len(symbols) >= total or len(rows) < limit:
                break
            page += 1
        return _dedupe(symbols)

    def _fetch_cn_symbols_from_sina(self) -> list[str]:
        count_query = urlencode({"node": "hs_a"})
        count_request = Request(f"{SINA_SYMBOL_COUNT_URL}?{count_query}", headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(count_request, timeout=15) as response:
            total = int(json.loads(response.read().decode("utf-8")))
        page_size = int(os.environ.get("SINA_SYMBOL_PAGE_LIMIT", "100"))
        symbols = []
        for page in range(1, total // page_size + 2):
            query = urlencode(
                {
                    "page": page,
                    "num": page_size,
                    "sort": "symbol",
                    "asc": 1,
                    "node": "hs_a",
                    "symbol": "",
                    "_s_r_a": "page",
                }
            )
            request = Request(f"{SINA_SYMBOLS_URL}?{query}", headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=20) as response:
                rows = json.loads(response.read().decode("utf-8"))
            if not rows:
                break
            for row in rows:
                code = self._first_text(row, ("code",))
                name = self._first_text(row, ("name",))
                symbol = self._sina_symbol(self._first_text(row, ("symbol",)), code)
                if symbol and A_SHARE_SYMBOL.match(symbol):
                    symbols.append(symbol)
                    self.symbol_meta[symbol] = {"symbol": symbol, "name": name, "source": "sina"}
            if len(symbols) >= total:
                break
        return _dedupe(symbols)

    def _sina_symbol(self, raw_symbol: str, code: str) -> str:
        prefix = raw_symbol[:2].lower()
        if prefix == "sh":
            return f"{code}.SH"
        if prefix == "sz":
            return f"{code}.SZ"
        if prefix == "bj":
            return f"{code}.BJ"
        return self._normalize_symbol(code)

    def _fetch_cn_symbols_from_eastmoney(self) -> list[str]:
        symbols = []
        page = 1
        page_size = int(os.environ.get("EASTMONEY_SYMBOL_PAGE_LIMIT", "100"))
        total = None
        while True:
            query = urlencode(
                {
                    "pn": page,
                    "pz": page_size,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f3",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                    "fields": "f12,f13,f14",
                }
            )
            request = Request(
                f"{EASTMONEY_SYMBOLS_URL}?{query}",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            )
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else {}
            rows = data.get("diff", []) if isinstance(data, dict) else []
            total = int(data.get("total", len(symbols))) if isinstance(data, dict) else len(symbols)
            if isinstance(rows, dict):
                rows = list(rows.values())
            if not rows:
                break
            for row in rows:
                code = self._first_text(row, ("f12", "code"))
                name = self._first_text(row, ("f14", "name"))
                symbol = self._eastmoney_symbol(code)
                if symbol and A_SHARE_SYMBOL.match(symbol):
                    symbols.append(symbol)
                    self.symbol_meta[symbol] = {"symbol": symbol, "name": name, "source": "eastmoney"}
            if len(symbols) >= total or len(rows) < page_size:
                break
            page += 1
        return _dedupe(symbols)

    def _eastmoney_symbol(self, code: str) -> str:
        if not code:
            return ""
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8", "9")):
            return f"{code}.BJ"
        return ""

    def _write_symbol_cache(self, symbols: list[str]) -> None:
        SYMBOLS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": "tickdb",
            "market": "CN",
            "updated_at": time.time(),
            "symbols": symbols,
            "meta": self.symbol_meta,
        }
        SYMBOLS_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_symbol_cache(self) -> list[str]:
        if not SYMBOLS_CACHE.exists():
            return []
        payload = json.loads(SYMBOLS_CACHE.read_text(encoding="utf-8"))
        meta = payload.get("meta", {})
        if isinstance(meta, dict):
            self.symbol_meta = meta
        symbols = payload.get("symbols", [])
        if not isinstance(symbols, list):
            return []
        return [str(symbol) for symbol in symbols if A_SHARE_SYMBOL.match(str(symbol))]

    def _build_sector_index(self) -> dict[str, str]:
        index = {}
        for sector, codes in load_sectors().items():
            for code in codes:
                index[str(code)] = sector
        return index

    def _name_for_code(self, code: str) -> str:
        for suffix in ("SH", "SZ", "BJ"):
            meta = self.symbol_meta.get(f"{code}.{suffix}")
            if meta:
                return self._first_text(meta, ("name", "symbol_name", "base_asset"))
        return ""

    def _normalize_symbol(self, value: str) -> str:
        code = value.strip().upper()
        if "." in code:
            return code
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8", "9")):
            return f"{code}.BJ"
        return code

    def _infer_board(self, code: str) -> str:
        if code.startswith("30"):
            return "gem"
        if code.startswith("68"):
            return "star"
        if code.startswith(("4", "8", "9")):
            return "bj"
        return "main"

    def _normalize_ts(self, value: float | None) -> float:
        if not value:
            return time.time()
        if value > 10_000_000_000:
            return value / 1000
        return value

    def _first_text(self, row: dict, keys: tuple[str, ...]) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    def _first_float(self, row: dict, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = row.get(key)
            if value is None or value == "":
                continue
            return float(value)
        return None


def _short_json(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    return text if len(text) <= 500 else text[:500] + "..."


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


BRIDGE: TickDBBridge | None = None


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        writer.close()
        await writer.wait_closed()
        return

    request_line = request.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
    raw_path = request_line.split()[1] if len(request_line.split()) >= 2 else "/"
    path = urlparse(raw_path).path
    if BRIDGE is None:
        await send_response(writer, {"error": "bridge not ready"}, status="503 Service Unavailable")
        return
    if path == "/ticks":
        await send_response(writer, {"ticks": BRIDGE.snapshot(), "health": BRIDGE.health()})
        return
    if path == "/health":
        await send_response(writer, BRIDGE.health())
        return
    await send_response(writer, {"error": "use /ticks or /health"}, status="404 Not Found")


async def send_response(writer: asyncio.StreamWriter, payload: dict, status: str = "200 OK") -> None:
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


async def main() -> None:
    global BRIDGE
    BRIDGE = TickDBBridge()
    BRIDGE.start()
    server = await asyncio.start_server(handle_client, HOST, PORT)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    print(f"TickDB 行情桥已启动: http://{HOST}:{PORT}/ticks")
    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        await stop_event.wait()
        server.close()
        await server.wait_closed()
        serve_task.cancel()
        BRIDGE.stop()
        await asyncio.gather(serve_task, return_exceptions=True)
    print("TickDB 行情桥已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
