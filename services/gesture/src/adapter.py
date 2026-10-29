"""입력 어댑터: 두 가지 입력 형식을 내부 LandmarkMessage 하나로 바꾼다.

- 레포 형식 (message_type 있음): shared/contracts/landmark.schema.json
- vision 자체 형식 (message_type 없음): services/vision/src/contract.py
    {"type": "landmarks", "session_id", "frame_id", "ts",
     "hands": [{"handedness", "score", "landmarks": [[x, y, z], ... 21]}], "gesture": null}
"""
import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from . import config
from .logs import log_json
from .models import LandmarkMessage

_NOT_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")
_REPO_REQUIRED = ("schema_version", "trace_id", "session_id", "frame_id", "timestamp_ms",
                  "image_width", "image_height", "hands", "vision_ms")
ID_MIN, ID_MAX = 8, 64


class MessageError(ValueError):
    """잘못된 입력. code는 api-contract.md 3.3의 오류 코드."""
    code = "SCHEMA_VIOLATION"


class VersionMismatch(MessageError):
    code = "SCHEMA_VERSION_MISMATCH"


def decode(payload: bytes | str) -> dict[str, Any]:
    """MQTT payload(bytes) → dict."""
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MessageError(f"payload is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise MessageError("payload must be a JSON object")
    return data


def sanitize_session_id(session_id: str) -> str:
    """schema가 허용하지 않는 문자는 "_"로 바꾸고 64자로 자른다."""
    clean = _NOT_ALLOWED.sub("_", session_id)[:ID_MAX]
    if clean != session_id:
        log_json(logging.WARNING, event="session_id_sanitized", original=session_id, session_id=clean)
    return clean


def make_trace_id(session_id: str, frame_id: int) -> str:
    """"{session_id}-{frame_id}". 8자보다 짧으면 frame_id 앞을 0으로 채우고, 64자를 넘으면 session_id를 자른다."""
    frame = str(frame_id)
    frame = frame.zfill(max(len(frame), ID_MIN - len(session_id) - 1))
    return f"{session_id[:ID_MAX - len(frame) - 1]}-{frame}"


def to_landmark_message(data: dict[str, Any]) -> LandmarkMessage:
    if "message_type" in data:
        return _from_repo(data)
    return _from_vision(data)


def _validate(data: dict[str, Any]) -> LandmarkMessage:
    try:
        return LandmarkMessage.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise MessageError(f"{where}: {first['msg']}") from None


def _from_repo(data: dict[str, Any]) -> LandmarkMessage:
    missing = [k for k in _REPO_REQUIRED if k not in data]
    if missing:
        raise MessageError(f"missing field(s): {', '.join(missing)}")
    version = data["schema_version"]
    if isinstance(version, str) and version.split(".")[0] != config.SCHEMA_MAJOR:
        raise VersionMismatch(f"expected major {config.SCHEMA_MAJOR}, got {version}")
    return _validate(data)


def _from_vision(data: dict[str, Any]) -> LandmarkMessage:
    if data.get("type") != "landmarks":
        raise MessageError("unknown message: no message_type and type != 'landmarks'")
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise MessageError("session_id must be a non-empty string")
    frame_id = data.get("frame_id")
    if not isinstance(frame_id, int) or isinstance(frame_id, bool) or frame_id < 0:
        raise MessageError("frame_id must be a non-negative integer")
    hands = data.get("hands")
    if not isinstance(hands, list):
        raise MessageError("hands must be a list")

    session_id = sanitize_session_id(session_id)
    out_hands = []
    for i, hand in enumerate(hands):
        if not isinstance(hand, dict) or not isinstance(hand.get("landmarks"), list):
            raise MessageError(f"hands.{i} must have a landmarks list")
        points = []
        for j, p in enumerate(hand["landmarks"]):
            if not isinstance(p, list) or len(p) != 3:
                raise MessageError(f"hands.{i}.landmarks.{j} must be [x, y, z]")
            points.append({"id": j, "x": p[0], "y": p[1], "z": p[2]})
        out_hands.append({
            "hand_index": i,
            "handedness": hand.get("handedness"),
            "confidence": hand.get("score"),
            "landmarks": points,
        })

    return _validate({
        "trace_id": make_trace_id(session_id, frame_id),
        "session_id": session_id,
        "frame_id": frame_id,
        "timestamp_ms": data.get("ts"),
        "hands": out_hands,
    })
