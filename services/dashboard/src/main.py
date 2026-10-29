"""Real-time latency dashboard for gateway → vision → gesture → canvas.

Run: uvicorn src.main:app --host 0.0.0.0 --port 8004
Passive: it only subscribes to MQTT and reads vision's /metrics; it never publishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .feeds import MqttFeed, VisionMetricsPoller
from .tracker import LatencyTracker

STATIC = Path(__file__).resolve().parents[1] / "static"
WINDOWS_S = (10, 30, 60, 120)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        tracker = LatencyTracker(settle_ms=settings.settle_ms)
        feed = MqttFeed(settings, tracker)
        feed.start()
        poller = VisionMetricsPoller(settings.vision_metrics_url, settings.vision_poll_s) if settings.vision_metrics_url else None
        poll_task = asyncio.create_task(poller.run()) if poller else None
        app.state.tracker, app.state.feed, app.state.poller = tracker, feed, poller
        try:
            yield
        finally:
            if poll_task:
                poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task
            feed.stop()

    app = FastAPI(title="pipeline latency dashboard", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def snapshot(state, window_s: float) -> dict:
        snap = state.tracker.snapshot(time.time() * 1000.0, window_s=window_s)
        snap["mqtt"] = {"broker": state.feed.broker, "connected": state.feed.connected,
                        "prefix": settings.mqtt_topic_prefix, "invalid": state.feed.invalid}
        snap["vision"] = state.poller.view() if state.poller else None
        snap["settle_ms"] = settings.settle_ms
        return snap

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": "dashboard", "mqtt_connected": app.state.feed.connected}

    @app.get("/api/snapshot")
    async def api_snapshot(window_s: float = 30) -> dict:
        return snapshot(app.state, max(1.0, min(window_s, 300.0)))

    @app.websocket("/ws/live")
    async def live(ws: WebSocket) -> None:
        """Pushes a snapshot every PUSH_INTERVAL_S. The client may send {"window_s": 10|30|60|120}."""
        await ws.accept()
        window = {"s": 30.0}

        async def receive() -> None:
            while True:
                msg = await ws.receive_json()
                if isinstance(msg, dict) and msg.get("window_s") in WINDOWS_S:
                    window["s"] = float(msg["window_s"])

        reader = asyncio.create_task(receive())
        try:
            while not reader.done():
                await ws.send_json(snapshot(ws.app.state, window["s"]))
                await asyncio.wait({reader}, timeout=settings.push_interval_s)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await reader

    return app


app = create_app()
