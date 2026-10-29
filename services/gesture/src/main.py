"""gesture 서비스 (Container C) 진입점.

입력 (둘 다 켜 둘 수 있음)
- MQTT: {prefix}/landmarks/+ 구독 (mqtt_io.py, MQTT_INPUT_ENABLED)
- WebSocket: /ws/landmarks, vision이 client로 접속해 텍스트 메시지 1개 = 프레임 1개 (WS_INPUT_ENABLED)
출력
- MQTT: {prefix}/gestures/{session_id} 발행
HTTP /v1/classify 는 테스트·확인용. 같은 어댑터와 처리 함수를 쓰지만 발행하지 않는다.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import config
from .adapter import MessageError, to_landmark_message
from .logs import log_json
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


def _publish(app: FastAPI, out: dict) -> None:
    bridge: MqttBridge | None = getattr(app.state, "mqtt", None)
    if bridge is None:
        log_json(logging.WARNING, event="publish_skipped_mqtt_disabled",
                 frame_id=out["frame_id"], gesture=out["gesture"])
        return
    bridge.publish(out)


if config.WS_INPUT_ENABLED:

    @app.websocket("/ws/landmarks")
    async def ws_landmarks(websocket: WebSocket) -> None:
        """vision → gesture. 단방향이라 응답은 보내지 않는다. 메시지는 받은 순서대로 하나씩 처리한다."""
        await websocket.accept()
        app_ = websocket.app
        peer = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "?"
        seen_sessions: set[str] = set()  # 이 연결로 들어온 세션 (끊길 때 그리던 획을 닫기 위해)
        log_json(event="ws_connected", peer=peer)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                payload = message.get("text") if message.get("text") is not None else message.get("bytes")
                try:
                    out = processor.process_payload(payload, source="ws", origin=peer)
                except Exception:  # 처리 중 예상 못 한 오류도 연결은 유지
                    logging.getLogger(config.SERVICE_NAME).exception("ws process failed (peer=%s)", peer)
                    continue
                if out is not None:
                    seen_sessions.add(out["session_id"])
                    _publish(app_, out)
        except WebSocketDisconnect:
            pass
        finally:
            for sid in seen_sessions:
                end = processor.end_session(sid, source="ws_disconnect")
                if end is not None:
                    _publish(app_, end)
            log_json(event="ws_disconnected", peer=peer, sessions=sorted(seen_sessions))
