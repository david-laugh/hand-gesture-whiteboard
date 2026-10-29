"""End-to-end through the FastAPI app: real inference, landmarks published over MQTT.

Uses the `mqtt_broker` fixture (VISION_MQTT_BROKER or a throwaway docker mosquitto).
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.frame_input import encode_ws_frame
from src.main import create_app
from tools import frames

pytestmark = pytest.mark.model


@pytest.fixture
def client(settings, mqtt_broker, mqtt_prefix, recorder):
    app = create_app(replace(settings, mqtt_enabled=True, mqtt_host=mqtt_broker.host,
                             mqtt_port=mqtt_broker.port, mqtt_topic_prefix=mqtt_prefix))
    with TestClient(app) as c:
        deadline = time.monotonic() + 10
        while not c.get("/healthz").json()["mqtt_connected"] and time.monotonic() < deadline:
            time.sleep(0.05)
        yield c


def _headers(session_id: str, frame_id: int, ts: int) -> dict:
    return {"Content-Type": "image/jpeg", "X-Session-Id": session_id, "X-Frame-Id": str(frame_id), "X-Ts": str(ts)}


def _wait_metrics(client, predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = client.get("/metrics").json()
        if predicate(snap):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"metrics condition not met: {snap}")


def test_healthz(client, mqtt_prefix):
    body = client.get("/healthz").json()
    assert body["model_loaded"] and body["api"].endswith("HandLandmarker") and body["running_mode"] == "VIDEO"
    assert body["mqtt_connected"] is True
    assert body["frames_topic"] == f"{mqtt_prefix}/frames/+"
    assert body["landmarks_topic"] == f"{mqtt_prefix}/landmarks/{{session_id}}"


def test_detect_endpoint_returns_contract(client, recorder, hand_jpeg):
    r = client.post("/v1/detect", content=hand_jpeg, headers=_headers("http-s", 5, 1793235000123))
    assert r.status_code == 200
    msg = r.json()
    assert (msg["session_id"], msg["frame_id"], msg["ts"]) == ("http-s", 5, 1793235000123)
    assert len(msg["hands"]) == 1 and len(msg["hands"][0]["landmarks"]) == 21
    assert float(r.headers["X-Inference-Ms"]) > 0
    time.sleep(0.3)
    assert recorder.messages == []  # /v1/detect is a debug path and does not publish


def test_frames_are_published_on_session_topic(client, recorder, hand_jpeg, no_hand_jpeg):
    assert client.post("/v1/frames", content=hand_jpeg, headers=_headers("pub", 1, 111)).status_code == 202
    assert recorder.wait_for(1)
    assert client.post("/v1/frames", content=no_hand_jpeg, headers=_headers("pub", 2, 222)).status_code == 202
    assert recorder.wait_for(2)
    first, second = recorder.messages[:2]
    assert (first["session_id"], first["frame_id"], first["ts"], len(first["hands"])) == ("pub", 1, 111, 1)
    assert (second["frame_id"], second["ts"], second["hands"]) == (2, 222, [])
    assert recorder.errors == []  # includes the topic suffix == session_id check
    snap = client.get("/metrics").json()
    assert snap["publish_count"] == 2 and snap["publish_dropped_count"] == 0
    assert snap["timings_ms"]["publish_ms"]["count"] == 2


@pytest.mark.parametrize(
    "content,headers,status",
    [
        (b"\xff\xd8\xff", {"Content-Type": "image/jpeg", "X-Frame-Id": "1", "X-Ts": "1"}, 400),     # no session
        (b"\x89PNG....", {"Content-Type": "image/jpeg", "X-Session-Id": "s", "X-Frame-Id": "1", "X-Ts": "1"}, 400),
        (b"\xff\xd8\xff", {"Content-Type": "application/json", "X-Session-Id": "s", "X-Frame-Id": "1", "X-Ts": "1"}, 415),
    ],
)
def test_bad_input_rejected(client, content, headers, status):
    assert client.post("/v1/frames", content=content, headers=headers).status_code == status


def test_ws_frames_continuous_input_fps(client, recorder, test_image):
    """Burst 60 frames over /ws/frames: every frame is either processed or dropped (latest-wins)."""
    seq = frames.sliding_sequence(test_image, count=30)
    total = 60
    with client.websocket_connect("/ws/frames") as ws:
        for i in range(total):
            meta = {"session_id": "ws-s", "frame_id": i, "ts": 1_000 + i * 33, "encoding": "jpeg",
                    "width": 640, "height": 480}
            ws.send_bytes(encode_ws_frame(meta, seq[i % len(seq)]))
            time.sleep(1 / 60)
        snap = _wait_metrics(client, lambda s: s["processed_frame_count"] + s["dropped_frame_count"] >= total)
    assert snap["input_frame_count"] == total
    assert snap["processed_frame_count"] + snap["dropped_frame_count"] == total
    assert snap["processing_fps"] > 0 and snap["input_fps"] > 0
    assert snap["timings_ms"]["inference_ms"]["p95"] is not None
    assert recorder.wait_for(snap["processed_frame_count"])
    received_ids = [m["frame_id"] for m in recorder.messages if m["session_id"] == "ws-s"]
    assert received_ids == sorted(received_ids) and received_ids[-1] == total - 1
    records = {r["frame_id"]: r for r in client.get("/metrics/frames", params={"limit": 300}).json()
               if r["session_id"] == "ws-s"}
    dropped = {fid for fid, r in records.items() if r["status"] == "dropped"}
    assert dropped.isdisjoint(received_ids)  # dropped frames are never sent as "no hand"


def test_broker_down_is_not_reported_as_no_hand(settings, hand_jpeg):
    # Point at a port with no broker: inference still runs, results count as publish_dropped.
    app = create_app(replace(settings, mqtt_enabled=True, mqtt_host="127.0.0.1", mqtt_port=1))
    with TestClient(app) as c:
        assert c.get("/healthz").json()["mqtt_connected"] is False
        c.post("/v1/frames", content=hand_jpeg, headers=_headers("down", 1, 1))
        snap = _wait_metrics(c, lambda s: s["processed_frame_count"] == 1)
        snap = _wait_metrics(c, lambda s: s["publish_dropped_count"] == 1)
    assert snap["detected_frame_count"] == 1 and snap["no_hand_frame_count"] == 0
    assert snap["publish_count"] == 0
