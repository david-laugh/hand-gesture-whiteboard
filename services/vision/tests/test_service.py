"""End-to-end through the FastAPI app: real inference + publish to the mock gesture server."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.frame_input import encode_ws_frame
from src.main import create_app
from tests.mock_gesture_server import MockGestureServer
from tools import frames

pytestmark = pytest.mark.model


@pytest.fixture
def gesture_server():
    server = MockGestureServer()
    server.start_in_thread()
    yield server
    server.stop()


@pytest.fixture
def client(settings, gesture_server):
    app = create_app(replace(settings, gesture_ws_url=f"ws://127.0.0.1:{gesture_server.port}/ws/landmarks",
                             ws_reconnect_interval_s=0.2))
    with TestClient(app) as c:
        deadline = time.monotonic() + 5
        while not c.get("/healthz").json()["gesture_ws_connected"] and time.monotonic() < deadline:
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


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["model_loaded"] and body["api"].endswith("HandLandmarker") and body["running_mode"] == "VIDEO"
    assert body["gesture_ws_connected"] is True


def test_detect_endpoint_returns_contract(client, hand_jpeg):
    r = client.post("/v1/detect", content=hand_jpeg, headers=_headers("http-s", 5, 1793235000123))
    assert r.status_code == 200
    msg = r.json()
    assert (msg["session_id"], msg["frame_id"], msg["ts"]) == ("http-s", 5, 1793235000123)
    assert len(msg["hands"]) == 1 and len(msg["hands"][0]["landmarks"]) == 21
    assert float(r.headers["X-Inference-Ms"]) > 0


def test_frames_endpoint_publishes_to_gesture(client, gesture_server, hand_jpeg, no_hand_jpeg):
    assert client.post("/v1/frames", content=hand_jpeg, headers=_headers("pub", 1, 111)).status_code == 202
    assert gesture_server.wait_for(1)
    assert client.post("/v1/frames", content=no_hand_jpeg, headers=_headers("pub", 2, 222)).status_code == 202
    assert gesture_server.wait_for(2)
    first, second = gesture_server.messages[:2]
    assert (first["frame_id"], first["ts"], len(first["hands"])) == (1, 111, 1)
    assert (second["frame_id"], second["ts"], second["hands"]) == (2, 222, [])
    assert gesture_server.errors == []


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


def test_ws_frames_continuous_input_fps(client, gesture_server, test_image):
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
    assert gesture_server.wait_for(snap["processed_frame_count"])
    received_ids = [m["frame_id"] for m in gesture_server.messages if m["session_id"] == "ws-s"]
    assert received_ids == sorted(received_ids) and received_ids[-1] == total - 1
    records = {r["frame_id"]: r for r in client.get("/metrics/frames", params={"limit": 300}).json()
               if r["session_id"] == "ws-s"}
    dropped = {fid for fid, r in records.items() if r["status"] == "dropped"}
    assert dropped.isdisjoint(received_ids)  # dropped frames are never sent as "no hand"
