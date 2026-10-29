"""gesture 서비스 (Container C) 진입점.

- MQTT: {prefix}/landmarks/+ 구독 → 처리 → {prefix}/gestures/{session_id} 발행 (mqtt_io.py)
- HTTP: /v1/classify 는 테스트·확인용. 같은 어댑터와 처리 함수를 쓰지만 발행하지 않는다.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import config
from .adapter import MessageError, to_landmark_message
from .mqtt_io import MqttBridge
from .processor import GestureProcessor, now_ms

processor = GestureProcessor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bridge = None
    if config.MQTT_ENABLED:
        bridge = MqttBridge(processor)
        bridge.start()
    app.state.mqtt = bridge
    yield
    if bridge is not None:
        await asyncio.to_thread(bridge.stop)


app = FastAPI(title="gesture", lifespan=lifespan)


def _error_body(code: str, message: str, ids: dict) -> dict:
    """api-contract.md 3.3 오류 형식."""
    return {
        "schema_version": config.SCHEMA_VERSION,
        "message_type": "error",
        "trace_id": ids.get("trace_id", ""),
        "session_id": ids.get("session_id", ""),
        "frame_id": ids.get("frame_id", 0),
        "timestamp_ms": now_ms(),
        "service": config.SERVICE_NAME,
        "code": code,
        "message": message,
    }


def _ids_from(body: object, request: Request) -> dict:
    """오류 응답에 넣을 trace_id/session_id/frame_id를 body → header 순서로 찾는다."""
    body = body if isinstance(body, dict) else {}
    ids = {
        "trace_id": body.get("trace_id") or request.headers.get("X-Trace-Id", ""),
        "session_id": body.get("session_id") or request.headers.get("X-Session-Id", ""),
        "frame_id": body.get("frame_id", request.headers.get("X-Frame-Id", 0)),
    }
    if not isinstance(ids["trace_id"], str):
        ids["trace_id"] = ""
    if not isinstance(ids["session_id"], str):
        ids["session_id"] = ""
    try:
        ids["frame_id"] = max(0, int(ids["frame_id"]))
    except (TypeError, ValueError):
        ids["frame_id"] = 0
    return ids


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = jsonable_encoder(exc.errors())
    first = errors[0] if errors else {}
    where = ".".join(str(p) for p in first.get("loc", []))
    message = f"{where}: {first.get('msg', 'invalid request')}"
    return JSONResponse(
        status_code=422,
        content=_error_body("SCHEMA_VIOLATION", message, _ids_from(exc.body, request)),
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": config.SERVICE_NAME}


@app.post("/v1/sessions/{session_id}/reset", status_code=204)
def reset_session(session_id: str):
    processor.reset(session_id)
    return Response(status_code=204)


@app.post("/v1/classify")
def classify(request: Request, payload: dict[str, Any] = Body(...)):
    """레포 LandmarkMessage 또는 vision 형식 → GestureMessage. 응답만 하고 MQTT로 발행하지 않는다."""
    try:
        msg = to_landmark_message(payload)
    except MessageError as exc:
        return JSONResponse(status_code=422, content=_error_body(exc.code, str(exc), _ids_from(payload, request)))
    return processor.process(msg, source="http")
