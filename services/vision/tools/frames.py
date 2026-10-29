"""Deterministic 640x480 JPEG frames cut from the official MediaPipe test image.

These are crops of a still photo, not camera footage: the "continuous" sequence slides
the crop window to move the hands across the frame. Inference on them is real.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

FRAME_W, FRAME_H = 640, 480
JPEG_QUALITY = 80

# woman_hands.jpg is 640x960 (portrait). Rows ~320-770 contain the hands, rows 0-300 are a plain wall.
HAND_CROP_Y = 300
NO_HAND_CROP = (0, 300)  # y0, y1 → resized to 640x480


def load_bgr(path: Path) -> np.ndarray:
    # cv2.imread cannot open non-ASCII paths on Windows (e.g. a Korean user name), so decode from bytes.
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"cannot read image {path}")
    return image


def save_image(path: Path, bgr: np.ndarray) -> None:
    ok, buf = cv2.imencode(Path(path).suffix or ".jpg", bgr)
    if not ok:
        raise RuntimeError(f"cannot encode image for {path}")
    buf.tofile(str(path))


def encode_jpeg(bgr: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def _crop(bgr: np.ndarray, y: int) -> np.ndarray:
    y = max(0, min(y, bgr.shape[0] - FRAME_H))
    crop = bgr[y : y + FRAME_H, :FRAME_W]
    if crop.shape[:2] != (FRAME_H, FRAME_W):
        crop = cv2.resize(crop, (FRAME_W, FRAME_H))
    return crop


def hand_frame(path: Path) -> bytes:
    return encode_jpeg(_crop(load_bgr(path), HAND_CROP_Y))


def no_hand_frame(path: Path) -> bytes:
    y0, y1 = NO_HAND_CROP
    wall = load_bgr(path)[y0:y1, :FRAME_W]
    return encode_jpeg(cv2.resize(wall, (FRAME_W, FRAME_H)))


def gateway_packet(session_id: str, frame_id: int, jpeg: bytes, captured_at_ms: int | None = None) -> bytes:
    """Build the MQTT payload the way services/gateway publishes it (FrameMessage + JPEG)."""
    import json
    import struct
    import time
    import uuid

    now = int(time.time() * 1000)
    meta = {
        "frame_id": frame_id,
        "captured_at_ms": now if captured_at_ms is None else captured_at_ms,
        "width": FRAME_W,
        "height": FRAME_H,
        "schema_version": "1.0.0",
        "message_type": "frame",
        "trace_id": uuid.uuid4().hex,
        "session_id": session_id,
        "timestamp_ms": now,
        "received_at_ms": now,
        "byte_length": len(jpeg),
        "content_type": "image/jpeg",
    }
    header = json.dumps(meta, separators=(",", ":")).encode()
    return struct.pack(">I", len(header)) + header + jpeg


def sliding_sequence(path: Path, count: int = 30, start_y: int = HAND_CROP_Y, step: int = 6) -> list[bytes]:
    bgr = load_bgr(path)
    return [encode_jpeg(_crop(bgr, start_y + i * step)) for i in range(count)]
