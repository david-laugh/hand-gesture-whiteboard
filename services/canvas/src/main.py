import asyncio
import io
import json
import os
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw

from .engine import CanvasEngine

ROOT = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self):
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "11883"))
        self.prefix = os.getenv("MQTT_TOPIC_PREFIX", "hgw/v1").strip("/")
        self.width = int(os.getenv("CANVAS_WIDTH", "1280"))
        self.height = int(os.getenv("CANVAS_HEIGHT", "720"))
        self.smoothing = float(os.getenv("CANVAS_SMOOTHING", "1.0"))
        self.segment_px = float(os.getenv("CANVAS_SEGMENT_PX", "8"))


def create_app(settings=None):
    settings = settings or Settings()
    engine = CanvasEngine(settings.width, settings.height, settings.smoothing, settings.segment_px)
    connected = False
    loop = None
    viewers = {}
    control_frames = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"canvas-{os.urandom(4).hex()}")

    async def broadcast(result):
        stale = []
        for viewer, session_id in tuple(viewers.items()):
            if session_id != result["session_id"]:
                continue
            try:
                await viewer.send_json(result)
            except (WebSocketDisconnect, RuntimeError):
                stale.append(viewer)
        for viewer in stale:
            viewers.pop(viewer, None)

    def on_connect(mqtt_client, userdata, flags, reason, properties):
        nonlocal connected
        if not reason.is_failure:
            connected = True
            mqtt_client.subscribe(f"{settings.prefix}/gestures/+", qos=0)

    def on_disconnect(mqtt_client, userdata, flags, reason, properties):
        nonlocal connected
        connected = False

    def on_message(mqtt_client, userdata, message):
        if message.retain or len(message.payload) > 256 * 1024:
            return
        try:
            gesture = json.loads(message.payload)
            result = engine.apply(gesture)
            mqtt_client.publish(f"{settings.prefix}/canvas/{result['session_id']}", json.dumps(result, separators=(",", ":")), qos=0, retain=False)
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(broadcast(result), loop)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message

    async def lifespan(app):
        nonlocal loop
        loop = asyncio.get_running_loop()
        client.connect_async(settings.host, settings.port, 30)
        client.loop_start()
        try:
            yield
        finally:
            client.disconnect()
            await asyncio.to_thread(client.loop_stop)

    app = FastAPI(title="Canvas", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    async def board():
        return FileResponse(ROOT / "templates" / "board.html")

    @app.websocket("/ws/board")
    async def board_stream(ws: WebSocket):
        session_id = ws.query_params.get("session_id", "default")[:64]
        await ws.accept()
        viewers[ws] = session_id
        snapshot = engine.snapshot(session_id)
        await ws.send_json({"type": "snapshot", "session_id": session_id, **snapshot})
        try:
            while True:
                await ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            viewers.pop(ws, None)

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
                gesture = {"schema_version": "1.0.0", "message_type": "gesture", "trace_id": uuid.uuid4().hex,
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
        return {"status": "ok" if connected else "degraded", "service": "canvas", "mqtt_connected": connected}

    @app.post("/v1/apply")
    async def apply(gesture: dict):
        try:
            result = engine.apply(gesture)
            await broadcast(result)
            return result
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/v1/sessions/{session_id}/export.png")
    async def export(session_id: str):
        state = engine.sessions.get(session_id)
        image = Image.new("RGB", (settings.width, settings.height), "white")
        draw = ImageDraw.Draw(image)
        if state:
            for stroke in state.strokes.values():
                if not stroke.visible or not stroke.points:
                    continue
                points = [(point["x"], point["y"]) for point in stroke.points]
                color = "white" if stroke.erase else stroke.style.get("color", "#172554")
                width = max(1, round(stroke.style.get("width", 4)))
                if len(points) == 1:
                    x, y = points[0]; draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=color)
                else:
                    draw.line(points, fill=color, width=width, joint="curve")
        output = io.BytesIO(); image.save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png")

    return app


app = create_app()
