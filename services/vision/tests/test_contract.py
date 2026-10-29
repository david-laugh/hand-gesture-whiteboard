"""Unit tests for the vision → gesture contract builder (synthetic values, NO inference)."""

from __future__ import annotations

import json

import pytest

from src.contract import FrameMeta, Hand, build_landmarks_message, serialize, validate_landmarks_message
from src.frame_input import FrameInputError, encode_ws_frame, meta_from_headers, parse_ws_frame

META = FrameMeta("session-001", 7, 1793235000123)
FAKE_LANDMARKS = [(0.1 + i * 0.01, 0.2 + i * 0.01, -0.05 * i) for i in range(21)]


def test_message_shape_and_preserved_ids():
    msg = build_landmarks_message(META, [Hand("Right", 0.95, FAKE_LANDMARKS)])
    validate_landmarks_message(msg)
    assert msg["type"] == "landmarks"
    assert (msg["session_id"], msg["frame_id"], msg["ts"]) == ("session-001", 7, 1793235000123)
    assert msg["gesture"] is None
    assert msg["hands"][0]["score"] == 0.95
    assert len(msg["hands"][0]["landmarks"]) == 21


def test_z_is_passed_through_unchanged():
    msg = build_landmarks_message(META, [Hand("Left", 0.8, FAKE_LANDMARKS)])
    assert [p[2] for p in msg["hands"][0]["landmarks"]] == [z for _, _, z in FAKE_LANDMARKS]


def test_no_hand_message():
    msg = build_landmarks_message(META, [])
    validate_landmarks_message(msg)
    assert msg["hands"] == [] and msg["gesture"] is None


def test_serialization_roundtrip():
    msg = build_landmarks_message(META, [Hand("Right", 0.9, FAKE_LANDMARKS)])
    assert json.loads(serialize(msg)) == msg


def test_serialization_rejects_nan():
    bad = [(float("nan"), 0.0, 0.0)] + FAKE_LANDMARKS[1:]
    with pytest.raises(ValueError):
        serialize(build_landmarks_message(META, [Hand("Right", 0.9, bad)]))


@pytest.mark.parametrize("count", [0, 20, 22])
def test_builder_rejects_wrong_landmark_count(count):
    with pytest.raises(ValueError):
        build_landmarks_message(META, [Hand("Right", 0.9, FAKE_LANDMARKS[:1] * count)])


def test_builder_rejects_unknown_handedness():
    with pytest.raises(ValueError):
        build_landmarks_message(META, [Hand("left", 0.9, FAKE_LANDMARKS)])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(gesture="DRAW"),
        lambda m: m.update(type="frame"),
        lambda m: m.update(frame_id=-1),
        lambda m: m.update(ts="123"),
        lambda m: m.pop("session_id"),
        lambda m: m["hands"][0]["landmarks"].pop(),
        lambda m: m["hands"].append(dict(m["hands"][0])),
    ],
)
def test_validator_rejects(mutate):
    msg = json.loads(serialize(build_landmarks_message(META, [Hand("Right", 0.9, FAKE_LANDMARKS)])))
    mutate(msg)
    with pytest.raises(ValueError):
        validate_landmarks_message(msg)


def test_ws_frame_roundtrip():
    jpeg = b"\xff\xd8\xff\xe0fakejpeg"
    meta, body = parse_ws_frame(encode_ws_frame(
        {"session_id": "s1", "frame_id": 3, "ts": 99, "encoding": "jpeg", "width": 640, "height": 480}, jpeg))
    assert meta == FrameMeta("s1", 3, 99, 640, 480) and body == jpeg


@pytest.mark.parametrize(
    "meta,body",
    [
        ({"session_id": "s1", "frame_id": 1}, b"\xff\xd8\xff"),                      # missing ts
        ({"session_id": "", "frame_id": 1, "ts": 1}, b"\xff\xd8\xff"),               # empty session
        ({"session_id": "s1", "frame_id": 1, "ts": 1, "encoding": "png"}, b"\xff\xd8\xff"),
        ({"session_id": "s1", "frame_id": 1, "ts": 1}, b"\x89PNG"),                  # not JPEG
        ({"session_id": "s1", "frame_id": 1.5, "ts": 1}, b"\xff\xd8\xff"),
    ],
)
def test_ws_frame_rejects(meta, body):
    with pytest.raises(FrameInputError):
        parse_ws_frame(encode_ws_frame(meta, body))


def test_meta_from_headers():
    meta = meta_from_headers({"x-session-id": "s9", "x-frame-id": "12", "x-ts": "1793235000123"})
    assert meta == FrameMeta("s9", 12, 1793235000123)
