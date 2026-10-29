"""Vision → Gesture message contract (agreed with the gesture owner).

    {
      "type": "landmarks",
      "session_id": "...", "frame_id": 1, "ts": 1793235000123,
      "hands": [{"handedness": "Right", "score": 0.95, "landmarks": [[x, y, z], ... 21]}],
      "gesture": null
    }

- session_id / frame_id / ts are copied unchanged from the input frame.
- score is the MediaPipe *handedness classification* score, not a detection confidence.
- x, y are MediaPipe normalized image coordinates. They are not clamped and can fall
  slightly outside [0, 1] when part of the hand is outside the frame.
- z is MediaPipe's relative depth (wrist-origin), passed through unchanged.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

MESSAGE_TYPE = "landmarks"
NUM_LANDMARKS = 21
HANDEDNESS_LABELS = ("Left", "Right")


@dataclass(frozen=True)
class FrameMeta:
    session_id: str
    frame_id: int
    ts: int
    width: int | None = None
    height: int | None = None
    encoding: str = "jpeg"
    # Issued by the gateway (MQTT input). Kept for vision logs/metrics only: the agreed
    # vision → gesture message has no trace_id field.
    trace_id: str | None = None


@dataclass(frozen=True)
class Hand:
    handedness: str
    score: float
    landmarks: list[tuple[float, float, float]] = field(default_factory=list)


def build_landmarks_message(meta: FrameMeta, hands: list[Hand]) -> dict[str, Any]:
    out_hands = []
    for hand in hands:
        if hand.handedness not in HANDEDNESS_LABELS:
            raise ValueError(f"invalid handedness {hand.handedness!r}")
        if len(hand.landmarks) != NUM_LANDMARKS:
            raise ValueError(f"expected {NUM_LANDMARKS} landmarks, got {len(hand.landmarks)}")
        out_hands.append(
            {
                "handedness": hand.handedness,
                "score": float(hand.score),
                "landmarks": [[float(x), float(y), float(z)] for x, y, z in hand.landmarks],
            }
        )
    return {
        "type": MESSAGE_TYPE,
        "session_id": meta.session_id,
        "frame_id": meta.frame_id,
        "ts": meta.ts,
        "hands": out_hands,
        "gesture": None,
    }


def serialize(message: dict[str, Any]) -> str:
    # allow_nan=False: NaN/Infinity are not valid JSON and would break the receiver.
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def validate_landmarks_message(message: Any, *, max_hands: int = 1) -> None:
    """Raise ValueError if `message` does not follow the vision → gesture contract."""

    def fail(reason: str) -> None:
        raise ValueError(f"contract violation: {reason}")

    if not isinstance(message, dict):
        fail("message must be an object")
    expected_keys = {"type", "session_id", "frame_id", "ts", "hands", "gesture"}
    if set(message) != expected_keys:
        fail(f"keys must be exactly {sorted(expected_keys)}, got {sorted(message)}")
    if message["type"] != MESSAGE_TYPE:
        fail("type must be 'landmarks'")
    if not isinstance(message["session_id"], str) or not message["session_id"]:
        fail("session_id must be a non-empty string")
    for key in ("frame_id", "ts"):
        value = message[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            fail(f"{key} must be a non-negative integer")
    if message["gesture"] is not None:
        fail("gesture must be null")
    hands = message["hands"]
    if not isinstance(hands, list) or len(hands) > max_hands:
        fail(f"hands must be a list with at most {max_hands} item(s)")
    for hand in hands:
        if not isinstance(hand, dict) or set(hand) != {"handedness", "score", "landmarks"}:
            fail("hand must have exactly handedness, score, landmarks")
        if hand["handedness"] not in HANDEDNESS_LABELS:
            fail("handedness must be 'Left' or 'Right'")
        score = hand["score"]
        if not isinstance(score, (int, float)) or not 0.0 <= score <= 1.0:
            fail("score must be a number in [0, 1]")
        landmarks = hand["landmarks"]
        if not isinstance(landmarks, list) or len(landmarks) != NUM_LANDMARKS:
            fail(f"landmarks must contain exactly {NUM_LANDMARKS} points")
        for point in landmarks:
            if (
                not isinstance(point, list)
                or len(point) != 3
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)
            ):
                fail("each landmark must be [x, y, z] with finite numbers")
