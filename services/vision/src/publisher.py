"""WebSocket client that pushes landmark messages to the gesture service (Container C).

C is the server (ws://gesture:8002/ws/landmarks); vision connects as a client and keeps
reconnecting at a fixed interval. Landmarks are real-time data, so nothing is buffered
while disconnected: such messages are counted as `ws_unsent_count`, which keeps
"C is unreachable" distinguishable from "no hand detected".
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .metrics import VisionMetrics

log = logging.getLogger("vision.publisher")

PublishStatus = Literal["queued", "not_connected", "queue_full"]


class GesturePublisher:
    def __init__(
        self,
        url: str,
        metrics: VisionMetrics,
        *,
        reconnect_interval_s: float = 2.0,
        open_timeout_s: float = 3.0,
        queue_size: int = 64,
    ) -> None:
        self.url = url
        self._metrics = metrics
        self._reconnect_interval_s = reconnect_interval_s
        self._open_timeout_s = open_timeout_s
        self._queue: asyncio.Queue[tuple[str, int, str]] = asyncio.Queue(maxsize=queue_size)
        self._connected = False
        self._stopping = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_unsent_warning = 0.0
        self._last_error: str | None = None
        self._last_error_logged = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="gesture-publisher")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False

    def publish(self, session_id: str, frame_id: int, payload: str) -> PublishStatus:
        """Non-blocking; must be called from the event loop thread."""
        if not self._connected:
            self._metrics.incr("ws_unsent_count")
            self._metrics.record_frame(session_id, frame_id, publish_status="not_connected")
            now = time.monotonic()
            if now - self._last_unsent_warning > 5.0:
                log.warning("gesture WS not connected (%s); dropping landmark messages", self.url)
                self._last_unsent_warning = now
            return "not_connected"
        if self._queue.full():
            # Keep the newest data: discard the oldest queued message.
            old_session, old_frame, _ = self._queue.get_nowait()
            self._metrics.incr("ws_unsent_count")
            self._metrics.record_frame(old_session, old_frame, publish_status="queue_full")
        self._queue.put_nowait((session_id, frame_id, payload))
        return "queued"

    async def _resolve(self) -> None:
        # Resolve DNS outside connect(): if open_timeout fires during a slow lookup (e.g. the
        # gesture container is not running), the orphaned lookup logs "exception never retrieved".
        parts = urlsplit(self.url)
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        await asyncio.get_running_loop().getaddrinfo(parts.hostname, port)

    def _log_failure(self, exc: BaseException) -> None:
        # Same error repeats every reconnect interval while C is down: warn on change or every 30 s.
        text = f"{type(exc).__name__}: {exc}"
        now = time.monotonic()
        if text != self._last_error or now - self._last_error_logged > 30.0:
            log.warning("gesture WS %s unavailable (%s); retrying every %.1fs", self.url, text, self._reconnect_interval_s)
            self._last_error, self._last_error_logged = text, now
        else:
            log.debug("gesture WS %s still unavailable: %s", self.url, text)

    async def _run(self) -> None:
        while not self._stopping.is_set():
            self._metrics.incr("ws_connect_attempt_count")
            try:
                await self._resolve()
                async with connect(self.url, open_timeout=self._open_timeout_s, ping_interval=10, ping_timeout=10) as ws:
                    self._connected = True
                    self._last_error = None
                    self._metrics.incr("ws_connect_count")
                    log.info("connected to gesture WS %s", self.url)
                    await self._serve(ws)
            except asyncio.CancelledError:
                raise
            except (OSError, TimeoutError, InvalidHandshake, ConnectionClosed) as exc:
                self._log_failure(exc)
            except InvalidURI as exc:
                log.error("invalid GESTURE_WS_URL %r: %s", self.url, exc)
            except Exception:
                log.exception("unexpected gesture WS error")
            finally:
                if self._connected:
                    log.warning("disconnected from gesture WS %s", self.url)
                self._connected = False
                self._drain_queue_as_unsent()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._reconnect_interval_s)
            except TimeoutError:
                pass

    async def _serve(self, ws: ClientConnection) -> None:
        reader = asyncio.create_task(self._read_until_closed(ws))
        sender = asyncio.create_task(self._send_loop(ws))
        try:
            done, _ = await asyncio.wait({reader, sender}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # propagate ConnectionClosed etc.
        finally:
            for task in (reader, sender):
                task.cancel()
            await asyncio.gather(reader, sender, return_exceptions=True)

    async def _read_until_closed(self, ws: ClientConnection) -> None:
        # C is not expected to send anything; reading detects the close promptly.
        async for message in ws:
            log.debug("ignoring message from gesture WS: %.200r", message)

    async def _send_loop(self, ws: ClientConnection) -> None:
        while True:
            session_id, frame_id, payload = await self._queue.get()
            t0 = time.perf_counter()
            try:
                await ws.send(payload)
            except ConnectionClosed:
                self._metrics.incr("ws_send_error_count")
                self._metrics.record_frame(session_id, frame_id, publish_status="send_error")
                log.warning("send failed for frame %s/%s: connection closed", session_id, frame_id)
                raise
            send_ms = (time.perf_counter() - t0) * 1000.0
            self._metrics.incr("ws_sent_count")
            self._metrics.observe("websocket_send_ms", send_ms)
            self._metrics.record_frame(session_id, frame_id, publish_status="sent", websocket_send_ms=round(send_ms, 3))

    def _drain_queue_as_unsent(self) -> None:
        while not self._queue.empty():
            session_id, frame_id, _ = self._queue.get_nowait()
            self._metrics.incr("ws_unsent_count")
            self._metrics.record_frame(session_id, frame_id, publish_status="not_connected")
