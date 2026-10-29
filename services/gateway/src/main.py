import asyncio
from collections import Counter
from contextlib import asynccontextmanager, suppress
import io
import json
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .messaging import MqttTransport
from .protocol import Contracts, now_ms

ROOT = Path(__file__).resolve().parent.parent


def create_app(settings=None, transport=None):
    settings = settings or Settings.from_env()
    contracts = Contracts(settings)
    link = transport or MqttTransport(settings)
    counters = Counter()
    sessions = set()
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(app):
        await link.start()
        try:
            yield
        finally:
            await link.stop()

    app = FastAPI(title="Hand Gesture Gateway", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    async def dashboard():
        return FileResponse(ROOT / "templates" / "dashboard.html")

    @app.get("/cam")
    async def camera():
        return FileResponse(ROOT / "templates" / "index.html")

    def camera_url(request):
        base = settings.public_url or str(request.base_url).rstrip("/")
        return f"{base}/cam"

    @app.get("/api/info")
    async def info(request: Request):
        return {"camera_url": camera_url(request), "transport": "mqtt", "mqtt_connected": link.connected}

    @app.get("/qr.png")
    async def qr(request: Request):
        import qrcode
        output = io.BytesIO()
        qrcode.make(camera_url(request), border=2, box_size=8).save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png")

    @app.get("/healthz")
    async def health():
        return JSONResponse({"status": "ok" if link.connected else "degraded",
                             "service": "gateway", "transport": "mqtt", "mqtt_connected": link.connected},
                            status_code=200 if link.connected else 503)

    @app.get("/api/metrics")
    async def metrics():
        elapsed = max(time.monotonic() - started, 0.001)
        return {**counters, "sessions": len(sessions), "mqtt_connected": link.connected,
                "input_fps": counters["received"] / elapsed,
                "result_fps": counters["completed"] / elapsed,
                "drop_rate": counters["dropped"] / max(1, counters["received"])}

    @app.websocket("/ws")
    @app.websocket("/ws/stream")
    async def stream(ws: WebSocket):
        if len(sessions) >= settings.max_sessions:
            await ws.close(code=1013)
            return
        session_id = uuid.uuid4().hex
        sessions.add(session_id)
        queue = asyncio.Queue(maxsize=1)
        send_lock = asyncio.Lock()
        previous_id = -1

        async def send(message):
            async with send_lock:
                await asyncio.wait_for(ws.send_json(message), timeout=settings.timeout)

        async def error(code, message, envelope=None):
            await send({"schema_version": settings.schema_version, "message_type": "error",
                        "session_id": session_id, "timestamp_ms": now_ms(), "service": "gateway",
                        "code": code, "message": message,
                        **({k: envelope[k] for k in ("trace_id", "frame_id")} if envelope else {})})

        async def worker():
            while True:
                envelope, jpeg = await queue.get()
                try:
                    await link.publish_frame(envelope, jpeg)
                except TimeoutError:
                    counters["timeouts"] += 1
                    await error("PUBLISH_TIMEOUT", "MQTT publish timed out", envelope)
                except (ConnectionError, ValueError):
                    counters["errors"] += 1
                    await error("PUBLISH_ERROR", "MQTT is unavailable", envelope)
                else:
                    counters["published"] += 1

        async def receive():
            nonlocal previous_id
            while True:
                event = await ws.receive()
                if event["type"] == "websocket.disconnect":
                    return
                data = event.get("bytes")
                if data is None:
                    text = event.get("text")
                    if text:
                        try:
                            ping = json.loads(text)
                        except ValueError:
                            ping = {}
                        if ping.get("type") == "ping":
                            await send({"type": "pong", "t0": ping.get("t0"), "server_ms": now_ms()})
                            continue
                    await error("INVALID_FRAME", "Send a binary camera frame")
                    continue
                try:
                    envelope, jpeg = await asyncio.to_thread(contracts.browser_frame, data, session_id,
                                                            previous_id, settings.max_frame_bytes)
                except ValueError:
                    counters["invalid"] += 1
                    await error("INVALID_FRAME", "Invalid metadata, frame_id, or JPEG")
                    continue
                previous_id = envelope["frame_id"]
                counters["received"] += 1
                if queue.full():
                    queue.get_nowait()
                    counters["dropped"] += 1
                queue.put_nowait((envelope, jpeg))

        tasks = []
        try:
            await ws.accept()
            await send({"type": "hello", "message_type": "hello", "session_id": session_id,
                        "schema_version": settings.schema_version, "transport": "mqtt", "server_ms": now_ms()})
            tasks = [asyncio.create_task(receive()), asyncio.create_task(worker())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, RuntimeError, TimeoutError, OSError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError, TimeoutError, OSError):
                    await task
            sessions.discard(session_id)

    return app


app = create_app()
