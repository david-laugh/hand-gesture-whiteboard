"""Mock gesture (Container C) WebSocket server — TEST ONLY, not used by the service.

Accepts connections on /ws/landmarks, validates every message against the agreed
contract and keeps them in memory.

    python -m tests.mock_gesture_server --port 8002 [--save received.jsonl]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading

from websockets.asyncio.server import ServerConnection, serve

from src.contract import validate_landmarks_message

PATH = "/ws/landmarks"


class MockGestureServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, *, verbose: bool = False, save: str | None = None) -> None:
        self.host = host
        self.port = port
        self.verbose = verbose
        self.save = save
        self.messages: list[dict] = []
        self.errors: list[str] = []
        self.connections = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Future | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    async def _handler(self, ws: ServerConnection) -> None:
        if ws.request is None or ws.request.path != PATH:
            await ws.close(code=1008, reason=f"expected path {PATH}")
            return
        self.connections += 1
        async for raw in ws:
            try:
                message = json.loads(raw)
                validate_landmarks_message(message)
            except Exception as exc:  # record, keep the connection open
                with self._lock:
                    self.errors.append(f"{type(exc).__name__}: {exc}")
                continue
            with self._lock:
                self.messages.append(message)
            if self.save:
                with open(self.save, "a", encoding="utf-8") as fh:
                    fh.write(raw if isinstance(raw, str) else raw.decode("utf-8"))
                    fh.write("\n")
            if self.verbose:
                hands = message["hands"]
                desc = f"{hands[0]['handedness']} {hands[0]['score']:.2f}" if hands else "no hand"
                print(f"[mock gesture] {message['session_id']} frame {message['frame_id']} ts {message['ts']}: {desc}")

    async def serve_forever(self, ready: threading.Event | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = self._loop.create_future()
        async with serve(self._handler, self.host, self.port) as server:
            self.port = server.sockets[0].getsockname()[1]
            if ready is not None:
                ready.set()
            await self._stop

    def start_in_thread(self) -> int:
        ready = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self.serve_forever(ready)), daemon=True)
        self._thread.start()
        if not ready.wait(5):
            raise RuntimeError("mock gesture server did not start")
        return self.port

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(lambda: self._stop.done() or self._stop.set_result(None))
        if self._thread is not None:
            self._thread.join(5)

    def wait_for(self, count: int, timeout: float = 5.0) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.messages) >= count:
                    return True
            time.sleep(0.02)
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--save", help="append received messages to a JSON Lines file")
    args = parser.parse_args()
    server = MockGestureServer(args.host, args.port, verbose=True, save=args.save)
    print(f"mock gesture server on ws://{args.host}:{args.port}{PATH}")
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
