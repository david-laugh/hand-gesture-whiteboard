"""gesture 서비스 (Container C) HTTP 진입점."""
import json
import logging
import time

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import config
from .models import Hand, LandmarkMessage
from .state_machine import GestureStateMachine

logger = logging.getLogger(config.SERVICE_NAME)
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)
logger.propagate = False

app = FastAPI(title="gesture")

# session_id → 세션 상태 머신 (메모리에만 보관)
sessions: dict[str, GestureStateMachine] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _error_body(code: str, message: str, ids: dict) -> dict:
    """api-contract.md 3.3 오류 형식."""
    return {
        "schema_version": config.SCHEMA_VERSION,
        "message_type": "error",
        "trace_id": ids.get("trace_id", ""),
        "session_id": ids.get("session_id", ""),
        "frame_id": ids.get("frame_id", 0),
        "timestamp_ms": _now_ms(),
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
    sessions.pop(session_id, None)
    return Response(status_code=204)


def _pick_hand(msg: LandmarkMessage) -> Hand | None:
    """손은 1개만 쓴다. 여러 개면 confidence가 가장 높은 손."""
    if not msg.hands:
        return None
    return max(msg.hands, key=lambda h: h.confidence)


@app.post("/v1/classify")
def classify(msg: LandmarkMessage):
    started = time.perf_counter()

    if msg.schema_version.split(".")[0] != config.SCHEMA_MAJOR:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "SCHEMA_VERSION_MISMATCH",
                f"expected major {config.SCHEMA_MAJOR}, got {msg.schema_version}",
                {"trace_id": msg.trace_id, "session_id": msg.session_id, "frame_id": msg.frame_id},
            ),
        )

    hand = _pick_hand(msg)
    machine = sessions.setdefault(msg.session_id, GestureStateMachine())
    result = machine.step(hand.landmarks if hand else None, msg.timestamp_ms, _now_ms())

    gesture = result.gesture
    pointer = None if result.pointer is None else {"x": result.pointer[0], "y": result.pointer[1]}
    confidence = hand.confidence if hand else 1.0
    hand_index = hand.hand_index if hand else None
    params = {"state_frames": result.state_frames}
    if result.stroke_id is not None:
        params["stroke_id"] = result.stroke_id
    gesture_ms = round((time.perf_counter() - started) * 1000, 3)

    logger.info(json.dumps({
        "service": config.SERVICE_NAME,
        "trace_id": msg.trace_id,
        "frame_id": msg.frame_id,
        "gesture": gesture,
        "state_frames": params["state_frames"],
        "gesture_ms": gesture_ms,
    }))

    return {
        "schema_version": config.SCHEMA_VERSION,
        "message_type": "gesture",
        "trace_id": msg.trace_id,
        "session_id": msg.session_id,
        "frame_id": msg.frame_id,
        "timestamp_ms": _now_ms(),
        "gesture": gesture,
        "pointer": pointer,
        "confidence": confidence,
        "hand_index": hand_index,
        "params": params,
        "gesture_ms": gesture_ms,
    }
