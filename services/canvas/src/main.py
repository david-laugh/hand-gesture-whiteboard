import asyncio
import io
import json
import os
import time

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageDraw

from .engine import CanvasEngine


class Settings:
    def __init__(self):
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "11883"))
        self.prefix = os.getenv("MQTT_TOPIC_PREFIX", "hgw/v1").strip("/")
        self.width = int(os.getenv("CANVAS_WIDTH", "1280"))
        self.height = int(os.getenv("CANVAS_HEIGHT", "720"))
        self.smoothing = float(os.getenv("CANVAS_SMOOTHING", ".55"))
        self.segment_px = float(os.getenv("CANVAS_SEGMENT_PX", "8"))


def create_app(settings=None):
    settings = settings or Settings()
    engine = CanvasEngine(settings.width, settings.height, settings.smoothing, settings.segment_px)
    connected = False
    loop = None
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"canvas-{os.urandom(4).hex()}")

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

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok" if connected else "degraded", "service": "canvas", "mqtt_connected": connected}

    @app.post("/v1/apply")
    async def apply(gesture: dict):
        try:
            return engine.apply(gesture)
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
