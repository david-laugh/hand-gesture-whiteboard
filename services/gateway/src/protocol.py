"""Shared framing: uint32 metadata length, UTF-8 JSON, then JPEG bytes."""
import json
import struct
import time
import uuid


def now_ms():
    return time.time_ns() // 1_000_000


def encode_frame(meta, jpeg):
    header = json.dumps(meta, separators=(",", ":"), allow_nan=False).encode()
    return struct.pack(">I", len(header)) + header + jpeg


def decode_frame(data, limit=2 * 1024 * 1024):
    if len(data) > limit or len(data) < 5:
        raise ValueError("Invalid frame size")
    size = struct.unpack_from(">I", data)[0]
    if not 0 < size <= 16384 or 4 + size >= len(data):
        raise ValueError("Invalid metadata length")
    try:
        meta = json.loads(data[4:4 + size])
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid metadata JSON") from exc
    if not isinstance(meta, dict):
        raise ValueError("Metadata must be an object")
    return meta, data[4 + size:]


def jpeg_dimensions(data):
    """Read JPEG SOF dimensions without decoding or retaining the image."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("Frame payload is not a JPEG")
    index = 2
    while index + 9 <= len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            if width and height:
                return width, height
            break
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 4 > len(data):
            break
        length = struct.unpack(">H", data[index + 2:index + 4])[0]
        if length < 2:
            break
        index += 2 + length
    raise ValueError("JPEG dimensions are missing")


class Contracts:
    def __init__(self, settings):
        self.version = settings.schema_version
    def validate(self, kind, message):
        if kind != "frame" or not isinstance(message, dict):
            raise ValueError("Unsupported message")
        required = {"schema_version", "message_type", "trace_id", "session_id", "frame_id", "timestamp_ms",
                    "content_type", "width", "height", "captured_at_ms", "byte_length"}
        if not required <= message.keys() or message["message_type"] != "frame" or message["content_type"] != "image/jpeg":
            raise ValueError("Frame metadata does not match the contract")
        if not isinstance(message["frame_id"], int) or message["frame_id"] < 0:
            raise ValueError("Invalid frame_id")
        if not all(isinstance(message[key], int) and message[key] > 0 for key in ("width", "height", "byte_length")):
            raise ValueError("Invalid frame dimensions or size")
        if message["schema_version"].split(".")[0] != self.version.split(".")[0]:
            raise ValueError("Schema major version mismatch")

    def browser_frame(self, data, session_id, previous_id, limit):
        received = now_ms()
        meta, jpeg = decode_frame(data, limit)
        allowed = {"frame_id", "captured_at_ms", "width", "height", "jpeg_quality", "mirrored"}
        if set(meta) - allowed:
            raise ValueError("Unexpected browser metadata field")
        envelope = dict(meta, schema_version=self.version, message_type="frame",
                        trace_id=uuid.uuid4().hex, session_id=session_id,
                        timestamp_ms=received, received_at_ms=received,
                        byte_length=len(jpeg), content_type="image/jpeg")
        self.validate("frame", envelope)
        if envelope["frame_id"] <= previous_id:
            raise ValueError("frame_id must increase within a session")
        if jpeg_dimensions(jpeg) != (meta["width"], meta["height"]):
            raise ValueError("JPEG dimensions do not match metadata")
        return envelope, jpeg
