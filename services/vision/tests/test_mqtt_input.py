"""MQTT frame input from the gateway.

- Parsing tests use the gateway's own packet code (services/gateway/src/protocol.py,
  loaded read-only) so a change in the gateway wire format breaks these tests.
- The broker test needs a running MQTT broker: set VISION_MQTT_BROKER=host:port
  (e.g. `docker run --rm -d -p 127.0.0.1:18830:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf`).
"""

from __future__ import annotations

import importlib.util
import os
import struct
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.frame_input import FrameInputError, parse_mqtt_frame
from tools import frames

GATEWAY_PROTOCOL = Path(__file__).resolve().parents[2] / "gateway" / "src" / "protocol.py"
PREFIX = "hgw/v1"


def _load_gateway_protocol():
    if not GATEWAY_PROTOCOL.is_file():
        pytest.skip(f"gateway source not available at {GATEWAY_PROTOCOL} (e.g. inside the vision container)")
    spec = importlib.util.spec_from_file_location("gateway_protocol", GATEWAY_PROTOCOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gateway():
    return _load_gateway_protocol()


@pytest.fixture(scope="module")
def jpeg():
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".jpg", np.zeros((480, 640, 3), np.uint8))
    return buf.tobytes()


def _gateway_packet(gateway, jpeg, session_id="a" * 32, captured_at_ms=1793235000123):
    contracts = gateway.Contracts(SimpleNamespace(schema_version="1.0.0"))
    # Camera page wire format: uint64 captured_at_ms + JPEG
    envelope, body = contracts.browser_frame(struct.pack(">Q", captured_at_ms) + jpeg, session_id, -1, 2 * 1024 * 1024)
    return envelope, gateway.encode_frame(envelope, body)


def test_parses_real_gateway_packet(gateway, jpeg):
    envelope, packet = _gateway_packet(gateway, jpeg)
    meta, body = parse_mqtt_frame(f"{PREFIX}/frames/{envelope['session_id']}", packet,
                                  topic_prefix=PREFIX, schema_version="1.0.0")
    assert body == jpeg
    assert meta.session_id == envelope["session_id"]
    assert meta.frame_id == envelope["frame_id"] == 0
    assert meta.ts == envelope["captured_at_ms"] == 1793235000123
    assert meta.trace_id == envelope["trace_id"]
    assert (meta.width, meta.height) == (640, 480)


def test_tools_packet_matches_gateway_format(gateway, jpeg):
    # tools.frames.gateway_packet (used by bench/manual tests) must stay compatible with the gateway
    meta, body = gateway.decode_frame(frames.gateway_packet("s1", 3, jpeg))
    gateway.Contracts(SimpleNamespace(schema_version="1.0.0")).validate("frame", meta)
    assert body == jpeg


@pytest.mark.parametrize(
    "mutate,topic_session",
    [
        (lambda m: m.update(message_type="landmarks"), None),
        (lambda m: m.update(content_type="image/png"), None),
        (lambda m: m.update(schema_version="2.0.0"), None),
        (lambda m: m.update(byte_length=m["byte_length"] + 1), None),
        (lambda m: m.pop("trace_id"), None),
        (lambda m: m.pop("captured_at_ms"), None),
        (lambda m: m.update(frame_id=-1), None),
        (lambda m: None, "other-session"),
    ],
)
def test_rejects_invalid_packets(gateway, jpeg, mutate, topic_session):
    envelope, _ = _gateway_packet(gateway, jpeg)
    envelope = dict(envelope)
    mutate(envelope)
    packet = gateway.encode_frame(envelope, jpeg)
    topic = f"{PREFIX}/frames/{topic_session or 'a' * 32}"
    with pytest.raises(FrameInputError):
        parse_mqtt_frame(topic, packet, topic_prefix=PREFIX, schema_version="1.0.0")


@pytest.mark.parametrize("topic", ["hgw/v1/frames", "hgw/v2/frames/abc", "hgw/v1/frames/abc/extra", "other"])
def test_rejects_unexpected_topics(gateway, jpeg, topic):
    _, packet = _gateway_packet(gateway, jpeg, session_id="abc")
    with pytest.raises(FrameInputError):
        parse_mqtt_frame(topic, packet, topic_prefix=PREFIX, schema_version="1.0.0")


def test_rejects_oversized_payload(gateway, jpeg):
    _, packet = _gateway_packet(gateway, jpeg)
    with pytest.raises(FrameInputError):
        parse_mqtt_frame(f"{PREFIX}/frames/{'a' * 32}", packet, topic_prefix=PREFIX,
                         schema_version="1.0.0", max_bytes=len(packet) - 1)


# -- broker integration (real inference + mock gesture server) -----------------------------

BROKER = os.getenv("VISION_MQTT_BROKER")


@pytest.mark.model
@pytest.mark.skipif(not BROKER, reason="set VISION_MQTT_BROKER=host:port to run against a real MQTT broker")
def test_frames_from_broker_reach_gesture(settings, hand_jpeg, no_hand_jpeg):
    import paho.mqtt.client as mqtt
    from fastapi.testclient import TestClient

    from src.main import create_app
    from tests.mock_gesture_server import MockGestureServer

    host, port = BROKER.rsplit(":", 1)
    prefix = f"test-{uuid.uuid4().hex[:8]}/v1"  # isolate from other publishers on the broker
    gesture = MockGestureServer()
    gesture.start_in_thread()
    app = create_app(replace(
        settings, mqtt_enabled=True, mqtt_host=host, mqtt_port=int(port), mqtt_topic_prefix=prefix,
        gesture_ws_url=f"ws://127.0.0.1:{gesture.port}/ws/landmarks", ws_reconnect_interval_s=0.2))

    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    publisher.connect(host, int(port))
    publisher.loop_start()
    session = uuid.uuid4().hex
    try:
        with TestClient(app) as client:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                health = client.get("/healthz").json()
                if health["mqtt_connected"] and health["gesture_ws_connected"]:
                    break
                time.sleep(0.05)
            assert health["mqtt_connected"] and health["gesture_ws_connected"], health
            time.sleep(0.3)  # let the SUBSCRIBE settle before publishing (QoS 0, no retain)

            publisher.publish(f"{prefix}/frames/{session}", frames.gateway_packet(session, 0, hand_jpeg, 111)).wait_for_publish()
            assert gesture.wait_for(1, timeout=10)
            publisher.publish(f"{prefix}/frames/{session}", frames.gateway_packet(session, 1, no_hand_jpeg, 222)).wait_for_publish()
            assert gesture.wait_for(2, timeout=10)
            # malformed packet: counted as invalid, never forwarded
            publisher.publish(f"{prefix}/frames/{session}", b"\x00\x00\x00\x02{}garbage").wait_for_publish()
            time.sleep(0.5)

            snap = client.get("/metrics").json()
            records = client.get("/metrics/frames").json()
    finally:
        publisher.loop_stop()
        publisher.disconnect()
        gesture.stop()

    first, second = gesture.messages[:2]
    assert (first["session_id"], first["frame_id"], first["ts"], len(first["hands"])) == (session, 0, 111, 1)
    assert (second["frame_id"], second["ts"], second["hands"]) == (1, 222, [])
    assert gesture.errors == []
    assert snap["mqtt_message_count"] == 3 and snap["invalid_frame_count"] == 1
    assert snap["processed_frame_count"] == 2
    assert all(r.get("trace_id") for r in records if r["session_id"] == session)
