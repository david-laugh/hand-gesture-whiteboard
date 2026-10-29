"""Vision service (Container B): FastAPI app.

Run: uvicorn src.main:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import mediapipe as mp
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from .config import Settings
from .detector import MEDIAPIPE_API, RUNNING_MODE, HandDetector
from .frame_input import FrameInputError, check_jpeg, meta_from_headers, parse_ws_frame
from .metrics import VisionMetrics
from .mqtt_bus import MqttBus
from .pipeline import FramePipeline

log = logging.getLogger("vision")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _log_metrics_periodically(metrics: VisionMetrics, interval_s: float) -> None:
    last_input = -1
    while True:
        await asyncio.sleep(interval_s)
        snap = metrics.snapshot()
        if snap["input_frame_count"] == last_input:
            continue
        last_input = snap["input_frame_count"]
        t = snap["timings_ms"]
        log.info(
            "metrics mqtt_msgs=%d input=%d processed=%d dropped=%d detected=%d no_hand=%d invalid=%d failed=%d "
            "published=%d publish_dropped=%d input_fps=%.1f processing_fps=%.1f inference_p50=%s p95=%s",
            snap["mqtt_message_count"], snap["input_frame_count"], snap["processed_frame_count"], snap["dropped_frame_count"],
            snap["detected_frame_count"], snap["no_hand_frame_count"], snap["invalid_frame_count"],
            snap["failed_frame_count"], snap["publish_count"], snap["publish_dropped_count"],
            snap["input_fps"], snap["processing_fps"],
            t["inference_ms"]["p50"], t["inference_ms"]["p95"],
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _configure_logging(settings.log_level)
        metrics = VisionMetrics(window=settings.metrics_window)
        # Raises ModelLoadError → startup fails and uvicorn exits non-zero (no silent fallback).
        detector = HandDetector(settings)
        # One MQTT connection: frames in from the gateway, landmarks out to gesture.
        bus = MqttBus(settings, metrics, on_frame=lambda meta, jpeg: pipeline.submit(meta, jpeg)) if settings.mqtt_enabled else None
        publisher = bus if bus is not None and settings.publish_enabled else None
        if bus is None:
            log.warning("MQTT is disabled: no gateway frames in, no landmarks out (HTTP/WS inputs + /v1/detect only)")
        elif publisher is None:
            log.warning("publishing landmarks to gesture is disabled (PUBLISH_ENABLED=false)")
        pipeline = FramePipeline(detector, metrics, publisher)
        await pipeline.start()
        if bus is not None:
            await bus.start()
        metrics_task = asyncio.create_task(_log_metrics_periodically(metrics, settings.metrics_log_interval_s))

        app.state.settings = settings
        app.state.metrics = metrics
        app.state.detector = detector
        app.state.publisher = publisher
        app.state.pipeline = pipeline
        app.state.bus = bus
        try:
            yield
        finally:
            metrics_task.cancel()
            await pipeline.stop()
            if bus is not None:
                await bus.stop()
            detector.close()
            log.info("vision service stopped")

    app = FastAPI(title="vision", version="1.0.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        state = request.app.state
        return {
            "status": "ok",
            "service": "vision",
            "model_loaded": True,
            "model_path": state.settings.model_path,
            "mediapipe_version": mp.__version__,
            "api": MEDIAPIPE_API,
            "running_mode": RUNNING_MODE,
            "num_hands": state.settings.num_hands,
            "active_sessions": state.detector.session_count,
            "mqtt_enabled": state.bus is not None,
            "mqtt_broker": state.bus.broker if state.bus else None,
            "mqtt_connected": bool(state.bus and state.bus.connected),
            "frames_topic": state.bus.frames_topic if state.bus else None,
            "publish_enabled": state.publisher is not None,
            "landmarks_topic": state.bus.landmarks_topic("{session_id}") if state.publisher else None,
        }

    @app.get("/metrics")
    async def get_metrics(request: Request) -> dict:
        snap = request.app.state.metrics.snapshot()
        bus = request.app.state.bus
        snap["mqtt_connected"] = bool(bus and bus.connected)
        return snap

    @app.get("/metrics/frames")
    async def get_frame_records(request: Request, limit: int = 50) -> list[dict]:
        return request.app.state.metrics.recent_frames(max(1, min(limit, 300)))

    @app.post("/metrics/reset", status_code=204)
    async def reset_metrics(request: Request) -> Response:
        request.app.state.metrics.reset()
        return Response(status_code=204)

    async def _read_frame(request: Request):
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in {"image/jpeg", "image/jpg"}:
            raise HTTPException(415, "Content-Type must be image/jpeg (raw JPEG body)")
        body = await request.body()
        try:
            meta = meta_from_headers(request.headers)
            check_jpeg(body)
        except FrameInputError as exc:
            request.app.state.metrics.incr("invalid_frame_count")
            raise HTTPException(400, str(exc)) from None
        return meta, body

    @app.post("/v1/frames", status_code=202)
    async def submit_frame(request: Request) -> dict:
        """Streaming input: queue the frame (latest-wins); the result is published to gesture over MQTT."""
        meta, body = await _read_frame(request)
        result = request.app.state.pipeline.submit(meta, body)
        return {
            "accepted": result.accepted,
            "session_id": meta.session_id,
            "frame_id": meta.frame_id,
            "replaced_frame_id": result.replaced_frame_id,
        }

    @app.post("/v1/detect")
    async def detect(request: Request) -> Response:
        """Synchronous input for testing/integration: returns the landmarks message directly.

        Not published to gesture. Stage timings are returned in X-*-Ms headers so the body
        stays exactly the agreed contract.
        """
        meta, body = await _read_frame(request)
        metrics = request.app.state.metrics
        metrics.mark_input()
        result = await asyncio.to_thread(request.app.state.pipeline.process_and_record, meta, body)
        if result is None:
            raise HTTPException(400, "frame could not be processed (see /metrics/frames)")
        return Response(
            content=result.payload,
            media_type="application/json",
            headers={
                "X-Decode-Ms": f"{result.decode_ms:.3f}",
                "X-Inference-Ms": f"{result.inference_ms:.3f}",
                "X-Serialization-Ms": f"{result.serialization_ms:.3f}",
                "X-Image-Size": f"{result.image_width}x{result.image_height}",
            },
        )

    @app.delete("/v1/sessions/{session_id}")
    async def reset_session(request: Request, session_id: str) -> JSONResponse:
        removed = await asyncio.to_thread(request.app.state.detector.reset_session, session_id)
        return JSONResponse({"session_id": session_id, "reset": removed})

    @app.websocket("/ws/frames")
    async def ws_frames(websocket: WebSocket) -> None:
        """Streaming input over WS: binary [uint32 BE meta_len][meta JSON][JPEG]. No replies on success."""
        await websocket.accept()
        state = websocket.app.state
        peer = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "?"
        log.info("frame WS connected: %s", peer)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is None:
                    await websocket.send_json({"type": "error", "error": "send frames as binary messages"})
                    continue
                try:
                    meta, jpeg = parse_ws_frame(data)
                except FrameInputError as exc:
                    state.metrics.incr("invalid_frame_count")
                    await websocket.send_json({"type": "error", "error": str(exc)})
                    continue
                state.pipeline.submit(meta, jpeg)
        except WebSocketDisconnect:
            pass
        finally:
            log.info("frame WS disconnected: %s", peer)

    return app


app = create_app()
