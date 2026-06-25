from __future__ import annotations

import asyncio
import json
import os
import signal
from urllib.parse import parse_qs, urlparse

from market_data import SimulatedMarketDataSource

HOST = "127.0.0.1"
PORT = int(os.environ.get("TICKS_PORT", "9000"))


class TickFeed:
    def __init__(self) -> None:
        self.source = SimulatedMarketDataSource()
        self.latest: list[dict] = []

    async def start(self) -> None:
        async for ticks in self.source.stream():
            self.latest = [tick.__dict__ for tick in ticks]


FEED = TickFeed()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        writer.close()
        await writer.wait_closed()
        return

    request_line = request.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
    raw_path = request_line.split()[1] if len(request_line.split()) >= 2 else "/"
    parsed = urlparse(raw_path)
    if parsed.path != "/ticks":
        await send_response(writer, {"error": "use /ticks"}, status="404 Not Found")
        return
    ticks = list(FEED.latest)
    if parse_qs(parsed.query).get("bad") == ["1"]:
        ticks.append({"code": "BAD001", "price": 0})
    await send_response(writer, {"ticks": ticks})


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
    feed_task = asyncio.create_task(FEED.start())
    server = await asyncio.start_server(handle_client, HOST, PORT)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    print(f"样例行情接口已启动: http://{HOST}:{PORT}/ticks")
    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        await stop_event.wait()
        server.close()
        await server.wait_closed()
        serve_task.cancel()
        feed_task.cancel()
        await asyncio.gather(serve_task, feed_task, return_exceptions=True)
    print("样例行情接口已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
