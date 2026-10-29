import json

import pytest

from src.adapter import (
    MessageError,
    VersionMismatch,
    decode,
    make_trace_id,
    sanitize_session_id,
    to_landmark_message,
)
from src.processor import GestureProcessor
from tests.conftest import load_fixture, to_vision_format


def test_vision_and_repo_formats_become_same_landmarks():
    repo = load_fixture("index_pose")
    a = to_landmark_message(repo)
    b = to_landmark_message(to_vision_format(repo))
    assert [(p.id, p.x, p.y, p.z) for p in a.hands[0].landmarks] == [
        (p.id, p.x, p.y, p.z) for p in b.hands[0].landmarks
    ]
    assert (b.session_id, b.frame_id, b.timestamp_ms) == (repo["session_id"], repo["frame_id"], repo["timestamp_ms"])
    assert b.hands[0].confidence == repo["hands"][0]["confidence"]
    assert b.hands[0].hand_index == 0


@pytest.mark.parametrize("pose", ["index_pose", "fist", "no_hand"])
def test_both_formats_give_same_gesture_sequence(pose):
    """같은 프레임을 두 형식으로 넣으면 결과(gesture, pointer, state_frames)가 같다."""
    results = {}
    for fmt in ("repo", "vision"):
        proc = GestureProcessor()
        outs = []
        for i in range(5):
            repo = load_fixture(pose)
            repo["frame_id"] = i
            repo["timestamp_ms"] = 1_000_000 + i * 33
            data = repo if fmt == "repo" else to_vision_format(repo)
            outs.append(proc.process(to_landmark_message(data)))
        results[fmt] = [(o["gesture"], o["pointer"], o["params"]["state_frames"]) for o in outs]
    assert results["repo"] == results["vision"]


def test_repo_trace_id_is_copied_unchanged():
    repo = load_fixture("index_pose")
    assert to_landmark_message(repo).trace_id == repo["trace_id"]


@pytest.mark.parametrize(
    "session_id, frame_id, expected",
    [
        ("sess-01", 1042, "sess-01-1042"),
        ("s", 7, "s-000007"),  # 8자 이상이 되도록 0으로 채움
        ("a" * 64, 5, "a" * 62 + "-5"),  # 64자를 넘지 않도록 session_id를 자름
    ],
)
def test_make_trace_id(session_id, frame_id, expected):
    trace_id = make_trace_id(session_id, frame_id)
    assert trace_id == expected
    assert 8 <= len(trace_id) <= 64


def test_vision_message_gets_generated_trace_id():
    msg = to_vision_format(load_fixture("index_pose"))
    assert to_landmark_message(msg).trace_id == "sess-01-1042"


def test_session_id_disallowed_chars_are_replaced():
    assert sanitize_session_id("cam.01:a") == "cam_01_a"
    msg = to_vision_format(load_fixture("index_pose"))
    msg["session_id"] = "cam.01:a"
    out = to_landmark_message(msg)
    assert out.session_id == "cam_01_a"
    assert out.trace_id == "cam_01_a-1042"


def test_session_id_replacement_is_logged(caplog):
    sanitize_session_id("cam.01")
    assert "session_id_sanitized" in caplog.text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(type="frame"),
        lambda m: m.update(session_id=""),
        lambda m: m.update(frame_id=-1),
        lambda m: m.update(ts="abc"),
        lambda m: m["hands"][0]["landmarks"].pop(),
        lambda m: m["hands"][0]["landmarks"].__setitem__(0, [0.1, 0.2]),
        lambda m: m["hands"][0].update(score=2.0),
    ],
    ids=["type", "empty_session", "negative_frame", "ts_not_int", "20_points", "point_len", "score"],
)
def test_invalid_vision_messages_raise(mutate):
    msg = to_vision_format(load_fixture("index_pose"))
    mutate(msg)
    with pytest.raises(MessageError):
        to_landmark_message(msg)


def test_repo_major_mismatch_raises_version_error():
    repo = load_fixture("index_pose")
    repo["schema_version"] = "2.0.0"
    with pytest.raises(VersionMismatch):
        to_landmark_message(repo)


def test_repo_missing_required_field_raises():
    repo = load_fixture("index_pose")
    del repo["vision_ms"]
    with pytest.raises(MessageError):
        to_landmark_message(repo)


@pytest.mark.parametrize("payload", [b"not json", b"[1, 2]", b"\xff\xfe"])
def test_decode_rejects_non_object(payload):
    with pytest.raises(MessageError):
        decode(payload)


def test_decode_accepts_bytes():
    raw = json.dumps(to_vision_format(load_fixture("no_hand"))).encode()
    assert decode(raw)["type"] == "landmarks"
