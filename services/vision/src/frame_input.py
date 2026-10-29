"""Parsing of gateway → vision frame input (metadata + raw JPEG bytes, no base64).

Two transports carry the same metadata fields:

HTTP  POST /v1/frames, /v1/detect
      body = JPEG bytes (Content-Type: image/jpeg)
      headers: X-Session-Id, X-Frame-Id, X-Ts, [X-Width], [X-Height]

WS    /ws/frames, one binary message per frame:
      [uint32 big-endian meta_len][meta JSON, utf-8][JPEG bytes]
      meta = {"session_id", "frame_id", "ts", "encoding": "jpeg", "width", "height"}

MQTT  topic {prefix}/frames/{session_id}, published by services/gateway:
      same [uint32 BE meta_len][meta JSON][JPEG] layout, but meta is the gateway's
      FrameMessage (shared/contracts/frame.schema.json). ts := captured_at_ms.
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Mapping
from typing import Any

from .contract import FrameMeta

JPEG_MAGIC = b"\xff\xd8\xff"
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_META_LEN = struct.Struct(">I")
MAX_META_BYTES = 4096


class FrameInputError(ValueError):
    pass


def _int_field(name: str, value: Any, *, required: bool) -> int | None:
    if value is None or value == "":
        if required:
            raise FrameInputError(f"missing {name}")
        return None
    if isinstance(value, bool):
        raise FrameInputError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise FrameInputError(f"{name} must be an integer, got {value!r}") from None
    if isinstance(value, float) and value != number:
        raise FrameInputError(f"{name} must be an integer, got {value!r}")
    if number < 0:
        raise FrameInputError(f"{name} must be >= 0")
    return number


def build_meta(fields: Mapping[str, Any]) -> FrameMeta:
    session_id = fields.get("session_id")
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        raise FrameInputError("session_id must match [A-Za-z0-9_.:-]{1,64}")
    encoding = fields.get("encoding") or "jpeg"
    if str(encoding).lower() not in {"jpeg", "jpg", "image/jpeg"}:
        raise FrameInputError(f"unsupported encoding {encoding!r}; only jpeg is accepted")
    return FrameMeta(
        session_id=session_id,
        frame_id=_int_field("frame_id", fields.get("frame_id"), required=True),
        ts=_int_field("ts", fields.get("ts"), required=True),
        width=_int_field("width", fields.get("width"), required=False),
        height=_int_field("height", fields.get("height"), required=False),
    )


def check_jpeg(data: bytes) -> None:
    if not data:
        raise FrameInputError("empty image payload")
    if not data.startswith(JPEG_MAGIC):
        raise FrameInputError("payload is not a JPEG (missing FF D8 FF signature)")


def meta_from_headers(headers: Mapping[str, str]) -> FrameMeta:
    return build_meta(
        {
            "session_id": headers.get("x-session-id"),
            "frame_id": headers.get("x-frame-id"),
            "ts": headers.get("x-ts"),
            "width": headers.get("x-width"),
            "height": headers.get("x-height"),
        }
    )


def parse_ws_frame(message: bytes) -> tuple[FrameMeta, bytes]:
    fields, jpeg = _split_packet(message, MAX_META_BYTES)
    check_jpeg(jpeg)
    return build_meta(fields), jpeg


def _split_packet(message: bytes, max_meta_bytes: int) -> tuple[dict, bytes]:
    if len(message) < _META_LEN.size:
        raise FrameInputError("binary message too short")
    (meta_len,) = _META_LEN.unpack_from(message, 0)
    if meta_len == 0 or meta_len > max_meta_bytes or _META_LEN.size + meta_len > len(message):
        raise FrameInputError(f"invalid meta_len {meta_len}")
    raw_meta = message[_META_LEN.size : _META_LEN.size + meta_len]
    try:
        fields = json.loads(raw_meta.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameInputError(f"meta is not valid JSON: {exc}") from None
    if not isinstance(fields, dict):
        raise FrameInputError("meta must be a JSON object")
    return fields, message[_META_LEN.size + meta_len :]


def parse_mqtt_frame(
    topic: str,
    payload: bytes,
    *,
    topic_prefix: str,
    schema_version: str,
    max_bytes: int = 2 * 1024 * 1024,
) -> tuple[FrameMeta, bytes]:
    """Parse a gateway FrameMessage packet received on {topic_prefix}/frames/{session_id}."""
    if len(payload) > max_bytes:
        raise FrameInputError(f"payload too large ({len(payload)} > {max_bytes} bytes)")
    expected_prefix = f"{topic_prefix}/frames/"
    if not topic.startswith(expected_prefix) or "/" in topic[len(expected_prefix):]:
        raise FrameInputError(f"unexpected topic {topic!r}")
    topic_session = topic[len(expected_prefix):]

    fields, jpeg = _split_packet(payload, 16384)  # same metadata limit as the gateway
    if fields.get("message_type") != "frame":
        raise FrameInputError("message_type must be 'frame'")
    if fields.get("content_type") != "image/jpeg":
        raise FrameInputError("content_type must be 'image/jpeg'")
    version = fields.get("schema_version")
    if not isinstance(version, str) or version.split(".")[0] != schema_version.split(".")[0]:
        raise FrameInputError(f"schema_version {version!r} incompatible with {schema_version}")
    if fields.get("session_id") != topic_session:
        raise FrameInputError("session_id does not match the topic")
    trace_id = fields.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise FrameInputError("missing trace_id")
    byte_length = _int_field("byte_length", fields.get("byte_length"), required=True)
    if byte_length != len(jpeg):
        raise FrameInputError(f"byte_length {byte_length} != JPEG size {len(jpeg)}")
    check_jpeg(jpeg)
    meta = build_meta(
        {
            "session_id": fields.get("session_id"),
            "frame_id": fields.get("frame_id"),
            # The original frame timestamp is the browser capture time.
            "ts": fields.get("captured_at_ms"),
            "width": fields.get("width"),
            "height": fields.get("height"),
        }
    )
    return FrameMeta(meta.session_id, meta.frame_id, meta.ts, meta.width, meta.height, trace_id=trace_id), jpeg


def encode_ws_frame(meta: Mapping[str, Any], jpeg: bytes) -> bytes:
    """Helper for clients/tests: build the binary WS frame message."""
    raw = json.dumps(dict(meta), separators=(",", ":")).encode("utf-8")
    return _META_LEN.pack(len(raw)) + raw + jpeg
