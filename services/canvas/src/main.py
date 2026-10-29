import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .engine import CanvasEngine
from .sessions import SessionTracker, normalise_hands, render_png, save_drawing

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("canvas")
AUTO = "auto"


class Settings:
    def __init__(self):
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "11883"))
        self.prefix = os.getenv("MQTT_TOPIC_PREFIX", "hgw/v1").strip("/")
        self.width = int(os.getenv("CANVAS_WIDTH", "1280"))
        self.height = int(os.getenv("CANVAS_HEIGHT", "720"))
        self.smoothing = float(os.getenv("CANVAS_SMOOTHING", "1.0"))
        self.segment_px = float(os.getenv("CANVAS_SEGMENT_PX", "8"))
        self.save_dir = Path(os.getenv("CANVAS_SAVE_DIR", str(ROOT / "drawings")))
        self.session_idle_s = float(os.getenv("CANVAS_SESSION_IDLE_SEC", "5"))


def create_app(settings=None):
    settings = settings or Settings()
    engine = CanvasEngine(settings.width, settings.height, settings.smoothing, settings.segment_px)
    engine_lock = threading.Lock()
    tracker = SessionTracker(settings.session_idle_s)
    connected = False
    loop = None
    viewers = {}          # board websocket -> session_id it currently shows
    auto_viewers = set()  # board websockets that follow the live camera session
    control_frames = {}
    control_traces = deque(maxlen=2048)  # trace ids published by the mouse UI: not camera activity
    saved_files = {}
    settings.save_dir.mkdir(parents=True, exist_ok=True)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"canvas-{os.urandom(4).hex()}")

    async def send(viewer, message):
        try:
            await viewer.send_json(message)
            return True
        except (WebSocketDisconnect, RuntimeError):
            viewers.pop(viewer, None)
            auto_viewers.discard(viewer)
            return False

    async def broadcast(result):
        for viewer, session_id in tuple(viewers.items()):
            if session_id == result["session_id"]:
                await send(viewer, result)

    def snapshot(session_id):
        with engine_lock:
            return {"type": "snapshot", "session_id": session_id, **engine.snapshot(session_id)}

    async def save(session_id):
        started_at = tracker.started_at.get(session_id)
        if started_at is None:
            return None
        def write():
            with engine_lock:
                return save_drawing(engine, session_id, started_at, settings.save_dir, settings.width, settings.height)
        try:
            name = await asyncio.to_thread(write)
        except OSError:
            log.exception("failed to save drawing of session %s", session_id)
            return None
        if name:
            saved_files[session_id] = name
            for viewer, shown in tuple(viewers.items()):
                if shown == session_id:
                    await send(viewer, {"type": "saved", "session_id": session_id, "file": name})
        return name

    async def show(viewer, session_id):
        viewers[viewer] = session_id
        if await send(viewer, {"type": "session", "session_id": session_id, "auto": viewer in auto_viewers,
                               "live": tracker.active(session_id), "saved": saved_files.get(session_id)}):
            await send(viewer, snapshot(session_id))

    async def on_activity(session_id):
        change = tracker.touch(session_id)
        if not change:
            return
        previous, current = change
        for viewer in tuple(auto_viewers):
            if viewers.get(viewer) != current:
                await show(viewer, current)
        if previous and previous != current:
            await save(previous)

    async def expire_sessions():
        while True:
            await asyncio.sleep(1)
            for session_id in tracker.expire():
                await save(session_id)
                for viewer, shown in tuple(viewers.items()):
                    if shown == session_id:
                        await send(viewer, {"type": "session_end", "session_id": session_id})

    async def broadcast_landmarks(session_id, hands):
        for viewer, shown in tuple(viewers.items()):
            if shown == session_id:
                await send(viewer, {"type": "landmarks", "session_id": session_id, "hands": hands})

    def schedule(coroutine):
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        else:
            coroutine.close()

    def on_connect(mqtt_client, userdata, flags, reason, properties):
        nonlocal connected
        if not reason.is_failure:
            connected = True
            mqtt_client.subscribe([(f"{settings.prefix}/gestures/+", 0), (f"{settings.prefix}/landmarks/+", 0)])

    def on_disconnect(mqtt_client, userdata, flags, reason, properties):
        nonlocal connected
        connected = False

    def on_landmarks(message):
        session_id = message.topic.rsplit("/", 1)[-1][:64]
        data = json.loads(message.payload)
        if not isinstance(data, dict):
            return
        schedule(on_activity(session_id))
        schedule(broadcast_landmarks(session_id, normalise_hands(data)))

    def on_gesture(mqtt_client, message):
        gesture = json.loads(message.payload)
        with engine_lock:
            result = engine.apply(gesture)
        mqtt_client.publish(f"{settings.prefix}/canvas/{result['session_id']}", json.dumps(result, separators=(",", ":")), qos=0, retain=False)
        if gesture.get("trace_id") not in control_traces:
            schedule(on_activity(result["session_id"]))
        schedule(broadcast(result))

    def on_message(mqtt_client, userdata, message):
        if message.retain or len(message.payload) > 256 * 1024:
            return
        try:
            if message.topic.startswith(f"{settings.prefix}/landmarks/"):
                on_landmarks(message)
            else:
                on_gesture(mqtt_client, message)
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
            return

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message

    async def lifespan(app):
        nonlocal loop
        loop = asyncio.get_running_loop()
        expiry = asyncio.create_task(expire_sessions())
        client.connect_async(settings.host, settings.port, 30)
        client.loop_start()
        try:
            yield
        finally:
            expiry.cancel()
            for session_id in tuple(tracker.last_seen):
                await save(session_id)
            client.disconnect()
            await asyncio.to_thread(client.loop_stop)

    app = FastAPI(title="Canvas", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.mount("/drawings", StaticFiles(directory=settings.save_dir), name="drawings")

    @app.get("/")
    async def board():
        return FileResponse(ROOT / "templates" / "board.html")

    @app.websocket("/ws/board")
    async def board_stream(ws: WebSocket):
        requested = ws.query_params.get("session_id", AUTO)[:64] or AUTO
        await ws.accept()
        if requested == AUTO:
            auto_viewers.add(ws)
            requested = tracker.live or "default"
        await show(ws, requested)
        try:
            while True:
                await ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            viewers.pop(ws, None)
            auto_viewers.discard(ws)

    @app.websocket("/ws/control")
    async def control_stream(ws: WebSocket):
        session_id = ws.query_params.get("session_id", "default")[:64]
        await ws.accept()
        allowed = {"DRAW_START", "DRAW_MOVE", "DRAW_END", "ERASE_START", "ERASE_MOVE", "ERASE_END"}
        try:
            while True:
                command = await ws.receive_json()
                if command.get("type") != "gesture" or command.get("gesture") not in allowed:
                    await ws.send_json({"type": "error", "message": "Unsupported drawing command"})
                    continue
                pointer = command.get("pointer")
                if not isinstance(pointer, dict) or not all(isinstance(pointer.get(axis), (int, float)) and 0 <= pointer[axis] <= 1 for axis in ("x", "y")):
                    await ws.send_json({"type": "error", "message": "Invalid canvas pointer"})
                    continue
                if not connected:
                    await ws.send_json({"type": "error", "message": "MQTT is not connected"})
                    continue
                frame_id = control_frames.get(session_id, 0)
                control_frames[session_id] = frame_id + 1
                params = command.get("params") if isinstance(command.get("params"), dict) else {}
                trace_id = uuid.uuid4().hex
                control_traces.append(trace_id)
                gesture = {"schema_version": "1.0.0", "message_type": "gesture", "trace_id": trace_id,
                           "session_id": session_id, "frame_id": frame_id, "timestamp_ms": time.time_ns() // 1_000_000,
                           "gesture": command["gesture"], "pointer": pointer, "confidence": 1.0,
                           "params": params, "gesture_ms": 0}
                info = client.publish(f"{settings.prefix}/gestures/{session_id}", json.dumps(gesture, separators=(",", ":")), qos=0, retain=False)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    await ws.send_json({"type": "error", "message": "MQTT publish failed"})
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok" if connected else "degraded", "service": "canvas", "mqtt_connected": connected,
                "live_session": tracker.live, "active_sessions": len(tracker.last_seen)}

    @app.post("/v1/apply")
    async def apply(gesture: dict):
        try:
            with engine_lock:
                result = engine.apply(gesture)
            await broadcast(result)
            return result
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/v1/drawings")
    async def drawings():
        files = sorted((path for path in settings.save_dir.glob("*.png") if not path.name.startswith(".")),
                       key=lambda path: path.name, reverse=True)
        return {"directory": str(settings.save_dir), "files": [{"file": path.name, "url": f"/drawings/{path.name}"} for path in files]}

    @app.post("/v1/sessions/{session_id}/save")
    async def save_now(session_id: str):
        tracker.started_at.setdefault(session_id, time.time())
        return {"session_id": session_id, "file": await save(session_id)}

    @app.get("/v1/sessions/{session_id}/export.png")
    async def export(session_id: str):
        with engine_lock:
            png = render_png(engine, session_id, settings.width, settings.height)
        return Response(png, media_type="image/png")

    return app


app = create_app()
